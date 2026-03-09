# media file 数据字典

- generated_at: 2026-03-08T11:18:06.896282Z
- entity: media_file

## 业务定位
- 关联 API: GET /api/v1/media-files
- 业务目的: 总体模型已定义 media_files 作为飞行记录的媒体资源实体，但当前缺少基础查询接口。先补齐媒体列表只读能力，外部系统可按 flight_record_id/type 条件组合检索，再自行编排后续下载与分析流程。

## 字段定义（来自模型代码）
- id: type=BigAutoField; constraints=pk; verbose=ID
- flight_record: type=ForeignKey; constraints=nullable, blank; verbose=flight_record.FlightRecord
- media_type: type=PositiveSmallIntegerField; constraints=N/A; verbose=媒体类型
- file_name: type=CharField; constraints=max_length=255; verbose=文件名
- file_url: type=CharField; constraints=max_length=500; verbose=文件URL
- thumbnail_url: type=CharField; constraints=blank, max_length=500, default=""; verbose=缩略图URL
- file_size: type=BigIntegerField; constraints=nullable, blank; verbose=文件大小（字节）
- latitude: type=DecimalField; constraints=nullable, blank; verbose=拍摄位置-纬度
- longitude: type=DecimalField; constraints=nullable, blank; verbose=拍摄位置-经度
- captured_at: type=DateTimeField; constraints=nullable, blank; verbose=拍摄时间
- is_deleted: type=BooleanField; constraints=default=False; verbose=是否已删除
- deleted_at: type=DateTimeField; constraints=nullable, blank; verbose=删除时间
- created_at: type=DateTimeField; constraints=N/A; verbose=创建时间

## 序列化读写边界
- MediaFileReadSerializer: id, flight_record, media_type, file_name, file_url, thumbnail_url, file_size, latitude, longitude, captured_at, is_deleted, deleted_at, created_at
- MediaFileWriteSerializer: flight_record, media_type, file_name, file_url, thumbnail_url, file_size, latitude, longitude, captured_at

## 业务状态码覆盖
- 已覆盖: SUCCESS, PERMISSION_DENIED
- 目标集合: SUCCESS, INVALID_PARAMS, PERMISSION_DENIED, RESOURCE_NOT_FOUND, STATE_CONFLICT, IDEMPOTENT_DUPLICATE
- 当前缺口: INVALID_PARAMS, RESOURCE_NOT_FOUND, STATE_CONFLICT, IDEMPOTENT_DUPLICATE

## 权限码
- view_media_file: 可查看媒体文件

## 证据文件
- apps/access/management/commands/seed_role_permissions.py
- apps/api_v1/urls.py
- apps/api_v1/views.py
- config/settings.py
- apps/media_file/
- apps/media_file/models.py
- apps/media_file/serializers.py
- apps/media_file/views.py
- apps/media_file/urls.py
- apps/media_file/tests.py

## 本轮增补（2026-03-08）
- 本轮聚焦 API: GET /api/v1/media-files/{id}
- 入参语义: `id` 为媒体文件主键；接口只提供单条详情读取，不承担上传、删除、转码、归档等写操作。
- 对象过滤语义: 若记录 `is_deleted=true`，按“资源不存在”处理，不对外暴露逻辑删除数据。
- 返回业务码语义:
  - SUCCESS: 记录存在且有查看权限。
  - RESOURCE_NOT_FOUND: `id` 不存在或记录已逻辑删除。
  - PERMISSION_DENIED: 未认证或无 `media_file.view_media_file` 权限。
- 本轮业务码覆盖更新:
  - 已覆盖: SUCCESS, PERMISSION_DENIED, RESOURCE_NOT_FOUND
  - 当前缺口: INVALID_PARAMS, STATE_CONFLICT, IDEMPOTENT_DUPLICATE

## 本轮增补（2026-03-08，迭代16）
- 本轮聚焦 API: DELETE /api/v1/media-files/{id}
- 入参语义: `id` 为媒体文件主键，接口执行单条逻辑删除。
- 删除语义:
  - 记录存在且未删除时，写入 `is_deleted=true` 与 `deleted_at`。
  - 已删除记录与不存在记录统一返回资源不存在，不做二次删除成功兜底。
- 权限语义:
  - 需要 `media_file.manage_media_file` 权限。
  - 未认证或无权限时返回 `PERMISSION_DENIED`。
- 返回业务码语义:
  - SUCCESS: 逻辑删除成功。
  - RESOURCE_NOT_FOUND: 目标记录不存在或已逻辑删除。
  - PERMISSION_DENIED: 未认证或无删除权限。
- 本轮业务码覆盖更新:
  - 已覆盖: SUCCESS, PERMISSION_DENIED, RESOURCE_NOT_FOUND
  - 仍未覆盖: INVALID_PARAMS, STATE_CONFLICT, IDEMPOTENT_DUPLICATE










## 本轮增补（2026-03-08，迭代17）
- 本轮聚焦 API: POST /api/v1/media-files
- 写入语义:
  - 创建一条媒体文件主记录，核心输入为 `flight_record`、`media_type`、`file_name`、`file_url`。
  - 创建时固定写入 `is_deleted=false`、`deleted_at=null`，保持新建即有效。
- 权限语义:
  - 需要 `media_file.manage_media_file` 权限。
  - 未认证或无权限时返回 `PERMISSION_DENIED`。
- 返回业务码语义:
  - SUCCESS: 创建成功（HTTP 201）。
  - INVALID_PARAMS: 必填字段缺失或字段不可写（HTTP 400）。
  - PERMISSION_DENIED: 未认证或无创建权限（HTTP 401/403）。
- 本轮业务码覆盖更新:
  - 已覆盖: SUCCESS, INVALID_PARAMS, PERMISSION_DENIED, RESOURCE_NOT_FOUND
  - 仍未覆盖: STATE_CONFLICT, IDEMPOTENT_DUPLICATE

## 本轮增补（2026-03-09）
- 本轮聚焦 API: PATCH /api/v1/media-files/{id}
- 入参语义:
  - `id` 为媒体文件主键。
  - 请求体至少包含一个可写字段；允许提交的白名单字段与 `MediaFileWriteSerializer` 一致。
- 更新边界:
  - 仅更新媒体文件自身元数据，不开放 `is_deleted`、`deleted_at`、`created_at` 等系统字段写入。
  - 已逻辑删除记录不参与更新，对外统一返回资源不存在。
- 权限语义:
  - 需要 `media_file.manage_media_file` 权限。
  - 未认证或无权限时返回 `PERMISSION_DENIED`。
- 返回业务码语义:
  - SUCCESS: 更新成功。
  - INVALID_PARAMS: 空请求体、字段不可写或字段校验失败。
  - RESOURCE_NOT_FOUND: `id` 不存在或记录已逻辑删除。
  - PERMISSION_DENIED: 未认证或无更新权限。
- 本轮业务码覆盖更新:
  - 已覆盖: SUCCESS, INVALID_PARAMS, PERMISSION_DENIED, RESOURCE_NOT_FOUND
  - 仍未覆盖: STATE_CONFLICT, IDEMPOTENT_DUPLICATE

<!-- stage6_doc_sync::media_file::data_dictionary.md::start -->
## Stage6 本轮同步
- 关联 API: PATCH /api/v1/media-files/{id}
- 提名依据: 当前 media_file 已具备列表、详情、创建与逻辑删除能力，但缺少基础编辑入口，调用方无法修正媒体文件名、URL、拍摄位置、归属飞行记录等元数据。补齐 PATCH 后，媒体实体才能形成最小可维护闭环。
- 业务事件: EVT-001 更新媒体文件
- 业务码覆盖: 目标=SUCCESS, INVALID_PARAMS, PERMISSION_DENIED, RESOURCE_NOT_FOUND, STATE_CONFLICT, IDEMPOTENT_DUPLICATE; 已覆盖=SUCCESS, PERMISSION_DENIED, RESOURCE_NOT_FOUND, INVALID_PARAMS; 缺口=STATE_CONFLICT, IDEMPOTENT_DUPLICATE
- 测试沉淀: 生成用例数: 4, 已执行用例数: 4, 已沉淀到项目测试: 4, 待沉淀 case: N/A, 失败 case: N/A
- 证据文件: apps/media_file/tests.py, apps/media_file/views.py, apps/media_file/models.py, apps/media_file/serializers.py, apps/media_file/urls.py
<!-- stage6_doc_sync::media_file::data_dictionary.md::end -->
