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
