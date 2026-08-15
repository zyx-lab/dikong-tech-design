# DJI HMS 告警持久化规格

## 1. 目标

后端消费 DJI MQTT 健康告警消息，并将机场、飞行器的每次告警生命周期持久化到数据库。

一次生命周期从告警首次出现在 HMS 全量列表开始，到它从后续全量列表中消失结束。同一告警解除后再次出现时，必须创建新记录，不得覆盖上一次历史。

## 2. 范围

本期只实现：

- 识别 `thing/product/{gateway_sn}/events` 中 `method=hms` 的消息。
- 保存新告警、更新持续告警、标记已解除告警。
- 保留每次告警的开始、最近上报和解除时间。
- 保留 DJI 告警原始字段，供后续展示或解析。
- 继续执行现有 MQTT 最新消息存储和广播逻辑。

本期不实现查询 API、告警文案翻译、Redis 告警缓存、已读/确认/处置流程、历史清理策略，也不将告警强制关联到本地机场或飞行器外键。

## 3. 上游消息

处理条件：

```text
topic:  thing/product/{gateway_sn}/events
method: hms
data.list: array
```

消息示意：

```json
{
  "tid": "tid-1",
  "bid": "bid-1",
  "timestamp": 1786492800000,
  "gateway": "DOCK-SN-001",
  "from": "DOCK-SN-001",
  "method": "hms",
  "data": {
    "list": [
      {
        "code": "0x16100083",
        "device_type": {"domain": 3, "type": 0, "sub_type": 0},
        "imminent": 0,
        "in_the_sky": 0,
        "level": 2,
        "module": 3,
        "args": {}
      }
    ]
  }
}
```

DJI HMS 为全量上报：上一次存在、本次消失的告警表示已经解除。

## 4. 数据模型

在 `resource_v2` 中新增 `HmsAlert`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `dji_connection` | ForeignKey | 接收消息的 DJI 连接，连接删除时级联删除 |
| `gateway_sn` | CharField(128) | 从 MQTT topic 提取的网关/机场 SN |
| `from_sn` | CharField(128) | 消息 `from`；缺失时使用 `gateway_sn` |
| `alarm_key` | CharField(64) | 告警身份指纹 |
| `code` | CharField(64) | DJI 告警码 |
| `device_domain` | IntegerField(null=True) | `device_type.domain`，无法解析时为空 |
| `level` | IntegerField(null=True) | DJI 告警等级 |
| `module` | IntegerField(null=True) | DJI 告警模块 |
| `raw_item` | JSONField | 最近一次上报的单条告警原文 |
| `first_reported_at` | DateTimeField | 本轮首次接收时间 |
| `last_reported_at` | DateTimeField | 本轮最近接收时间 |
| `resolved_at` | DateTimeField(null=True) | 本轮解除时间；为空表示活动中 |
| `created_at` / `updated_at` | DateTimeField | 复用项目时间字段 |

活动告警唯一约束：

```text
(dji_connection, gateway_sn, from_sn, alarm_key)
WHERE resolved_at IS NULL
```

该约束只限制活动记录，因此同一告警解除后可以创建下一条生命周期记录。

## 5. 告警身份

`alarm_key` 用于判断两次上报是否为同一条告警。其输入固定为：

```text
code + device_type + in_the_sky + args
```

生成规则：

1. 将上述字段组成对象；缺失字段保留为空值。
2. 使用 JSON key 排序、无多余空格的规范化 JSON 序列化。
3. 计算 SHA-256，保存 64 位十六进制字符串。

不得使用 Python `hash()`，其结果不跨进程稳定。`imminent`、`level` 和 `module` 不参与身份计算，它们变化时更新当前活动记录，不新建生命周期。

## 6. 持久化规则

每条合法 HMS 消息使用同一个后端接收时间 `received_at`，并在一个数据库事务中完成以下操作：

1. 从 topic 提取 `gateway_sn`，从 payload 提取 `from_sn`。
2. 规范化 `data.list` 中每个合法对象并计算 `alarm_key`。
3. 对本次出现的每个告警查询同作用域内相同 `alarm_key` 的活动记录。
4. 活动记录存在时，更新 `last_reported_at=received_at`、告警字段和 `raw_item`。
5. 活动记录不存在时，创建记录，并令 `first_reported_at=last_reported_at=received_at`、`resolved_at=NULL`。
6. 将同作用域内未出现在本次列表中的活动记录更新为 `resolved_at=received_at`。

“同作用域”固定指：

```text
dji_connection + gateway_sn + from_sn
```

`data.list=[]` 是合法全量消息，必须解除该作用域内全部活动告警。

同一条消息中的重复告警按 `alarm_key` 去重，只处理一次。

## 7. 异常输入

- payload 不是对象：忽略 HMS 处理。
- `data` 不是对象，或 `list` 缺失、不是数组：不写入告警，也不得解除已有告警。
- `list` 中任一元素不是对象或缺少非空 `code`：整条 HMS 状态消息无效，不写入，也不得解除已有告警。
- topic 无法提取 `gateway_sn`：不处理告警。
- 没有 DJI connection：不持久化告警，保持现有无连接调用行为。
- 单条 HMS 持久化失败：事务整体回滚；错误不得影响 worker 后续消费其他 MQTT 消息。

无论 HMS 告警处理结果如何，现有 `MqttLatestMessage` 仍按原流程保存收到的完整 MQTT payload。

## 8. 时间语义

- `first_reported_at`、`last_reported_at`、`resolved_at` 均使用后端接收时间，不依赖设备时钟。
- DJI 原始 `timestamp` 不转换为生命周期时间，仍保存在 `MqttLatestMessage.raw_payload` 中。
- 告警解除时间是后端收到“该告警已从全量列表消失”的消息时间，不代表设备故障实际消失的精确时间。

示例：

```text
10:00 告警 A 首次出现 -> 新建记录 1，first=10:00，last=10:00
10:05 告警 A 仍存在 -> 更新记录 1，last=10:05
10:06 告警 A 消失   -> 更新记录 1，resolved=10:06
11:20 告警 A 再出现 -> 新建记录 2，first=11:20，last=11:20
```

## 9. 接入位置

- MQTT 订阅不变，复用现有 `thing/product/+/events` topic。
- 在现有 worker `handle_message` 中，先识别 `payload.method == "hms"`，再调用 HMS 持久化函数。
- HMS 分支处理完成后不进入飞行任务进度解析。
- 持久化函数放在现有 `apps/resource_v2/mqtt.py`，不新增 service 层或处理器抽象。

## 10. 验收标准

使用一个聚焦测试覆盖完整生命周期：

1. 首次收到包含机场和飞行器告警的 HMS 列表，创建两条活动记录。
2. 再次收到相同列表，不增加记录，只更新 `last_reported_at`。
3. 下一次列表缺少其中一条，缺少的记录写入 `resolved_at`，仍存在的记录保持活动。
4. 收到空列表，当前作用域内剩余活动记录全部解除。
5. 已解除告警再次出现，创建新记录，旧记录保持不变。
6. `list` 缺失、类型错误或包含非法元素时，不新增记录，也不解除活动记录。
7. 非 HMS events 消息继续执行原有 OSD、设备状态和飞行任务事件逻辑。

数据库迁移和该聚焦测试通过后，即视为本规格的实现完成。
