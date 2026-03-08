# route 逻辑模型

- updated_at: 2026-03-08T07:52:00Z

## 实体主表
- table: routes
- 主键: id (BigAutoField)
- 排序规则: `ordering = ['-id']`

## 状态机
- RouteStatus:
  - DISABLED(0): 禁用
  - ACTIVE(1): 正常
- RouteType:
  - PENDING_EXTENSION(0): 待扩展（语义待后续业务明确）

## 关系与约束
- 当前模型无显式 ForeignKey（`drone_type_id` 为外部 ID 引用位）
- 唯一约束: N/A

## 生命周期入口
- 创建入口（历史）：POST /api/v1/routes
- 查询入口（历史增补）：GET /api/v1/routes
- 详情入口（当前轮增补）：GET /api/v1/routes/{id}
- 更新入口：N/A
- 删除入口：N/A

## 当前轮业务语义
- `GET /api/v1/routes/{id}` 仅做“按主键读取”，不引入状态流转与跨实体副作用。
- 成功路径：返回 route 当前快照，业务码 `SUCCESS`。
- 失败路径：
  - 无权限：`PERMISSION_DENIED`
  - 目标 route 不存在：`RESOURCE_NOT_FOUND`


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
