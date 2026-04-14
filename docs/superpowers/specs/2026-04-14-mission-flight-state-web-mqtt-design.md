# 2026-04-14 Mission Flight State via In-Process MQTT Design

## 1. 背景

当前 `Mission` 已经收敛为本地任务单：

- `mission.status` 持久化状态为 `待执行 / 执行中 / 执行完成`
- `POST /api/v1/missions/{id}/advance` 负责 `待执行 -> 执行中 -> 执行完成`
- 后台同步脚本 `run_dji_sync_scheduler` 只负责设备索引和媒体索引轮询同步
- 系统尚未接入 DJI MQTT 遥测，无法根据无人机实时飞行状态把任务展示为“飞行中”

本次新增需求：

- 在任务展示层增加一个“飞行中”状态
- 对于已经被推进到“执行中”的任务，若对应无人机正在飞行，则接口返回“飞行中”
- 无人机落地后，如用户尚未手动点“完成”，接口应自动恢复显示为“执行中”

用户已确认：

- “飞行中”是实时派生状态，不要求持久化到 `missions` 表
- 当前部署为单 Web 进程，可以接受进程内内存态
- 现有独立同步脚本不承担飞行态共享职责

## 2. 目标

1. 在不改变现有任务持久化主状态机的前提下，对外增加“飞行中”展示状态
2. 通过 DJI MQTT `thing/product/{device_sn}/osd` 观测飞行器实时状态
3. 仅对 `status=执行中` 的 mission 做飞行态覆盖
4. 避免把高频 OSD 心跳写入 SQLite
5. 保持媒体同步、设备同步、任务推进接口现有语义不变

## 3. 非目标

- 不把 `飞行中` 持久化到 `missions.status`
- 不修改 `POST /api/v1/missions/{id}/advance` 的推进链路
- 不通过 MQTT 自动结束 mission
- 不把飞行态 watcher 挂到独立 `run_dji_sync_scheduler` 进程
- 不引入 Redis、Celery 或其他新的共享基础设施
- 不在本阶段接入“按飞行时段自动创建 flight record”之类的新流程

## 4. 关键结论

### 4.1 “飞行中”是派生状态，不是 durable 状态

`missions.status` 继续只保存：

- `0 = 待执行`
- `1 = 执行中`
- `2 = 执行完成`

对外接口新增一层实时覆盖规则：

- 若 mission 持久化状态不是 `执行中`，直接返回原值
- 若 mission 持久化状态是 `执行中`，且该 mission 的 `device_sn` 当前被判定为在飞，则接口返回 `飞行中`
- 若 mission 持久化状态是 `执行中`，且该 mission 的 `device_sn` 当前不在飞，则接口返回 `执行中`

因此，“飞行中”不会写回数据库，也不会影响 `started_at`、`finished_at` 的语义。

### 4.2 飞行态只在单 Web 进程内维护

由于用户当前部署为单进程，本阶段采用最小方案：

- Django Web 进程启动时创建一个 MQTT watcher
- watcher 在内存中维护 `device_sn -> runtime flight state`
- mission 读取接口直接查询这份内存态，动态覆盖返回值

这意味着飞行态不跨进程共享。若未来切为多 worker 或多副本部署，必须改为共享存储方案。

### 4.3 独立同步脚本不负责飞行态

现有 `run_dji_sync_scheduler` 继续只做：

- `sync_device_indexes`
- `sync_media_indexes`

不在该脚本中建立 MQTT 常驻连接。原因：

- 它是独立进程，内存态无法被 Web API 读取
- 即使只有一个脚本进程，也无法满足 mission 接口实时返回“飞行中”的需求

## 5. 协议依据

根据 DJI 官方文档：

- `thing/product/{device_sn}/osd` 是设备定频属性上报 topic
- `thing/product/{device_sn}/state` 是设备事件性属性上报 topic
- `sys/product/{gateway_sn}/status` 主要用于上下线和拓扑更新
- 飞行器属性中存在 `mode_code`，可以区分待机、手动飞行、自动起飞、航线飞行、返航、降落等状态

因此本次飞行态判定以 `osd.data.mode_code` 为主，`state` 作为补充订阅但不作为主判定源。

## 6. 核心设计

### 6.1 Mission 对外状态扩展为四态返回

新增一个对外返回专用状态值：

- `0 = 待执行`
- `1 = 执行中`
- `2 = 执行完成`
- `3 = 飞行中`

其中：

- 数据库模型层仍只接受 `0/1/2`
- API serializer / API schema / API docs 会展示 `3=飞行中`
- 仅 `list` 和 `retrieve` 的响应会根据内存态返回 `3`
- `create`、`update`、`advance` 仍然按原有持久化规则写库

这样前端仍然只读一个 `status` 字段，不需要拼接多个布尔值或额外接口。

### 6.2 进程内 Flight State Registry

新增一个进程内注册表，维护最小运行态：

- key: `device_sn`
- value:
  - `is_airborne: bool`
  - `mode_code: int | None`
  - `last_osd_at: datetime | None`
  - `updated_at: datetime`

职责：

- 接收 watcher 更新
- 对 mission 读取逻辑提供只读查询
- 处理过期判断和兜底回退

该注册表不落库、不走 ORM、不做审计日志。

### 6.3 Web 进程内 MQTT Watcher

新增一个长期存活的 MQTT watcher，嵌入 Django Web 进程。

职责：

- 使用 `DjiWorkspaceConfig` 中缓存的 `mqtt_username / mqtt_password / mqtt_addr` 建立连接
- 连接建立前先通过 `DjiGateway()._ensure_authenticated()` 刷新 workspace 配置，确保凭据存在
- 周期性扫描当前 `status=执行中` 且 `device_sn` 非空的 missions
- 对这些 mission 对应的 `device_sn` 维护订阅集合
- 订阅：
  - `thing/product/{device_sn}/osd`
  - `thing/product/{device_sn}/state`
- 处理断线重连、重复订阅、任务集合变化
- 收到 OSD 后更新 Flight State Registry

连接策略：

- 整个 workspace 只保留一个 MQTT client
- 不按 mission 建连接
- 同一个 `device_sn` 只保留一组订阅

### 6.4 飞行态判定规则

主判定字段：`osd.data.mode_code`

初版规则采用保守显式映射：

判定为“飞行中”的 `mode_code`：

- `3` 手动飞行
- `4` 自动起飞
- `5` 航线飞行
- `6` 全景拍照
- `7` 智能跟随
- `8` ADS-B 躲避
- `9` 自动返航
- `10` 自动降落
- `11` 强制降落
- `12` 三桨叶降落
- `15` APAS
- `16` 虚拟摇杆状态
- `17` 指令飞行
- `18` 空中 RTK 收敛模式

判定为“非飞行中”的 `mode_code`：

- `0` 待机
- `1` 起飞准备
- `2` 起飞准备完毕
- `13` 升级中
- `14` 未连接

解释：

- 对业务来说，只要已经离地并处于飞行任务上下文，就统一展示为“飞行中”
- `自动降落/返航` 阶段仍视为飞行中，直到真正回到非飞行模式
- `起飞准备` 仍显示为“执行中”，避免未离地就显示“飞行中”

### 6.5 过期与兜底规则

如果 watcher 很久没有收到某架设备的 OSD，则不能无限信任旧状态。

新增过期阈值，例如 10 秒：

- 若 `now - last_osd_at <= threshold`，按 `is_airborne` 判定
- 若超时未收到新 OSD，则强制视为 `is_airborne = false`

这样可以避免以下问题：

- MQTT 断流后一直残留“飞行中”
- 进程短暂异常后状态不回落

### 6.6 Mission 读取时的状态覆盖

在 mission 的响应序列化阶段增加一个状态解析器：

1. 读取 mission 持久化状态
2. 若不是 `RUNNING`，直接返回原状态
3. 若是 `RUNNING`：
   - 若 `device_sn` 为空，返回 `RUNNING`
   - 查询 Flight State Registry
   - 若 registry 判定该机在飞，返回 `FLYING`
   - 否则返回 `RUNNING`

这样满足用户规则：

- 用户点开始后，先变“执行中”
- 飞机起飞后，自动显示“飞行中”
- 飞机落地后，若还没点完成，自动恢复“执行中”
- 用户手动点完成后，变“执行完成”

## 7. 生命周期与启动方式

### 7.1 启动时机

推荐在 Django app ready 阶段触发 watcher 启动，但必须加保护：

- 只在实际 Web 进程启动 watcher
- 避免在 `manage.py migrate`、`manage.py test`、`manage.py shell`、`run_dji_sync_scheduler` 等非 Web 场景误启动
- 避免 Django autoreload 主进程和子进程重复启动两个 watcher

因此需要一个显式的启动门禁，至少判断：

- 当前命令是否为 `runserver` 或正式 WSGI 入口
- autoreload 是否处于实际服务子进程
- 测试环境默认关闭 watcher

### 7.2 关闭策略

- 进程退出时尽量优雅断开 MQTT
- watcher 停止后不阻塞 Django 退出
- 线程应设为 daemon，避免卡住服务关闭

## 8. 错误处理

### 8.1 凭据缺失

若 `DjiWorkspaceConfig` 中缺少 `mqtt_addr / mqtt_username / mqtt_password`：

- watcher 启动失败，但不影响 Web API 启动
- mission 接口此时永远不会返回“飞行中”
- 服务日志中打印明确错误，提示 MQTT 凭据不可用

### 8.2 连接失败或断线

- watcher 应自动退避重连
- 重连成功后重新订阅当前需要观测的 `device_sn`
- 重连期间 mission 接口回退为只返回持久化三态中的 `执行中`

### 8.3 非法消息

- JSON 解析失败、topic 不匹配、`mode_code` 缺失时直接丢弃
- 不写数据库，不影响主 API
- 仅记录 debug/warn 级别日志

## 9. 对现有接口的影响

### 9.1 受影响接口

- `GET /api/v1/missions`
- `GET /api/v1/missions/{id}`
- `POST /api/v1/missions/{id}/advance` 的响应体展示值
- `/api/v1/docs/` 中 mission 状态说明与 schema

### 9.2 不受影响接口

- mission 数据库存储结构中的 `started_at` / `finished_at`
- mission 的 create / update / destroy 权限和行为
- `run_dji_sync_scheduler`
- 设备索引同步和媒体索引同步
- route、media、drone 的既有业务语义

## 10. 测试策略

### 10.1 单元测试

新增覆盖：

- `mode_code -> is_airborne` 映射
- 过期状态回退
- `RUNNING + airborne => FLYING`
- `RUNNING + not airborne => RUNNING`
- `PENDING / COMPLETED` 不受 watcher 影响

### 10.2 API 测试

新增 mission API 测试：

- mission 被推进到 `执行中` 后，在 registry 标记飞行中，接口返回 `status=3`
- registry 标记落地后，接口恢复返回 `status=1`
- mission 完成后，即使 registry 仍有飞行态，接口仍返回 `status=2`

### 10.3 运行集成测试

由于测试环境不应真的连接外部 MQTT，测试采用：

- mock watcher / mock registry
- 或者直接注入内存 flight state

本阶段不做真实服务器 MQTT 压测。

## 11. 文档更新

需要同步更新：

- `/api/v1/docs/` OpenAPI schema
- mission 接口说明中的 `status` 枚举和读取方式
- 项目文档中关于 mission 状态来源的说明

文档必须写清楚：

- `飞行中` 是实时派生状态
- 只有 `执行中` 的任务可能显示为 `飞行中`
- 数据库存储状态仍只有三态

## 12. 风险与后续演进

### 12.1 当前方案的边界

当前方案依赖单进程部署。若未来改为：

- gunicorn 多 worker
- uWSGI 多进程
- 多副本部署

则每个进程会维护各自独立的 flight state registry，结果不一致。届时必须改为共享状态存储。

### 12.2 后续可演进方向

若后续部署拓扑升级，可平滑迁移为：

- watcher 独立进程 + Redis 共享状态
- 或 watcher 独立进程 + 小型 runtime state 表

但这些不属于本阶段范围。
