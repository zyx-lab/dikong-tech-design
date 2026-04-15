# 2026-04-15 Flight Record Media Files Detail Design

## 1. 背景

当前 `flight_record` 已重新启用为任务完成后的历史快照，`GET /api/v1/flight-records/{id}` 会返回任务名称、航线名称、执行时段、媒体数量等摘要信息。

与此同时，媒体主表 `media_files` 已经具备两层关系能力：

1. `mission` 关联
2. `flight_record` 关联

但现在的 `flight_record` 详情接口还不能直接告诉前端“这条飞行记录对应哪些媒体可以下载”。前端如果要展示这部分信息，只能额外按 `flight_record_id` 去查媒体接口，再拼接下载入口。

用户这次希望把这层信息直接放进 `flight_record` 详情里，但不希望把临时直链写死到数据库，也不希望把列表接口做重。

## 2. 目标

1. 仅在 `GET /api/v1/flight-records/{id}` 详情接口中新增媒体列表字段
2. 返回该 `flight_record` 当前关联的媒体文件列表
3. 每条媒体包含平台自己的稳定下载入口，而不是 DJI 原始直链
4. 不修改 `GET /api/v1/flight-records` 列表返回结构
5. 不新增 `flight_record` 表字段

## 3. 非目标

1. 不在 `flight_record` 表中保存媒体链接快照
2. 不在 `GET /api/v1/flight-records` 列表接口中展开媒体列表
3. 不按 `mission` 自动兜底推导媒体列表
4. 不修改 `/api/v1/media-files/{id}/download` 的行为
5. 不修改媒体同步脚本
6. 不新增媒体绑定到 `flight_record` 的新接口

## 4. 方案对比

### 4.1 方案 A：在 flight_record 详情里动态展开 media_files

做法：

1. 读取当前 `flight_record`
2. 查询 `MediaFile.objects.filter(flight_record=record, is_deleted=False)`
3. 序列化为只读列表
4. 每项返回平台下载入口 `/api/v1/media-files/{id}/download`

优点：

1. 改动最小
2. 不新增表字段
3. 不保存会过期的上游直链
4. 与现有 `MediaFile.flight_record` 关系保持单一事实来源

缺点：

1. 返回的是当前绑定结果，不是永久快照
2. 后续若媒体解绑或软删，详情页结果会跟着变化

### 4.2 方案 B：只返回 media_file_ids

做法：

1. `flight_record` 详情只返回 `media_file_ids`
2. 前端再逐个请求媒体详情或下载接口

优点：

1. 后端改动更小

缺点：

1. 前端要额外发请求
2. 不能直接满足“看到媒体链接列表”的目标

### 4.3 方案 C：把媒体链接快照存入 flight_record

做法：

1. 在 `flight_record` 上新增 JSON 字段
2. 在任务完成或媒体绑定时写入链接快照

问题：

1. 与 `MediaFile` 关系重复
2. 会把可变的下载地址写死
3. 需要额外处理快照更新与一致性
4. 超出当前最小改动范围

### 4.4 选型结论

采用方案 A。

原因：

1. 复用现有 `MediaFile.flight_record` 关系即可完成需求
2. 不引入新的数据冗余
3. 不会把 DJI 临时直链写入数据库
4. 与当前“最小正确改动”的目标一致

## 5. 核心设计

### 5.1 返回位置

只修改：

- `GET /api/v1/flight-records/{id}`

不修改：

- `GET /api/v1/flight-records`

这样可以避免列表页响应膨胀，也符合用户明确要求“只在详情接口返回”。

### 5.2 数据来源

`media_files` 的唯一来源是当前 `flight_record` 绑定的媒体记录。

查询条件：

- `flight_record_id = 当前 record.id`
- `is_deleted = false`

本次不按 `mission_id` 做补算或兜底。也就是说：

- 如果媒体只绑定到 `mission`，但还没有绑定到 `flight_record`，它不会出现在 `flight_record` 详情的 `media_files` 中
- 只有明确归属到这条飞行记录的媒体，才会被返回

这样边界最清晰，也避免“任务媒体”和“飞行记录媒体”混用。

### 5.3 返回字段设计

在 `flight_record` 详情的 `data` 中新增：

- `media_files`: 数组

每项建议返回这些只读字段：

- `id`
- `media_type`
- `file_name`
- `thumbnail_url`
- `captured_at`
- `download_url`

其中：

- `download_url` 不是 DJI 原始文件地址
- `download_url` 返回平台自身下载入口：`/api/v1/media-files/{id}/download`

这样前端只需要消费平台 API，不需要理解 DJI 上游下载地址的时效性问题。

### 5.4 排序规则

`media_files` 建议按以下顺序返回：

1. `captured_at` 倒序
2. `id` 倒序

原因：

1. 更符合前端按“最近拍摄媒体优先展示”的直觉
2. 当 `captured_at` 为空时，`id` 倒序仍可提供稳定顺序

### 5.5 序列化结构

建议新增一个 flight-record 专用的嵌套媒体只读 serializer，而不是直接复用完整 `MediaFileReadSerializer`。

原因：

1. `MediaFileReadSerializer` 当前字段较多，包含 `mission_id`、`device_sn`、同步状态等
2. `flight_record` 详情这里只需要展示“这条记录对应哪些媒体可点开下载”
3. 单独的嵌套 serializer 更轻，也更稳定

推荐职责：

- `FlightRecordMediaFileSerializer`
  - 面向 `flight_record` 详情展示
  - 只返回少量展示字段和平台下载地址

### 5.6 接口示例

返回结构示意：

```json
{
  "code": "00000",
  "msg": "success",
  "data": {
    "id": 12,
    "flight_no": "FR-8",
    "mission_name": "城区巡检",
    "route_name": "园区航线",
    "video_count": 3,
    "media_files": [
      {
        "id": 101,
        "media_type": 2,
        "file_name": "DJI_0001.MP4",
        "thumbnail_url": "http://example.com/thumb.jpg",
        "captured_at": "2026-04-15T10:00:00+08:00",
        "download_url": "/api/v1/media-files/101/download"
      }
    ]
  }
}
```

## 6. 权限与范围

`media_files` 的返回不新增独立权限判断。

原因：

1. 当前 `flight_record` 详情本身已经经过租户和 scope 控制
2. 返回的媒体集合仅来自当前 `flight_record` 已绑定媒体
3. 这些媒体本来就在当前租户内，且从属于当前可见的 `flight_record`

也就是说：

- 能看到该 `flight_record` 详情，就能看到它的 `media_files` 列表
- 真正下载媒体时，仍然走 `/api/v1/media-files/{id}/download`，由该接口沿用既有权限控制

## 7. 测试设计

至少补这几类测试：

1. 详情返回媒体列表
   - 给一条 `flight_record` 绑定多条媒体
   - 断言 `GET /api/v1/flight-records/{id}` 返回 `media_files`
   - 断言每项都带平台下载地址

2. 软删除媒体不返回
   - 同一条 `flight_record` 下存在已软删除媒体
   - 断言它不会出现在 `media_files` 中

3. 不串记录
   - 另一条 `flight_record` 的媒体不会混进当前详情

4. 列表接口不膨胀
   - `GET /api/v1/flight-records` 仍不返回 `media_files`

## 8. 风险与边界

1. 这次返回的是动态视图，不是永久快照
2. 若后续媒体解绑或软删，`flight_record` 详情里的 `media_files` 会变化
3. 但这与当前“只做详情展示、不新增快照字段”的取舍一致

如果未来业务明确要求“飞行记录必须冻结媒体清单”，那时再考虑单独设计媒体快照模型或 JSON 快照字段，而不是在本次顺手引入。
