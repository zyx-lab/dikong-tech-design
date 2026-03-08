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


<!-- stage6_round_context::media_file::GET /api/v1/media-files/{id} -->
## Stage6 同步上下文（可追溯）
```json
{
  "generated_at": "2026-03-08T11:48:36.944441Z",
  "entity": "media_file",
  "focus_api_keys": [
    "GET /api/v1/media-files/{id}"
  ],
  "stage0_reason": "在已具备媒体列表检索后，补齐按媒体ID读取详情的基础只读接口，供外部系统先列表筛选再精确拉取单条媒体元数据，保持接口简单且可组合。",
  "source_artifacts": [
    "codex_devflow_scaffold/artifacts/stage0/latest.json",
    "codex_devflow_scaffold/artifacts/stage2/latest.json",
    "codex_devflow_scaffold/artifacts/stage4/latest.json",
    "codex_devflow_scaffold/artifacts/stage5/latest.json"
  ],
  "related_paths": [
    "apps/access/management/commands/seed_role_permissions.py",
    "apps/api_v1/urls.py",
    "apps/api_v1/views.py",
    "config/settings.py",
    "apps/media_file/",
    "apps/media_file/models.py",
    "apps/media_file/serializers.py",
    "apps/media_file/views.py",
    "apps/media_file/urls.py",
    "apps/media_file/tests.py"
  ]
}
```


<!-- stage6_round_context::media_file::DELETE /api/v1/media-files/{id} -->
## Stage6 同步上下文（可追溯）
```json
{
  "generated_at": "2026-03-08T12:29:57.254159Z",
  "entity": "media_file",
  "focus_api_keys": [
    "DELETE /api/v1/media-files/{id}"
  ],
  "stage0_reason": "总体设计已定义 media_files 采用逻辑删除（is_deleted/deleted_at），当前缺少单条媒体的删除入口。补齐该基础接口后，外部可自行组合“查询详情->执行删除->再次查询校验”流程，无需编排型接口。",
  "source_artifacts": [
    "codex_devflow_scaffold/artifacts/stage0/latest.json",
    "codex_devflow_scaffold/artifacts/stage2/latest.json",
    "codex_devflow_scaffold/artifacts/stage4/latest.json",
    "codex_devflow_scaffold/artifacts/stage5/latest.json"
  ],
  "related_paths": [
    "apps/access/management/commands/seed_role_permissions.py",
    "apps/media_file/models.py",
    "apps/media_file/tests.py",
    "apps/media_file/views.py",
    "apps/media_file/migrations/0002_add_manage_media_file_permission.py",
    "apps/media_file/serializers.py",
    "apps/media_file/urls.py"
  ]
}
```


<!-- stage6_round_context::media_file::POST /api/v1/media-files -->
## Stage6 同步上下文（可追溯）
```json
{
  "generated_at": "2026-03-08T13:12:10.606036Z",
  "entity": "media_file",
  "focus_api_keys": [
    "POST /api/v1/media-files"
  ],
  "stage0_reason": "总体设计已定义 media_files 为飞行记录关联的核心实体；当前已具备 GET 列表/详情与 DELETE 逻辑删除，但缺少基础写入入口。补齐 POST 后，外部系统可用“创建媒体元数据->查询->删除”组合业务流，无需新增编排型接口。",
  "source_artifacts": [
    "codex_devflow_scaffold/artifacts/stage0/latest.json",
    "codex_devflow_scaffold/artifacts/stage2/latest.json",
    "codex_devflow_scaffold/artifacts/stage4/latest.json",
    "codex_devflow_scaffold/artifacts/stage5/latest.json"
  ],
  "related_paths": [
    "apps/media_file/serializers.py",
    "apps/media_file/tests.py",
    "apps/media_file/views.py",
    "apps/media_file/models.py",
    "apps/media_file/urls.py"
  ]
}
```

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
