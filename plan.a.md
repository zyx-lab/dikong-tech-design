# 媒体同步与访问 URL 缓存刷新计划

## Summary

媒体处理拆成两个独立机制：

- 媒体归属同步：把 DJI 上云 media files 同步成本地 CloudMediaFile，并绑定到 mission / flightRecord。
- 访问 URL 缓存刷新：把 previewUrl / playbackUrl 当作 DJI 上云返回的临时 signed URL 缓存，按 URL 自身过期时间刷新。

GET /api/v2/inspection/flight-records 和 GET /api/v2/inspection/flight-records/{id} 继续只读本地，不触发 DJI media files 同步，也不入队。任务结束后 30 分钟内由后台 worker 定时同步媒体；历史异常数据用管理命令补
偿。

## DJI URL 来源与过期依据

previewUrl 和 playbackUrl 来源不同，但缓存刷新规则一致。

图片预览地址来源：

GET /api/v1/media/workspaces/{workspace_id}/files/{dji_file_id}/preview-url

视频播放地址来源：

GET /api/v1/media/workspaces/{workspace_id}/files/{dji_file_id}/playback-url

其中：

- {workspace_id} 来自当前 DJI connection；
- {dji_file_id} 使用本地 CloudMediaFile.cloud_file_id；
- DJI 可能通过 Location header、纯字符串 URL、或 JSON 字段返回访问地址；
- 如果响应里没有可用 URL，后端认为该媒体当前无法生成访问地址。

URL 失效判断来源于 URL 本身：

- 如果 URL query 中有 X-Amz-Date 和 X-Amz-Expires，后端解析出实际过期时间；
- 当前时间距离过期不足 5 分钟时，认为需要刷新；
- URL 已过期时，必须刷新；
- URL 为空时，必须刷新；
- 如果 URL 不含可解析的过期参数，后端无法可靠判断失效时间，默认保留现有 URL，不主动刷新，避免频繁打上游。

这个刷新机制与媒体同步状态无关：

- InspectionFlightRecordMediaSyncState 只控制 media files 同步；
- previewUrl / playbackUrl 由 media-files 查询或显式 refresh-url 接口按需刷新；
- 不因为 flight record 已同步完成就认为 URL 永久可用。

## Key Changes

- 保持 flight-records 查询只读：
  - 不调用 DjiConnectionGateway.list_media_files；
  - 不调用 sync_media_for_record()；
  - 不调用 schedule_flight_record_media_sync()；
  - 不写 InspectionFlightRecordMediaSyncState。

- 任务结束后媒体归属同步仍由后台处理：
  - 创建 flight record 时创建 PENDING sync state；
  - deadline_at = record.end_time + 30分钟；
  - worker 在窗口内按现有 retry delay 定时拉 DJI media files；
  - 窗口结束后成功为 COMPLETED，失败为 FAILED；
  - 历史异常用 refresh_v2_flight_record_media 管理命令补偿。

- media-files 查询负责访问 URL 缓存刷新：
  - GET /api/v2/inspection/media-files 返回前检查当前结果项；
  - 图片检查 previewUrl；
  - 视频检查 playbackUrl；
  - 空 URL、已过期 URL、临近过期 URL 都触发刷新；
  - 只刷新当前返回的媒体项，不重新拉 DJI media files 列表。

- 单个媒体详情同样处理：
  - GET /api/v2/inspection/media-files/{id} 对当前媒体按类型刷新访问 URL；
  - 图片刷新 previewUrl；
  - 视频刷新 playbackUrl。

- 显式刷新接口保持明确语义：
  - POST /api/v2/inspection/media-files/{id}/refresh-url
  - {"urlType":"preview"} 刷新图片预览；
  - {"urlType":"playback"} 刷新视频播放；
  - {"urlType":"download"} 刷新下载地址。

## Public API Behavior

CloudMediaFileReadSerializer 保留现有字段：

- previewUrl
- playbackUrl
- downloadUrl
- thumbnailUrl

新增播放状态字段：

- playbackStatus
  - READY：视频有可用 playbackUrl。
  - UNAVAILABLE：视频文件已同步，但 DJI 当前没有返回播放地址。
  - NOT_APPLICABLE：非视频媒体。

- playbackError
  - 正常为空字符串；
  - UNAVAILABLE 时返回原因，例如 未获取到媒体播放地址。

图片示例：

{
"mediaType": "PHOTO",
"previewUrl": "https://...",
"playbackUrl": "",
"playbackStatus": "NOT_APPLICABLE",
"playbackError": ""
}

正常视频示例：

{
"mediaType": "VIDEO",
"playbackUrl": "https://...",
"playbackStatus": "READY",
"playbackError": ""
}

视频暂不可播放示例：

{
"mediaType": "VIDEO",
"playbackUrl": "",
"playbackStatus": "UNAVAILABLE",
"playbackError": "未获取到媒体播放地址"
}

无视频任务：

- GET /api/v2/inspection/media-files?missionId=... 正常返回空列表或只有图片；
- 不调用 playback-url 接口；
- 不报 未获取到媒体播放地址。

## Error Semantics

- URL 为空
  - 图片尝试刷新 previewUrl；
  - 视频尝试刷新 playbackUrl。

- URL 可判断已过期
  - 返回前刷新；
  - 成功则写回数据库并返回新 URL。

- URL 可判断临近过期
  - 距离过期不足 5 分钟时刷新，避免前端刚拿到就失效。

- URL 无法判断过期时间
  - 默认认为当前缓存可用；
  - 不因为无法解析而频繁请求上游。

- 视频存在但 DJI 暂无 playback URL
  - 媒体条目仍返回；
  - playbackUrl=""；
  - playbackStatus="UNAVAILABLE"；
  - 不让整个列表失败。

- 系统级上游错误
  - 例如 DJI 认证失败、连接配置错误、网络不可达；
  - 可以继续返回上游错误，因为这不是单个视频不可播放，而是整体刷新能力不可用。

## Test Plan

- flight-records 查询：
  - GET /api/v2/inspection/flight-records 不调用 DJI；
  - GET /api/v2/inspection/flight-records/{id} 不调用 DJI；
  - 不写 sync state；
  - 不改变媒体计数。

- 媒体同步：
  - 任务结束后创建 PENDING media sync state；
  - worker 在 30 分钟窗口内定时同步；
  - 历史数据可用 refresh_v2_flight_record_media --mission-id <id> 补偿。

- 图片 URL：
  - previewUrl 为空时调用 preview-url 接口；
  - previewUrl 已过期或临近过期时刷新；
  - 无法解析过期时间时不主动刷新。

- 视频 URL：
  - playbackUrl 为空时调用 playback-url 接口；
  - playbackUrl 已过期或临近过期时刷新；
  - 刷新成功写回数据库；
  - DJI 无播放地址时返回 playbackStatus=UNAVAILABLE，列表整体仍 200。

- 无视频任务：
  - 不调用 playback-url；
  - 不返回播放地址错误。

- 回归 mission 29：
  - 媒体同步后应有 1 张图、2 个视频；
  - GET /media-files?missionId=29 返回 3 条；
  - 有 playback URL 的视频返回 READY；
  - DJI 暂无 playback URL 的视频返回 UNAVAILABLE，不能从列表消失，也不能让整个列表失败。

## Assumptions

- DJI 上云返回的 previewUrl / playbackUrl 都是临时访问 URL，可能过期。
- 过期判断优先依赖 URL 自身签名参数，而不是额外数据库字段。
- 为保持最小入侵，暂不新增 preview_url_expires_at / playback_url_expires_at 字段。
- 媒体存在和媒体可访问是两个状态；不能用 playbackUrl 是否为空判断视频是否存在。
- flight-records 查询不承担媒体同步职责；media-files 查询只承担当前媒体项的访问 URL 刷新职责。
