依据文件：
- `项目总体概览/api重构文档.md`
- `项目总体概览/api设计规范v2.md`
- `config/urls.py`
- `config/business_api_urlconf.py`
- `config/internal_api_urlconf.py`
- `config/settings.py`
- `apps/access/urls.py`
- `apps/access/views.py`
- `apps/access/serializers.py`
- `apps/access/services.py`
- `apps/access/drf_permissions.py`
- `apps/access/middleware.py`
- `apps/access/models.py`
- `apps/access/exceptions.py`
- `apps/api_v1/business_response.py`
- `apps/api_v1/pagination.py`
- `apps/api_v1/openapi_hooks.py`

# Access API 实际代码重构顺序与落地清单

本文不是重新定义 API 契约，而是把定稿文档落成一份可执行的代码实施顺序。  
源契约以 `项目总体概览/api重构文档.md` 为准；本文件只回答三件事：

- 现在的代码和目标契约差在哪里。
- 代码重构应该按什么顺序做，才能少返工。
- 每一阶段具体要改哪些文件、删哪些旧能力、补哪些测试。

本文不覆盖原定稿文档。

## 1. 实施总原则

- 最终正式挂载只保留 `/api/v1/iam/*`，不保留 `/internal/auth/*` 兼容层。
- 最终正式认证只保留 Bearer Token，不保留 Session/Cookie 与 BasicAuthentication 兼容。
- 最终正式 API 不感知 `superuser`、Django Admin、技术兜底账号；这些能力继续留在 `admin/后台`。
- 平台工作态账号仍可调用 `platform/*`，但这类账号不通过正式 API 管理。
- 实施过程可以在开发分支里暂时保留旧代码作参考，但最终合并结果必须同时完成：
  - 新路由挂载生效。
  - 旧 `/internal/auth/*` 挂载移除。
  - OpenAPI 和测试切到新契约。

## 2. 当前实现与目标契约的主要差距

### 2.1 挂载与文档入口

当前现状：
- `config/urls.py` 仍直接挂载 `path("internal/auth/", include("apps.access.urls"))`
- `config/internal_api_urlconf.py` 仍把 `apps.access.urls` 作为内部文档入口
- `config/business_api_urlconf.py` 还没有纳入 IAM 正式接口

目标差距：
- 需要新增正式 `iam` 路由树，并进入 `/api/v1/docs/*`
- 旧 `internal/auth` 路由树最终必须整体摘掉

### 2.2 认证方式

当前现状：
- `config/settings.py` 只配置了 `SessionAuthentication` 和 `BasicAuthentication`
- `apps/access/views.py` 的 `LoginView` / `LogoutView` 仍使用 Django Session 的 `authenticate/login/logout`
- 仓库里没有 Bearer Token + refreshToken 的正式认证会话模型

目标差距：
- 需要补一套正式 IAM 会话模型、Token 签发/刷新/撤销逻辑、Bearer 认证类
- 认证层必须先落地，否则后续 `me/*`、`tenant/*`、`platform/*` 都无法按正式契约实现

### 2.3 鉴权模型与作用域边界

当前现状：
- `apps/access/drf_permissions.py` 的 `RequireInternalPermission` 仍以 `superuser`、`is_platform_admin`、当前租户成员权限混合判断
- `apps/access/services.py` 仍把 `platform_admin` 作为平台身份主判断
- `apps/access/middleware.py` 会对任意请求读取 `X-TENANT-CODE`

目标差距：
- 需要把正式鉴权切成三类运行态：`unassigned`、`tenant_member`、`platform_operator`
- 需要把 `tenant/*`、`platform/*`、`me/*` 的作用域判断从旧内部权限体系中拆出来
- `hasPlatformAccess` 只能作为 `session/login` 响应字段，不能继续作为正式资源定义的一部分

### 2.4 资源契约

当前现状：
- `apps/access/serializers.py` 仍暴露和接收大量旧字段：`tenant_id`、`member_no`、`qualifications`、`roles`
- `TenantMemberStatus` 仍包含 `INVITED`、`REJECTED`、`EXPIRED`、`REVOKED`
- `TenantMemberInviteSerializer`、`TenantMemberConfirmInvitationSerializer`、`TenantMemberRejectInvitationSerializer` 仍完整存在
- `TenantInitializeAdminSerializer` 在复用已有成员关系时仍会改写 `display_name`
- `TenantMemberDetailView` 仍是 `RetrieveUpdateDestroyAPIView`

目标差距：
- 正式 API 只保留 camelCase 输入输出
- 正式成员接口只暴露 `ACTIVE`、`DISABLED`
- 邀请流、`memberNo`、`qualifications`、`DELETE`、`me/permissions`、`tenant/profile`、`tenant/users/*` 都必须退出正式实现

### 2.5 响应与错误码

当前现状：
- `/api/v1/` 已有统一 `code/msg/data` 和标准分页实现
- 但 `apps/api_v1/business_response.py` 当前只内置了 `C0101`、`C0201` 等通用码，没有正式收口 `C0102`、`C0103`、`C0203`
- `apps/access/exceptions.py` 对旧 `/internal` 和新 `/api/v1` 仍是双轨处理

目标差距：
- 需要让正式 IAM 视图全部稳定输出定稿文档中的错误码落点
- 需要避免依赖字符串猜测，把 `C0102` / `C0103` / `C0203` 明确化

## 3. 推荐施工顺序

推荐按 8 个阶段执行。  
顺序不要打乱，尤其不要在 Bearer 会话层没落地前就大面积改业务视图。

### 阶段 0：建立新目录骨架，不切正式挂载

目标：
- 先把新实现放到独立目录，避免继续在 `apps/access/views.py`、`apps/access/serializers.py` 上叠加复杂度。

建议新增：
- `apps/access/api_v1/urls.py`
- `apps/access/api_v1/base.py`
- `apps/access/api_v1/authentication.py`
- `apps/access/api_v1/permissions.py`
- `apps/access/api_v1/views/session.py`
- `apps/access/api_v1/views/me.py`
- `apps/access/api_v1/views/tenant.py`
- `apps/access/api_v1/views/platform.py`
- `apps/access/api_v1/serializers/session.py`
- `apps/access/api_v1/serializers/me.py`
- `apps/access/api_v1/serializers/tenant.py`
- `apps/access/api_v1/serializers/platform.py`
- `apps/access/services/auth_session_service.py`
- `apps/access/services/identity_service.py`
- `apps/access/services/tenant_context_service.py`
- `apps/access/services/tenant_member_service.py`
- `apps/access/services/platform_tenant_service.py`
- `apps/access/services/audit_service.py`

阶段完成标准：
- 新目录可导入。
- 不修改旧挂载。
- 不改变线上行为。

### 阶段 1：先补正式 IAM 会话层

这是第一优先级。没有这层，后面的正式 API 都没法稳定开发。

#### 1.1 数据模型

新增模型建议：
- `AuthSession` 或等价命名

至少包含：
- `user`
- `access_token_hash`
- `refresh_token_hash`
- `access_expires_at`
- `refresh_expires_at`
- `revoked_at`
- `last_refreshed_at`
- `last_used_at`
- `client_ip`
- `user_agent`
- `created_at`
- `updated_at`

说明：
- Token 对客户端是 opaque token，不要求 JWT。
- 服务端只存 hash，不直接存明文 token。
- 一条记录代表一个正式认证会话。

涉及文件：
- `apps/access/models.py`
- 新 migration 文件

#### 1.2 认证类与会话服务

新增：
- Bearer 认证类，负责解析 `Authorization: Bearer <accessToken>`
- 会话服务，负责：
  - `login`
  - `refresh`
  - `logout`
  - 账号 `DISABLED` 时批量撤销会话
  - refresh token rotation

涉及文件：
- `apps/access/api_v1/authentication.py`
- `apps/access/services/auth_session_service.py`
- `config/settings.py`

建议做法：
- 不要全局把 DRF 默认认证直接替换成 Bearer，避免误伤已有 `/api/v1/*` 其他模块。
- 给 IAM 正式视图统一基类，在基类里显式指定正式认证类。

#### 1.3 Session 视图

先实现并测通：
- `POST /api/v1/iam/session/login`
- `POST /api/v1/iam/session/refresh`
- `POST /api/v1/iam/session/logout`
- `POST /api/v1/iam/session/register`
- `POST /api/v1/iam/session/register-by-phone`

可以复用的旧逻辑：
- `UserSelfRegisterView`
- `UserPhoneRegisterView`
- 用户创建的部分 serializer 逻辑

必须重写的点：
- 登录不再调用 Django Session `login()`
- 登出不再调用 Django Session `logout()`
- 登录响应从 `is_platform_admin` 改成 `hasPlatformAccess`
- 注册成功不自动登录

### 阶段 2：建立新的正式作用域基类

目标：
- 把 `session/*`、`me/*`、`tenant/*`、`platform/*` 需要的共性约束集中到基类，不再继续依赖 `RequireInternalPermission`。

建议新增基类：
- `IamAPIViewBase`
- `SessionAPIViewBase`
- `MeAPIViewBase`
- `TenantAPIViewBase`
- `PlatformAPIViewBase`

每个基类至少处理：
- 正式认证方式
- 标准响应封装
- 租户 header 校验
- 作用域判定
- 通用 400/401/403/404 映射

重点说明：
- 当前 `TenantContextMiddleware` 会对任意请求解析 `X-TENANT-CODE`。正式实现里不要再让 `platform/*` 隐式依赖它。
- 推荐把“是否需要 tenant header”下沉到 `TenantAPIViewBase` 或专用 resolver，而不是继续由全局中间件隐式决定。

涉及文件：
- `apps/access/api_v1/base.py`
- `apps/access/api_v1/permissions.py`
- `apps/access/services/identity_service.py`
- `apps/access/services/tenant_context_service.py`

### 阶段 3：先落 `session/*` 和 `me/*`

这组最独立，依赖最少，适合在 Bearer 会话层之后最先稳定。

#### 3.1 `me/profile`

来源：
- 新增正式实现，不直接复用旧 `UserDetailView`

正式输出：
- `userId`
- `username`
- `status`
- `createdAt`
- `updatedAt`
- `staffProfile`

注意：
- `status` 成功响应固定为 `ACTIVE`
- `platform_operator` 不允许访问

#### 3.2 `me/tenants`

可复用旧逻辑：
- `MeTenantListView` 的 queryset 方向

必须调整：
- 只返回 `ACTIVE` 租户 + `ACTIVE` 成员关系
- 输出字段改成正式 camelCase
- 返回分页结构 `data.list` + `data.total`
- 只允许 `unassigned`、`tenant_member`
- `unassigned` 返回空列表

这一阶段不要实现：
- `me/permissions`
- `me/invitations`

它们都属于明确下线能力。

### 阶段 4：先做租户工作侧最小闭环

这一步优先做能让客户侧真正用起来的最小闭环：

1. `GET /api/v1/iam/tenant/me`
2. `GET /api/v1/iam/tenant/roles`
3. `GET /api/v1/iam/tenant/members`
4. `GET /api/v1/iam/tenant/members/{memberId}`
5. `POST /api/v1/iam/tenant/members`
6. `PATCH /api/v1/iam/tenant/members/{memberId}`
7. `PUT /api/v1/iam/tenant/members/{memberId}/roles`
8. `POST /api/v1/iam/tenant/members/{memberId}/enable`
9. `POST /api/v1/iam/tenant/members/{memberId}/disable`

#### 4.1 这一步建议优先重写 serializer，而不是硬改旧 serializer

原因：
- 旧 `TenantMemberSerializer`、`TenantMemberCreateSerializer`、`TenantInitializeAdminSerializer` 带有大量正式 API 不需要的历史字段：
  - `tenant_id`
  - `member_no`
  - `qualifications`
  - `roles`
  - 邀请态字段

建议新建：
- `TenantMeSerializer`
- `TenantMemberListSerializer`
- `TenantMemberDetailSerializer`
- `TenantMemberCreateV1Serializer`
- `TenantMemberPatchV1Serializer`
- `TenantMemberRoleReplaceV1Serializer`

#### 4.2 成员状态的处理原则

数据库当前仍有：
- `INVITED`
- `REJECTED`
- `EXPIRED`
- `REVOKED`
- `ACTIVE`
- `DISABLED`

正式 API 只允许对外暴露：
- `ACTIVE`
- `DISABLED`

落地要求：
- `tenant/members` 列表默认只看 `ACTIVE + DISABLED`
- 对旧邀请相关状态，不再通过正式 API 暴露“业务可用语义”
- `enable/disable` 只接受正式状态流转，其他旧状态一律按冲突处理，不要继续沿用旧内部逻辑

#### 4.3 角色替换接口

可复用旧逻辑：
- `TenantMemberRoleAssignSerializer.save()` 的“全量覆盖”方向是对的

必须补的正式约束：
- 请求字段只允许 `roleCodes`
- 空数组允许，表示清空
- 保护最后一个 `ACTIVE tenant_admin`
- 返回正式成员对象

#### 4.4 明确删除的旧能力

这一步完成后，以下类不应再进入正式实现：
- `TenantMemberInviteView`
- `TenantMemberConfirmInvitationView`
- `MeInvitationListView`
- `MeInvitationRejectView`

### 阶段 5：再做平台目录和租户实体

这一步放在租户工作侧之后，因为它对客户侧主链路阻塞较小。

需要落地：
- `GET /api/v1/iam/platform/permissions`
- `GET /api/v1/iam/platform/roles`
- `GET /api/v1/iam/platform/roles/{roleId}`
- `GET /api/v1/iam/platform/tenants`
- `POST /api/v1/iam/platform/tenants`
- `GET /api/v1/iam/platform/tenants/{tenantId}`
- `POST /api/v1/iam/platform/tenants/{tenantId}/enable`
- `POST /api/v1/iam/platform/tenants/{tenantId}/disable`
- `POST /api/v1/iam/platform/tenants/{tenantId}/initialize-admin`

#### 5.1 可以部分复用的旧视图

- `PermissionCatalogView`
- `RoleListView`
- `RoleDetailView`
- `TenantViewSet`
- `TenantDetailView`
- `TenantEnableView`
- `TenantDisableView`
- `TenantInitializeAdminView`

#### 5.2 必须重写或强改的点

- `platform/*` 只能由 `platform_operator` 调用
- 不再通过 `superuser` 兜底进入正式 API
- `Tenant.code` 对外统一映射成 `tenantCode`
- `plan` 保留为只读 `null`
- 删除 `TenantSetPlanView`
- `initialize-admin` 的复用逻辑必须改成：
  - 目标租户必须 `ACTIVE`
  - 目标租户当前不存在 `ACTIVE tenant_admin`
  - 如果复用已有成员关系，只补齐 `tenant_admin` 并恢复 `status=ACTIVE`
  - 复用路径不得修改原 `displayName`

当前旧实现的风险：
- `TenantInitializeAdminSerializer` 复用已有成员关系时仍会覆盖 `display_name`
- 还会处理 `member_no`、`qualifications`
- 冲突码目前也不是正式落点

### 阶段 6：最后补审计、OpenAPI 和错误码

这一步放后面，因为它依赖所有正式路由和 serializer 稳定下来。

#### 6.1 审计接口

需要落地：
- `GET /api/v1/iam/tenant/audit-logs`
- `GET /api/v1/iam/platform/audit-logs`

可复用：
- `AuditLogListView`
- `TenantAuditLogListView`
- `log_action()`

必须调整：
- 正式分页
- 正式最少字段
- 作用域与权限边界分开，不再让一个接口混合返回两种日志范围

#### 6.2 错误码落点

必须处理的代码点：
- `apps/api_v1/business_response.py`
- `apps/access/exceptions.py`

原因：
- 当前 `StandardCode` 还没有定稿文档里的 `C0102`、`C0103`、`C0203`
- 不能继续只靠字符串猜测把 409 推成 `C0201`

建议做法：
- 为 IAM 正式异常新增明确错误码字段
- 在正式 IAM 视图里直接抛出定稿后的错误码，而不是再依赖通用推断

#### 6.3 OpenAPI

需要同步：
- `config/business_api_urlconf.py`
- `apps/api_v1/openapi_hooks.py`
- 新的 `apps/access/api_v1/views/*`

要求：
- 正式接口全部进 `/api/v1/docs/*`
- 不再让 `/internal/docs/*` 承载正式 IAM 文档
- 每条接口按定稿文档收口请求、响应、错误示例

### 阶段 7：切挂载并删除旧入口

只有当前 1 到 6 阶段都完成并通过测试，才做这一阶段。

必须同时完成：
- `config/urls.py` 新增正式挂载：`/api/v1/iam/*`
- `config/business_api_urlconf.py` 纳入 IAM 文档路由
- 删除 `config/internal_api_urlconf.py` 对正式 IAM 的承载
- 移除 `config/urls.py` 中 `path("internal/auth/", include("apps.access.urls"))`

说明：
- 如果为了开发方便，在中间某个提交里暂时并存新旧入口，这个状态不能作为最终交付结果。
- 最终提交必须把旧入口从路由层摘掉。

### 阶段 8：清理旧类和旧协议

路由切换完成后，再做一次代码清理，删除不会再被正式代码调用的旧实现，避免误用。

优先清理对象：
- `apps/access/urls.py` 中所有旧路径定义
- `SessionStatusView`
- `LoginView`
- `LogoutView`
- `UserListCreateView`
- `UserDetailView`
- `MePermissionsView`
- `MeInvitationListView`
- `MeInvitationRejectView`
- `TenantMemberInviteView`
- `TenantMemberConfirmInvitationView`
- `TenantSetPlanView`

可以暂不做数据库物理清理的对象：
- 邀请相关字段
- `member_no`
- `qualifications`
- 旧成员状态枚举

说明：
- 这些字段和状态本期可以继续留在数据库层，避免把“接口重构”和“数据清理”绑成一批高风险变更。
- 但它们不能再进入正式 serializer、正式 OpenAPI、正式响应和正式测试。

## 4. 文件级落地清单

### 4.1 必改文件

- `config/urls.py`
  - 新增 `/api/v1/iam/` 正式挂载
  - 删除 `/internal/auth/` 正式入口
- `config/business_api_urlconf.py`
  - 把 IAM 正式路由纳入业务文档
- `config/internal_api_urlconf.py`
  - 不再承载 IAM 正式路由
- `config/settings.py`
  - 补正式 IAM Bearer 认证所需配置
  - 不让 IAM 正式视图继续依赖 Session/Basic
- `apps/access/models.py`
  - 新增正式认证会话模型
  - 如有需要，补必要索引
- `apps/access/exceptions.py`
  - 补正式 IAM 错误码映射
- `apps/api_v1/business_response.py`
  - 扩展到定稿错误码集合
- `apps/access/middleware.py`
  - 收窄租户 header 解析责任，避免平台接口隐式依赖租户上下文

### 4.2 建议新建文件

- `apps/access/api_v1/urls.py`
- `apps/access/api_v1/base.py`
- `apps/access/api_v1/authentication.py`
- `apps/access/api_v1/permissions.py`
- `apps/access/api_v1/views/session.py`
- `apps/access/api_v1/views/me.py`
- `apps/access/api_v1/views/tenant.py`
- `apps/access/api_v1/views/platform.py`
- `apps/access/api_v1/serializers/session.py`
- `apps/access/api_v1/serializers/me.py`
- `apps/access/api_v1/serializers/tenant.py`
- `apps/access/api_v1/serializers/platform.py`

### 4.3 可以保留但要拆分的旧文件

- `apps/access/services.py`
  - 当前太大，建议把会话、身份、租户上下文、成员、平台租户、审计拆出去
- `apps/access/views.py`
  - 不建议继续作为正式 API 主入口
- `apps/access/serializers.py`
  - 不建议继续承载正式 serializer

## 5. 推荐提交顺序

建议按下面 7 组提交或 7 个 PR 做，不要一次性糊成一个超大提交。

1. 新目录骨架 + 正式 IAM 会话模型
2. Bearer 认证类 + `session/*`
3. `me/*`
4. `tenant/me` + `tenant/roles` + `tenant/members` 基础接口
5. `platform/*` 租户与目录接口
6. 审计、错误码、OpenAPI、测试
7. 路由切换 + 删除旧 `/internal/auth/*` 挂载

## 6. 测试落地清单

### 6.1 认证

- 登录成功返回 `accessToken`、`refreshToken`
- refresh 成功后旧 refresh token 失效
- logout 后当前会话 token 失效
- `DISABLED` 账号无法登录
- 账号转 `DISABLED` 后既有会话全部失效

### 6.2 作用域

- `unassigned` 可访问 `me/*`，不可访问 `tenant/*`、`platform/*`
- `tenant_member` 可访问 `me/*`、命中合法 `tenant/*`，不可访问 `platform/*`
- `platform_operator` 可访问 `platform/*`，不可访问 `me/*`、`tenant/*`

### 6.3 租户工作侧

- 缺少 `X-TENANT-CODE` 返回 `400 + B0001`
- header 命中无效或无权限租户返回 `403 + A0403`
- `tenant/me` 成功响应固定 `tenant.status=ACTIVE`、`member.status=ACTIVE`
- `tenant/members` 默认返回 `ACTIVE + DISABLED`
- `tenant/members` 的 `userId` 精确筛选仍返回分页结构
- `PATCH member` 只允许 `displayName`
- `PUT roles` 全量覆盖，空数组清空
- 禁止移除最后一个 `ACTIVE tenant_admin`

### 6.4 平台侧

- `platform/tenants` 只接受平台工作态账号
- `initialize-admin` 对非 `ACTIVE` 租户返回 `409 + C0203`
- `initialize-admin` 在已有 `ACTIVE tenant_admin` 时返回 `409 + C0203`
- `initialize-admin` 复用已有成员关系时不改 `displayName`

### 6.5 下线能力

- `/internal/auth/*` 不可访问
- 不再暴露：
  - `me/permissions`
  - 邀请流程
  - `tenant/profile`
  - `tenant/users/*`
  - `platform/users/*`
  - `set-plan`
  - `PUT /tenant/members/{memberId}`
  - `POST /tenant/members/{memberId}/roles`

## 7. 最后建议

这次重构不要从“改 URL”开始，而要从“先补正式认证会话层、再补正式作用域基类”开始。  
如果顺序反过来，会出现三个问题：

- 新路径先挂出来，但认证和鉴权还是旧逻辑，接口表面变了，语义没变。
- `tenant/*` 和 `platform/*` 会继续共用旧内部权限体系，后面返工更大。
- OpenAPI 和测试会在中途反复推翻。

最稳的落地顺序就是：

1. 先做 Bearer 会话
2. 再做正式基类
3. 再做 `session/*`、`me/*`
4. 再做 `tenant/*`
5. 再做 `platform/*`
6. 最后切挂载、删旧入口

