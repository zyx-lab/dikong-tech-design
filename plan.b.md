# Flight Record 详情页受控触发媒体同步计划

## Summary

调整 flight-record 查询策略：

- GET /api/v2/inspection/flight-records：继续只读本地，不触发同步。
- GET /api/v2/inspection/flight-records/{id}：允许触发媒体同步，但必须经过本地同步状态门控。
- 触发同步只发生在“任务刚结束后的同步窗口内”和“状态确实到期需要同步”时。
- 已同步完成、未到下次同步时间、正在同步、超过同步窗口、历史异常数据，都不由详情 GET 反复触发。
- 历史旧数据仍用管理命令补偿，不靠详情 GET 自愈。

## Detail GET Sync Gate

GET /api/v2/inspection/flight-records/{id} 在返回详情前执行一个 best-effort helper，例如：

maybe_sync_media_for_record_on_detail_read(record)

该 helper 只做本地判断，满足条件才调用 DJI。

触发条件：

- record 有可同步的 MissionCloudExecution；
- record 是任务结束后 30 分钟窗口内的飞行记录；
- 存在 InspectionFlightRecordMediaSyncState，或 record 是新近结束但缺少 state，需要补建 state；
- sync state 为 PENDING；
- next_run_at <= now；
- deadline_at >= now；
- 当前没有其他请求或 worker 正在处理该 state。

不触发条件：

- GET /flight-records 列表请求；
- state 为 RUNNING；
- state 为 COMPLETED；
- state 为 FAILED；
- next_run_at > now；
- deadline_at < now；
- record 没有云端执行记录；
- record 属于历史旧数据，已经超过任务结束后 30 分钟窗口；
- record 的执行模式无法通过当前同步规则匹配 DJI media files。

## Pressure Control

通过四层机制避免频繁增加后端压力：

- 接口分层：只有详情 GET 可以触发；列表 GET 永不触发。
- 时间窗口：只在任务结束后 30 分钟内允许详情 GET 补偿触发。
- 状态冷却：复用 next_run_at，一次同步后按现有 retry delay 推迟下一次触发。
- 并发抢占：新增一个轻量 claim 机制，只有成功把 state 从 PENDING 原子更新为 RUNNING 的请求才能执行同步。

claim 语义：

UPDATE sync_state
SET status = RUNNING
WHERE id = ?
AND status = PENDING
AND next_run_at <= now
AND deadline_at >= now

如果更新行数为 0，说明：

- 已经有 worker 或其他详情请求抢到了；
- 或状态已经不满足同步条件；

此时详情 GET 直接返回本地数据，不再同步。

worker 也应复用同一个 claim helper，避免 worker 和详情 GET 同时同步同一条 flight record。

## Response Behavior

详情 GET 的同步是 best-effort：

- 同步成功：
  - 更新 CloudMediaFile 绑定；
  - 更新 photo_count / video_count；
  - 更新 sync state；
  - 当前响应返回刷新后的 flight record 计数。

- 同步失败：
  - 不让详情 GET 返回 502；
  - 更新 sync state 的 attempt_count、last_error、next_run_at；
  - 当前响应仍返回本地 flight record 数据；
  - 后续由 worker 或下一次到期详情 GET 继续尝试。

- 没有触发同步：
  - 直接返回本地数据；
  - 不写库；
  - 不访问 DJI。

建议在 FlightRecordReadSerializer 增加只读同步状态字段，方便前端解释“为什么刚进详情页还没看到最新媒体”：

- mediaSyncStatus
  - PENDING
  - RUNNING
  - COMPLETED
  - FAILED
  - NONE

- mediaSyncLastSyncedAt
- mediaSyncNextRunAt

不建议把 last_error 默认暴露给普通前端页面；如需调试可后续加管理端字段。

## Media Files 与 URL 刷新边界

详情 GET 触发的是媒体归属同步：

- 拉 DJI media files；
- 创建/更新 CloudMediaFile；
- 绑定 mission / flightRecord；
- 更新 photo_count / video_count。

它不负责保证每个视频一定有可播放 URL。

previewUrl / playbackUrl 刷新仍由 media-files 接口负责：

- 图片 previewUrl 来自：

GET /api/v1/media/workspaces/{workspace_id}/files/{dji_file_id}/preview-url

- 视频 playbackUrl 来自：

GET /api/v1/media/workspaces/{workspace_id}/files/{dji_file_id}/playback-url

- URL 是否过期由 URL 自身的签名参数判断，例如 X-Amz-Date / X-Amz-Expires。
- GET /api/v2/inspection/media-files 和 GET /api/v2/inspection/media-files/{id} 负责对当前返回项刷新访问 URL。
- 视频存在但 DJI 暂无播放地址时，媒体条目仍返回，状态为 playbackStatus=UNAVAILABLE。

## Historical Data

历史旧数据不由详情 GET 自愈。

原因：

- 历史数据量不可控；
- 前端用户点击历史详情可能造成大量 DJI 上游调用；
- 很多历史异常需要人工确认归属，不能靠查询接口隐式修复。

历史补偿继续使用管理命令：

python manage.py refresh_v2_flight_record_media --mission-id <missionId>

或：

python manage.py refresh_v2_flight_record_media --flight-record-id <flightRecordId>

批量修复时再使用：

python manage.py refresh_v2_flight_record_media --all-completed

后续可增强命令，增加筛选：

- --missing-media-only
- --sync-status FAILED
- --ended-after
- --ended-before

这些属于运维补偿能力，不放进普通 GET 查询链路。

## Endpoint Rules

GET /api/v2/inspection/flight-records

- 永远只读本地；
- 不触发媒体同步；
- 不入队；
- 不写 sync state；
- 不访问 DJI。

GET /api/v2/inspection/flight-records/{id}

- 先做本地 sync gate 判断；
- 只有到期且处于 30 分钟窗口内的 PENDING state 才触发一次同步；
- 同步成功则当前响应可看到更新后的 photoCount / videoCount；
- 同步失败仍返回本地详情，不阻断用户查看结果。

POST /api/v2/inspection/flight-records/{id}/refresh-media

- 保持显式强制刷新语义；
- {id} 是 flightRecordId，不是 missionId；
- 用户或前端明确点“刷新媒体”时使用；
- 可以返回上游错误，因为这是主动刷新动作。

## Test Plan

- 列表 GET：
  - 不调用 DjiConnectionGateway.list_media_files；
  - 不创建或更新 sync state；
  - 不改变媒体计数。

- 详情 GET，无 sync state 且 record 已超过 30 分钟：
  - 不触发同步；
  - 不访问 DJI；
  - 返回本地数据。

- 详情 GET，新近结束且缺少 sync state：

- 详情 GET，state 为 PENDING 且 next_run_at <= now：
  - 成功 claim；
  - 调用一次 DJI media files；
  - 更新媒体绑定和计数；
  - 返回更新后的 photoCount / videoCount。

- 详情 GET，state 为 PENDING 但 next_run_at > now：
  - 不调用 DJI；
  - 返回本地数据。

- 详情 GET，state 为 RUNNING：
  - 不调用 DJI；
  - 返回本地数据。

- 详情 GET，state 为 COMPLETED：
  - 不调用 DJI；
  - 返回本地数据。

- 详情 GET，state 为 FAILED：
  - 不调用 DJI；
  - 返回本地数据；历史补偿由管理命令处理。

- 并发请求：
  - 多个详情 GET 同时进入，只有一个请求 claim 成功；
  - DJI list_media_files 只调用一次。

- worker 并发：
  - worker 与详情 GET 同时处理同一 state 时，只有一个执行同步。

- 同步失败：
  - 详情接口仍返回 200 和本地 flight record；
  - state 更新 attempt_count、last_error、next_run_at。

## Assumptions

- 用户进入 flight record 详情页代表“正在看某个任务结果”，允许一次受控 best-effort 同步。
- 高频列表页不应触发同步。
- 任务结束后 30 分钟是 DJI 上云延迟的主要补偿窗口。
- 超过 30 分钟的历史问题由脚本或管理命令处理。
- 详情 GET 触发同步是 best-effort，不应因为 DJI 上游失败阻断 flight record 详情读取。
