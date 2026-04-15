# 2026-04-15 Mission Media Finish Grace Window Design

## 1. 背景

当前 DJI 媒体自动绑定 mission 的规则由 [apps/dji_bff/tasks.py](/home/charles/dikong-tech-design/apps/dji_bff/tasks.py) 中的 `_match_mission_for_media()` 实现。

现状问题是：

1. 已同步到平台的媒体文件，其 `captured_at` 可能比 mission 的 `finished_at` 晚 1 到 2 秒。
2. 这类媒体已经属于同一架无人机、同一次任务执行，但因为当前窗口上界严格等于 `finished_at`，所以不会自动绑定 mission。
3. 用户当前明确要求只放宽“任务结束后的少量延迟”，不放宽开始前窗口，也不让 `finished_at` 为空的任务参与自动匹配。

线上已确认的真实例子：

1. 设备 `1581F7FVC252A00CJ5TT` 的两条媒体已经同步落库。
2. 其中一条媒体时间为 `2026-04-15T17:01:46+08:00`，对应任务结束时间为 `2026-04-15T17:01:44.803989+08:00`。
3. 另一条媒体时间为 `2026-04-15T17:04:42+08:00`，对应任务结束时间为 `2026-04-15T17:04:40.811148+08:00`。

这说明当前系统需要一个保守但明确的“结束后宽限窗口”。

## 2. 目标

1. 将已完成任务的媒体自动绑定窗口向后放宽 `5` 秒。
2. 只允许 `finished_at` 非空的 mission 参与自动匹配。
3. 保持“唯一命中才自动绑定”的现有保守策略。
4. 用最小改动解决当前线上未自动绑定的问题。

## 3. 非目标

1. 不放宽 `started_at` 之前的匹配窗口。
2. 不让 `finished_at` 为空的任务参与自动匹配。
3. 不修改媒体同步入口、接口协议或数据库结构。
4. 不把这个宽限时间做成运行时配置项。
5. 不改动人工绑定 mission 的优先级规则。

## 4. 方案对比

### 4.1 方案 A：只放宽 `finished_at` 后 5 秒

规则：

- 候选任务必须 `finished_at` 非空
- 命中条件改为：
  `mission.started_at <= captured_at <= mission.finished_at + 5s`

优点：

1. 与当前线上问题完全对齐。
2. 改动最小。
3. 不增加任务开始前误绑风险。

缺点：

1. 只能处理任务结束后的延迟，不能处理开始前抖动。

### 4.2 方案 B：前后都放宽 5 秒

规则：

- `mission.started_at - 5s <= captured_at <= mission.finished_at + 5s`

问题：

1. 会扩大相邻任务重叠窗口。
2. 不符合当前确认的边界要求。

### 4.3 方案 C：继续允许 `finished_at` 为空并用 `timezone.now()` 兜底

问题：

1. 会让执行中任务参与自动匹配。
2. 绑定结果更依赖同步时机，行为不够稳定。
3. 与用户明确要求冲突。

### 4.4 选型结论

采用方案 A。

## 5. 核心设计

### 5.1 参与自动匹配的任务范围

媒体自动匹配 mission 时，候选任务必须同时满足：

1. 属于当前 tenant
2. `is_deleted = False`
3. `device_sn` 与媒体一致
4. `started_at` 非空
5. `finished_at` 非空
6. 任务状态不是 `PENDING`

其中第 5 条是本次新增的硬约束。

### 5.2 自动绑定窗口

新增模块级常量：

```python
MISSION_MEDIA_FINISH_GRACE_SECONDS = 5
```

对每个候选任务：

1. `window_start = mission.started_at`
2. `window_end = mission.finished_at + timedelta(seconds=MISSION_MEDIA_FINISH_GRACE_SECONDS)`
3. 当 `window_start <= captured_at <= window_end` 时视为命中

### 5.3 唯一命中规则保持不变

仍然沿用现有保守策略：

1. 若命中任务数量为 `1`，则自动绑定该 mission
2. 若命中任务数量为 `0` 或大于 `1`，则不自动绑定

### 5.4 人工绑定优先保持不变

现有逻辑里，已经手工绑定的 `mission` 不能被自动匹配结果覆盖。本次不改变这一规则。

### 5.5 对线上当前数据的预期影响

按新规则：

1. `2026-04-15T17:01:46+08:00` 会命中 `mission13`
2. `2026-04-15T17:04:42+08:00` 会命中 `mission14`

因为它们都落在各自 `finished_at + 5s` 的窗口内，且当前同设备没有额外冲突任务窗口。

## 6. 实现落点

### 6.1 代码改动

修改 [apps/dji_bff/tasks.py](/home/charles/dikong-tech-design/apps/dji_bff/tasks.py)：

1. 增加 `MISSION_MEDIA_FINISH_GRACE_SECONDS = 5`
2. 在 `_match_mission_for_media()` 中增加 `finished_at__isnull=False` 过滤
3. 将 `window_end` 改为 `mission.finished_at + timedelta(seconds=5)`
4. 删除 `finished_at or timezone.now()` 这类兜底逻辑

### 6.2 不改动的部分

1. 不修改 `sync_media_indexes()` 的落库主流程
2. 不修改 `MediaFile` / `TenantMediaIndex` 模型
3. 不修改公开 API
4. 不修改数据库 migration

## 7. 测试设计

在 [apps/dji_bff/tests.py](/home/charles/dikong-tech-design/apps/dji_bff/tests.py) 增加回归测试：

1. `captured_at` 比 `finished_at` 晚 `2` 秒时，能自动绑定
2. `captured_at` 比 `finished_at` 晚超过 `5` 秒时，不能自动绑定
3. `finished_at` 为空时，即使时间接近，也不能自动绑定

同时保留并重跑已有回归，确保以下行为不变：

1. 多个 mission 窗口同时命中时，不自动绑定
2. 手工 mission 绑定优先，不被自动匹配覆盖
3. 无法解析 `captured_at` 或 `device_sn` 的媒体仍按现有逻辑处理

## 8. 验收标准

1. `apps.dji_bff.tests` 全量通过
2. 已完成任务结束后 `5` 秒内到达的媒体可以自动绑定
3. 超过 `5` 秒的媒体不会误绑
4. `finished_at` 为空的任务不会参与自动绑定

## 9. 风险与取舍

### 9.1 相邻任务窗口可能更接近

结束后窗口放宽会略微增加相邻任务重叠概率，但本次只增加 `5` 秒，且系统仍要求“唯一命中”，所以风险可控。

### 9.2 宽限时间仍是硬编码

本次不做配置化，是刻意的 KISS 取舍。当前需求只有固定 `5` 秒，没有引入额外配置面的必要。
