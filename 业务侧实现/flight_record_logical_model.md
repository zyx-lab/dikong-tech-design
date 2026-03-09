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

## 本轮增补（POST /api/v1/flight-records）
- 创建入口: POST /api/v1/flight-records
- 业务规则: 创建时写入 `flight_records` 主表，保持 `flight_no` 唯一约束。
- 业务码: `SUCCESS` / `INVALID_PARAMS` / `PERMISSION_DENIED`。


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


<!-- stage6_round_context::flight_record::POST /api/v1/flight-records -->
## Stage6 同步上下文（可追溯）
```json
{
  "generated_at": "2026-03-08T15:58:25.980661Z",
  "entity": "flight_record",
  "focus_api_keys": [
    "POST /api/v1/flight-records"
  ],
  "stage0_reason": "总体设计已定义 flight_records 为任务执行后的核心记录实体，当前仅有列表/详情读取，缺少基础写入入口。补齐 POST 后，外部系统可组合“创建飞行记录 -> 关联媒体创建 -> 查询复盘”链路，无需编排型接口。",
  "source_artifacts": [
    "codex_devflow_scaffold/artifacts/stage0/latest.json",
    "codex_devflow_scaffold/artifacts/stage2/latest.json",
    "codex_devflow_scaffold/artifacts/stage4/latest.json",
    "codex_devflow_scaffold/artifacts/stage5/latest.json"
  ],
  "related_paths": [
    "apps/access/management/commands/seed_role_permissions.py",
    "apps/flight_record/models.py",
    "apps/flight_record/serializers.py",
    "apps/flight_record/tests.py",
    "apps/flight_record/views.py",
    "apps/flight_record/migrations/0002_alter_flightrecord_options.py",
    "apps/flight_record/urls.py"
  ]
}
```
