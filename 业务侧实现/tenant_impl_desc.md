# 租户实现说明

- generated_at: 2026-03-14
- entity: tenant

## 数据模型

当前实现已经收敛到“平台目录 + 租户运行数据”两层：

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

授权链只认：

```text
TenantMember(ACTIVE) -> TenantMemberRole(GRANTED) -> RolePermissionGrant -> Permission
```

业务资格链只认：

```text
TenantMember(ACTIVE) -> TenantMemberQualification(有效) -> QualificationType
```

### 人员档案与工号

- `StaffProfile` 现在只保留全局人员档案字段：`name / phone / email / employment_status / org_id`。
- `StaffProfile` 不再保存 `staff_no`。
- 账号进入 `TenantMember` 租户成员链前，必须先存在全局 `StaffProfile`。
- 业务上的工号 / 成员编号统一使用 `TenantMember.member_no`。
- 因此，同一个 `User` 加入不同租户时，可以在不同 `TenantMember` 上拥有不同 `member_no`。
- `member_no` 按租户内唯一处理：同租户重复拒绝，跨租户允许。
- 该规则同时适用于成员创建、成员更新、初始化租户管理员。
- `superuser` 不允许进入 `TenantMember` 体系：不能被直接创建为成员、不能被邀请、也不能被初始化为租户管理员。
- `platform_admin` 通过 `User.is_platform_admin` 独立承载，不走 `TenantMember -> TenantMemberRole` 租户授权链。
- `platform_admin` 账号同样不允许进入 `TenantMember` 体系：不能被直接创建为成员、不能被邀请、也不能被初始化为租户管理员。
- 当前内部鉴权实际分成三条链：

```text
superuser -> 直接放行（技术 root）
platform_admin(User.is_platform_admin=true) -> Role(code=platform_admin) -> RolePermissionGrant -> Permission
普通账号 -> TenantMember(ACTIVE) -> TenantMemberRole(GRANTED) -> RolePermissionGrant -> Permission
```

### 业务标识唯一性

- 业务表里的 `code / serial_no / flight_no` 都按租户内唯一处理，不再按全局唯一处理。
- `Drone.code` 表示租户内设备台账编号 / 业务编码，不表示型号。
- `Drone.model` 才是无人机型号，所以同一租户可以有多架相同型号的无人机。
- `Drone.serial_no` 当前实现按租户内唯一校验。
- `FlightRecord.flight_no` 当前实现按租户内唯一校验。
- API 校验口径统一为：同租户重复拒绝，跨租户重复允许。

示例：

- 租户 A 已有 `Drone.code = DR-001`，租户 A 再创建 `DR-001`，接口应拒绝。
- 租户 A 已有 `Drone.code = DR-001`，租户 B 创建 `DR-001`，接口应允许。
- 同一租户下可以同时存在多架 `model = M300` 的无人机，只要 `code` 不重复。

### 模型层硬约束

- 关键业务规则不只在 serializer 校验，也下沉到模型层 `clean() + save()->full_clean()`，避免直接 `model.save()` 绕过接口约束。
- 这里的三个点都是 Django 模型的标准机制：
  - `clean()`：模型级业务校验钩子，适合写跨字段、跨关联、跨租户这类规则。
  - `full_clean()`：执行完整模型校验，包含字段校验、`clean()`、唯一性校验。
  - `save()`：默认只负责落库，Django 不会自动在 `save()` 前调用 `full_clean()`。
- 当前实现采用的口径是：在模型里覆写 `save()`，先执行 `self.full_clean()`，再真正保存。
- 这样做的原因是：serializer 校验只能覆盖 API 入口；如果有人直接在代码里调用 `Model.objects.create(...)` 或 `instance.save()`，模型层仍然会阻断非法数据。
- 角色分工可以理解为：
  - serializer：负责接口入参校验、返回更友好的错误信息。
  - model：负责最终数据一致性兜底，防止绕过接口直接写脏数据。
- 当前已下沉的核心口径：
  - `Drone`：`RETIRED` 状态不可逆，一旦退役，不能再改回其他状态。
  - `TenantMember`：禁止把没有 `StaffProfile` 的账号直接绑定进租户成员链。
  - `TenantMemberRole`：只能绑定启用中的租户可分配角色；`platform_admin` 不允许进入 `TenantMember` 链；`status=GRANTED` 时必须写入 `assigned_at`。
  - `RolePermissionGrant`：`scope_type=OWN / ASSIGNED` 时，`Permission.resource_code` 必须非空，避免落出无法判定归属范围的脏授权。
  - `OWN / ASSIGNED`：当前统一以 `TenantMember.id` 作为判定主体，不再使用全局 `staff_id`。
  - `TenantMemberQualification`：资质类型必须是启用状态；要求有效期的资质必须提供 `valid_from / valid_until`；资质 schema 要求的必填扩展字段不能缺失。
  - `Mission`：航线必须属于当前租户且为 `ACTIVE`，无人机必须属于当前租户且为 `ENABLED`，飞手必须是当前租户内在职且带 `pilot_operator` 角色的成员。
  - `Mission`：状态流转必须符合当前接口状态机，不能通过直接 `save()` 把 `PENDING` 跳成 `COMPLETED` 之类的非法状态。
  - `DroneAssignment`：无人机必须属于当前租户且不能是 `RETIRED`，分配对象必须是当前租户内在职且带 `pilot_operator` 角色的 `TenantMember`。
  - `FlightRecord`：任务 / 无人机 / 飞手必须属于当前租户链路，`end_time` 不能早于 `start_time`，并且 `mission` 与 `drone / pilot` 的绑定关系必须一致。
  - `FlightRecord`：状态流转必须符合当前接口状态机，终态不能通过直接 `save()` 改回其他状态。
  - `Waypoint`：只能挂在 `ACTIVE` 航线下。

---

## API 实现

### 1. 创建租户
- 功能：创建租户。
- 路径：`/internal/auth/tenants`
- 方法：`POST`
- 权限：`access.manage_tenant`
- 请求体：`code`、`name` 必填
- 响应：创建成功返回租户基本信息
- 业务码：`SUCCESS / INVALID_PARAMS`

### 2. 停用 / 启用租户
- 功能：切换租户可用状态。
- 路径：`/internal/auth/tenants/{id}/disable`
- 路径：`/internal/auth/tenants/{id}/enable`
- 方法：`POST`
- 权限：`access.manage_tenant`
- 业务码：`SUCCESS / RESOURCE_NOT_FOUND / IDEMPOTENT_DUPLICATE`

### 3. 初始化租户管理员
- 功能：为指定租户创建或激活首个 `tenant_admin` 成员，并绑定角色。
- 路径：`/internal/auth/tenants/{id}/initialize-admin`
- 方法：`POST`
- 权限：`access.manage_tenant`
- 请求体：`user_id` 必填；`display_name`、`member_no`、`qualifications` 可选
- 响应：返回 `member_id / tenant_id / user_id / roles / status`
- 业务码：`SUCCESS / INVALID_PARAMS / RESOURCE_NOT_FOUND / STATE_CONFLICT / IDEMPOTENT_DUPLICATE`

### 4. 配置租户套餐
- 功能：设置租户套餐标识。
- 路径：`/internal/auth/tenants/{id}/set-plan`
- 方法：`POST`
- 权限：`access.manage_tenant`
- 请求体：`plan`
- 业务码：`SUCCESS / INVALID_PARAMS / RESOURCE_NOT_FOUND / IDEMPOTENT_DUPLICATE`

### 4.1 租户接口边界

- `/internal/auth/tenants`、`/internal/auth/tenants/{id}` 及其子动作接口，在非 `superuser` 场景下都只能看到或操作当前租户。
- 非平台管理员即使持有 `access.view_tenant`，也不能读取其他租户信息。
- 这里的“平台管理员”当前落地为 `User.is_platform_admin = true`，不是租户内分配出来的角色。
- `platform_admin` 只允许处理平台级治理，不允许进入租户成员治理接口，也不允许查看租户级审计日志。
- `/api/v1/*` 业务接口在公共租户作用域层直接拒绝 `platform_admin`，不依赖权限矩阵是否误配置。
- 审计日志落库口径：
  - 业务接口只要运行在租户上下文内，审计日志必须自动写入当前 `tenant`
  - 只有真正的平台动作日志才允许 `tenant = null`
  - 不能把租户内业务日志记成平台日志
- 统一口径：
  - `users`
  - `tenants`
  这些平台治理接口在非平台管理员场景下都必须按当前租户收口。
  - `tenant-members`
  - `tenant-members/invite`
  - `tenant-members/{id}/roles`
  - `tenant-members/{id}/disable`
  - `tenant-members/{id}/enable`
  这些租户成员治理接口直接禁止 `platform_admin` 进入。
  - `/internal/auth/audit-logs`
  对 `superuser` 返回全量日志；对 `platform_admin` 只返回 `tenant = null` 的平台日志；对租户角色只返回当前租户日志。

### 5. 平台账号注册
- 功能：创建平台账号与全局 `StaffProfile`。
- 路径：`/internal/auth/users/register`
- 方法：`POST`
- 权限：公开接口
- 请求体：`username / password / name / phone`
- 实现约束：注册流程只创建账号与全局档案，不再生成任何工号；工号应在加入租户后写入 `TenantMember.member_no`
- 业务码：`SUCCESS / INVALID_PARAMS / IDEMPOTENT_DUPLICATE`

### 6. 手机号注册
- 功能：通过 mock 验证码完成平台注册。
- 路径：`/internal/auth/users/register/by-phone`
- 方法：`POST`
- 权限：公开接口
- 请求体：`phone / sms_code`
- 说明：当前仅支持固定验证码 `123456`
- 业务码：`SUCCESS / INVALID_PARAMS / IDEMPOTENT_DUPLICATE`

### 7. 直接创建租户成员
- 功能：跳过邀请，直接创建 `ACTIVE` 成员。
- 路径：`/internal/auth/tenant-members`
- 方法：`POST`
- 权限：`access.manage_tenant_member`
- 请求体：`tenant_id / user_id` 必填；可附带 `display_name / member_no / role_codes / qualifications`
- 实现约束：
  - 非平台管理员调用时，必须先携带当前租户上下文（`X-Tenant-Code`）
  - 非平台管理员调用时，`tenant_id` 必须等于当前租户，不能替其他租户直接建成员
  - `superuser` 和 `platform_admin` 可直接指定目标租户
- 业务码：`SUCCESS / INVALID_PARAMS`

### 8. 查询租户成员列表
- 功能：查询租户成员列表。
- 路径：`/internal/auth/tenant-members`
- 方法：`GET`
- 权限：`access.view_tenant_member`
- 查询参数：`tenant_id`、`status` 可选
- 返回：成员基础信息、当前 `roles`、资质列表
- 实现约束：非平台管理员场景下，只返回当前租户成员；即使传入其他 `tenant_id`，也不能越过当前租户边界
- `platform_admin` 不允许调用该接口

### 9. 邀请租户成员
- 功能：向已存在平台账号发出租户邀请，并预绑定角色。
- 路径：`/internal/auth/tenant-members/invite`
- 方法：`POST`
- 权限：`access.manage_tenant_member`
- 请求体：`tenant_id / user_id` 必填；可附带 `display_name / role_codes / qualifications`
- 实现要点：
  1. 仅允许邀请已存在 `User`
  2. 非平台管理员调用时，必须先进入当前租户上下文，且 `tenant_id` 必须等于当前租户
  3. 不允许替其他租户发邀请
  4. `superuser` 和 `platform_admin` 账号都不能被邀请为租户成员
  5. 新建 `TenantMember(status=INVITED)`
  6. 写入 `invitation_token / invited_at / expires_at`
  7. 预绑定 `TenantMemberRole(status=GRANTED)`
- `platform_admin` 不允许调用该接口
- 业务码：`SUCCESS / INVALID_PARAMS / RESOURCE_NOT_FOUND / STATE_CONFLICT`

### 10. 确认邀请
- 功能：当前登录用户接受邀请。
- 路径：`/internal/auth/tenant-members/confirm-invitation`
- 方法：`POST`
- 权限：登录用户
- 请求体：`invitation_token`
- 状态流转：`INVITED -> ACTIVE`
- 实现要点：
  1. 先检查邀请是否属于当前用户
  2. 先执行过期清理
  3. 激活后清空邀请元数据
  4. 保留预绑定角色
- 业务码：`SUCCESS / INVALID_PARAMS / RESOURCE_NOT_FOUND / PERMISSION_DENIED / STATE_CONFLICT`

### 11. 拒绝邀请
- 功能：当前登录用户拒绝邀请。
- 路径：`/internal/auth/me/invitations/reject`
- 方法：`POST`
- 权限：登录用户
- 请求体：`invitation_token`
- 状态流转：`INVITED -> REJECTED`
- 实现要点：
  1. 只清空 `invitation_token`
  2. 保留 `invited_by_user / invited_at / expires_at`
  3. 写入 `responded_at`

### 12. 查询 / 更新 / 删除成员
- 功能：查看单个成员，或更新成员基础属性与资质，或直接删除成员关系。
- 路径：`/internal/auth/tenant-members/{id}`
- 方法：
  - `GET`
  - `PUT / PATCH`
  - `DELETE`
- 权限：
  - `GET` 需要 `access.view_tenant_member`
  - `PUT / PATCH / DELETE` 需要 `access.manage_tenant_member`
- 更新范围：
  - 可更新 `display_name`
  - `ACTIVE` 成员可更新 `member_no`
  - 传入 `qualifications` 时会整批替换成员资质
- 说明：`DELETE` 为物理删除当前 `TenantMember`

### 13. 停用 / 启用成员
- 功能：管理员停用成员，或重新启用被停用成员。
- 路径：`/internal/auth/tenant-members/{id}/disable`
- 路径：`/internal/auth/tenant-members/{id}/enable`
- 方法：`POST`
- 权限：`access.manage_tenant_member`
- 状态流转：
  - `ACTIVE -> DISABLED`
  - `INVITED -> REVOKED`
  - `DISABLED -> ACTIVE`

### 14. 同步成员角色
- 功能：把成员当前生效角色集合直接同步为请求中的 `role_codes`。
- 路径：`/internal/auth/tenant-members/{id}/roles`
- 方法：`POST`
- 权限：`access.assign_tenant_member_role`
- 请求体：`role_codes`
- 实现要点：
  1. 传入角色会写成 `GRANTED`
  2. 未传入但已存在的角色绑定会改成 `REVOKED`
  3. 当前实现不区分成员状态，按请求值直接同步绑定结果

### 15. 当前用户租户列表
- 功能：获取当前用户所有 `ACTIVE` 成员关系对应的租户列表。
- 路径：`/internal/auth/me/tenants`
- 方法：`GET`
- 权限：登录用户

### 16. 当前用户待处理邀请列表
- 功能：获取当前用户所有 `INVITED` 邀请。
- 路径：`/internal/auth/me/invitations`
- 方法：`GET`
- 权限：登录用户
- 实现要点：请求前会先将已过期邀请批量切成 `EXPIRED`

### 17. 当前租户权限快照
- 功能：返回当前租户上下文下，当前用户聚合后的权限列表与 scope。
- 路径：`/internal/auth/me/permissions`
- 方法：`GET`
- 权限：登录用户
- 前置条件：请求必须携带当前租户上下文（`X-Tenant-Code`）
- 响应核心字段：
  - `tenant_code`
  - `roles`
  - `items[].permission`
  - `items[].scope`

### 18. 平台目录只读接口
- 功能：查看平台权限目录与角色目录。
- 路径：`/internal/auth/permissions`
- 路径：`/internal/auth/roles`
- 路径：`/internal/auth/roles/{id}`
- 方法：`GET`
- 权限：
  - `access.view_permission_catalog`
  - `access.view_role`

### 19. 审计日志查询
- 功能：查看平台全量审计日志，或当前租户下的审计日志。
- 路径：`/internal/auth/audit-logs`
- 路径：`/internal/auth/tenant-audit-logs`
- 方法：`GET`
- 权限：
  - 平台全量审计：`access.view_auth_audit_logs`
  - 当前租户审计：登录用户 + 当前租户上下文 + `access.view_auth_audit_logs`
- 查询参数：`action / target_type / actor_user_id / request_id / date_from / date_to`
- 实现约束：
  - 平台管理员可查看平台全量审计
  - 非平台管理员调用 `/internal/auth/audit-logs` 时，也只能看到当前租户审计，不能查看其他租户或平台全量日志

---

## 审计动作

当前实现会为以下动作写入审计日志：

- `USER_REGISTER`
- `USER_REGISTER_BY_PHONE`
- `USER_CREATE`
- `USER_UPDATE`
- `TENANT_CREATE`
- `TENANT_DISABLE`
- `TENANT_ENABLE`
- `TENANT_PLAN_CHANGE`
- `TENANT_ADMIN_INITIALIZE`
- `TENANT_MEMBER_CREATE`
- `TENANT_MEMBER_INVITE`
- `TENANT_MEMBER_CONFIRM_INVITATION`
- `TENANT_MEMBER_REJECT_INVITATION`
- `TENANT_MEMBER_DISABLE`
- `TENANT_MEMBER_ENABLE`
- `TENANT_MEMBER_DELETE`

---

## 实现约束

1. 不再使用 `GroupPermissionScope / SystemRoleGroup / TenantMemberPosition`。
2. 岗位判断统一通过 `role.code` 完成。
3. 资质判断统一通过 `QualificationType + TenantMemberQualification` 完成。
4. 平台管理员和租户管理员负责修正脏数据，不提供复杂的自动兼容层。
