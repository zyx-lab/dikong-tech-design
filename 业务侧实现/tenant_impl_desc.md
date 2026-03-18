# 租户实现说明

- generated_at: 2026-03-19
- entity: tenant

## 数据模型

当前实现已经收敛到“平台目录 + 租户运行数据 + 正式认证会话”三层：

### 平台目录

- `Role`
- `Permission`
- `RolePermissionGrant`
- `QualificationType`

平台目录由平台统一维护，租户管理员不可修改。

### 租户运行数据

- `Tenant`
- `TenantMember`
- `TenantMemberRole`
- `TenantMemberQualification`

正式授权链只认：

```text
TenantMember(ACTIVE) -> TenantMemberRole(GRANTED) -> RolePermissionGrant -> Permission
```

业务资格链只认：

```text
TenantMember(ACTIVE) -> TenantMemberQualification(有效) -> QualificationType
```

### 正式认证会话

- `AuthSession`

正式 `/api/v1/*` 接口统一使用 Bearer Token。
`AuthSession` 只承载正式 IAM 会话，不承载 Django Admin 登录态。

### 人员档案与工号

- `StaffProfile` 只保留全局人员档案字段：`name / phone / email / employment_status / org_id`。
- `StaffProfile` 不保存业务工号。
- 账号进入 `TenantMember` 前，必须先存在全局 `StaffProfile`。
- 业务工号 / 成员编号统一使用 `TenantMember.member_no`。
- `member_no` 按租户内唯一处理：同租户重复拒绝，跨租户允许。
- `superuser` 不允许进入 `TenantMember` 体系。
- `platform_admin` 通过 `User.is_platform_admin` 独立承载，也不允许进入 `TenantMember` 体系。

### 运行态与角色边界

当前正式 IAM 运行态分成三类：

```text
unassigned
tenant_member
platform_operator
```

对应边界：

```text
superuser -> 技术 root，不属于正式 IAM 账号集合
platform_operator -> User.is_platform_admin=true
tenant_member -> TenantMember(ACTIVE) + 租户内角色绑定
```

### 成员状态

当前模型里的 `TenantMember.status` 仍保留：

- `INVITED`
- `ACTIVE`
- `REJECTED`
- `EXPIRED`
- `REVOKED`
- `DISABLED`

但当前正式 API 已不再开放邀请链路，因此正式对外只使用：

- `ACTIVE`
- `DISABLED`

新成员统一通过 `POST /api/v1/iam/tenant/members` 直接创建为 `ACTIVE`，并在写入时清空邀请元数据。

### 当前 `pilot_operator` 权限收口

- `pilot_operator` 当前对 `drone.view_drone / mission.view_mission / flight_record.view_flight_record / flight_record.manage_flight_record / media_file.view_media_file / media_file.manage_media_file` 都按 `ASSIGNED` 生效。
- `ASSIGNED` 的主体统一按当前租户内的 `TenantMember.id` 判定。
- 具体口径：
  - 无人机：命中当前成员的 `DroneAssignment`
  - 任务：`Mission.pilot_id == 当前 TenantMember.id`
  - 飞行记录：`FlightRecord.pilot_id == 当前 TenantMember.id`
  - 媒体文件：`MediaFile.flight_record.pilot_id == 当前 TenantMember.id`

### 业务标识唯一性

- `Tenant.code` 全局唯一。
- `TenantMember.member_no` 按租户内唯一。
- 业务表里的 `Drone.code / Drone.serial_no / FlightRecord.flight_no` 都按租户内唯一处理。
- API 校验统一口径：同租户重复拒绝，跨租户重复允许。

### 模型层硬约束

- 关键业务规则不只放在 serializer，也下沉到模型层 `clean() + save()->full_clean()`。
- 当前已下沉的核心口径：
  - `User`：`superuser` 与 `platform_admin` 不能同时为 true。
  - `StaffProfile`：`superuser` 不允许绑定全局档案。
  - `TenantMember`：禁止把 `superuser`、`platform_admin` 或没有 `StaffProfile` 的账号绑定进租户成员链。
  - `TenantMemberRole`：只能分配启用中的租户可分配角色；`platform_admin` 不允许进入租户成员角色链；`GRANTED` 必须写入 `assigned_at`。
  - `RolePermissionGrant`：`OWN / ASSIGNED` 要求 `Permission.resource_code` 非空。
  - `TenantMemberQualification`：资质类型必须启用；要求有效期时必须提供 `valid_from / valid_until`；schema 的必填扩展字段不能缺失。

---

## API 实现

### 0. 正式 API 分层

当前租户相关正式 IAM API 统一收口到 `/api/v1/iam/*`：

- `session/*`：会话建立与注册
- `me/*`：当前登录用户自己的全局资料与可进入租户列表
- `tenant/*`：当前租户上下文内工作
- `platform/*`：平台侧租户治理与平台目录

### 1. 会话接口

- `POST /api/v1/iam/session/login`
- `POST /api/v1/iam/session/refresh`
- `POST /api/v1/iam/session/logout`
- `POST /api/v1/iam/session/register`
- `POST /api/v1/iam/session/register-by-phone`

实现约束：

- 正式认证统一签发 Bearer Token。
- `session/register*` 创建的是正式 IAM 业务账号，不自动入租。
- `superuser` 不能通过正式 `session/login` 登录。
- `platform_operator` 可以通过 `session/login` 建立平台工作态会话。

### 2. 当前用户接口

- `GET /api/v1/iam/me/profile`
- `GET /api/v1/iam/me/tenants`

实现约束：

- `me/*` 只允许正式 IAM 业务账号调用。
- `platform_operator` 不可访问 `me/*`。
- `me/profile` 返回当前账号全局资料与 `userId`。
- `me/tenants` 返回当前账号可进入的租户列表，只包含“租户 ACTIVE + 成员 ACTIVE”的关系。

### 3. 当前租户上下文

- `GET /api/v1/iam/tenant/me`

实现约束：

- 必须携带合法 `X-TENANT-CODE`。
- 只要求当前账号在该租户下存在 `ACTIVE` 成员关系，不要求一定已分配业务角色。
- 返回 `tenant` 与 `member` 两个对象。

### 4. 租户成员目录与成员管理

- `GET /api/v1/iam/tenant/members`
- `POST /api/v1/iam/tenant/members`
- `GET /api/v1/iam/tenant/members/{memberId}`
- `PATCH /api/v1/iam/tenant/members/{memberId}`
- `PUT /api/v1/iam/tenant/members/{memberId}/roles`
- `POST /api/v1/iam/tenant/members/{memberId}/enable`
- `POST /api/v1/iam/tenant/members/{memberId}/disable`

实现约束：

- 以上接口统一只允许当前租户内的 `tenant_admin` 调用。
- `POST /tenant/members` 只允许把已存在的全局用户加入当前租户。
- 成员对象对外统一输出：
  - `memberId`
  - `userId`
  - `username`
  - `displayName`
  - `status`
  - `roleCodes`
- 正式 API 不开放邀请、拒绝邀请、确认邀请接口。
- 成功启用后 `status=ACTIVE`；成功停用后 `status=DISABLED`。
- 不能通过禁用或改角色把当前租户变成“0 个有效 `tenant_admin`”。

### 5. 当前租户角色目录

- `GET /api/v1/iam/tenant/roles`

实现约束：

- 返回当前租户可分配角色模板目录。
- 允许包含 `tenant_admin`。
- 不包含 `platform_admin`。
- 为固定目录接口，非分页。

### 6. 租户审计

- `GET /api/v1/iam/tenant/audit-logs`

实现约束：

- 只返回当前租户审计日志。
- 统一要求合法 `X-TENANT-CODE`。
- 当前只允许 `tenant_admin` 调用。

### 7. 平台租户治理

- `GET /api/v1/iam/platform/tenants`
- `POST /api/v1/iam/platform/tenants`
- `GET /api/v1/iam/platform/tenants/{tenantId}`
- `POST /api/v1/iam/platform/tenants/{tenantId}/enable`
- `POST /api/v1/iam/platform/tenants/{tenantId}/disable`
- `POST /api/v1/iam/platform/tenants/{tenantId}/initialize-admin`

实现约束：

- 只允许运行态为 `platform_operator` 的账号调用。
- `plan` 当前是只读保留字段，响应统一返回 `null`。
- `initialize-admin` 是平台侧唯一允许写入租户成员关系的正式例外。
- `initialize-admin` 只在目标租户为 `ACTIVE` 且当前没有任何有效 `tenant_admin` 时允许执行。

### 8. 平台固定目录

- `GET /api/v1/iam/platform/permissions`
- `GET /api/v1/iam/platform/roles`
- `GET /api/v1/iam/platform/roles/{roleId}`
- `GET /api/v1/iam/platform/audit-logs`

实现约束：

- `platform/permissions` 与 `platform/roles` 是平台固定目录接口。
- `platform/roles` 返回角色模板及其默认权限模板。
- `platform/audit-logs` 返回平台维度审计日志。

### 9. 已下线的旧正式接口

以下旧接口已不属于当前正式 API：

- `/internal/auth/*`
- `GET /me/permissions`
- 邀请流：
  - `POST /tenant-members/invite`
  - `POST /tenant-members/confirm-invitation`
  - `POST /me/invitations/reject`
  - `GET /me/invitations`
- `POST /tenants/{id}/set-plan`
- 平台侧全局用户目录 / 通用成员管理接口
