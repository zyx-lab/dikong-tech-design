# media file 逻辑模型

- generated_at: 2026-03-08T11:18:06.896282Z

## 实体主表
- table: media_files
- 主键: id (BigAutoField)

## 状态机
- N/A

## 关系与约束
- flight_record -> "flight_record.FlightRecord"

## 生命周期入口
- 创建入口: N/A
- 更新入口: N/A

## 本轮增补（2026-03-08）
- 本轮聚焦 API: GET /api/v1/media-files/{id}
- 读取模型语义: 以 `media_files.id` 为对象标识进行单条读取，并沿 `flight_record -> mission/drone` 关系返回关联上下文。
- 可见性约束: 逻辑删除数据（`is_deleted=true`）不参与详情读取，统一按资源不存在处理。
- 权限约束: `retrieve` 与 `list` 共用 `media_file.view_media_file` 权限码，属于同一只读能力域。
- 业务码映射:
  - SUCCESS -> 对象存在且可读
  - RESOURCE_NOT_FOUND -> 对象不存在或被逻辑删除
  - PERMISSION_DENIED -> 访问者无认证或无权限

## 本轮增补（2026-03-08，迭代16）
- 本轮聚焦 API: DELETE /api/v1/media-files/{id}
- 删除模型语义: 以 `media_files.id` 定位对象，执行逻辑删除（`is_deleted=true`、`deleted_at=now`）。
- 一致性约束: 不做物理删除，保留与 `flight_record` 的追溯关系。
- 权限约束: `destroy` 走 `media_file.manage_media_file`，与 `view_media_file` 分离。
- 业务码映射:
  - SUCCESS -> 删除动作完成
  - RESOURCE_NOT_FOUND -> 目标不存在或已删除
  - PERMISSION_DENIED -> 无认证或无删除权限


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
