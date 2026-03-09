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

### 迭代 C：GET /api/v1/routes/{id}
- 业务目的：提供“按 route_id 精确读取航线详情”的基础能力，供调度或任务模块在引用前校验目标航线。
- 设计边界：只读详情接口，不承担创建、更新、删除，也不引入编排行为。
- 请求语义：路径参数 `id` 为航线主键；调用方需具备 `route.view_route` 权限。
- 响应语义：成功时返回单条航线详情对象（字段与 `RouteReadSerializer` 一致），并携带业务码状态。
- 关键业务码：
  - SUCCESS / OK：详情读取成功
  - PERMISSION_DENIED / NOT_AUTHENTICATED 或 FORBIDDEN：未认证或无查看权限
  - RESOURCE_NOT_FOUND / ROUTE_NOT_FOUND：`id` 不存在或已不可见

### 迭代 D：DELETE /api/v1/routes/{id}
- 业务目的：补齐航线台账删除入口，让外部系统能完成“创建 -> 查询 -> 删除/停用”的最小生命周期闭环。
- 设计边界：
  - 只处理单条 route 删除，不做任务解绑、批量清理或调度编排。
  - DELETE 请求体必须为空；非空按 `INVALID_PARAMS` 处理。
  - 若 route 已被 mission 引用，则不物理删除，改为置为 `DISABLED(0)` 并返回成功。
  - 若 route 未被 mission 引用，则物理删除 route，并同步删除其下属 waypoints。
- 关键业务码：
  - SUCCESS / OK：删除成功，或已引用航线被禁用成功
  - INVALID_PARAMS / VALIDATION_ERROR：DELETE 请求携带 body
  - PERMISSION_DENIED / NOT_AUTHENTICATED 或 FORBIDDEN：未认证或无删除权限
  - RESOURCE_NOT_FOUND / NOT_FOUND：`id` 不存在

## 当前轮增补
### 迭代 E：PATCH /api/v1/routes/{id}
- 业务目的：补齐航线台账的基础编辑入口，让外部系统能修正 route 名称、适配机型、总里程、预计时长等主记录元数据。
- 设计边界：
  - 只更新单条 route 主记录，不承担航点重排、任务解绑、状态流转或删除恢复。
  - PATCH 请求体必须至少包含一个可写字段。
  - `status`、`creator_name` 等生命周期/审计字段不可写；未知字段按 `INVALID_PARAMS` 处理。
- 关键业务码：
  - SUCCESS / OK：局部更新成功
  - INVALID_PARAMS / VALIDATION_ERROR：空 body 或包含不可写字段
  - PERMISSION_DENIED / NOT_AUTHENTICATED 或 FORBIDDEN：未认证或无编辑权限
  - RESOURCE_NOT_FOUND / NOT_FOUND：`id` 不存在

## 本轮实现文件
- apps/route/views.py（`partial_update` 动作）
- apps/route/serializers.py（`RouteWriteSerializer` / `RouteReadSerializer`）
- apps/route/tests.py（更新场景用例）

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

<!-- stage6_doc_sync::route::impl_desc.md::start -->
## Stage6 本轮同步
- 本轮 focus API: POST /api/v1/routes/{id}/enable
- 本轮实现目标: route 当前在被 mission 引用时执行 DELETE 只会软禁用为 DISABLED，但没有任何恢复入口，导致可引用航线会进入不可逆停用状态。补齐 enable 后，route 的软禁用路径才形成可恢复的最小闭环。
- 业务事件: EVT-001 启用航线
- 业务约束: N/A
- 测试沉淀: 生成用例数: 4, 已执行用例数: 4, 已沉淀到项目测试: 4, 待沉淀 case: N/A, 失败 case: N/A
- 关键文件: apps/route/tests.py, apps/route/views.py, apps/route/models.py, apps/route/serializers.py, apps/route/urls.py
<!-- stage6_doc_sync::route::impl_desc.md::end -->
