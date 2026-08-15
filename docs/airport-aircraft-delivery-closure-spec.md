# 机场与飞行器需求交付闭环规格

## 1. 目的

在暂时没有 Dock 3 真机调试条件的情况下，将《机场飞行器告警与属性记录》涉及的现有实现推进到以下可交付状态：

```text
代码可合并 + 数据库可迁移 + API 可调用 + mock 自动化通过 + 真机验收待补
```

本规格只补齐现有实现的读取和交付闭环，不重复定义已经完成的 MQTT、直播、KMZ 和立即任务协议。以下文档继续作为各自写入与控制链路的规范：

- `docs/dji-hms-alert-persistence-spec.md`
- `docs/dji-aircraft-cumulative-properties-spec.md`
- `docs/dock3-livestream-spec.md`
- `docs/dji-route-import-and-mission-options-spec.md`

## 2. 当前基线

当前工作区已经具备：

1. DJI HMS 告警生命周期持久化。
2. 飞行器累计航时、里程、架次和当前电池循环次数持久化。
3. 无人机和 Dock 直播能力查询及直播控制。
4. KMZ 导入、替换及 DJI wayline 文件同步。
5. 普通任务、立即执行，以及 RTK/GNSS、返航和失联参数下发。

当前缺口：

1. HMS 告警没有业务查询 API。
2. 资源 API 没有结构化返回飞行器累计属性和快照更新时间。
3. Django、Java、数据库变更尚未形成可部署交付。
4. 没有真机验收条件，只能完成协议 mock 和自动化验证。

## 3. 本期范围

### 3.1 必须实现

1. 新增按 DJI connection 查询 HMS 告警的只读分页接口。
2. 在现有无人机资源响应的 `latestTelemetry` 中增加累计属性。
3. 同步 OpenAPI、schema 测试和前端接口指南。
4. 使用现有 DJI mock 验证 HMS、OSD、直播、KMZ 和立即任务主链路。
5. 整理 Django 迁移、Java 增量 SQL、部署顺序和自动化证据。

### 3.2 明确不实现

- 告警确认、已读、处置、翻译、清理策略和 Redis 缓存。
- 新的飞行器累计属性接口或 OSD 历史表。
- 单次定时、重复定时、连续执行。
- 自动断点续飞。
- 多模态识别。
- 新消息队列、调度框架、功能开关或第二份直播状态。

完成动作继续以 KMZ `wpml:finishAction` 为唯一来源，不在任务 API 中增加同义字段。

## 4. HMS 告警查询 API

### 4.1 接口

```http
GET /api/v2/resource/dji-connections/{id}/hms-alerts
```

接口只读取现有 `HmsAlert`，不得调用 DJI 上游，不得修改告警状态。

### 4.2 权限

- 平台超管可以查询任意 DJI connection。
- 部门管理员只能查询本部门拥有的 DJI connection。
- 其他账号返回 `403`。
- connection 不存在时返回 `404`，不得通过错误差异泄露其他部门连接信息。

本期不实现普通监控账号的资源级 HMS 可见性。当前 HMS 消息可能在同一 Dock 网关作用域内同时携带机场和飞行器告警，而 `HmsAlert` 没有本地资源外键；在来源语义未通过真机确认前，不做不可靠的权限推断。

### 4.3 查询参数

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `gatewaySn` | string | 否 | 精确匹配 MQTT topic 中的网关 SN |
| `fromSn` | string | 否 | 精确匹配消息 `from` SN |
| `code` | string | 否 | 精确匹配 DJI 告警码 |
| `level` | integer | 否 | 精确匹配告警等级 |
| `active` | boolean | 否 | `true` 查询活动告警，`false` 查询已解除告警 |
| `firstReportedAfter` | datetime | 否 | `first_reported_at >=` 指定时间 |
| `firstReportedBefore` | datetime | 否 | `first_reported_at <=` 指定时间 |
| `pageNum` | integer | 否 | 默认 `1` |
| `pageSize` | integer | 否 | 默认 `20`，最大 `100` |

非法整数、布尔值或时间返回 `400`，不得静默忽略。结果固定按 `first_reported_at DESC, id DESC` 排序，本期不增加自定义排序。

### 4.4 响应

```json
{
  "code": "00000",
  "msg": "success",
  "data": {
    "list": [
      {
        "id": 21,
        "djiConnectionId": 3,
        "gatewaySn": "DOCK-SN-001",
        "fromSn": "DOCK-SN-001",
        "alarmKey": "sha256-value",
        "code": "0x16100083",
        "deviceDomain": 3,
        "level": 2,
        "module": 3,
        "rawItem": {},
        "firstReportedAt": "2026-08-14T10:00:00+08:00",
        "lastReportedAt": "2026-08-14T10:05:00+08:00",
        "resolvedAt": null,
        "active": true,
        "createdAt": "2026-08-14T10:00:00+08:00",
        "updatedAt": "2026-08-14T10:05:00+08:00"
      }
    ],
    "total": 1
  }
}
```

`active` 是 `resolved_at IS NULL` 的只读派生值，不增加数据库字段。

## 5. 飞行器累计属性输出

### 5.1 复用接口

不新增接口。以下现有响应继续通过 `latestTelemetry` 返回数据：

```http
GET /api/v2/resource/drones
GET /api/v2/resource/drones/{id}
```

### 5.2 新增字段

```json
{
  "latestTelemetry": {
    "latitude": "31.23040000",
    "longitude": "121.47370000",
    "batteryPercent": 82,
    "totalFlightTime": 128400,
    "totalFlightDistance": "35240.50",
    "totalFlightSorties": 83,
    "batteryCycles": [
      {"sn": "BATTERY-SN-LEFT", "index": 0, "loopTimes": 37}
    ],
    "reportedAt": "2026-08-14T10:05:00+08:00",
    "updatedAt": "2026-08-14T10:05:01+08:00",
    "isStale": false,
    "rawPayload": {}
  }
}
```

规则：

1. `totalFlightDistance` 与现有经纬度等 Decimal 字段一致，序列化为字符串，避免精度丢失。
2. 数据库字段为空时返回 `null`，不得返回 `0` 代替未知值。
3. `batteryCycles` 始终返回数组。
4. `reportedAt` 表示 DJI OSD 时间，`updatedAt` 表示后端最后一次接受并保存该快照的时间。
5. 没有快照时，现有 `latestTelemetry=null` 行为不变。
6. 不删除 `rawPayload`，保证现有调用方兼容。

本期只修改序列化输出，不新增模型字段或迁移。

## 6. Mock 验证

没有真机条件时，以下场景仍是本期强制验收项：

1. HMS 首次出现、持续、解除、重复出现和非法全量消息。
2. HMS 查询权限、筛选、分页、排序和空结果。
3. OSD 累计值首次写入、稀疏更新、乱序消息、非法值和换电。
4. 无人机列表及详情返回结构化累计属性。
5. Dock 和飞行器直播 capacity、start、stop、update、switch、camera-change。
6. KMZ 创建、替换、非法文件及 DJI mock 上传失败。
7. 立即任务五个参数从 Django 进入 Java REST，并进入 `flighttask_prepare`。
8. OpenAPI 路由、请求/响应 schema 和前端指南保持同步。

Mock 只能证明本项目协议映射和状态处理正确，不能证明真实 Dock、飞行器、网络和固件行为正确。

## 7. 测试

实现顺序遵循 TDD：先增加会失败的查询和响应测试，再做最小实现。

Django 至少运行：

```bash
.venv/bin/python manage.py test apps.resource_v2.tests apps.inspection_v2.test_route_kmz apps.inspection_v2.tests apps.api_v2.tests
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
```

Java 至少运行：

```bash
cd feature/DJI-Cloud-API-Demo-main
mvn test
```

测试必须覆盖 `400`、`403`、`404` 边界，以及空列表和 `null` 累计字段。不得只测试成功路径。

## 8. 交付与部署

### 8.1 提交边界

Django 主仓库和 `feature/DJI-Cloud-API-Demo-main` Java 仓库分别提交。每个提交必须包含对应测试；不得只提交 Django 代理而遗漏 Java/SQL 契约。

### 8.2 部署顺序

1. 执行 Java `wayline_job` 增量 SQL。
2. 部署 Java 服务。
3. 执行 Django 迁移。
4. 部署 Django Web。
5. 部署 Django MQTT worker。
6. 执行 mock 冒烟测试。

数据库迁移必须先于消费 HMS 和累计 OSD 的新版 worker，避免缺表或缺列。

## 9. 完成状态

本规格使用两个独立状态，禁止混用：

### 9.1 `AUTOMATED_ACCEPTED`

满足以下全部条件：

1. HMS 查询和累计属性输出符合本规格。
2. Django、Java、迁移和 OpenAPI 检查通过。
3. mock 主链路通过。
4. 两个仓库变更均已提交，部署步骤已记录。

达到此状态后可以合并和部署到非生产验证环境。

### 9.2 `DEVICE_VALIDATED`

在 `AUTOMATED_ACCEPTED` 基础上，使用 Dock 3 和配对飞行器完成：

1. 真实 HMS 告警出现、持续和解除。
2. 开机、飞行、换电后的累计属性。
3. 五个直播 method。
4. 真实 KMZ 立即任务及 MQTT 参数抓包。

当前没有调试条件时，状态必须停留在 `AUTOMATED_ACCEPTED`，并记录 `PENDING_DEVICE_VALIDATION`，不得宣称生产真机验证完成。

## 10. 前端边界

当前仓库不包含业务前端源码。本规格完成后，后端提供告警列表和飞行器属性展示所需接口契约；告警页面、筛选交互和属性卡片属于前端仓库的独立交付项，不作为本后端仓库 `AUTOMATED_ACCEPTED` 的阻塞条件，但仍是原始产品需求完整验收的一部分。

## 11. 最终验收标准

1. 已有 HMS 写入规则不回归，新增查询不修改告警状态。
2. 告警数据不能跨部门读取。
3. 告警列表支持固定筛选、分页和稳定排序。
4. 飞行器累计属性通过现有资源 API 结构化返回。
5. 不新增重复接口、表或基础设施。
6. 自动化通过时明确标记 `AUTOMATED_ACCEPTED`。
7. 无真机时明确保留 `PENDING_DEVICE_VALIDATION`。
8. 高级任务能力保持在后续阶段，不以无效开关或空字段混入本期。
