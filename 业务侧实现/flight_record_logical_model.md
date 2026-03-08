# flight record 逻辑模型

- generated_at: 2026-03-08T09:24:39.176004Z

## 实体主表
- table: flight_records
- 主键: id (BigAutoField)

## 状态机
- N/A

## 关系与约束
- mission -> "mission.Mission"
- drone -> "drone.Drone"
- pilot -> "access.StaffProfile"
- 唯一约束: flight_no

## 生命周期入口
- 创建入口: N/A
- 更新入口: N/A


<!-- stage6_round_context::flight_record::GET /api/v1/flight-records -->
## Stage6 同步上下文（可追溯）
```json
{
  "generated_at": "2026-03-08T10:16:51.849636Z",
  "entity": "flight_record",
  "focus_api_keys": [
    "GET /api/v1/flight-records"
  ],
  "stage0_reason": "在已具备按ID读取飞行记录详情后，补齐飞行记录列表查询这一基础只读接口，供外部系统先筛选记录再按ID拉取详情，形成可组合的复盘检索链路。",
  "source_artifacts": [
    "codex_devflow_scaffold/artifacts/stage0/latest.json",
    "codex_devflow_scaffold/artifacts/stage2/latest.json",
    "codex_devflow_scaffold/artifacts/stage4/latest.json",
    "codex_devflow_scaffold/artifacts/stage5/latest.json"
  ],
  "related_paths": [
    "apps/access/management/commands/seed_role_permissions.py",
    "apps/api_v1/urls.py",
    "config/settings.py",
    "apps/flight_record/",
    "apps/flight_record/models.py",
    "apps/flight_record/serializers.py",
    "apps/flight_record/views.py",
    "apps/flight_record/urls.py",
    "apps/flight_record/tests.py"
  ]
}
```
