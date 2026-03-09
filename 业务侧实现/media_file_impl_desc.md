# media file 实现说明

- generated_at: 2026-03-08T11:18:06.896282Z

## 本轮实现目标
- 总体模型已定义 media_files 作为飞行记录的媒体资源实体，但当前缺少基础查询接口。先补齐媒体列表只读能力，外部系统可按 flight_record_id/type 条件组合检索，再自行编排后续下载与分析流程。

## API 行为范围
- 本轮聚焦 API: GET /api/v1/media-files

## 关键业务码
- 已覆盖业务码: SUCCESS, PERMISSION_DENIED
- 未覆盖目标码: INVALID_PARAMS, RESOURCE_NOT_FOUND, STATE_CONFLICT, IDEMPOTENT_DUPLICATE

## 可追溯来源
- codex_devflow_scaffold/artifacts/stage0/latest.json
- codex_devflow_scaffold/artifacts/stage2/latest.json
- codex_devflow_scaffold/artifacts/stage4/latest.json
- codex_devflow_scaffold/artifacts/stage5/latest.json

## 关键实现文件
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
- 新增对象级读取 API: GET /api/v1/media-files/{id}
- 业务价值: 在列表筛选后支持按主键精确获取单条媒体详情，保持“基础接口可组合”原则，避免额外编排型接口。
- 实现位置: `MediaFileViewSet.retrieve`（复用 `BusinessApiResponseMixin` + `ScopedActionPermission`）。
- 边界处理:
  - 仅读取未逻辑删除数据（`is_deleted=false`），已删除记录按不存在返回。
  - 无认证或无查看权限时拒绝访问。
- 业务码语义:
  - SUCCESS: 详情读取成功。
  - RESOURCE_NOT_FOUND: 记录不存在或已逻辑删除。
  - PERMISSION_DENIED: 未认证或无 `media_file.view_media_file`。
- 项目内回归沉淀:
  - `test_retrieve_media_file_should_return_success`
  - `test_retrieve_media_file_not_found_should_return_resource_not_found`
  - `test_retrieve_media_file_deleted_should_return_resource_not_found`
  - `test_retrieve_media_file_without_auth_should_return_permission_denied`
  - `test_retrieve_media_file_without_permission_should_return_permission_denied`

## 本轮增补（2026-03-08，迭代16）
- 新增对象级删除 API: DELETE /api/v1/media-files/{id}
- 业务价值: 补齐媒体文件生命周期的基础删除能力，外部系统可自行组合“查询详情 -> 执行删除 -> 再次查询校验”流程。
- 实现位置: `MediaFileViewSet.destroy` / `perform_destroy`。
- 实现策略:
  - 采用逻辑删除，不做物理删除；
  - 删除时仅更新 `is_deleted` 与 `deleted_at`。
- 权限与业务码:
  - 权限码: `media_file.manage_media_file`
  - SUCCESS: 删除成功（HTTP 200）
  - RESOURCE_NOT_FOUND: 记录不存在或已删除（HTTP 404）
  - PERMISSION_DENIED: 未认证或无权限（HTTP 401/403）
- 项目内回归沉淀:
  - `test_delete_media_file_should_return_success_and_soft_delete`
  - `test_delete_media_file_not_found_should_return_resource_not_found`
  - `test_delete_media_file_deleted_should_return_resource_not_found`
  - `test_delete_media_file_without_auth_should_return_permission_denied`
  - `test_delete_media_file_without_permission_should_return_permission_denied`










## 本轮增补（2026-03-08，迭代17）
- 新增写入 API: POST /api/v1/media-files
- 业务价值: 补齐媒体实体的基础创建能力，外部系统可组合“创建 -> 列表查询 -> 详情读取 -> 逻辑删除”完整链路，无需编排型接口。
- 实现位置: `MediaFileViewSet.create` + `MediaFileWriteSerializer`。
- 请求语义:
  - 必填字段为 `media_type`、`file_name`、`file_url`；
  - `flight_record` 可选，用于绑定飞行记录上下文；
  - 非白名单字段会按参数错误拒绝。
- 返回语义:
  - SUCCESS/OK: 创建成功并返回媒体记录快照（含 `business_code`、`business_detail_code`）。
  - INVALID_PARAMS/VALIDATION_ERROR: 参数缺失或字段不可写。
  - PERMISSION_DENIED: 未认证或无 `media_file.manage_media_file` 权限。
- 项目内回归沉淀:
  - `test_create_media_file_should_return_success`
  - `test_create_media_file_invalid_params_should_return_invalid_params`
  - `test_create_media_file_without_auth_should_return_permission_denied`
  - `test_create_media_file_without_permission_should_return_permission_denied`

## 本轮增补（2026-03-09）
- 新增写入 API: PATCH /api/v1/media-files/{id}
- 业务价值: 补齐媒体文件元数据的基础编辑入口，允许调用方在不触碰上传/删除链路的前提下修正文件名、URL、拍摄位置、媒体类型和归属飞行记录。
- 实现位置: `MediaFileViewSet.partial_update` + `perform_update`，复用 `MediaFileWriteSerializer` 白名单。
- 请求语义:
  - 仅允许局部更新 `flight_record`、`media_type`、`file_name`、`file_url`、`thumbnail_url`、`file_size`、`latitude`、`longitude`、`captured_at`。
  - PATCH 请求体必须至少包含一个可写字段；空 body 直接返回参数错误。
  - 已逻辑删除记录不参与更新，统一按资源不存在处理。
- 返回语义:
  - SUCCESS/OK: 更新成功并返回最新媒体记录快照。
  - INVALID_PARAMS/VALIDATION_ERROR: 空请求体、字段校验失败或提交了不可写字段。
  - RESOURCE_NOT_FOUND/NOT_FOUND: 目标记录不存在或已逻辑删除。
  - PERMISSION_DENIED: 未认证或无 `media_file.manage_media_file` 权限。
- 审计语义:
  - 成功更新后写入 `MEDIA_FILE_UPDATE` 审计日志，保留 before/after 快照。
- 项目内回归沉淀:
  - `test_patch_media_file_should_return_success`
  - `test_patch_media_file_empty_body_should_return_invalid_params`
  - `test_patch_media_file_not_found_should_return_resource_not_found`
  - `test_patch_media_file_without_auth_should_return_permission_denied`
  - `test_patch_media_file_without_permission_should_return_permission_denied`

<!-- stage6_doc_sync::media_file::impl_desc.md::start -->
## Stage6 本轮同步
- 本轮 focus API: PATCH /api/v1/media-files/{id}
- 本轮实现目标: 当前 media_file 已具备列表、详情、创建与逻辑删除能力，但缺少基础编辑入口，调用方无法修正媒体文件名、URL、拍摄位置、归属飞行记录等元数据。补齐 PATCH 后，媒体实体才能形成最小可维护闭环。
- 业务事件: EVT-001 更新媒体文件
- 业务约束: N/A
- 测试沉淀: 生成用例数: 4, 已执行用例数: 4, 已沉淀到项目测试: 4, 待沉淀 case: N/A, 失败 case: N/A
- 关键文件: apps/media_file/tests.py, apps/media_file/views.py, apps/media_file/models.py, apps/media_file/serializers.py, apps/media_file/urls.py
<!-- stage6_doc_sync::media_file::impl_desc.md::end -->
