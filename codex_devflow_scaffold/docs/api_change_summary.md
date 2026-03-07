# API Change Summary

- generated_at: 2026-03-07T01:22:14.261777Z
- stage2.new_api_keys: ['GET /api/v1/', 'GET /api/v1/drone-assignments', 'GET /api/v1/drone-assignments/{id}', 'GET /api/v1/drones', 'GET /api/v1/drones/{id}', 'GET /api/v1/health', 'PATCH /api/v1/drones/{id}', 'POST /api/v1/drone-assignments', 'POST /api/v1/drone-assignments/{id}/cancel', 'POST /api/v1/drones', 'POST /api/v1/drones/{id}/disable', 'POST /api/v1/drones/{id}/enable', 'POST /api/v1/drones/{id}/maintenance', 'POST /api/v1/drones/{id}/retire', 'PUT /api/v1/drones/{id}']
- 本轮仅新增 1 个 API（规划约束，候选仍需人工确认）。
- touched_entities: ['drone']
- drone 四件套已对齐检查
- 测试覆盖风险：无新增失败用例
- 权限矩阵无变更
