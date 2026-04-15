# 2026-04-15 Flight Record Reactivation Design

## 1. 背景

当前项目里 `flight_record` 实体、表结构和 CRUD 主体其实仍然存在，但业务文档已将其标记为“失效”，主流程也不再依赖它。

与此同时，`mission` 现在已经具备明确的执行时间语义：

- `POST /api/v1/missions/{id}/advance` 负责 `待执行 -> 执行中 -> 执行完成`
- `started_at` 表示任务开始执行时间
- `finished_at` 表示任务完成时间
- `media_files` 已经可以通过 `mission_id` 与任务建立关联

用户当前确认的新目标是重新启用 `flight_record`，但边界必须收紧：

- `flight_record` 作为任务完成后的历史快照存在
- `flight_record` 不再承担任务执行中的状态机职责
- `flight_record` 由系统在任务完成时自动生成
- `flight_record` 允许删、改、查，但不允许手动创建

这次调整的重点，不是重做一个全新的执行系统，而是把现有 `flight_record` 收敛成“任务完成后的归档记录”。

## 2. 目标

1. 重新启用 `flight_record` 作为有效业务实体
2. 在 `mission` 从“执行中”推进到“执行完成”时自动生成一条飞行记录
3. 保留 `flight_record` 的查询、读取、更新、删除能力
4. 删除改为软删除
5. 禁止手动创建 `flight_record`
6. 用最小改动复用现有模型、权限和接口骨架

## 3. 非目标

1. 不重做 `flight_record` 的整体数据模型
2. 不引入新的“飞行记录状态流转”主流程
3. 不让 `flight_record` 驱动 `mission` 或 `media` 的主逻辑
4. 不对历史已完成任务做自动补录或回填
5. 不把 `flight_record.video_count` 改造成实时动态统计字段
6. 不新增机场实体或机场关联逻辑
7. 不新增手动创建飞行记录的后台兼容接口

## 4. 方案对比

### 4.1 方案 A：复用现有 `flight_record` 实体并收敛职责

特点：

1. 保留现有 `flight_records` 表
2. 增补少量字段与约束
3. 任务完成时自动创建记录
4. 保留查、改、删，关闭手动创建

优点：

1. 改动最小
2. 可以复用现有权限和路由结构
3. 对现有数据兼容性最好
4. 测试和回归面清晰

缺点：

1. 会保留一些历史字段包袱
2. `flight_no` 与 `device_sn` 的语义需要明确分离

### 4.2 方案 B：重建全新的飞行记录模型

特点：

1. 按新需求重构数据结构
2. 重新定义所有接口语义

问题：

1. 迁移成本明显更高
2. 容易把需求范围扩大
3. 会引入不必要的兼容和数据清洗问题

### 4.3 方案 C：不落表，只从 `mission + media` 动态拼装

特点：

1. 查询时动态生成“飞行记录”
2. 不维护独立记录实体

问题：

1. 无法满足“允许删、改、查”
2. 无法形成历史快照
3. 数据会随 mission/media 后续变化而漂移

### 4.4 选型结论

采用方案 A。

原因：

1. 满足当前需求
2. 改动最小
3. 风险最低
4. 符合 KISS 和 YAGNI

## 5. 核心设计

### 5.1 `flight_record` 的职责

`flight_record` 重新定义为“任务完成后的历史快照记录”。

它的职责是：

1. 记录某个 mission 完成时的执行摘要
2. 固化任务、航线、无人机、飞手、执行时段和媒体数量
3. 为后续列表查询、详情查看和人工修正提供落库对象

它不再承担：

1. 任务执行中的状态机
2. 任务推进入口
3. 媒体自动归档主锚点
4. 任务是否飞行中的实时判断

### 5.2 自动生成时机

自动生成仅发生在：

- `POST /api/v1/missions/{id}/advance`
- 且任务状态从 `执行中` 推进到 `执行完成`

执行顺序要求：

1. 锁定目标 mission
2. 将 mission 更新为 `执行完成`
3. 写入 `finished_at`
4. 基于 mission 当前快照创建 flight record
5. 在同一个数据库事务中提交

这样可以保证：

1. mission 完成和 flight record 创建要么一起成功
2. 不会出现 mission 已完成但缺失记录的半成状态

### 5.3 一条 mission 对应一条 flight record

本次收口后的业务语义是：

- 一个 mission 最多生成一条 `flight_record`

约束方式：

1. 在模型语义上把 `mission` 视为一对一来源
2. 在落库前显式检查是否已存在同 mission 的记录
3. 推荐在数据库层为 `mission` 增加唯一约束，避免并发重复创建

由于 `mission` 的完成推进本身已经受状态机约束，正常路径下不会重复创建，但数据库唯一约束仍然有必要保底。

### 5.4 字段语义与落值规则

本次不推翻现有字段，而是在最小范围内重定义和补充：

- 新增 `device_sn` 字段
- 保留 `flight_no`
- 保留现有冗余展示字段
- 补充软删除字段

#### 5.4.1 `device_sn`

新增 `device_sn` 字段，专门承载用户定义的“架次编号，相当于设备 SN”。

规则：

1. 自动取自 `mission.device_sn`
2. 不做唯一约束
3. 作为展示和检索依据，不承担系统内部唯一标识职责

#### 5.4.2 `flight_no`

保留现有 `flight_no` 字段作为系统内部唯一编号。

规则：

1. 保留租户内唯一约束
2. 不允许前端写入
3. 由系统自动生成
4. 推荐格式：`FR-{mission_id}`

保留该字段的原因：

1. 兼容现有表约束
2. 避免把 `device_sn` 误用为唯一键
3. 让历史记录仍有稳定唯一编号

#### 5.4.3 其他字段落值

任务完成时自动创建 `flight_record` 时，字段按如下规则写入：

- `mission` = 当前 mission
- `mission_name` = `mission.name`
- `route_name` = `mission.route_name`
- `airport_name` = `""`
- `drone` = `mission.drone`
- `device_sn` = `mission.device_sn`
- `drone_name` = `mission.drone_name`
- `pilot` = `mission.pilot`
- `pilot_name` = `mission.pilot_name`
- `start_time` = `mission.started_at`
- `end_time` = `mission.finished_at`
- `flight_duration` = `max(int((finished_at - started_at).total_seconds()), 0)`
- `photo_count` = `0`
- `video_count` = 当前已关联到该 mission 的未删除媒体数量
- `status` = `COMPLETED`

其中“图片/视频”在当前阶段统一折算到 `video_count`。

### 5.5 `video_count` 的统计口径

`video_count` 在 flight record 创建时做一次快照统计。

统计规则：

1. 统计 `media_files`
2. 条件为：
   - `mission_id = 当前 mission.id`
   - `is_deleted = false`
3. 当前阶段不区分照片和视频类型，统一记入 `video_count`
4. `photo_count` 固定为 `0`

本次不做实时回刷。也就是说：

- 创建 flight record 后，即使 mission 下后续又新增或解绑媒体，也不会自动改写历史记录

这是有意为之，因为 `flight_record` 的定位是“完成时快照”，不是动态投影。

### 5.6 软删除

`flight_record` 新增软删除能力：

- `is_deleted`
- `deleted_at`

删除规则：

1. `DELETE /api/v1/flight-records/{id}` 执行软删除
2. 已软删除记录默认不再出现在列表中
3. 软删除后的详情读取按不存在处理
4. 不提供恢复接口

### 5.7 可更新字段范围

`flight_record` 自动生成后，允许人工修正，但只能修改非锚点字段。

允许更新的字段建议收敛为：

1. `mission_name`
2. `route_name`
3. `airport_name`
4. `drone_name`
5. `pilot_name`
6. `flight_duration`
7. `photo_count`
8. `video_count`

明确禁止通过更新接口修改：

1. `flight_no`
2. `mission`
3. `device_sn`
4. `drone`
5. `pilot`
6. `start_time`
7. `end_time`
8. `status`

原因：

1. 这些字段是自动快照的锚点
2. 允许修改会破坏 flight record 与原始 mission 的对应关系
3. 本次需求并未要求“重建飞行事实”，只允许对展示摘要做补正

## 6. API 设计

### 6.1 保留的接口

保留并重新作为有效业务接口对外开放：

1. `GET /api/v1/flight-records`
2. `GET /api/v1/flight-records/{id}`
3. `PUT /api/v1/flight-records/{id}`
4. `DELETE /api/v1/flight-records/{id}`

### 6.2 关闭的接口

以下接口不再作为有效业务能力保留：

1. `POST /api/v1/flight-records`
2. `POST /api/v1/flight-records/{id}/complete`
3. `POST /api/v1/flight-records/{id}/abort`

返回策略：

- 统一返回 `405 Method Not Allowed`

原因：

1. `flight_record` 不再允许手动创建
2. `flight_record` 不再承担状态流转

### 6.3 `mission advance` 的文档补充

`POST /api/v1/missions/{id}/advance` 的文档需要补充：

1. `待执行 -> 执行中` 时，仅写 mission，不生成 flight record
2. `执行中 -> 执行完成` 时，会自动生成一条 flight record
3. 该 flight record 为任务完成时的历史快照

## 7. 数据迁移

### 7.1 模型变更

本次需要的表结构调整：

1. `flight_records` 新增 `device_sn`
2. `flight_records` 新增 `is_deleted`
3. `flight_records` 新增 `deleted_at`
4. 为 `mission` 增加唯一约束，确保一个 mission 最多对应一条记录

### 7.2 历史数据处理

本次不做历史任务自动补录。

已有历史 `flight_record`：

1. 保留原样
2. 如有空字段，可通过迁移提供安全默认值

推荐默认值：

1. `device_sn = ""`
2. `is_deleted = false`
3. `deleted_at = null`

如果数据库里已存在同一个 `mission` 对应多条 `flight_record` 的异常数据，则需要在落唯一约束前先做一次数据清理；这属于迁移前置检查，不属于业务接口行为本身。

## 8. 测试范围

### 8.1 任务推进链路

需要新增或调整测试覆盖：

1. `待执行 -> 执行中` 时不生成 flight record
2. `执行中 -> 执行完成` 时自动生成一条 flight record
3. 自动生成的字段值符合约定
4. 同一 mission 不会重复生成多条记录

### 8.2 `flight_record` API

需要覆盖：

1. 列表只返回未删除记录
2. 详情读取软删除记录返回 `404`
3. `DELETE` 为软删除
4. `PUT` 只能更新白名单字段
5. 更新锚点字段返回 `400`
6. `POST /flight-records` 返回 `405`
7. `POST /flight-records/{id}/complete` 返回 `405`
8. `POST /flight-records/{id}/abort` 返回 `405`

### 8.3 文档与 Schema

需要更新 API schema 断言，确保：

1. `flight-records` 不再标记为“失效”
2. 文档明确说明自动生成语义
3. 文档不再把 `POST /flight-records` 作为可用创建能力

## 9. 风险与取舍

### 9.1 为什么不让 `video_count` 实时刷新

因为当前需求的核心是“任务完成时自动归档一条记录”，不是“提供一个永远实时变化的聚合视图”。

如果允许它持续变化，会带来两个问题：

1. `flight_record` 失去“快照”语义
2. 后续媒体同步会持续回写旧记录，增加耦合和并发复杂度

### 9.2 为什么保留 `flight_no`

因为直接把 `device_sn` 用作唯一编号会与“同一无人机多次执行任务”的场景冲突。

因此：

1. `device_sn` 负责业务展示
2. `flight_no` 负责内部唯一性

这是当前最稳妥的最小方案。

### 9.3 为什么关闭手动创建

因为本次重新启用 `flight_record` 的前提就是“它是任务完成后的历史归档”。

如果继续允许手动创建：

1. 自动记录和人工记录会混在一起
2. 记录来源会变得不可信
3. 未来再做媒体关联时会增加歧义

因此本次直接收口，不保留双轨语义。

## 10. 实施边界

本 spec 只定义以下范围：

1. 重新启用 `flight_record`
2. 在 mission 完成时自动生成
3. 将删除改为软删除
4. 收口接口面和文档

明确不包含：

1. 历史数据回填脚本
2. 实时媒体数量回刷
3. 机场模型设计
4. 基于 DJI job 或飞行日志的更精细归档链路
