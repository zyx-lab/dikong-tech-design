# V2 Mission Media Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 去掉 v1 同步脚本依赖后，v2 mission 完成、flight record 详情查询、media 列表查询都能按 DJI 平台刷新媒体，并把图片/视频稳定对应到 mission 与 flight record。

**Architecture:** 复用现有 `refresh_platform_media_indexes()` 作为唯一的 v2 媒体刷新入口，避免新增后台脚本。把 flight record 详情序列化改成 v2 平台感知版本，并把 `photo_count` 与 `video_count` 统一改为从 DJI 媒体自动回算。

**Tech Stack:** Django, Django REST Framework, existing `DjiGateway`, existing v2 API module, Django TestCase.

---

## Summary

- 在 v2 mission `RUNNING -> COMPLETED` 时，创建 `FlightRecord` 后立即刷新该 mission 所属 `dji_platform` 的媒体。
- v2 flight record detail 查询前，按 `flight_record.dji_platform` 刷新媒体，而不是依赖 v1 默认 workspace。
- v2 flight record detail 的 `media_files` 查询限定为 `flight_record + dji_platform + dji_index`，不再走旧 v1 serializer 的默认 workspace 过滤。
- 保留 `GET /api/v2/media-files?platform_id=...` 作为手动/列表刷新入口。
- 将 `FlightRecord.photo_count` 和 `video_count` 都改成由当前绑定的 DJI 媒体自动回算。

## Key Implementation Changes

### Task 1: 统一 FlightRecord 媒体计数

**Files:**
- Modify: `apps/flight_record/models.py`
- Modify tests: `apps/media_file/tests.py`, `apps/dji_bff/tests.py`
- Add/modify v2 tests: `apps/media_file/test_v2_api.py`

- [ ] 把 `FlightRecord.sync_video_count_from_media()` 扩展为兼容包装方法，内部调用新的 `sync_media_counts_from_media(flight_record=...)`。
- [ ] 新方法在同一个事务锁里统计 `photo_count` 和 `video_count`，只计算 `is_deleted=False` 且 `dji_index__isnull=False` 的 `MediaFile`。
- [ ] 更新媒体删除、v2 刷新媒体、v1 legacy 同步媒体里所有计数回算调用，确保图片和视频都回算。
- [ ] 测试覆盖：flight record 同时绑定 1 张图片和 1 个视频后，刷新/删除后两个计数字段都正确变化。

### Task 2: Mission 完成时立即刷新平台媒体

**Files:**
- Modify: `apps/api_v2/views.py`
- Add tests: `apps/mission/test_v2_api.py`

- [ ] 在 `V2MissionViewSet` 覆盖 `advance()`，只改 v2 行为，v1 `MissionViewSet.advance()` 保持不变。
- [ ] 当 mission 从 `RUNNING` 推进到 `COMPLETED` 后，先保存 `finished_at`，调用 `FlightRecord.create_from_completed_mission(mission=mission)`，再按 mission 的内部兼容边界和 `dji_platform` 刷新平台媒体索引。
- [ ] 刷新失败时沿用当前异常链路，不吞异常，避免用户误以为素材已刷新。
- [ ] 测试覆盖：mock `DjiGateway.list_media_files()` 返回该平台、该无人机、任务时间窗口内的一张图片和一个视频；调用 `/api/v2/missions/{id}/advance` 完成后，断言 `MediaFile.mission_id`、`flight_record_id`、`photo_count`、`video_count` 都正确。

### Task 3: v2 FlightRecord detail 查询前按平台刷新

**Files:**
- Modify: `apps/api_v2/views.py`
- Modify: `apps/api_v2/serializers.py`
- Add tests: `apps/flight_record/test_v2_api.py`

- [ ] 在 `V2FlightRecordViewSet.retrieve()` 中先 `get_object()` 取得 record。
- [ ] 如果 record 有 `dji_platform`，按 record 的内部兼容边界和 `dji_platform` 刷新平台媒体索引。
- [ ] 刷新后重新读取 record，使用 v2 detail serializer 返回。
- [ ] list 接口不做全量刷新，避免一次列表请求打爆多个 DJI 平台；只保留已有 platform filter。
- [ ] 测试覆盖：访问 `/api/v2/flight-records/{id}` 前数据库没有 media，mock DJI 返回素材，响应详情里能看到 `media_files`，并且只调用该 record 所属平台。

### Task 4: v2 FlightRecord detail 使用平台限定 media_files

**Files:**
- Modify: `apps/api_v2/serializers.py`
- Existing reference: `apps/flight_record/serializers.py`

- [ ] 新增 `V2FlightRecordMediaFileSerializer` 或在 `V2FlightRecordDetailSerializer.get_media_files()` 中直接使用 v2 专用查询。
- [ ] 查询条件固定为 `flight_record=obj`、`dji_platform=obj.dji_platform`、`is_deleted=False`、`dji_index__isnull=False`。
- [ ] 排序沿用现有规则：`-captured_at`, `-id`。
- [ ] 下载、播放、预览 URL 使用 v2 路由名，确保详情里返回 `/api/v2/media-files/{id}/...` 对应的接口，而不是 v1 URL。
- [ ] 不再获取默认 workspace，不再按默认 workspace 过滤 v2 detail 素材。

### Task 5: 保持 v2 media list 手动刷新入口

**Files:**
- Modify only if needed: `apps/api_v2/views.py`
- Existing tests: `apps/media_file/test_v2_api.py`

- [ ] 保留 `GET /api/v2/media-files?platform_id=...` 现有行为：请求时刷新指定平台媒体。
- [ ] 只补测试，不改变接口 wire shape。
- [ ] 测试覆盖：media list 仍要求有效 `platform_id`；只刷新请求的平台，不刷新同一部门体系的其他平台。

## Public API / Interface Changes

- No new endpoint.
- Existing v2 behavior becomes stronger:
  - `POST /api/v2/missions/{id}/advance` completing a mission now also refreshes that mission platform's DJI media.
  - `GET /api/v2/flight-records/{id}` now refreshes that record platform's DJI media before returning detail.
  - `GET /api/v2/media-files?platform_id=...` remains the explicit manual refresh/list endpoint.
- Response shape stays compatible, except v2 flight record detail `media_files[*].download_url/playback_url/preview_url` should point to v2 media routes.
- `photo_count` becomes system-calculated from bound DJI photo media, matching `video_count`.

## Test Plan

- Run targeted v2 tests:
  - `/home/charles/dikong-tech-design/.venv/bin/python manage.py test apps.mission.test_v2_api apps.flight_record.test_v2_api apps.media_file.test_v2_api`
- Run affected legacy/v1 regression tests:
  - `/home/charles/dikong-tech-design/.venv/bin/python manage.py test apps.mission apps.flight_record apps.media_file apps.dji_bff`
- Run full verification:
  - `/home/charles/dikong-tech-design/.venv/bin/python manage.py check`
  - `/home/charles/dikong-tech-design/.venv/bin/python manage.py test`
- Acceptance criteria:
  - v2 mission complete immediately creates flight record and binds DJI photo/video media when media timestamps fall inside the mission window.
  - v2 flight record detail can discover newly uploaded DJI media without running v1 sync scripts.
  - v2 flight record detail never filters by v1 default workspace.
  - Same `device_sn` or `dji_file_id` across different DJI platforms does not cross-bind.
  - Ambiguous media windows remain unbound unless manually bound through `bind-mission`.
  - `photo_count` and `video_count` reflect non-deleted DJI media bound to the flight record.

## Assumptions

- `refresh_platform_media_indexes()` remains the canonical v2 media refresh function.
- v1 background sync code remains only for legacy null-platform data and is not removed in this task.
- Refresh failures should surface as request failures for mission completion and flight record detail, because silently succeeding would hide stale media state.
- `photo_count` is included in this implementation and becomes automatically maintained.
