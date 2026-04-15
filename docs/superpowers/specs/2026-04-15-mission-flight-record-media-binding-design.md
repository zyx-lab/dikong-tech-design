# 2026-04-15 Mission Flight Record Media Binding Design

## 1. 背景

当前线上已经有两条相关能力，但它们还没有闭环：

1. DJI 媒体同步可以按 `device_sn + captured_at` 自动匹配 `mission`
2. `GET /api/v1/flight-records/{id}` 详情接口会返回 `media_files`

问题在于，这两条链路的关联锚点还不一致：

1. 媒体同步当前只会写入 `MediaFile.mission`
2. `flight_record` 详情接口当前只读取 `MediaFile.flight_record`

所以线上现在已经能看到媒体被自动绑定到 `mission`，但 `flight_record` 详情里的 `media_files` 仍然是空数组。

这不是接口展示层的问题，而是数据关系只补到一半的问题。

用户这次进一步明确：

1. `flight_record` 对应的就是某个 `mission` 的 `flight_record`
2. 不希望把 `flight_record` 详情接口改成“按 `mission` 回落兜底”
3. 希望把 `mission -> flight_record` 这一层自动补齐

## 2. 目标

1. 当媒体同步唯一命中某个 `mission` 时，自动补齐它对应的 `flight_record`
2. 保持 `GET /api/v1/flight-records/{id}` 详情接口现有语义不变
3. 让已自动绑定到 `mission` 的媒体，在下一轮同步后自然出现在 `flight_record` 详情接口中
4. 用最小正确改动补齐现有关系链路

## 3. 非目标

1. 不把 `flight_record` 详情接口改成按 `mission` 自动回落查询媒体
2. 不修改 `GET /api/v1/flight-records/{id}` 的返回字段结构
3. 不修改 `MediaFile` / `FlightRecord` 数据库结构
4. 不新增手工绑定 `flight_record` 的单独接口
5. 不修改 `video_count` / `photo_count` 的快照写入逻辑
6. 不处理没有对应 `flight_record` 的异常历史数据修复脚本

## 4. 方案对比

### 4.1 方案 A：同步时自动补 `flight_record` 绑定

做法：

1. 媒体同步先按现有规则得到 `resolved_mission`
2. 若该 `mission` 存在唯一对应且未软删除的 `flight_record`，则把 `MediaFile.flight_record` 一起写入
3. 已有关联时优先保留，不主动覆盖

优点：

1. 数据关系完整
2. `flight_record` 详情接口语义无需变化
3. 其他任何读取 `MediaFile.flight_record` 的地方都能直接复用
4. 和“`flight_record` 是 `mission` 历史快照”这一语义一致

缺点：

1. 需要在同步链路中补一层关系解析
2. 历史媒体要等下一轮同步后才会看到结果

### 4.2 方案 B：只改 `flight_record` 详情接口做 `mission` fallback

做法：

1. 详情先查 `obj.media_files`
2. 如果结果为空，再回落读取 `obj.mission.media_files`

优点：

1. 表面改动更小
2. 接口可以更快展示媒体

缺点：

1. 混淆了 `mission` 媒体和 `flight_record` 媒体的边界
2. 与现有 [2026-04-15-flight-record-media-files-detail-design.md](/home/charles/dikong-tech-design/docs/superpowers/specs/2026-04-15-flight-record-media-files-detail-design.md) 冲突
3. 只修复一个读取入口，不能补齐底层数据关系

### 4.3 方案 C：同时做自动补绑和 fallback

优点：

1. 短期表现最“保险”

缺点：

1. 两套语义并存
2. 更难测试和解释
3. 不符合这次最小改动目标

### 4.4 选型结论

采用方案 A。

原因：

1. `flight_record` 详情接口的现有语义已经明确，没必要改成模糊的 fallback 读法
2. 问题根因是数据关系未补齐，不是展示层没兜底
3. 自动补齐 `MediaFile.flight_record` 后，现有接口自然就能返回 media link

## 5. 现有约束

### 5.1 flight_record 与 mission 的关系

当前 `FlightRecord` 已通过唯一约束保证：

1. 一个 `mission` 最多只有一条 `flight_record`
2. 完成任务时会通过 `FlightRecord.create_from_completed_mission()` 自动创建快照

因此在正常数据下，`mission -> flight_record` 是单值映射。

### 5.2 flight_record 详情接口的当前语义

当前 [apps/flight_record/serializers.py](/home/charles/dikong-tech-design/apps/flight_record/serializers.py) 中的 `get_media_files()` 只查询：

1. `obj.media_files`
2. `is_deleted = False`
3. `dji_index__isnull = False`

本次保持这条语义不变。

### 5.3 媒体模型的一致性约束

`MediaFile.clean()` 当前已经约束：

1. 如果同时写入 `mission` 和 `flight_record`
2. 且 `flight_record.mission_id` 非空
3. 则两者必须一致

所以本次自动补绑必须始终以“同一个 mission 对应的 flight_record”为准，不能跨 mission 写入。

## 6. 核心设计

### 6.1 自动补绑触发点

只修改媒体同步链路：

- [apps/dji_bff/tasks.py](/home/charles/dikong-tech-design/apps/dji_bff/tasks.py) 中的 `sync_media_indexes()`

不修改：

- `flight_record` 详情 serializer
- 公开 API 协议
- 数据表结构

### 6.2 flight_record 解析规则

在 `sync_media_indexes()` 中得到 `resolved_mission` 后，增加 `resolved_flight_record` 解析：

1. 若 `resolved_mission is None`，则 `resolved_flight_record = None`
2. 若 `resolved_mission` 存在，则查询：
   - `mission = resolved_mission`
   - `is_deleted = False`
3. 命中唯一记录时返回该 `flight_record`
4. 没有命中时返回 `None`

依赖前提：

1. `mission` 到 `flight_record` 是单值约束
2. 已软删除的 `flight_record` 不参与自动补绑

### 6.3 新建媒体时的写入规则

创建新的 `MediaFile` 时：

1. 继续按现有逻辑写入 `mission=resolved_mission`
2. 同时写入 `flight_record=resolved_flight_record`

创建新的 `TenantMediaIndex` 时：

1. 保持当前字段不扩展
2. 仍只维护 `mission`
3. 不在 index 模型里新增 `flight_record` 冗余

原因：

1. 当前 `TenantMediaIndex` 的本职是 DJI 文件索引，不是 `flight_record` 读模型
2. `MediaFile.flight_record` 已经足够作为单一事实来源

### 6.4 更新已有媒体时的写入规则

更新已有 `MediaFile` 时，按以下优先级决定最终 `flight_record`：

1. 若 `media_file.flight_record` 已存在，则保留原值
2. 否则若 `resolved_flight_record` 存在，则写入该值
3. 否则保持 `None`

对应的 `mission` 仍沿用当前优先级：

1. 已存在 `mission` 优先
2. 否则使用本轮自动匹配到的 `resolved_mission`

这样可以保证：

1. 已有显式绑定不会被同步覆盖
2. 历史“只有 `mission` 没有 `flight_record`”的媒体，可以在后续同步时自动补齐

### 6.5 不做 serializer fallback

`GET /api/v1/flight-records/{id}` 保持只读 `MediaFile.flight_record`。

不做以下逻辑：

1. 如果 `obj.media_files` 为空，就去读 `obj.mission.media_files`
2. 因为 `mission` 相同就把媒体“视为属于”该 `flight_record`

原因：

1. 当前系统已经有明确的 `flight_record` 外键
2. 展示层 fallback 只会掩盖底层数据缺口
3. 保持接口语义稳定，避免以后再解释“为什么有时是真绑定，有时是回落推导”

### 6.6 对线上现有数据的预期影响

按本次设计，线上已经自动匹配到以下 mission 的媒体：

1. 媒体 `id=2` 已绑定 `mission_id=31`
2. 媒体 `id=1` 已绑定 `mission_id=32`
3. 媒体 `id=3` 已绑定 `mission_id=33`

部署后执行下一轮媒体同步，预期会进一步变成：

1. 媒体 `id=2` 自动补到 `flight_record 20`
2. 媒体 `id=1` 自动补到 `flight_record 21`
3. 媒体 `id=3` 自动补到 `flight_record 22`

随后 `GET /api/v1/flight-records/20|21|22` 的 `media_files` 应自然返回对应下载入口。

## 7. 测试设计

在 [apps/dji_bff/tests.py](/home/charles/dikong-tech-design/apps/dji_bff/tests.py) 增加或扩展回归测试：

1. 同步新媒体时，若唯一命中 `mission` 且该 mission 已有 `flight_record`，则同时写入 `mission` 和 `flight_record`
2. 已存在媒体若当前只有 `mission`、没有 `flight_record`，下一轮同步会自动补齐 `flight_record`
3. 若命中 `mission` 但还没有对应 `flight_record`，则只写 `mission`，不误写 `flight_record`
4. 已存在 `flight_record` 绑定时，同步不会覆盖它

同时重跑现有 `flight_record` 详情测试，确保：

1. 详情接口依旧只展示 `flight_record` 已绑定媒体
2. 不引入 `mission` fallback

## 8. 验收标准

1. `apps.dji_bff.tests` 通过
2. `apps.flight_record.tests` 通过
3. 媒体同步后，命中 mission 的媒体会自动补齐对应 `flight_record`
4. `GET /api/v1/flight-records/{id}` 无需改接口语义即可返回对应 `media_files`

## 9. 风险与取舍

### 9.1 历史数据依赖下一轮同步补齐

本次不额外写一次性回填脚本，而是依赖已有同步任务补齐历史媒体的 `flight_record`。这符合当前最小改动原则，但意味着生效依赖再次同步。

### 9.2 不联动更新 flight_record 的视频数快照

`FlightRecord.video_count` 是创建快照时写入的历史值。本次不因为媒体后续补绑就回写该字段，避免把“历史快照”和“当前实时关联数”混为一谈。
