# route 实现说明

- updated_at: 2026-03-08T07:52:00Z
- entity: route

## 历史实现（保留）
### 迭代 A：POST /api/v1/routes
- 业务目的：创建航线基础台账（`name/route_type/drone_type_id/...`），为后续任务编排提供可引用的航线资源。
- 设计边界：只做“创建主记录”，不承载航点维护、调度编排、状态流转。
- 关键业务码：
  - SUCCESS / OK：创建成功
  - INVALID_PARAMS / VALIDATION_ERROR：参数校验失败
  - PERMISSION_DENIED / NOT_AUTHENTICATED 或 FORBIDDEN：未认证或无创建权限

### 迭代 B：GET /api/v1/routes
- 业务目的：补齐航线管理的基础只读查询入口，支持外部系统组合筛选后再进入任务编排。
- 查询维度：`status`、`route_type`、`name(模糊匹配)`。
- 设计边界：本接口只做列表读取，不新增更新/删除能力。
- 关键业务码：
  - SUCCESS / OK：查询成功，返回分页 `results`
  - PERMISSION_DENIED / NOT_AUTHENTICATED 或 FORBIDDEN：未认证或无查看权限

## 当前轮增补
### 迭代 C：GET /api/v1/routes/{id}
- 业务目的：提供“按 route_id 精确读取航线详情”的基础能力，供调度或任务模块在引用前校验目标航线。
- 设计边界：只读详情接口，不承担创建、更新、删除，也不引入编排行为。
- 请求语义：路径参数 `id` 为航线主键；调用方需具备 `route.view_route` 权限。
- 响应语义：成功时返回单条航线详情对象（字段与 `RouteReadSerializer` 一致），并携带业务码状态。
- 关键业务码：
  - SUCCESS / OK：详情读取成功
  - PERMISSION_DENIED / NOT_AUTHENTICATED 或 FORBIDDEN：未认证或无查看权限
  - RESOURCE_NOT_FOUND / ROUTE_NOT_FOUND：`id` 不存在或已不可见

## 本轮实现文件
- apps/route/views.py（`retrieve` 动作）
- apps/route/serializers.py（`RouteReadSerializer`）
- apps/route/tests.py（详情成功/无权限/不存在用例）

## 可追溯产物
- Stage0 候选：`codex_devflow_scaffold/artifacts/stage0/latest.json`
- Stage2 扫描：`codex_devflow_scaffold/artifacts/stage2/latest.json`
- Stage4 用例：`codex_devflow_scaffold/artifacts/stage4/latest.json`
- Stage5 回归：`codex_devflow_scaffold/artifacts/stage5/latest.json`

## 关键实现文件
- apps/route/views.py
- apps/route/serializers.py
- apps/route/models.py
- apps/route/tests.py
- apps/route/urls.py
- apps/api_v1/urls.py
- apps/api_v1/business_response.py

<!-- stage6_round_context::route::GET /api/v1/routes/{id} -->
## Stage6 同步上下文（可追溯）
```json
{
  "generated_at": "2026-03-08T07:38:38.976856Z",
  "entity": "route",
  "focus_api_keys": [
    "GET /api/v1/routes/{id}"
  ],
  "stage0_reason": "在已具备 POST/GET 列表基础上，补齐航线详情读取能力，供任务编排前按 route_id 精确拉取航线主数据；接口保持只读、单一职责、可组合。",
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
    "apps/drone/urls.py",
    "config/settings.py",
    "apps/route/",
    "apps/route/models.py",
    "apps/route/serializers.py",
    "apps/route/views.py",
    "apps/route/urls.py",
    "apps/route/tests.py"
  ]
}
```
