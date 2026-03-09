# flight record 实现说明

- generated_at: 2026-03-08T09:24:39.176004Z

## 本轮实现目标
- 当前 flight_record 已具备创建、列表与详情能力，但缺少基础编辑入口；补齐 `PATCH /api/v1/flight-records/{id}` 后，调用方可修正飞行结果元数据，形成最小可维护闭环。

## API 行为范围
- 本轮聚焦 API: PATCH /api/v1/flight-records/{id}
- 唯一性约束字段: flight_no
- 默认状态: status=FlightRecordStatus.IN_PROGRESS

## 关键业务码
- 已覆盖业务码: SUCCESS, INVALID_PARAMS, PERMISSION_DENIED, RESOURCE_NOT_FOUND
- 未覆盖目标码: STATE_CONFLICT, IDEMPOTENT_DUPLICATE

## 可追溯来源
- codex_devflow_scaffold/artifacts/stage0/latest.json
- codex_devflow_scaffold/artifacts/stage2/latest.json
- codex_devflow_scaffold/artifacts/stage4/latest.json
- codex_devflow_scaffold/artifacts/stage5/latest.json

## 关键实现文件
- apps/access/management/commands/seed_role_permissions.py
- apps/api_v1/urls.py
- config/settings.py
- apps/flight_record/
- apps/flight_record/models.py
- apps/flight_record/serializers.py
- apps/flight_record/views.py
- apps/flight_record/urls.py
- apps/flight_record/tests.py

## 本轮增补（PATCH /api/v1/flight-records/{id}）
- 接口作用: PATCH /api/v1/flight-records/{id} 用于局部修正飞行记录主数据。
- 请求边界:
  - 仅处理 flight_record 自身可写字段；
  - 不做媒体文件编排、级联删除或跨实体状态流转；
  - PATCH 请求体必须至少包含一个可写字段。
- 响应语义:
  - 成功返回 `SUCCESS`
  - 参数校验失败返回 `INVALID_PARAMS`
  - 权限不足返回 `PERMISSION_DENIED`
  - 资源不存在返回 `RESOURCE_NOT_FOUND`

<!-- stage6_doc_sync::flight_record::impl_desc.md::start -->
## Stage6 本轮同步
- 本轮 focus API: PATCH /api/v1/flight-records/{id}
- 本轮实现目标: 当前 flight_record 已具备创建、列表和详情能力，但缺少基础编辑入口，调用方无法修正机场、飞行时间、图片视频数量或异常终止状态等执行结果数据。补齐 PATCH 后，飞行记录才能形成最小可维护闭环。
- 业务事件: EVT-001 更新飞行记录
- 业务约束: N/A
- 测试沉淀: 生成用例数: 4, 已执行用例数: 4, 已沉淀到项目测试: 4, 待沉淀 case: N/A, 失败 case: N/A
- 关键文件: apps/flight_record/serializers.py, apps/flight_record/tests.py, apps/flight_record/views.py, apps/flight_record/models.py, apps/flight_record/urls.py
<!-- stage6_doc_sync::flight_record::impl_desc.md::end -->
