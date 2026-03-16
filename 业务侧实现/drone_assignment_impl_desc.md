# 无人机分配关系实现说明

## 数据库

PostgreSQL

---

## 文档格式说明

本文档为 **实现描述** 类型文档，记录 API 实现、数据模型、审计动作等。

### 更新本文档的指南（大模型用）

当需要更新此文档时，请遵循以下格式：

```
## N. {模块名}

### N.X API / 功能名称
- 功能：{功能描述}
- 路径：{API路径}
- 方法：{HTTP方法}
- 权限：{所需权限}
- 请求体：{请求格式}
- 响应：{响应格式}
- 业务码：{返回的业务码}
```

---

本文档描述 `drone_assignment` 实体的当前落地实现，覆盖分配关系创建、查询、取消与权限控制。

---

## 1. 设计目标

1. 为无人机与飞手建立可审计的分配关系。  
2. 为 `drone.view_drone` 的 `ASSIGNED` 范围提供数据依据。  
3. 确保创建与取消分配动作可追溯、可幂等。  

---

## 2. 领域边界

包含：
1. `POST /api/v1/drone-assignments`：创建分配。  
2. `GET /api/v1/drone-assignments`：列表查询。  
3. `GET /api/v1/drone-assignments/{id}`：详情查询。  
4. `POST /api/v1/drone-assignments/{id}/cancel`：取消分配。  
5. `POST /api/v1/drone-assignments/{id}/reactivate`：恢复分配。  

不包含：
1. 排班/任务系统联动校验。  
2. 组织树隔离与跨组织调度策略。  

---

## 3. 数据模型与约束

实体：`drone_assignments`

关键字段：
1. `drone_id`：无人机。  
2. `tenant_member_id`：被分配成员（`tenant_members`）。  
3. `status`：`ACTIVE` / `INACTIVE`。  
4. `start_at` / `end_at`：生效与失效时间。  
5. `created_by_tenant_member_id`：租户内操作人。  

核心约束：
1. 条件唯一约束 `uniq_active_drone_tenant_member_assignment`：同一 `(drone_id, tenant_member_id)` 在 `ACTIVE` 下唯一。  
2. 取消分配使用软失效（改状态，不删记录）。  
3. `tenant_member` 必须属于当前租户、处于 `ACTIVE`，且带 `pilot_operator` 角色。  
4. `OWN / ASSIGNED` 口径统一以 `TenantMember.id` 命中，不再使用全局 `staff_id`。  

---

## 4. 接口行为

### 4.1 创建分配 `POST /api/v1/drone-assignments`

校验逻辑：
1. 无人机不能是 `RETIRED`。  
2. `tenant_member` 必须属于当前租户。  
3. `tenant_member` 对应人员档案必须在职（`employment_status=ACTIVE`）。  
4. `tenant_member` 必须带 `pilot_operator` 角色。  
5. 相同无人机+成员不能重复存在 `ACTIVE` 分配。  

成功行为：
1. 写入 `ACTIVE` 分配记录。  
2. 记录审计日志 `DRONE_ASSIGNMENT_CREATE`。  

### 4.2 取消分配 `POST /api/v1/drone-assignments/{id}/cancel`

行为定义：
1. 目标记录为 `ACTIVE` 时，更新为 `INACTIVE` 并写 `end_at`。  
2. 目标记录已是 `INACTIVE` 时，按幂等成功处理并直接返回当前记录。  
3. 记录审计日志 `DRONE_ASSIGNMENT_CANCEL`。  

### 4.3 查询接口

1. 列表支持过滤：`drone_id` / `tenant_member_id` / `status`。  
2. 列表与详情均要求 `drone.manage_drone_assignment` 权限。  

### 4.4 恢复分配 `POST /api/v1/drone-assignments/{id}/reactivate`

行为定义：
1. 请求体必须为空；非空返回 `400 + B0001`。  
2. 目标记录为 `INACTIVE` 时，更新为 `ACTIVE` 且清空 `end_at`。  
3. 目标记录已是 `ACTIVE` 时按幂等成功处理并返回当前记录。  
4. 若激活触发唯一约束冲突，返回 `409 + C0201`。  
5. 写审计日志 `DRONE_ASSIGNMENT_REACTIVATE`。  

---

## 5. 权限与范围

权限码：
1. `drone.manage_drone_assignment`（列表/详情/创建/取消统一使用）。  

范围：
1. 当前实现使用 `ALL` 范围。  
2. 通过 `ScopedActionPermission + ScopedQuerysetMixin` 执行统一鉴权链。  

---

## 6. 响应契约

业务 API 响应体统一包含：
1. `code`
2. `msg`
3. `data`

常见返回：
1. 成功：`00000`（200/201）。  
2. 参数校验失败：`B0001`（400）。  
3. 重复创建：`C0101`（409）。  
4. 无权限：`A0401 / A0403`（401/403）。  
5. 资源不存在：`C0404`（404）。  

---

## 7. 审计日志

动作：
1. `DRONE_ASSIGNMENT_CREATE`  
2. `DRONE_ASSIGNMENT_CANCEL`  
3. `DRONE_ASSIGNMENT_REACTIVATE`  

记录字段：
1. `target_type=drone_assignment`  
2. `target_id`  
3. `before_data` / `after_data`  
4. `actor_user` / `request_id` / `ip`  

---

## 8. 代码落点

1. `apps/drone_assignment/models.py`  
2. `apps/drone_assignment/serializers.py`  
3. `apps/drone_assignment/views.py`  
4. `apps/drone_assignment/tests.py`  
