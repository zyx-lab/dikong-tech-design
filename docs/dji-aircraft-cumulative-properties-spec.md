# 飞行器累计属性持久化规格

## 1. 目标

飞行器开机并通过机场或遥控器上云后，后端持续消费 DJI OSD，以飞行器 SN 对应的本地资源为唯一对象，保存最近一次累计属性：

- 累计飞行航时 `total_flight_time`
- 累计飞行里程 `total_flight_distance`
- 累计飞行架次 `total_flight_sorties`
- 当前装载电池的 SN、位置和循环次数 `battery.batteries[].loop_times`
- DJI 上报时间 `reportedAt`
- 后端成功持久化时间 `updatedAt`

本期保存每架飞行器的最新状态，不保存每一帧 OSD 历史。

本期不新增查询 API、Java 回调、消息队列、定时任务或电池资产表。

## 2. DJI 数据来源

沿用现有 MQTT topic：

```text
thing/product/{aircraft_sn}/osd
```

DJI Dock 飞行器 Property 协议中：

| 字段 | 类型 | 含义 | 单位 |
|---|---|---|---|
| `total_flight_time` | int | 飞行器累计飞行航时 | 秒 |
| `total_flight_distance` | float | 飞行器累计飞行里程 | 米 |
| `total_flight_sorties` | int | 飞行器累计飞行架次 | 次 |
| `battery.batteries[].sn` | text | 电池 SN | - |
| `battery.batteries[].index` | int | 电池位置 | - |
| `battery.batteries[].loop_times` | int | 电池循环次数 | 次 |

这些累计属性属于定频数据，设备在线时约以 `0.5Hz` 上报。飞行器 SN 取自 MQTT topic，不接受 payload 中另一个 SN 覆盖。

Java Cloud SDK 已经能解析上述结构：

- `OsdDockDrone`：`totalFlightTime`、`totalFlightDistance`、`totalFlightSorties`、`battery`
- `OsdRcDrone`：`totalFlightTime`、`totalFlightDistance`、`battery`
- `Battery`：`sn`、`index`、`loopTimes`

其中遥控器配对路径的 `OsdRcDrone` 当前没有 `totalFlightSorties`，未上报时保留数据库旧值或 `null`，不通过本地任务数推算。

## 3. 复用现有链路

当前 Django 已经直接消费并持久化 OSD：

```text
DJI MQTT
  -> run_v2_dji_worker
  -> apply_osd_telemetry
  -> upsert_drone_telemetry_from_osd
  -> DroneTelemetrySnapshot
```

因此不修改 Java 上云代码，不新增 Java REST 回调，也不增加第二个 worker。Java 仍负责现有设备在线、Redis OSD 和 WebSocket 推送；Django 继续作为本项目业务数据的唯一持久化入口。

## 4. 数据模型

扩展现有 `DroneTelemetrySnapshot`。该表通过 `OneToOne(drone)` 保证每架飞行器一条记录，而 `DroneResource.device_sn` 已有唯一约束，因此已经满足“以飞行器 SN 为键”，无需重复保存 SN。

新增字段：

| Django 字段 | 数据库类型 | 可空 | 说明 |
|---|---|---|---|
| `total_flight_time` | `PositiveBigIntegerField` | 是 | 累计秒数 |
| `total_flight_distance` | `DecimalField(max_digits=16, decimal_places=2)` | 是 | 累计米数 |
| `total_flight_sorties` | `PositiveIntegerField` | 是 | 累计架次 |
| `battery_cycles` | `JSONField(default=list)` | 否 | 当前装载电池信息 |

复用已有字段：

| 字段 | 语义 |
|---|---|
| `reported_at` | 当前记录所对应的 DJI OSD 时间 |
| `updated_at` | 后端最后一次成功写入该记录的时间 |
| `raw_payload` | 最近一帧完整原始 OSD，便于排障 |

`battery_cycles` 示例：

```json
[
  {"sn": "BATTERY-SN-LEFT", "index": 0, "loopTimes": 37},
  {"sn": "BATTERY-SN-RIGHT", "index": 1, "loopTimes": 35}
]
```

这里只表达当前飞行器装载的电池。跨飞行器的电池履历、换电历史和电池资产管理不在本期范围；出现该业务需求时再按电池 SN 建独立表。

## 5. 写入规则

在现有 `upsert_drone_telemetry_from_osd` 中一次完成实时遥测和累计属性更新。读取旧记录、比较 `reported_at`、合并累计值和保存必须放在同一数据库事务中，并对已有记录使用 `select_for_update`，避免并发 OSD 相互覆盖：

1. 仅当 topic 对应本地已有 `DroneResource.device_sn` 时落库；未知设备不自动创建资源。
2. 使用 MQTT 消息 `timestamp` 生成 `reported_at`；没有合法 timestamp 时使用接收时间。
3. 新消息早于数据库 `reported_at` 时整条忽略，避免乱序消息覆盖新数据或造成累计值倒退。
4. 累计字段存在且为非负数时，保存数据库旧值与本次上报值的较大者；字段缺失时保留旧值，不能写成 `null`。设备累计计数不因重复上报、固件异常或服务重启而回退。
5. 数值 `0` 是合法值，解析时不能使用 `a or b` 判断字段是否存在。
6. `battery.batteries` 是合法数组时，用其中结构完整的条目替换 `battery_cycles`；字段缺失时保留旧值。
7. 单个电池条目必须有稳定的 `sn` 和非负 `loop_times`；`index` 可空。相同电池 SN 的循环次数取旧值与新值的较大者，新换入的电池按自身 SN 单独计数。非法条目丢弃，不影响其他条目。
8. 飞行器离线后保留最后数据；在线状态仍由 `sys/product/{sn}/status` 管理。
9. 累计字段或电池字段格式异常时只忽略对应字段，不得阻断同一帧合法位置、电量等现有遥测的持久化。

OSD 会持续上报，沿用现有“一帧一次 upsert”。本期不增加节流、批处理和异步队列；实际写入压力不足以证明需要这些机制。

## 6. 改动范围

仅修改现有 Django OSD 链路：

| 文件 | 改动 |
|---|---|
| `apps/resource_v2/models.py` | 给 `DroneTelemetrySnapshot` 增加四个字段 |
| `apps/resource_v2/migrations/0009_*.py` | 数据库迁移 |
| `apps/resource_v2/mqtt.py` | 解析累计属性、电池循环并处理稀疏和乱序 OSD |
| `apps/resource_v2/tests.py` | 累计属性解析、合并与持久化测试 |
| `apps/inspection_v2/tests.py` | worker 真实入口的聚焦测试 |

不修改 Java、不新增服务组件、不新增 API、不保存 OSD 历史。

## 7. 测试

至少覆盖：

1. 首帧 OSD 按飞行器 SN 创建一条最新记录。
2. 后续 OSD 更新累计航时、里程、架次和电池循环。
3. 字段缺失的稀疏 OSD 保留已有累计属性。
4. `0` 值能正确保存。
5. 旧 timestamp 消息不能覆盖新记录。
6. 电池更换后 `batteryCycles` 反映当前装载电池。
7. 非法电池条目被忽略，其他合法条目仍保存。
8. 未知飞行器 OSD 不创建资源或累计属性记录。
9. 接受新 OSD 后 `updated_at` 更新，拒绝乱序 OSD 时不更新。
10. 现有资源 API、飞行任务实时遥测、在线状态和 MQTT latest-message 行为不回归。

迁移只增加可空字段和带默认值的 JSON 字段，不回填历史数据；部署后由首帧有效 OSD 自然补齐。

## 8. 验收标准

1. 飞行器开机或飞行期间，Django worker 能持续消费其 OSD。
2. 每个本地飞行器最多一条累计属性记录，并可通过唯一 SN 对应。
3. `total_flight_time`、`total_flight_distance`、可用时的 `total_flight_sorties` 和当前电池循环次数正确持久化。
4. 稀疏、重复和乱序消息不会清空或回退累计属性。
5. `reported_at` 反映 DJI OSD 时间，`updated_at` 反映后端最后一次接受并持久化 OSD 的时间。
6. 不引入 Java/Django 双写或新的基础设施。
7. 聚焦测试、现有资源 API 回归、`manage.py check` 和迁移检查通过。
8. 最后用一架经机场或遥控器配对的真机验证开机、飞行和换电三个场景；无真机时自动化通过不等同于真机验收。
