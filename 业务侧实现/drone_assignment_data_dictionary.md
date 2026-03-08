# 低空智能巡检平台 - Drone Assignment

## 数据库

PostgreSQL

---

## 阅读说明

本数据字典只覆盖当前已落地的分配关系实体：`drone_assignments`。

---

## 1. drone_assignments（无人机分配表）

**说明**：维护无人机与飞手的分配关系，是 `ASSIGNED` 范围授权的事实来源。

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 主键 |
| drone_id | bigint | FK, NOT NULL | - | 关联无人机 ID（`drones.id`） |
| staff_id | bigint | FK, NOT NULL | - | 关联飞手 ID（`staff_profiles.id`） |
| status | varchar(16) | NOT NULL | ACTIVE | 分配状态 |
| start_at | timestamp | NOT NULL | now() | 分配生效时间 |
| end_at | timestamp | - | - | 分配结束时间 |
| created_by_staff_id | bigint | - | - | 操作人 staff ID |
| created_at | timestamp | NOT NULL | now() | 创建时间 |
| updated_at | timestamp | NOT NULL | now() | 更新时间 |

**status 状态值**：

| 值 | 含义 |
|----|------|
| ACTIVE | 生效中 |
| INACTIVE | 已失效 |

**约束与规则**：
1. 唯一约束：同一 `(drone_id, staff_id)` 在 `ACTIVE` 状态下唯一。  
2. 取消分配采用软失效（`ACTIVE -> INACTIVE`），不做物理删除。  
3. 幂等取消：已是 `INACTIVE` 的记录再次取消仍返回成功态。  
4. 支持恢复分配：`INACTIVE -> ACTIVE`，并清空 `end_at`。  
5. 幂等恢复：已是 `ACTIVE` 的记录再次恢复仍返回成功态。  
6. 创建分配时，仅允许在职飞手（`staff_type.code=pilot_operator`）且无人机状态不为 `RETIRED`。  

---

## 2. 外键关系

1. `drone_assignments.drone_id -> drones.id`
2. `drone_assignments.staff_id -> staff_profiles.id`

---

## 3. 业务响应码（接口契约）

说明：`/api/v1/drone-assignments*` 响应体统一包含 `business_code` 与 `business_detail_code`。

| business_code | 典型 HTTP | 语义 |
| ------ | ------ | ------ |
| SUCCESS | 200 / 201 | 分配创建/查询/取消成功 |
| INVALID_PARAMS | 400 | 参数校验失败 |
| PERMISSION_DENIED | 401 / 403 | 身份或权限不足 |
| RESOURCE_NOT_FOUND | 404 | 分配记录不存在 |
| STATE_CONFLICT | 409 | 分配恢复冲突（激活唯一约束冲突） |
| IDEMPOTENT_DUPLICATE | 400 / 409 | 重复创建同一 ACTIVE 分配 |

| business_detail_code | 语义 |
| ------ | ------ |
| OK | 成功 |
| NOT_AUTHENTICATED | 未登录或认证信息缺失 |
| FORBIDDEN | 已登录但无权限 |
| NOT_FOUND | 资源不存在 |
| VALIDATION_ERROR | 参数校验失败 |
| DUPLICATE_REQUEST | 重复创建请求 |

---

## 4. 与实现对应

1. 模型：`apps/drone/models.py`  
2. 序列化与校验：`apps/drone/serializers.py`  
3. 接口：`apps/drone/views.py`（`DroneAssignmentViewSet`）  
