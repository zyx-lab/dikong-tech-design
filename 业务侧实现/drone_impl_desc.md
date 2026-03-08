# 无人机管理与分配设计（当前实现）

本文档描述当前已落地的无人机业务能力。  
当前版本同时包含“无人机台账管理”和“无人机-飞手分配关系管理”。

---

## 1. 设计范围

已包含：
1. 无人机台账：新增、编辑、删除、查询、状态变更（启用/停用/维护/退役）。
2. 分配关系：创建分配、查询分配、取消分配。
3. 权限链路：`StaffType -> Group -> Permission + Scope`。
4. 飞手可见范围：按分配关系生效（`ASSIGNED`）。
5. 所有写操作均进入审计日志。

不包含：
1. 任务执行联动校验（例如任务占用冲突）。
2. 组织树隔离策略（`org_id` 暂为预留字段）。

---

## 2. 架构边界

1. IAM 平面（`/internal/auth/*`）
- 只负责账号、角色、权限矩阵配置。
- 不承载无人机业务 CRUD。

2. Business 平面（`/api/v1/*`）
- 承载无人机台账与分配接口。
- 由业务前端调用。

---

## 3. 数据模型

### 3.1 无人机台账表 `drones`

字段：
1. `id`：主键
2. `code`：业务编码，唯一
3. `name`：无人机名称
4. `model`：型号
5. `serial_no`：出厂序列号，唯一
6. `status`：`ENABLED` / `DISABLED` / `MAINTENANCE` / `RETIRED`
7. `org_id`：组织 ID（预留）
8. `created_by_staff_id`：创建人 staff id（审计/OWN 扩展预留）
9. `created_at` / `updated_at`

### 3.2 分配关系表 `drone_assignments`

字段：
1. `id`：主键
2. `drone_id`：关联无人机
3. `staff_id`：关联人员（飞手）
4. `status`：`ACTIVE` / `INACTIVE`
5. `start_at`：分配开始时间
6. `end_at`：分配结束时间（取消时写入）
7. `created_by_staff_id`：操作人 staff id
8. `created_at` / `updated_at`

约束：
1. 同一 `(drone_id, staff_id)` 在 `ACTIVE` 状态下唯一。
2. 取消分配采用软失效（`INACTIVE`），不做硬删除。

---

## 4. 权限与范围

### 4.1 权限码

1. `drone.view_drone`：查看无人机
2. `drone.manage_drone`：新增/编辑/删除无人机基础信息
3. `drone.change_drone_status`：变更无人机状态
4. `drone.manage_drone_assignment`：管理分配关系

### 4.2 Scope

1. `drone.view_drone` 支持 `ALL` 和 `ASSIGNED`。
2. `drone.manage_drone` 固定使用 `ALL`。
3. `drone.change_drone_status` 固定使用 `ALL`。
4. `drone.manage_drone_assignment` 固定使用 `ALL`。

---

## 5. 角色矩阵（当前实现）

1. `ops_admin`
- `view_drone=ALL`
- `manage_drone=ALL`
- `change_drone_status=ALL`
- `manage_drone_assignment=ALL`

2. `business_admin`
- `view_drone=ALL`
- `manage_drone=ALL`
- `change_drone_status=ALL`
- `manage_drone_assignment=ALL`

3. `dispatcher`
- `view_drone=ALL`
- `manage_drone_assignment=ALL`

4. `pilot_operator`
- `view_drone=ASSIGNED`

5. `route_planner` / `auditor`
- 当前不授予无人机业务权限

---

## 6. API 设计（/api/v1）

### 6.1 无人机台账 `/api/v1/drones`

1. `GET /api/v1/drones`
- 列表查询，支持筛选：`code` / `name` / `model` / `status` / `org_id`
- `ASSIGNED` 用户只返回本人分配到的无人机

2. `POST /api/v1/drones`
- 新建无人机

3. `GET /api/v1/drones/{id}`
- 无人机详情

4. `DELETE /api/v1/drones/{id}`
- 删除无人机（DELETE 请求体必须为空）
- 若存在 `ACTIVE` 分配关系，返回 `409 + STATE_CONFLICT`

5. `PATCH /api/v1/drones/{id}`
- 编辑基础信息（不允许直接改 `status`）

6. `POST /api/v1/drones/{id}/enable`
7. `POST /api/v1/drones/{id}/disable`
8. `POST /api/v1/drones/{id}/maintenance`
9. `POST /api/v1/drones/{id}/retire`
- 状态动作接口
- 响应统一携带 `business_code` 与 `business_detail_code` 字段（见 6.3）

### 6.2 分配关系 `/api/v1/drone-assignments`

1. `GET /api/v1/drone-assignments`
- 列表查询，支持筛选：`drone_id` / `staff_id` / `status`

2. `POST /api/v1/drone-assignments`
- 新建分配关系

3. `GET /api/v1/drone-assignments/{id}`
- 分配关系详情

4. `POST /api/v1/drone-assignments/{id}/cancel`
- 取消分配（写入 `INACTIVE + end_at`）
- 响应统一携带 `business_code` 与 `business_detail_code` 字段（见 6.3）

### 6.3 响应契约（business_code + business_detail_code）

业务 API 响应统一补充字段：`business_code`（主业务码）与 `business_detail_code`（细分原因码）。

1. 成功类：`SUCCESS`（常见 HTTP：200/201）
2. 参数错误：`INVALID_PARAMS`（HTTP 400）
3. 权限拒绝：`PERMISSION_DENIED`（HTTP 401/403）
4. 资源不存在：`RESOURCE_NOT_FOUND`（HTTP 404）
5. 状态冲突：`STATE_CONFLICT`（HTTP 409）
6. 幂等重复：`IDEMPOTENT_DUPLICATE`（HTTP 409）

细分码示例：
1. 成功：`OK`
2. 未登录：`NOT_AUTHENTICATED`
3. 鉴权拒绝：`FORBIDDEN`
4. 资源不存在：`NOT_FOUND`
5. 参数校验：`VALIDATION_ERROR`
6. 状态冲突：`STATE_CONFLICT`
7. 重复请求：`DUPLICATE_REQUEST`

本轮落地重点：
1. `POST /api/v1/drones` 重复提交（唯一键冲突）返回 `409 + IDEMPOTENT_DUPLICATE`。
2. `POST /api/v1/drones/{id}/enable` 在 `RETIRED` 状态下返回 `409 + STATE_CONFLICT`。
3. `DELETE /api/v1/drones/{id}` 按业务规则返回 `SUCCESS/INVALID_PARAMS/PERMISSION_DENIED/RESOURCE_NOT_FOUND/STATE_CONFLICT`。

---

## 7. 业务规则

1. `RETIRED` 无人机不可恢复为其他状态。
2. 状态动作重复提交保持幂等（返回 200 + 当前状态）。
3. 创建分配时，`staff` 必须是 `pilot_operator` 且在职。
4. 已退役无人机不可创建分配关系。
5. 相同无人机与飞手不得重复存在 `ACTIVE` 分配。
6. 取消分配重复提交保持幂等（返回 200 + 当前状态）。
7. 删除无人机时，若存在 `ACTIVE` 分配关系则拒绝删除并返回 `STATE_CONFLICT`。

---

## 8. 审计动作

1. `DRONE_CREATE`
2. `DRONE_UPDATE`
3. `DRONE_STATUS_CHANGE`
4. `DRONE_DELETE`
5. `DRONE_ASSIGNMENT_CREATE`
6. `DRONE_ASSIGNMENT_CANCEL`

统一记录：
1. `target_type` / `target_id`
2. `before_data` / `after_data`
3. `actor_user` / `request_id` / `ip`

---

## 9. 代码落点

1. `apps/drone/models.py`
2. `apps/drone/serializers.py`
3. `apps/drone/views.py`
4. `apps/drone/urls.py`
5. `apps/drone/tests.py`
6. `apps/access/management/commands/seed_role_permissions.py`

---

## 10. 初始化命令

```bash
python manage.py makemigrations
python manage.py migrate
python manage.py seed_role_permissions --mode replace
```

创建业务管理员账号（普通业务用户，不是 Django superuser）：

```bash
python manage.py create_business_admin_account --username biz_root --password 'YourStrongPassword'
```
