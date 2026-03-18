
  依据文件：
  项目总体概览/api设计规范v2.md
  config/urls.py
  config/business_api_urlconf.py
  config/internal_api_urlconf.py
  apps/access/urls.py
  apps/access/views.py
  apps/access/serializers.py
  apps/access/middleware.py
  apps/access/exceptions.py
  apps/api_v1/business_response.py
  apps/api_v1/pagination.py
  apps/api_v1/openapi_hooks.py
  apps/access/management/commands/seed_role_permissions.py

  Access API 路由挂载重构计划（含实现规范）

  先看这几个词：

  - `userId`：全局用户账号 ID。一个账号只有一个 `userId`。用户自己可以通过 `GET /api/v1/iam/me/profile` 读到它。
  - `tenantId`：租户实体 ID。平台侧管理租户时使用。
  - `tenantCode`：租户业务编码。租户工作侧通过 `X-TENANT-CODE` 传递这个值。
  - `memberId`：租户成员关系 ID。它不是用户账号 ID，而是“某个 `userId` 加入某个租户后生成的那条成员记录 ID”。如果同一个 `userId` 加入两个租户，会有两条成员关系，也会有两个不同的 `memberId`。
  - 文中统一使用 `memberId` 这个写法，不使用 `MemberId`。
  - `正式 IAM 账号`：可通过 `/api/v1/iam/*` 建立或使用正式认证会话的账号总集合。
  - `正式 IAM 业务账号`：面向客户业务使用的正式 IAM 账号子集；可自助注册；不包含平台工作态账号。
  - `平台工作态账号`：正式 IAM 账号的另一子集；仅用于通过 `session/*` 建立认证会话并调用 `platform/*`；不通过正式 API 管理。

  可以把它们理解成：

  - `userId` 解决“这个人是谁”。
  - `tenantId` 解决“平台层面唯一定位哪个租户实体”。
  - `tenantCode` 解决“租户工作侧当前租户上下文是谁”。
  - `memberId` 解决“这个人在这个租户里的那条成员记录是哪一条”。

  例子：

  - 某用户注册后拿到 `userId=U1001`。这时他只是“一个全局账号”，还不是任何租户成员。
  - 这个用户加入租户 A 后，会生成一条成员记录，比如 `memberId=M2001`。
  - 如果同一个用户后来又加入租户 B，会再生成一条新的成员记录，比如 `memberId=M3050`；这和租户 A 里的 `memberId=M2001` 不是一回事。

  鉴权运行态固定为：

  - `unassigned`：正式 IAM 业务账号，已注册、可登录、可访问 `me/*`，但还没有任何租户成员关系。
  - `tenant_member`：正式 IAM 业务账号，已拥有至少一个租户成员关系；可访问 `me/*`；仅当 `X-TENANT-CODE` 命中的当前租户状态为 `ACTIVE`、该账号在该租户下的成员关系也为 `ACTIVE`，且该租户下权限校验通过时，才可访问 `tenant/*`。
  - `platform_operator`：平台工作态账号；可通过 `session/*` 建立认证会话，并访问 `platform/*`，不进入 `me/*`。
  - `tenant_member` 与 `platform_operator` 互斥；`unassigned` 是业务账号未入租时的初始运行态。
  - `tenant_member` 只表示“该账号已至少拥有一条租户成员关系”，不直接代表对任意 `tenant/*` 都有权限。
  - 上述运行态只用于鉴权与资源边界，不作为本期正式 API 的固定对外返回字段。

  1. 背景
  当前 access 实现挂载在 /internal/auth/*，见 config/urls.py 和 apps/access/urls.py。这套接口同时承载了登录会话、当前用户、租户成员管理、平
  台租户管理、角色权限目录、审计日志等多类能力，但路径分层并不清晰。

  按照 项目总体概览/api设计规范v2.md 的要求，正式接口应统一进入 /api/v1 体系，并遵循“租户工作侧用 header、平台管理侧用 path”的多租户分层规
  则。现有 access 仍保留了大量内部时期的痕迹，主要问题有：

  - 路径前缀仍是 /internal/auth，不符合正式业务 API 前缀规范。
  - auth 语义过窄，当前模块实际是 IAM，不只是认证。
  - users、tenants、audit-logs 等接口存在混合语义，同一路径会因为调用者身份不同返回不同范围的数据。
  - 响应风格不统一，access 里仍有 business_code/business_detail_code、裸 serializer 返回、自定义 count 等写法，而 /api/v1 业务接口已经统一
    为 code/msg/data。
  - 分页协议没有完全对齐 /api/v1 标准分页。
  - 正式客户接口和内部 helper 接口没有清晰分界。

  同时，代码事实也说明了成员管理的真实边界：

  - apps/access/middleware.py 已经用 X-TENANT-CODE 解析当前租户上下文。
  - tenant-members/* 相关视图在 apps/access/views.py 中显式设置了 platform_admin_forbidden = True，这说明它本质上是租户工作侧接口，而不是平
    台侧接口。
  - apps/access/models.py 与 apps/access/serializers.py 已明确限制 `platform_admin` 账号不能绑定 `TenantMember`，这说明“平台工作态账号”与“租
    户成员账号”在当前实现里本来就是互斥边界。
  - apps/access/management/commands/seed_role_permissions.py 中 tenant_admin 具备的是租户成员管理相关权限，而不是平台租户管理权限。

  所以这次改造不能只改前缀，必须连同接口分层、资源边界、响应规范、文档挂载一起收口。
  同时需要明确：本文遵循 `api设计规范v2.md` 的总原则，并尽量贴近其中的通用最佳实践；但对“平台工作态切入租户工作态”这类能力采用更严格的项目级收窄。凡涉及本项目已明确的业务边界、作用域边界与资源边界时，以本文定义为准。

  2. 已确认的业务边界
  以下边界已按你的口径固定，作为本方案的前提：

  - session-status 不作为正式对客户公开的 API 保留。
  - 客户侧需要“当前租户用户目录”能力，该能力统一由 `GET /api/v1/iam/tenant/members` 承担，不再单独保留 `tenant/users/*`。
  - 客户侧需要“当前租户上下文”接口；该接口应同时返回当前租户资料与当前登录用户在该租户下的业务角色信息。
  - 客户侧需要“当前登录用户自己的 profile”接口，且该接口应返回 `userId`。
  - 已拥有租户成员关系的正式 IAM 业务账号，需要通过正式 API 读取自己可进入的租户上下文列表，以获取 `tenantCode` 并完成租户工作态切换；该能力由 `GET /api/v1/iam/me/tenants` 提供。
  - 租户管理员需要通过正式 API 读取“当前租户可分配角色列表”。
  - 重构后的正式 IAM 认证域限定为“正式 IAM 账号”；这套账号集合按运行态分为 `unassigned`、`tenant_member`、`platform_operator` 三类；由 `admin/后台` 维护的技术兜底账号不属于这套认证域，不签发 Bearer Token，也不进入正式 API 资源集合与返回契约。
  - 正式 IAM 业务账号覆盖 `unassigned` 与 `tenant_member` 两种运行态；平台工作态账号单独覆盖 `platform_operator` 运行态。
  - 平台工作态账号只进入 `session/*` 与 `platform/*`，不进入 `me/*` 或 `tenant/*`；这类账号的创建、修改、查询与排障不纳入正式 API，统一留在 `admin/后台` 或内部工具。
  - 正式 API 不提供全局用户目录、全局用户检索或“未加入租户用户”批量查询接口。
  - 已创建但未加入租户的正式 IAM 业务账号，运行态固定为 `unassigned`；这类账号只保留两种处理方式：用户本人通过 `GET /api/v1/iam/me/profile` 获取自己的 `userId` 并交给租户管理员；平台或内部人员如需批量检索，统一通过 `admin/后台` 或内部工具处理。
  - 平台侧不提供正式通用成员管理 HTTP 接口。
  - 这里的“不提供通用成员管理”是指：除 `POST /api/v1/iam/platform/tenants/{tenantId}/initialize-admin` 这一条正式例外外，不提供通过路径显式指定某个租户后，再去做成员列表、详情、创建、角色分配、启停等操作的接口；该例外只允许在目标租户当前不存在任何 `ACTIVE` 状态且拥有 `tenant_admin` 角色的成员时，用于租户管理员 `bootstrap / break-glass` 修复。
  - 邀请流程相关 API 不纳入重构后的正式 API，成员加入只保留“租户管理员将已存在的全局用户加入当前租户”这一条路径。
  - 一个账号不能同时处于 `tenant_member` 与 `platform_operator` 两种已赋权运行态。
  - 租户 A 的管理员可以管理租户 A 当前租户上下文内的成员。
  - 租户 A 的管理员不能管理租户 B 的成员。
  - 旧 /internal/auth/* 不保留兼容期，直接切换。

  为避免歧义，正式表述应固定为：

  “平台侧不提供以租户 ID 显式指定目标租户的通用成员管理 HTTP 接口；`POST /api/v1/iam/platform/tenants/{tenantId}/initialize-admin` 是平台侧唯一允许写入租户成员关系的正式例外，只能在目标租户当前不存在任何 `ACTIVE` 状态且拥有 `tenant_admin` 角色的成员时，用于租户管理员 `bootstrap / break-glass` 修复，不承接日常成员管理；当前租户用户目录与成员管理统一通过租户工作侧 `tenant/members/*` 提供，租户管理员仅可管理自己当前租户内的成员；`tenant_member` 与 `platform_operator` 两种运行态互斥；平台工作态账号只进入 `session/*` 与 `platform/*`，不进入 `me/*` 与 `tenant/*`；平台工作态账号管理与后台技术兜底账号管理均不纳入重构后的正式 API。”

  3. 命名与目标挂载
  正式 API 目标前缀定义为：

  /api/v1/iam

  不再使用 /internal，原因是：

  - 规范只要求正式接口统一进入 /api/v1，没有要求必须带 internal。
  - 这些接口里已有客户侧正式使用场景，再带 internal 会和“重构后的正式 API”语义冲突。
  - 如果未来确实存在仅前端壳或内部运维使用的 helper，才另行保留内部前缀。

  不再使用 /auth，原因是：

  - 当前模块不只是认证，还包括 identity、membership、role、permission、tenant 等能力。
  - 领域名用 iam 更准确，auth 只适合作为 session/login 这一层子能力。

  正式 API 当前采用四段式分层：

  | 分层 | 路由前缀 | 租户表达方式 | 说明 |
  | --- | --- | --- | --- |
  | 会话层 | /api/v1/iam/session/* | 无 | 登录、刷新令牌、登出、注册 |
  | 当前用户层 | /api/v1/iam/me/* | 无 | 正式 IAM 业务账号读取自己当前登录会话的全局账号资料与可进入租户上下文列表 |
  | 租户工作侧 | /api/v1/iam/tenant/* | X-TENANT-CODE | 当前租户上下文内工作 |
  | 平台管理侧 | /api/v1/iam/platform/* | path 具名参数 | 平台层面管理租户实体、平台目录、平台审计 |

  核心原则：

  - 路径决定接口作用域，不能再靠“调用者是什么身份”动态切换同一路径的语义。
  - `me/*` 只处理正式 IAM 业务账号当前登录用户自己的全局账号资料与自有租户上下文，不承担平台工作态账号自省入口。
  - tenant/* 只处理当前租户工作侧问题。
  - platform/* 只处理平台层问题。
  - 顶层 `session`、`me`、`tenant`、`platform` 属于作用域命名空间，不按复数资源名词要求约束。
  - `me/profile`、`tenant/me` 属于当前上下文单例投影接口，是本项目在资源命名规则中的显式例外。
  - 除作用域命名空间、当前上下文单例投影接口和规范允许的动作接口外，其余正式资源命名统一使用复数资源名词。
  - `isPlatformAdmin` 只作为 `session/login` 的当前会话自省返回字段存在，用于提示“当前登录账号是否具备调用 `platform/*` 的能力”；除该接口外，它不参与任何正式业务资源的定义、筛选、排序或写入。
  - 平台管理员查看可管理租户实体列表统一走 `GET /api/v1/iam/platform/tenants`；`GET /api/v1/iam/me/tenants` 只用于正式 IAM 业务账号读取自己可进入的租户上下文列表。
  - 调用主体、租户上下文、成员资源共性和平台租户资源共性，统一收口到 4.1 通用约束。

  4. 目标路由结构

  4.1 通用约束
  本节先抽出后续路由表反复出现的共性规则。除非某条路由单独说明，否则默认都适用这些约束。

  认证与作用域：

  - 受保护的 `/api/v1/iam/*` 接口只面向正式 IAM 账号；`admin/后台` 维护的技术兜底账号不进入正式 API 认证域。
  - `session/*` 不依赖租户上下文；除 `session/logout` 外，其余 session 正式接口匿名可调用。
  - `me/*` 只返回当前会话自己的全局账号资料和可进入的租户上下文列表，不依赖 `X-TENANT-CODE`；只允许已登录的正式 IAM 业务账号调用，包括 `unassigned`、`tenant_member`；`platform_operator` 不可调用。
  - `tenant/*` 统一通过 `X-TENANT-CODE` 解析当前租户；只允许运行态为 `tenant_member` 的账号调用，但 `tenant_member` 只表示“至少已拥有一个租户成员关系”；某次请求是否可访问，最终仍以 `X-TENANT-CODE` 解析出的当前租户必须为 `ACTIVE`、该账号在该租户下的成员关系必须为 `ACTIVE`，以及该租户下角色权限校验结果为准；`unassigned` 与 `platform_operator` 均不可调用；不允许通过 path 指定租户。
  - `platform/*` 只允许运行态为 `platform_operator` 的账号调用；`unassigned` 与 `tenant_member` 不可调用；不依赖 `X-TENANT-CODE`；涉及单个租户实体时统一以 path `tenantId` 为准。
  - `tenant/*` 缺少 `X-TENANT-CODE` 或 header 格式非法时，统一返回 `400 + B0001`。
  - 已认证但无当前作用域权限的请求，统一返回 `403 + A0403`；典型场景包括 `unassigned` 账号访问 `tenant/*` 或 `platform/*`、`platform_operator` 访问 `me/*` 或 `tenant/*`、`tenant_member` 访问 `platform/*`，以及租户工作侧 header 命中不存在、已停用或当前用户无 `ACTIVE` 成员关系的租户。
  - 在已生效作用域内查询目标资源但资源不存在时，统一返回 `404 + C0404`；例如当前租户下不存在目标 `memberId`。

  全局账号资料共性：

  - `session/login`、`session/register`、`session/register-by-phone`、`me/profile` 如返回账号绑定的全局人员档案，正式字段名统一为 `staffProfile`。
  - `staffProfile` 表示账号绑定的全局人员档案，不是租户内成员资料，也不替代 `tenant/members/*` 中的 `displayName`、角色等租户内信息。
  - 正式 IAM 账号对外只暴露 `ACTIVE`、`DISABLED` 两种账号状态。
  - 自助注册成功后，账号状态固定写入 `ACTIVE`。
  - `DISABLED` 账号不得通过 `session/login` 建立新认证会话；账号状态转为 `DISABLED` 后，服务端应撤销该账号现有认证会话，后续 `session/refresh` 与受保护接口统一按 `401 + A0401` 处理。
  - `isPlatformAdmin` 只允许出现在 `session/login` 的响应中；在除此之外的正式 API 中禁止出现。

  租户实体与当前租户上下文共性：

  - 租户实体对外统一使用 `tenantId` 与 `tenantCode`；其中 `tenantCode` 对应租户业务编码，也是 `X-TENANT-CODE` 的取值。
  - `me/tenants` 返回当前登录用户可进入的租户上下文列表；只包含“租户状态为 `ACTIVE` 且当前用户在该租户下成员状态也为 `ACTIVE`”的成员关系，用于让业务账号获取可切换的 `tenantCode`。
  - `tenant/me` 返回的是“当前租户上下文中的我”，而不是脱离当前用户的纯租户实体详情；业务角色通过 `member.roleCodes` 表达。

  成员资源共性：

  - `tenant/members/*` 只处理当前租户内的成员关系，不创建也不修改全局用户账号。
  - `tenant/members` 只暴露 `ACTIVE`、`DISABLED` 两种正式成员状态；列表默认返回 `ACTIVE + DISABLED`。
  - `POST /tenant/members` 只负责把已存在的全局用户加入当前租户；新成员关系的 `tenantId` 由服务端根据 `X-TENANT-CODE` 确定。
  - 允许建立 `TenantMember` 关系的“可入租账号”，固定定义为 `status=ACTIVE` 且运行态不为 `platform_operator` 的正式 IAM 业务账号；其中既包括 `unassigned` 账号，也包括已在其他租户下拥有成员关系的 `tenant_member` 账号。
  - 除 `GET /api/v1/iam/tenant/me` 外，本期成员目录、成员管理与可分配角色目录相关接口，即 `GET /api/v1/iam/tenant/members`、`GET /api/v1/iam/tenant/members/{memberId}`、`GET /api/v1/iam/tenant/roles`、`POST /api/v1/iam/tenant/members`、`PATCH /api/v1/iam/tenant/members/{memberId}`、`PUT /api/v1/iam/tenant/members/{memberId}/roles`、`POST /api/v1/iam/tenant/members/{memberId}/enable`、`POST /api/v1/iam/tenant/members/{memberId}/disable`，统一只允许 `tenant_admin` 调用。
  - 成员角色只允许使用 `GET /api/v1/iam/tenant/roles` 返回的可分配租户角色，不得使用平台角色。
  - `PUT /api/v1/iam/tenant/members/{memberId}/roles` 与 `POST /api/v1/iam/tenant/members/{memberId}/disable` 不得把当前租户变成“0 个 `ACTIVE` 状态且拥有 `tenant_admin` 角色的成员”；如本次操作会移除最后一个有效租户管理员，统一返回 `409 + C0203`。
  - `POST /api/v1/iam/tenant/members`、`PATCH /api/v1/iam/tenant/members/{memberId}`、`PUT /api/v1/iam/tenant/members/{memberId}/roles`、`POST /api/v1/iam/tenant/members/{memberId}/enable`、`POST /api/v1/iam/tenant/members/{memberId}/disable` 成功后统一返回成员对象，至少包含 `memberId`、`userId`、`username`、`displayName`、`status`、`roleCodes`。
  - 成员资质能力本期不纳入重构后的正式 API；正式请求、响应与验收中不再出现 `qualifications`。

  平台租户资源共性：

  - `platform/tenants/*` 只处理租户实体，不表示进入 `tenant/*` 工作态，也不授予 `tenant/*` 调用资格。
  - `plan` 只是只读预留字段，当前统一返回 `null`；本期不支持筛选、设置或变更。
  - `POST /api/v1/iam/platform/tenants/{tenantId}/initialize-admin` 是平台侧唯一允许写入成员关系的正式例外，不等同于平台侧通用成员管理接口；它只在目标租户当前不存在任何 `ACTIVE` 状态且拥有 `tenant_admin` 角色的成员时允许执行，用于租户管理员 `bootstrap / break-glass` 修复。

  固定目录接口：

  - `GET /api/v1/iam/tenant/roles`
  - `GET /api/v1/iam/platform/permissions`
  - `GET /api/v1/iam/platform/roles`

  以上三个接口都是固定目录接口，必须固定为非分页接口。
  固定目录接口的响应 `data` 直接返回数组，不使用 `data.list`、`data.total`。
  其中目录字段约束固定为：

  - `GET /api/v1/iam/platform/permissions` 的 `status` 正式取值固定为 `ACTIVE`、`DISABLED`。
  - `GET /api/v1/iam/platform/roles`、`GET /api/v1/iam/platform/roles/{roleId}`、`GET /api/v1/iam/tenant/roles` 的 `status` 正式取值固定为 `ACTIVE`、`DISABLED`。
  - `permissionGrants[].permission` 固定表示权限 `code`，不是权限名称或权限 ID。
  - `permissionGrants[].scopeType` 正式取值固定为 `ALL`、`OWN`、`ASSIGNED`。

  4.2 Session

  | 路由 | 功能描述 | 权限标识 | 调用限制 |
  | --- | --- | --- | --- |
| POST /api/v1/iam/session/login | 用户名密码登录，创建正式 IAM 认证会话，签发 Bearer Token，并返回当前账号基础认证信息；登录响应中的 `user` 只暴露正式 IAM 账号字段。 | `public` | 匿名可调用；不依赖租户上下文；请求体必须提供 `username`、`password`；仅正式 IAM 账号可登录；若账号不属于正式 IAM 账号集合，或账号状态为 `DISABLED`，统一按认证失败处理，返回 `401 + A0401`；成功后客户端后续请求必须通过 `Authorization: Bearer <accessToken>` 调用正式 API。 |
| POST /api/v1/iam/session/refresh | 使用有效的 refreshToken 刷新当前认证会话，换取新的 Bearer Token。 | `public` | 匿名可调用；不依赖租户上下文；请求体必须提供有效 `refreshToken`；正式 API 固定启用 refresh token rotation；若当前认证会话所属账号状态已变为 `DISABLED`，统一返回 `401 + A0401`。 |
  | POST /api/v1/iam/session/logout | 退出当前认证状态，撤销当前认证会话，使当前认证会话下尚未过期的 accessToken 与 refreshToken 立即失效。 | `iam.session.logout` | 仅持有有效 Bearer Token 的用户可调用；不依赖 X-TENANT-CODE。 |
| POST /api/v1/iam/session/register | 用户名方式自助注册正式 IAM 业务账号，并创建基础全局资料。 | `public` | 匿名可调用；不依赖租户上下文；请求体必须提供 `username`、`password`、`name`、`phone`；注册成功后账号状态固定为 `ACTIVE`，初始运行态固定为 `unassigned`；仅返回新账号基础信息与 `userId`，不自动登录，也不负责加入租户。 |
| POST /api/v1/iam/session/register-by-phone | 手机号方式自助注册正式 IAM 业务账号，并创建基础全局资料。 | `public` | 匿名可调用；不依赖租户上下文；请求体必须提供 `phone`、`smsCode`、`password`；服务端以手机号作为登录用户名；注册成功后账号状态固定为 `ACTIVE`，初始运行态固定为 `unassigned`；仅返回新账号基础信息与 `userId`，不自动登录，也不负责加入租户。 |

  4.3 Me
  以下 `me/*` 路由默认继承 4.1 中 `me/*` 的通用约束。

| 路由 | 功能描述 | 权限标识 | 调用限制 |
| --- | --- | --- | --- |
| GET /api/v1/iam/me/profile | 返回当前登录用户自己的全局账号资料，响应至少包含 `userId`、`username`、`status`、`createdAt`、`updatedAt`；其中 `status` 正式取值固定为 `ACTIVE`、`DISABLED`；如返回全局人员档案，遵循 4.1 中 `staffProfile` 的统一定义。该接口同时承担“用户读取自己的 `userId` 并提供给租户管理员完成入租”的正式用途。 | `iam.me.profile.read` | 遵循 4.1 中 `me/*` 的通用约束；仅 `unassigned`、`tenant_member` 两种正式 IAM 业务账号运行态可调用；`platform_operator` 不可调用。 |
| GET /api/v1/iam/me/tenants | 返回当前登录用户可进入的租户上下文分页列表，用于获取可切换的 `tenantCode`；只返回“租户状态为 `ACTIVE` 且当前用户在该租户下成员状态也为 `ACTIVE`”的租户成员关系；列表只支持 `pageNum`、`pageSize`，默认按 `tenantId asc` 排序，并以 `memberId asc` 作为稳定次排序；列表项至少包含 `tenantId`、`tenantCode`、`name`、`memberId`、`roleCodes`。 | `iam.me.tenant.read` | 遵循 4.1 中 `me/*` 的通用约束；仅 `unassigned`、`tenant_member` 两种正式 IAM 业务账号运行态可调用；`platform_operator` 不可调用；`unassigned` 调用时返回空列表。 |

  4.4 Tenant
  以下 `tenant/*` 路由默认继承 4.1 中 `tenant/*` 与成员资源的通用约束。本节只写各接口自己的差异项。

  | 路由 | 功能描述 | 权限标识 | 调用限制 |
  | --- | --- | --- | --- |
  | GET /api/v1/iam/tenant/me | 返回当前租户上下文中的“我”；响应至少包含 `tenant` 与 `member` 两个对象；其中 `tenant` 至少包含 `tenantId`、`tenantCode`、`name`、`status`、`plan`、`remark`、`createdAt`、`updatedAt`，`member` 至少包含 `memberId`、`userId`、`displayName`、`status`、`roleCodes`；其中 `roleCodes` 表示当前登录用户在当前租户下的业务角色编码。 | `iam.tenant.me.read` | 遵循 4.1 中 `tenant/*` 的通用约束；只返回当前登录用户在当前租户下的成员上下文。 |
| GET /api/v1/iam/tenant/members | 返回当前租户成员目录，统一承担“当前租户用户目录”和“成员管理列表”两种正式用途；列表支持 `status`、`keywords`、`userId`、`sortBy`、`sortOrder`、`pageNum`、`pageSize` 查询；其中 `userId` 为当前租户内全局用户 ID 的单值精确匹配，用于把全局用户定位到当前租户成员关系；`sortBy` 仅允许 `displayName`、`username`、`status`、`createdAt`、`updatedAt`，默认按 `displayName asc` 排序，并以 `memberId asc` 作为稳定次排序；传 `userId` 时返回结果仍保持分页列表，但当前租户下最多命中 1 条记录；列表项至少应包含 `memberId`、`userId`、`username`、`displayName`、`status`、`roleCodes`。 | `iam.tenant.member.read` | 遵循 4.1 中 `tenant/*` 与成员资源通用约束；本接口额外支持 `status`、`keywords`、`userId`、`sortBy`、`sortOrder` 查询；仅 `tenant_admin` 可调用。 |
| POST /api/v1/iam/tenant/members | 将一个已存在的全局用户加入当前租户，并可一并写入成员显示名与初始角色；创建租户成员关系后立即生效；正式请求体固定为 `userId` 必填，`displayName`、`roleCodes` 选填；其中 `roleCodes` 可省略或传空数组，表示允许先创建、后分配角色。 | `iam.tenant.member.create` | 遵循 4.1 中 `tenant/*` 与成员资源通用约束；仅 `tenant_admin` 可调用。 |
| GET /api/v1/iam/tenant/members/{memberId} | 返回当前租户单个成员详情。这里的 `{memberId}` 是租户成员 ID；详情至少应包含 `memberId`、`userId`、`username`、`displayName`、`status`、`roleCodes`；其中与全局账号相关的信息在正式响应中只收口为 `userId` 与 `username` 两个只读字段，不单独开放全局用户详情资源。 | `iam.tenant.member.read` | 遵循 4.1 中 `tenant/*` 与成员资源通用约束；目标 `memberId` 必须属于当前租户；仅 `tenant_admin` 可调用。 |
| PATCH /api/v1/iam/tenant/members/{memberId} | 更新当前租户成员的租户内属性；正式可写字段只允许 `displayName`；`displayName` 显式传空字符串时表示清空该字段；成功后返回更新后的成员对象，至少包含 `memberId`、`userId`、`username`、`displayName`、`status`、`roleCodes`。 | `iam.tenant.member.update` | 遵循 4.1 中 `tenant/*` 与成员资源通用约束；目标 `memberId` 必须属于当前租户；仅 `tenant_admin` 可调用。 |
| GET /api/v1/iam/tenant/roles | 返回当前租户可分配角色目录，用于租户管理员给成员选择可分配角色；只返回允许分配给租户成员的角色；列表项至少包含 `roleId`、`code`、`name`、`description`、`status`。 | `iam.tenant.role.readAssignable` | 遵循 4.1 中 `tenant/*` 的通用约束；固定目录接口，非分页；仅 `tenant_admin` 可调用。 |
| PUT /api/v1/iam/tenant/members/{memberId}/roles | 全量更新当前租户成员的角色集合；正式请求体固定为 `roleCodes` 必填数组；传空数组表示清空该成员当前全部已授予租户角色；未包含在 `roleCodes` 中的旧角色一律撤销；成功后返回更新后的成员对象，至少包含 `memberId`、`userId`、`username`、`displayName`、`status`、`roleCodes`。 | `iam.tenant.member.assignRoles` | 遵循 4.1 中 `tenant/*` 与成员资源通用约束；目标 `memberId` 必须属于当前租户；仅 `tenant_admin` 可调用；若本次更新会移除当前租户最后一个 `ACTIVE tenant_admin`，统一返回 `409 + C0203`。 |
| POST /api/v1/iam/tenant/members/{memberId}/enable | 重新启用当前租户内已停用成员；请求体固定为空；成功后返回更新后的成员对象，至少包含 `memberId`、`userId`、`username`、`displayName`、`status`、`roleCodes`。 | `iam.tenant.member.enable` | 遵循 4.1 中 `tenant/*` 与成员资源通用约束；目标 `memberId` 必须属于当前租户，且状态必须允许启用；仅 `tenant_admin` 可调用。 |
| POST /api/v1/iam/tenant/members/{memberId}/disable | 停用当前租户内已生效成员；请求体固定为空；成功后返回更新后的成员对象，至少包含 `memberId`、`userId`、`username`、`displayName`、`status`、`roleCodes`。 | `iam.tenant.member.disable` | 遵循 4.1 中 `tenant/*` 与成员资源通用约束；目标 `memberId` 必须属于当前租户，且状态必须允许停用；仅 `tenant_admin` 可调用；若本次停用会移除当前租户最后一个 `ACTIVE tenant_admin`，统一返回 `409 + C0203`。 |
  | GET /api/v1/iam/tenant/audit-logs | 返回当前租户范围内的审计日志分页列表；支持 `pageNum`、`pageSize`、`action`、`operatorUserId`、`startAt`、`endAt`、`sortBy`、`sortOrder` 查询；默认按 `createdAt desc` 排序；列表项至少包含 `auditLogId`、`action`、`targetType`、`targetId`、`operatorUserId`、`operatorDisplayName`、`createdAt`、`summary`。 | `iam.tenant.auditLog.read` | 遵循 4.1 中 `tenant/*` 的通用约束；只返回当前租户日志。 |

  4.5 Platform
  以下 `platform/*` 路由默认继承 4.1 中 `platform/*` 与平台租户资源的通用约束。本节只写各接口自己的差异项。

  | 路由 | 功能描述 | 权限标识 | 调用限制 |
  | --- | --- | --- | --- |
  | GET /api/v1/iam/platform/permissions | 返回平台固定权限目录；列表项至少包含 `permissionId`、`code`、`name`、`module`、`resourceCode`、`status`。 | `iam.platform.permission.read` | 遵循 4.1 中 `platform/*` 的通用约束；固定目录接口，非分页。 |
  | GET /api/v1/iam/platform/roles | 返回平台固定角色目录及其权限模板，不用于租户管理员给成员分配角色；列表项至少包含 `roleId`、`code`、`name`、`description`、`status`、`createdAt`、`updatedAt`、`permissionGrants`。 | `iam.platform.role.read` | 遵循 4.1 中 `platform/*` 的通用约束；固定目录接口，非分页。 |
  | GET /api/v1/iam/platform/roles/{roleId} | 返回平台固定角色的详情；响应至少包含 `roleId`、`code`、`name`、`description`、`status`、`createdAt`、`updatedAt`、`permissionGrants`；其中 `permissionGrants` 每项至少包含 `permission`、`scopeType`。 | `iam.platform.role.read` | 遵循 4.1 中 `platform/*` 的通用约束；`{roleId}` 为角色 ID。 |
  | GET /api/v1/iam/platform/audit-logs | 返回平台维度审计日志分页列表；支持 `pageNum`、`pageSize`、`tenantId`、`action`、`operatorUserId`、`startAt`、`endAt`、`sortBy`、`sortOrder` 查询；默认按 `createdAt desc` 排序；列表项至少包含 `auditLogId`、`tenantId`、`tenantCode`、`action`、`targetType`、`targetId`、`operatorUserId`、`operatorDisplayName`、`createdAt`、`summary`。 | `iam.platform.auditLog.read` | 遵循 4.1 中 `platform/*` 的通用约束。 |
  | GET /api/v1/iam/platform/tenants | 返回平台租户实体分页列表，用于平台管理页展示与筛选可管理租户；列表固定支持 `pageNum`、`pageSize`、`keywords`、`status`、`sortBy`、`sortOrder` 查询；其中 `status` 正式取值固定为 `ACTIVE`、`DISABLED`；`keywords` 至少匹配 `tenantCode`、`name`；`sortBy` 仅允许 `tenantId`、`tenantCode`、`name`、`createdAt`、`updatedAt`；默认按 `tenantId desc` 排序；列表项至少应包含 `tenantId`、`tenantCode`、`name`、`status`、`plan`、`createdAt`、`updatedAt`。 | `iam.platform.tenant.read` | 遵循 4.1 中 `platform/*` 与平台租户资源通用约束；分页默认 `pageNum=1`、`pageSize=20`，最大 `pageSize=100`。 |
| POST /api/v1/iam/platform/tenants | 创建新的租户实体；正式请求体固定为 `tenantCode`、`name` 必填，`remark` 选填；重构后的正式 API 不在该接口上接收 `status` 或 `plan`；成功后返回新建租户对象，至少包含 `tenantId`、`tenantCode`、`name`、`status`、`plan`、`remark`、`createdAt`、`updatedAt`，其中新建后 `status` 固定为 `ACTIVE`，`plan` 固定为 `null`。 | `iam.platform.tenant.create` | 遵循 4.1 中 `platform/*` 与平台租户资源通用约束；`tenantCode`、`name` 不得为空，且 `tenantCode` 必须全局唯一；如 `tenantCode` 已存在，统一返回 `409 + C0101`。 |
  | GET /api/v1/iam/platform/tenants/{tenantId} | 返回单个租户实体详情；响应至少应包含 `tenantId`、`tenantCode`、`name`、`status`、`plan`、`remark`、`createdAt`、`updatedAt`；其中 `plan` 为只读预留字段，当前统一返回 `null`。 | `iam.platform.tenant.read` | 遵循 4.1 中 `platform/*` 与平台租户资源通用约束。 |
  | POST /api/v1/iam/platform/tenants/{tenantId}/enable | 启用指定租户；请求体固定为空；成功后返回更新后的租户对象，至少包含 `tenantId`、`tenantCode`、`name`、`status`、`plan`、`remark`、`createdAt`、`updatedAt`。 | `iam.platform.tenant.enable` | 遵循 4.1 中 `platform/*` 与平台租户资源通用约束；目标租户状态必须允许启用。 |
  | POST /api/v1/iam/platform/tenants/{tenantId}/disable | 停用指定租户；请求体固定为空；成功后返回更新后的租户对象，至少包含 `tenantId`、`tenantCode`、`name`、`status`、`plan`、`remark`、`createdAt`、`updatedAt`。 | `iam.platform.tenant.disable` | 遵循 4.1 中 `platform/*` 与平台租户资源通用约束；目标租户状态必须允许停用。 |
| POST /api/v1/iam/platform/tenants/{tenantId}/initialize-admin | 为指定租户执行租户管理员 bootstrap / break-glass 修复；这是平台侧唯一允许写入租户成员关系的正式例外动作，不归类为平台侧通用成员管理接口；正式请求体固定为 `userId` 必填，`displayName` 选填；不接受 `roleCodes`，服务端固定授予 `tenant_admin`；仅当目标租户当前不存在任何 `ACTIVE` 状态且拥有 `tenant_admin` 角色的成员时才允许调用；若该 `userId` 已是目标租户成员，则复用该成员关系、补齐 `tenant_admin` 角色，并确保成功后的成员状态为 `ACTIVE`；否则创建新的成员关系；成功后返回初始化结果，至少包含 `tenantId`、`memberId`、`userId`、`displayName`、`status`、`roleCodes`，且 `status` 必须为 `ACTIVE`，`roleCodes` 中必须包含 `tenant_admin`。 | `iam.platform.tenant.initializeAdmin` | 遵循 4.1 中 `platform/*` 与平台租户资源通用约束；仅在目标租户当前不存在任何 `ACTIVE tenant_admin` 时可调用；不承接日常成员管理。 |

  4.6 明确不提供

  | 路由 | 功能描述 | 权限标识 | 调用限制 |
  | --- | --- | --- | --- |
  | GET /api/v1/iam/me/permissions | 当前会话权限矩阵自省。 | `-` | 不纳入重构后的正式 API；前端不再依赖统一权限矩阵接口，是否有权执行动作以后端业务接口鉴权结果为准。 |
  | 任意面向全局用户目录、未加入租户用户批量检索或平台工作态账号管理的 HTTP 接口，例如 `/api/v1/iam/platform/users/*` | 全局用户或平台工作态账号管理。 | `-` | 不纳入重构后的正式 API；客户侧用户如需告知租户管理员自己的 `userId`，统一通过 `GET /api/v1/iam/me/profile`；平台或内部人员如需批量检索已注册未入租账号、查询平台工作态账号或做排障，统一通过 `admin/后台` 或内部工具处理。 |
  | GET /api/v1/iam/tenant/profile | 旧命名的当前租户资料接口。 | `-` | 不纳入重构后的正式 API；当前租户上下文统一通过 `GET /api/v1/iam/tenant/me` 返回，并同时带出当前登录用户在该租户下的业务角色信息。 |
  | GET /api/v1/iam/tenant/users，GET /api/v1/iam/tenant/users/{userId} | 当前租户用户目录历史接口。 | `-` | 不纳入重构后的正式 API；当前租户用户目录统一通过 `GET /api/v1/iam/tenant/members` 提供；如需按全局 `userId` 定位当前租户成员，统一通过 `GET /api/v1/iam/tenant/members?userId={userId}`，如需完整成员详情，再用返回的 `memberId` 调用 `GET /api/v1/iam/tenant/members/{memberId}`。 |
  | POST /api/v1/iam/tenant/members/{memberId}/roles | 通过 POST 修改成员角色集合。 | `-` | 不纳入重构后的正式 API；按规范 v2 的方法语义，角色集合更新统一通过 `PUT /api/v1/iam/tenant/members/{memberId}/roles` 表达全量覆盖，不再保留 POST 版本。 |
  | POST /api/v1/iam/tenant/members/invitations，GET /api/v1/iam/me/invitations，POST /api/v1/iam/me/invitations/accept，POST /api/v1/iam/me/invitations/reject | 邀请流程相关接口。 | `-` | 不纳入重构后的正式 API；邀请流程整体下线；成员加入统一通过 `POST /api/v1/iam/tenant/members` 由租户管理员将已存在的全局用户加入当前租户。 |
  | POST /api/v1/iam/platform/tenants/{tenantId}/set-plan | 平台侧调整租户套餐。 | `-` | 不纳入重构后的正式 API；套餐能力属于后续特性，`plan` 当前仅作为租户实体响应中的只读预留字段存在，列表与详情中统一返回 `null`；本期不支持筛选、设置或变更。 |
  | GET /api/v1/iam/platform/tenants/{tenantId}/members，POST /api/v1/iam/platform/tenants/{tenantId}/members，GET /api/v1/iam/platform/tenants/{tenantId}/members/{memberId}，PUT /api/v1/iam/platform/tenants/{tenantId}/members/{memberId}/roles，以及除 `POST /api/v1/iam/platform/tenants/{tenantId}/initialize-admin` 外任何通过 path 显式指定租户后执行成员列表、详情、创建、角色分配、启停等动作的接口 | 平台侧按租户 path 直接做通用成员管理。 | `-` | 不纳入重构后的正式 API；平台侧不提供通用成员管理 HTTP 接口；成员管理只保留在租户工作侧 `tenant/members/*`；平台工作态账号不能通过 `tenant/*` 替代执行；`initialize-admin` 仅保留为“当前租户不存在任何 `ACTIVE tenant_admin` 时”的 `bootstrap / break-glass` 修复接口，不承接日常成员管理。 |

  5. API 拆分方案

  5.1 Session 归位与 Me 收口
  当前 apps/access/urls.py 中会话与当前用户接口分散在 login/logout/users/register/me/* 等多个位置。正式 API 在本轮应收口为 `session/*` 正式会话接口和必要的 `me/*` 自身资料接口：

  - session/* 只负责登录、刷新令牌、登出和注册账号。
  - `session/login` 只为正式 IAM 账号建立认证会话并签发 Bearer Token；`admin/后台` 维护的技术兜底账号不进入这套认证域。
  - me/* 只保留正式 IAM 业务账号读取自己全局资料的 `profile` 接口，以及读取自己可进入租户上下文列表的 `me/tenants` 接口。
  - 具体路由迁移以下降范围以第 4 节和第 7 节为准，本节不再重复逐条罗列。

  典型入租流程应明确为：

  - 用户先通过 `POST /session/register` 或 `POST /session/register-by-phone` 完成自助注册。
  - 注册成功后不会自动登录，用户需显式调用 `POST /session/login` 获取 Bearer Token。
  - 用户登录后通过 `GET /me/profile` 获取自己的 `userId`。
  - 用户把 `userId` 提供给目标租户管理员。
  - 租户管理员调用 `POST /tenant/members`，将该 `userId` 加入当前租户。

  多租户切换口径应明确为：

  - 已拥有一个或多个有效租户成员关系的正式 IAM 业务账号，通过 `GET /me/tenants` 获取自己当前可进入的租户上下文列表。
  - 客户端从该列表中读取目标 `tenantCode`，并在后续 `tenant/*` 请求中通过 `X-TENANT-CODE` 指定当前租户上下文。
  - `GET /tenant/me` 只负责返回“当前已经选中的租户上下文中的我”，不替代 `GET /me/tenants`。

  已创建但未加入租户的账号，处理口径固定为：

  - 客户自助场景：用户本人登录后调用 `GET /me/profile` 获取自己的 `userId`，再交给目标租户管理员，由租户管理员调用 `POST /tenant/members` 完成入租。
  - 平台或内部批量处理场景：如需批量检索已注册未入租账号、核对全局账号资料或做排障，统一通过 `admin/后台` 或内部工具处理，不纳入正式 API。

  5.2 Users 语义拆分：正式 API 不再提供全局用户与平台账号管理
  当前 UserListCreateView 与 UserDetailView 在 apps/access/views.py 中有明显混合语义：

  - GET /users、GET /users/{id} 会根据调用者角色决定作用域。
  - POST /users、PUT/PATCH /users/{id} 实际在操作全局 User + StaffProfile。

  重构后的正式 API 必须拆开。

  收口后的正式 API 以第 4 节为准，这里只保留资源边界：

  - 当前登录用户查看自己的全局账号资料，统一走 `GET /me/profile`。
  - 当前租户用户目录与成员管理统一收口到 `tenant/members/*`，不再单独保留 `tenant/users/*`。
  - `tenant/members/*` 只处理当前租户内的成员关系，不承担全局账号创建和修改。
  - 新注册用户如需告知租户管理员自己的 `userId`，统一通过 `GET /me/profile` 获取。
  - 平台工作态账号管理、全局用户检索、未入租用户批量查询不纳入正式 API，统一留在 `admin/后台` 或内部工具。

  5.3 Tenants 拆分为当前租户上下文和平台租户实体
  当前 tenants 相关能力混合了三种需求：

  - 平台层面管理租户实体。
  - 当前用户读取自己可进入的租户上下文列表。
  - 租户侧读取“自己当前租户上下文中的资料与角色”。

  重构后的正式 API 必须拆开。

  拆分后的正式 API 以第 4 节为准，这里只强调边界：

  - `me/tenants` 表示“当前登录用户可进入的租户上下文列表”，只用于帮助业务账号获取 `tenantCode` 并做租户切换。
  - `tenant/me` 表示“当前租户上下文中的我”，同时返回当前租户资料与当前登录用户在该租户下的业务角色信息。
  - `platform/tenants/*` 表示“平台租户实体管理”，负责列表、详情、启停、初始化管理员等平台动作，也承担平台管理员查看可管理租户实体列表的能力。
  - `plan` 当前仅作为租户实体响应中的只读预留字段存在，不构成本期正式筛选条件或管理动作。
  - `tenant/me` 不带 path 租户 ID，也不承担任何平台管理动作；当前租户只能从 `X-TENANT-CODE` 解析。
  - 当前租户实体的对外业务编码统一命名为 `tenantCode`，不再使用裸 `code`。
  - `platform/tenants/{tenantId}` 中的目标租户必须以 path 为准，不能由 `X-TENANT-CODE` 替代。
  - `GET /platform/tenants` 只解决平台侧租户实体管理，不表示租户工作态切换入口，也不授予 `tenant/*` 调用资格。

  5.4 Audit Logs 拆分为平台审计和租户审计
  当前 /audit-logs 和 /tenant-audit-logs 并存，而 /audit-logs 本身又带混合作用域。

  重构后的正式 API 只保留两类固定语义资源：

  - `platform/audit-logs` 只表达平台维度审计日志。
  - `tenant/audit-logs` 只表达当前租户审计日志。
  - 不再保留“同一路径根据调用者不同返回不同日志范围”的正式 API 约束。

  5.5 Tenant Members 保留在租户工作侧，但要做协议清洗
  tenant-members/* 当前的方向是对的，因为它已经通过 platform_admin_forbidden 明确限制在租户工作侧。正式 API 只需要收口三件事：

  - 路径规范化：`tenant-members/*` 统一规范为 `tenant/members/*`；邀请流程相关接口不再迁移，直接下线。
  - 资源语义固定：`tenant/members/{memberId}` 的 `{memberId}` 是租户成员关系 ID；`tenant/members/*` 同时承担当前租户用户目录和成员管理能力；`POST /tenant/members` 只表示“将一个已存在的全局用户加入当前租户”，不表示发邀请，也不表示创建全局用户；成员创建、查询、更新与角色分配的具体契约统一以第 4 节、6.8、6.11、6.13 为准。
  - 删除策略调整：不提供 `DELETE /tenant/members/{memberId}`；成员移除统一通过 `disable` 表达，成员恢复统一通过 `enable` 表达。

  平台侧边界在这一组资源上也一并固定：

  - 成员管理只保留在 `tenant/members/*`。
  - 不提供 `/platform/tenants/{tenantId}/members/*` 这组平台侧通用成员管理接口。
  - `POST /platform/tenants/{tenantId}/initialize-admin` 是唯一例外，但它只允许在目标租户当前不存在任何 `ACTIVE tenant_admin` 时，用于租户管理员 `bootstrap / break-glass` 修复，不等同于平台侧通用成员管理能力。

  如果未来真的要支持“删除”概念，也应基于统一软删除能力重新设计，而不是把当前硬删除直接公开为重构后的正式 API。

  5.6 角色目录与平台目录能力归位
  当前权限目录和角色目录本质上都是系统固定目录，但租户管理员在正式 API 中还需要读取“当前租户可分配角色列表”。

  拆分后的正式 API 以第 4 节为准，这里只强调边界：

  - `tenant/roles` 表示“当前租户可分配角色目录”，只返回允许分配给租户成员的角色。
  - `platform/permissions`、`platform/roles`、`platform/roles/{roleId}` 都属于平台目录接口，不承担租户成员角色分配入口。
  - 成员角色集合更新只保留 `PUT /tenant/members/{memberId}/roles`；不再保留 `POST /tenant/members/{memberId}/roles`。
  - `PUT /tenant/members/{memberId}/roles` 的请求体、幂等语义和返回契约统一以 6.8、6.11、6.13 为准。
  - 重构后的正式 API 不再提供统一的当前会话权限矩阵接口；实际是否有权执行动作，以对应业务接口的后端鉴权结果为准。

  6. 实现规范
  本节把 项目总体概览/api设计规范v2.md 的协议规范与当前工程实现约束合并成落地规则。

  6.1 路由挂载规范
  正式挂载统一改为：

  path("api/v1/iam/", include(...))

  约束：

  - /api/v1/iam/* 是重构后的正式 API。
  - /internal/auth/* 直接下线，不做兼容。
  - 如果还有真正内部 helper，才单独保留在内部前缀下，但不能继续和重构后的正式 API 混挂。

  文档挂载也要同步调整：

  - 正式 IAM 文档应进入 /api/v1/docs/* 体系。
  - config/business_api_urlconf.py 应纳入 api/v1/iam。
  - config/internal_api_urlconf.py 不再承载这套正式接口。

  原因是 apps/api_v1/openapi_hooks.py 和 apps/access/exceptions.py 都是按 /api/v1/ 路径自动应用标准化逻辑的。只要接口正式进入 /api/v1/iam/
  *，它们就能复用现有标准响应和文档包裹能力。

  6.2 认证方式规范
  规范 15.1 明确推荐 Bearer Token 方式，因此重构后的正式 IAM API 直接按该规范落地，不保留 Session/Cookie 或 BasicAuthentication 兼容层。

  需要明确区分两件事：

  - 当前工程现状中，config/settings.py 仍配置了 `SessionAuthentication` 与 `BasicAuthentication`，apps/access/views.py 里的 `LoginView` / `LogoutView` 也仍是 Django Session 流程。
  - 这些都属于重构前现状，不属于重构后的正式 API 契约。

  重构后的正式认证口径固定为：

  - 受保护的 `/api/v1/iam/*` 正式接口统一使用 `Authorization: Bearer <token>`。
  - `POST /api/v1/iam/session/login` 负责创建认证会话并签发 token。
  - `POST /api/v1/iam/session/refresh` 负责刷新认证会话。
  - `POST /api/v1/iam/session/logout` 负责撤销当前认证会话。
  - `/api/v1/iam/*` 不再接受 Session/Cookie 作为正式认证方式。
  - `/api/v1/iam/*` 不再接受 BasicAuthentication。
  - 正式文档、OpenAPI、测试用例、客户端接入说明都只按 Bearer Token 书写。
  - 不通过“双认证方式并存”做过渡兼容。
  - 只有正式 IAM 账号可通过 `POST /api/v1/iam/session/login` 建立认证会话。
  - `admin/后台` 维护的技术兜底账号不通过 `/api/v1/iam/*` 登录，也不签发 Bearer Token。

  本项目的正式认证契约在本轮一并定稿：

  - 采用 Bearer Token + refreshToken 的双 token 方案。
  - `accessToken` 为短时有效凭证，用于访问受保护接口。
  - `refreshToken` 为长时有效凭证，仅用于 `POST /api/v1/iam/session/refresh`。
  - `accessToken` 默认有效期为 2 小时。
  - `refreshToken` 默认有效期为 7 天。
  - `accessToken` 与 `refreshToken` 对客户端都按 opaque token 对待，客户端不得假设其可解析为 JWT，也不得依赖其内部字段。
  - 同一用户允许存在多个并行认证会话；登录、刷新、登出都只作用于当前这一个认证会话。
  - refresh token rotation 为正式 API 固定行为：`POST /api/v1/iam/session/refresh` 成功后，旧 `refreshToken` 立即失效。
  - refresh 成功后，旧 `accessToken` 不要求立即失效，直到自然过期或该认证会话被撤销；客户端应始终替换并持久化最新 token。
  - logout 的正式语义不是“前端自行删除本地 token”，而是“服务端撤销当前认证会话”；同一认证会话下尚未过期的 `accessToken` 与 `refreshToken` 都必须一并失效。
  - 账号状态一旦转为 `DISABLED`，服务端必须撤销该账号现有的全部认证会话。
  - 被撤销、无效或已过期的 `accessToken` / `refreshToken` 继续使用时，统一按“Token 无效或过期”处理，返回 `401 + A0401`。
  - 自助注册创建的是正式 IAM 业务账号，初始运行态固定为 `unassigned`；因此注册成功后可登录并访问 `me/*`，但在建立任何租户成员关系之前不得访问 `tenant/*`。

  为保证 `POST /api/v1/iam/session/logout` 可以表达“立即失效”，本项目正式方案不采用纯无状态 JWT 语义，而采用“Bearer Token 外观 + 服务端可撤销认证会话”模型。

  会话接口的正式请求与响应字段固定如下：

  `POST /api/v1/iam/session/login`

  - 请求体字段：
    - `username`：登录用户名。若用户通过手机号注册，则其登录用户名固定为注册手机号。
    - `password`：登录密码。
  - 成功状态码：`200`
  - 成功响应 `data` 字段：
    - `accessToken`
    - `refreshToken`
    - `tokenType`，固定为 `Bearer`
    - `expiresIn`，单位秒，默认 `7200`
    - `refreshExpiresIn`，单位秒，默认 `604800`
    - `user`，至少包含 `userId`、`username`、`status`、`isPlatformAdmin`
    - 其中 `status` 正式取值固定为 `ACTIVE`、`DISABLED`
    - 其中 `isPlatformAdmin` 仅表示当前登录账号是否具备调用 `platform/*` 的能力；它是当前会话自省字段，只允许出现在 `session/login` 中
    - 如返回全局人员档案，遵循 4.1 中 `staffProfile` 的统一定义，至少包含 `name`、`phone`
  - 失败语义：
    - 用户名或密码错误，账号不属于正式 IAM 账号集合，或账号状态为 `DISABLED`：`401 + A0401`
    - 参数缺失或格式非法：`400 + B0001`

  `POST /api/v1/iam/session/refresh`

  - 请求体字段：
    - `refreshToken`：当前认证会话最新一次签发且尚未失效的 refreshToken。
  - 成功状态码：`200`
  - 成功响应 `data` 字段：
    - `accessToken`
    - `refreshToken`
    - `tokenType`
    - `expiresIn`
    - `refreshExpiresIn`
  - 失败语义：
    - `refreshToken` 缺失或格式非法：`400 + B0001`
    - `refreshToken` 无效、过期、已被轮换淘汰、已被登出撤销，或其所属账号状态已变为 `DISABLED`：`401 + A0401`

  `POST /api/v1/iam/session/logout`

  - 认证方式：请求头必须带 `Authorization: Bearer <accessToken>`。
  - 请求体：空对象或无请求体。
  - 成功状态码：`200`
  - 成功响应 `data`：`null`
  - 失败语义：
    - 未携带 `accessToken`：`401 + A0401`
    - `accessToken` 无效、过期或已被撤销：`401 + A0401`

  `POST /api/v1/iam/session/register`

  - 请求体字段：
    - `username`
    - `password`
    - `name`
    - `phone`
  - 成功状态码：`201`
  - 成功响应 `data` 字段至少包含：
    - `userId`
    - `username`
    - `status`，固定为 `ACTIVE`
    - `staffProfile`，至少包含 `name`、`phone`
  - 约束：
    - 注册成功后不自动创建登录态，不返回 `accessToken`、`refreshToken`
    - 用户后续需显式调用 `POST /api/v1/iam/session/login` 获取 Bearer Token
  - 失败语义：
    - 用户名已存在：`409 + C0101`
    - 手机号已存在：`409 + C0102`
    - 参数缺失或格式非法：`400 + B0001`

  `POST /api/v1/iam/session/register-by-phone`

  - 请求体字段：
    - `phone`
    - `smsCode`
    - `password`
  - 成功状态码：`201`
  - 成功响应 `data` 字段至少包含：
    - `userId`
    - `username`，值等于注册手机号
    - `status`，固定为 `ACTIVE`
    - `staffProfile`，至少包含 `phone`
  - 约束：
    - 服务端以手机号作为登录用户名
    - 注册成功后账号状态固定写入 `ACTIVE`
    - 注册成功后不自动创建登录态，不返回 `accessToken`、`refreshToken`
    - 用户后续通过 `username=phone` 与注册时设置的 `password` 调用 `POST /api/v1/iam/session/login`
  - 失败语义：
    - 手机号已存在：`409 + C0102`
    - 短信验证码错误或失效：`400 + B0001`
    - 参数缺失或格式非法：`400 + B0001`

  这意味着本轮不仅收敛正式 API 路径约束，也同步定稿正式 API 认证契约。

  6.3 多租户上下文规范
  按照规范 6.6 和 17.6，正式实现必须统一租户上下文解析方式。

  租户工作侧：

  - 统一从 X-TENANT-CODE 解析 effectiveTenant
  - 统一校验 effectiveTenant 必须为 `ACTIVE`
  - 统一校验当前登录用户在该租户下必须存在 `ACTIVE` 状态的成员关系，且具有对应权限
  - 统一禁止在 path、query、body 中再次传入租户标识表达同一上下文

  平台管理侧：

  - 涉及租户实体的 platform/tenants/* 路由，统一从 path `{tenantId}` 解析 targetTenant
  - 统一校验当前登录用户是否具有平台管理权限
  - 不允许用 X-TENANT-CODE 替代或覆盖目标租户

  账号身份边界：

  - 正式 IAM 账号的鉴权运行态固定为 `unassigned`、`tenant_member`、`platform_operator`。
  - `tenant_member` 与 `platform_operator` 互斥；`unassigned` 是正式 IAM 业务账号未入租时的初始运行态。
  - `tenant_member` 仅表示该账号已至少拥有一条租户成员关系，不直接代表对任意 `tenant/*` 都有权限；`tenant/*` 的最终访问权始终以 `X-TENANT-CODE` 解析出的 `effectiveTenant` 必须为 `ACTIVE`、该账号在该租户下的成员关系必须为 `ACTIVE`，以及角色权限校验结果为准。
  - “可入租账号”定义为状态为 `ACTIVE` 且运行态不为 `platform_operator` 的正式 IAM 业务账号；因此 `unassigned` 与 `tenant_member` 都允许继续建立新的租户成员关系。
  - `POST /tenant/members` 与 `POST /platform/tenants/{tenantId}/initialize-admin` 的目标 `userId` 都必须命中“可入租账号”；若未命中，统一返回 `404 + C0404`。
  - 正式 API 不提供 `unassigned -> platform_operator`、`tenant_member -> platform_operator`、`platform_operator -> tenant_member` 这类身份切换写接口。

  错误口径：

  - `tenant/*` 缺少 `X-TENANT-CODE` 或 header 格式非法，统一返回 `400 + B0001`。
  - `tenant/*` 中 `X-TENANT-CODE` 命中的租户不存在、已停用，或当前用户在该租户下不存在 `ACTIVE` 状态的成员关系时，统一返回 `403 + A0403`。
  - 已认证但无当前作用域权限的请求，统一返回 `403 + A0403`。
  - 在已生效作用域内查询资源但资源不存在，统一返回 `404 + C0404`。

  落地要求：

  - get_queryset
  - serializer 校验
  - service 层逻辑
  - 批量任务
  - 审计日志

  都必须使用统一解析后的 effectiveTenant 或 targetTenant，不能再各处手写一套不同的租户判断。

  当前 apps/access/middleware.py 已能把 X-TENANT-CODE 解析到 request.tenant_context。新的正式 API 实现应以此为基础继续下沉统一逻辑，而不是在各个视图里
  继续散落判断。

  6.4 权限校验规范
  权限控制以后端校验为准，不依赖前端展示层。重构后的正式 API 要求路径和权限边界一一对应：

  - session/*：`session/login`、`session/refresh`、`session/register`、`session/register-by-phone` 使用 AllowAny；`session/logout` 使用已登录校验。
  - me/*：当前保留 `GET /me/profile`、`GET /me/tenants`；要求当前用户已登录；不依赖 `X-TENANT-CODE`；仅 `unassigned`、`tenant_member` 两种正式 IAM 业务账号运行态可调用；`platform_operator` 不可调用。
  - tenant/*：要求当前用户运行态为 `tenant_member`，且 `X-TENANT-CODE` 命中的 effectiveTenant 为 `ACTIVE`、当前用户在该租户下存在 `ACTIVE` 状态的成员关系，并具备租户侧权限；`tenant_member` 本身不直接等于任意租户下都有访问权。
  - platform/*：要求当前用户运行态为 `platform_operator`，且具备平台侧权限。

  关键约束：

  - 不允许同一路径根据调用者身份不同切换成不同语义。
  - `me/permissions` 下线、`platform_operator` 不作为租户侧接口调用主体、`tenant_member` 与 `platform_operator` 互斥等边界，统一继承 4.1 与 6.3，不在本节重复展开。
  - `isPlatformAdmin` 只用于 `session/login` 的当前会话能力提示，不作为任何其他正式资源的筛选字段、排序字段或写入字段。
  - `GET /tenant/me` 允许任意满足 `tenant/*` 通用约束的 `tenant_member` 调用，用于读取自己在当前租户下的上下文与业务角色。
  - `GET /tenant/members`、`GET /tenant/members/{memberId}`、`GET /tenant/roles`、`POST /tenant/members`、`PATCH /tenant/members/{memberId}`、`PUT /tenant/members/{memberId}/roles`、`POST /tenant/members/{memberId}/enable`、`POST /tenant/members/{memberId}/disable` 本期统一仅 `tenant_admin` 可调用。
  - `tenant/*` 缺少 `X-TENANT-CODE` 或 header 格式非法时，统一返回 `400 + B0001`；`X-TENANT-CODE` 命中的租户不存在、已停用，或当前用户在该租户下不存在 `ACTIVE` 状态的成员关系时，统一返回 `403 + A0403`；在当前作用域内查不到目标资源时，统一返回 `404 + C0404`。
  - 如果未来真的需要平台人员“以某个租户身份工作”，应设计显式会话切换机制，而不是让 platform/* 和 tenant/* 混成一套。

  6.5 统一响应封装规范
  规范 8、9、14、17.1 要求正式 API 统一使用：

  {
    "code": "00000",
    "msg": "success",
    "data": {}
  }

  当前 /api/v1 已有现成实现：

  - apps/api_v1/business_response.py
  - BusinessApiResponseMixin
  - build_standard_response
  - StandardCode

  正式 IAM 接口应直接复用，不再自己维护一套 business_code/business_detail_code。

  推荐映射如下：

  | HTTP 状态码 | 标准业务码 | 说明 |
  | --- | --- | --- |
  | 200/201 | 00000 | 成功 |
  | 400 | B0001 | 参数校验失败 |
  | 401 | A0401 | 未登录或登录失效 |
  | 403 | A0403 | 无操作权限 |
  | 404 | C0404 | 资源不存在 |
  | 409 | C0101 / C0102 / C0103 / C0203 | 唯一性冲突、重复入租或状态冲突；例如 C0101 唯一业务标识已存在、C0102 手机号已存在、C0103 用户已在当前租户中、C0203 当前状态不允许执行该状态变更 |
  | 500 | E0001 | 系统异常 |

  其中本轮 IAM 文档对状态冲突码的落点固定为：

  - `C0101` 的正式语义固定为“唯一业务标识已存在”；本期至少覆盖 `POST /session/register` 的用户名冲突，以及 `POST /platform/tenants` 的 `tenantCode` 冲突。
  - `C0102` 作为本轮沿用业务码，用于手机号已存在。
  - `C0103` 作为本轮新增业务码，用于 `POST /tenant/members` 命中“用户已在当前租户中”这一重复入租冲突。
  - `C0201` 作为规范 v2 的保留码，仅保留语义说明，不纳入本轮正式 API 的错误码映射、OpenAPI 示例与验收断言。
  - `C0203` 作为本轮新增业务码，用于“当前状态不允许执行该状态变更”。
  - `POST /tenant/members/{memberId}/enable` 与 `POST /tenant/members/{memberId}/disable` 的重复或非法状态流转冲突，统一返回 `409 + C0203`。
  - `PUT /tenant/members/{memberId}/roles` 如会移除当前租户最后一个 `ACTIVE tenant_admin`，统一返回 `409 + C0203`。
  - `POST /tenant/members/{memberId}/disable` 如会停用当前租户最后一个 `ACTIVE tenant_admin`，统一返回 `409 + C0203`。
  - `POST /platform/tenants/{tenantId}/enable` 与 `POST /platform/tenants/{tenantId}/disable` 的重复或非法状态流转冲突，也统一返回 `409 + C0203`。
  - `POST /platform/tenants/{tenantId}/initialize-admin` 在目标租户当前已存在 `ACTIVE tenant_admin`、因此不允许再次执行 bootstrap / break-glass 修复时，统一返回 `409 + C0203`。
  - `tenant/*` 缺少 `X-TENANT-CODE` 或 header 格式非法，统一返回 `400 + B0001`。
  - `tenant/*` 中 `X-TENANT-CODE` 命中的租户不存在、已停用，或当前用户在该租户下不存在 `ACTIVE` 状态的成员关系时，统一返回 `403 + A0403`。
  - 已认证但无当前作用域权限的请求，统一返回 `403 + A0403`。
  - 在已生效作用域内查询目标资源但资源不存在，统一返回 `404 + C0404`。

  当前 apps/access/exceptions.py 已对 /api/v1/ 路径做标准响应转换，这意味着一旦路由进入 /api/v1/iam/*，异常响应会自动进入标准包裹。但前提
  是：

  - 所有正式 IAM 视图都必须统一使用标准 mixin 或同等封装。
  - 不允许部分接口继续返回裸 serializer。
  - 不允许列表接口继续拼自定义 count 结构。
  - 不允许继续输出 business_code/business_detail_code。

  6.6 分页规范
  正式列表接口统一对齐 apps/api_v1/pagination.py：

  请求参数：

  - pageNum
  - pageSize

  响应体内层：

  {
    "list": [],
    "total": 0
  }

  再由统一响应包裹成：

  {
    "code": "00000",
    "msg": "success",
    "data": {
      "list": [],
      "total": 0
    }
  }

  正式 IAM 列表接口不再允许：

  - count
  - results
  - next
  - previous

  除 4.1 已定义的固定目录接口外，不允许一部分列表接口分页、另一部分列表接口直接返回整数组。

  固定目录型接口必须声明为不分页。目前本轮固定为非分页的有：

  - `GET /api/v1/iam/tenant/roles`
  - `GET /api/v1/iam/platform/permissions`
  - `GET /api/v1/iam/platform/roles`

  上述三个固定目录接口的响应 `data` 直接返回数组，不使用 `data.list`、`data.total`。

  除上述固定目录接口外，其余列表接口统一分页；不能再随意出现一部分分页、一部分不分页的情况。
  分页接口的响应 `data` 只返回 `list` 与 `total`，不回显 `pageNum`、`pageSize`。

  其中 `GET /api/v1/iam/me/tenants` 作为当前用户租户上下文列表接口，必须进一步定死：

  - `GET /api/v1/iam/me/tenants` 为分页列表接口，默认 `pageNum=1`、`pageSize=20`，最大 `pageSize=100`。
  - `GET /api/v1/iam/me/tenants` 只返回“租户状态为 `ACTIVE` 且当前用户在该租户下成员状态也为 `ACTIVE`”的租户成员关系。
  - `GET /api/v1/iam/me/tenants` 除 `pageNum`、`pageSize` 外不支持其他正式筛选或排序参数。
  - `GET /api/v1/iam/me/tenants` 默认按 `tenantId asc` 排序，并以 `memberId asc` 作为稳定次排序。
  - `GET /api/v1/iam/me/tenants` 列表项至少返回 `tenantId`、`tenantCode`、`name`、`memberId`、`roleCodes`。

  其中平台侧两个正式分页列表接口必须进一步定死：

  - `GET /api/v1/iam/platform/tenants` 为分页列表接口，默认 `pageNum=1`、`pageSize=20`，最大 `pageSize=100`。
  - `GET /api/v1/iam/platform/tenants` 支持 `keywords`、`status`、`sortBy`、`sortOrder`；其中 `status` 正式取值固定为 `ACTIVE`、`DISABLED`；`keywords` 至少匹配 `tenantCode`、`name`。
  - `GET /api/v1/iam/platform/tenants` 的 `sortBy` 白名单固定为 `tenantId`、`tenantCode`、`name`、`createdAt`、`updatedAt`；默认排序固定为 `tenantId desc`。
  - `GET /api/v1/iam/platform/tenants` 列表项至少返回 `tenantId`、`tenantCode`、`name`、`status`、`plan`、`createdAt`、`updatedAt`；其中 `plan` 为只读预留字段，当前统一返回 `null`，不支持作为正式筛选条件。
  - `GET /api/v1/iam/platform/audit-logs` 为分页列表接口，默认 `pageNum=1`、`pageSize=20`，最大 `pageSize=100`。
  - `GET /api/v1/iam/platform/audit-logs` 支持 `tenantId`、`action`、`operatorUserId`、`startAt`、`endAt`、`sortBy`、`sortOrder`；`sortBy` 仅允许 `createdAt`，默认排序固定为 `createdAt desc`。
  - `GET /api/v1/iam/platform/audit-logs` 列表项至少返回 `auditLogId`、`tenantId`、`tenantCode`、`action`、`targetType`、`targetId`、`operatorUserId`、`operatorDisplayName`、`createdAt`、`summary`。

  `GET /api/v1/iam/tenant/members` 作为分页列表接口，还应满足：

  - 默认返回当前租户下全部正式成员，即 `ACTIVE + DISABLED`。
  - `status` 为可选筛选参数；如需多值筛选，使用重复 query 参数表达，例如 `?status=ACTIVE&status=DISABLED`。
  - `keywords` 为可选筛选参数，至少匹配 `username`、`displayName`。
  - `userId` 为可选单值精确筛选参数，用于按当前租户内全局用户 ID 定位成员关系；即使传入 `userId`，返回结构仍保持分页列表。
  - `sortBy` 仅允许 `displayName`、`username`、`status`、`createdAt`、`updatedAt`；`sortOrder` 仅允许 `asc`、`desc`；默认排序固定为 `displayName asc`。
  - 为保证分页稳定性，`GET /api/v1/iam/tenant/members` 在主排序字段相同的情况下，必须再按 `memberId asc` 做稳定次排序。
  - 客户侧用户目录如只看可用成员，应显式传 `status=ACTIVE`，不能依赖后端按页面语义切换默认结果。
  - `GET /api/v1/iam/tenant/audit-logs` 也必须按分页列表接口实现，默认 `pageNum=1`、`pageSize=20`，最大 `pageSize=100`。
  - `GET /api/v1/iam/tenant/audit-logs` 支持 `action`、`operatorUserId`、`startAt`、`endAt`、`sortBy`、`sortOrder`；`sortBy` 仅允许 `createdAt`，默认排序固定为 `createdAt desc`。
  - `GET /api/v1/iam/tenant/audit-logs` 列表项至少返回 `auditLogId`、`action`、`targetType`、`targetId`、`operatorUserId`、`operatorDisplayName`、`createdAt`、`summary`。

  6.7 字段命名规范
  本次重构后的正式 API 直接按规范 5.3 定稿：对外请求体、响应体、query 参数、具名 path 参数统一采用 camelCase。

  具体约束如下：

  - JSON 字段统一使用 camelCase，例如 tenantId、tenantCode、userId、roleCodes、createdAt、updatedAt。
  - query 参数统一使用 camelCase，例如 pageNum、pageSize、keywords、status、sortBy、sortOrder。
  - 需要显式命名的 path 参数统一使用 camelCase，例如 tenantId、memberId、userId。
  - Header 不适用本条规则，继续沿用 HTTP 头命名方式，例如 X-TENANT-CODE。
  - Python 内部实现、Django Model、数据库字段允许继续使用 snake_case，但必须通过 Serializer、DTO 或转换层完成映射，不能直接暴露到重构后的正式 API。
  - 旧 /internal/auth/* 中出现的 tenant_id、user_id 等命名，只能出现在现状说明、迁移说明、实现说明中，不能继续出现在重构后的正式 API 字段定义中。
  - 由于旧 /internal/auth/* 不保留兼容期，重构后的正式 API 不接受 snake_case 别名，不做 tenantId/tenant_id 这类双写兼容。

  这意味着字段命名在本轮已经拍板，不再保留“camelCase 或 snake_case 二选一”的描述空间。

  6.8 请求参数规范
  重构后的正式 API 中，租户工作侧接口禁止在 query/body/path 中重复表达租户上下文。

  明确要求：

  - GET /tenant/me 不接受 path 中显式传租户标识，也不接受 query/body 中重复传 `tenantId`、`tenantCode`、`code`；当前租户只能由 `X-TENANT-CODE` 解析。
  - tenant/* 缺少 `X-TENANT-CODE` 或 header 格式非法时，统一返回 `400 + B0001`。
  - tenant/* 中，`X-TENANT-CODE` 命中的租户必须为 `ACTIVE`，否则统一返回 `403 + A0403`。
  - tenant/* 中，当前用户在 `X-TENANT-CODE` 命中的租户下必须存在 `ACTIVE` 状态的成员关系，否则统一返回 `403 + A0403`。
  - tenant/* 命中当前用户无权访问的租户上下文，统一返回 `403 + A0403`。
  - GET /me/tenants 只接受 `pageNum`、`pageSize`。
  - GET /me/tenants 不接受 `tenantId`、`tenantCode`、`status`、`keywords`、`sortBy`、`sortOrder`。
  - GET /me/tenants 默认按 `tenantId asc` 排序；主排序字段相同的情况下，服务端必须再按 `memberId asc` 做稳定次排序。
  - GET /tenant/members/{memberId} 不再接收额外 query/body 字段表达成员身份；成员绑定账号的全局信息在正式响应中只收口为 `userId`、`username`。
  - POST /tenant/members 不再接收 tenantId；成员关系中的 `tenantId` 由服务端根据 `X-TENANT-CODE` 解析当前租户后确定并写入。
  - POST /tenant/members 不接受 tenant_id 旧别名。
  - POST /tenant/members 请求体固定字段为 `userId`、`displayName`、`roleCodes`，其中仅 `userId` 必填。
  - POST /tenant/members 必须先在“可入租账号”集合内按 `userId` 查询目标账号；若未命中，统一返回 `404 + C0404`。
  - POST /tenant/members 如命中“该 `userId` 已在当前租户中”场景，统一返回 `409 + C0103`。
  - POST /tenant/members 的 `userId` 必须指向已存在且可入租的正式 IAM 业务账号。
  - POST /tenant/members 的 `roleCodes` 可省略或传空数组；如传入，只能使用 `GET /tenant/roles` 返回的可分配租户角色。
  - POST /tenant/members 不接受 `qualifications`。
  - PUT /tenant/members/{memberId}/roles 请求体固定字段为 `roleCodes`，且必须为数组。
  - PUT /tenant/members/{memberId}/roles 不接受 `role_codes`、`roles` 等旧字段名。
  - PUT /tenant/members/{memberId}/roles 的 `roleCodes` 允许为空数组；空数组表示清空该成员当前全部租户角色。
  - PUT /tenant/members/{memberId}/roles 的语义固定为全量覆盖，不是增量追加；未包含在 `roleCodes` 中的旧角色一律撤销。
  - PUT /tenant/members/{memberId}/roles 的 `roleCodes` 按集合语义去重后生效。
  - PUT /tenant/members/{memberId}/roles 的 `roleCodes` 如传入，只能使用 `GET /tenant/roles` 返回的可分配租户角色，且不得包含平台角色。
  - PUT /tenant/members/{memberId}/roles 如本次全量覆盖会使当前租户不存在任何 `ACTIVE tenant_admin`，统一返回 `409 + C0203`。
  - POST /tenant/members/{memberId}/enable 与 POST /tenant/members/{memberId}/disable 的请求体固定为空对象或无请求体。
  - POST /tenant/members/{memberId}/enable 与 POST /tenant/members/{memberId}/disable 不接受任何业务字段。
  - POST /tenant/members/{memberId}/disable 如目标成员是当前租户最后一个 `ACTIVE tenant_admin`，统一返回 `409 + C0203`。
  - GET /platform/tenants 支持 `pageNum`、`pageSize`、`keywords`、`status`、`sortBy`、`sortOrder`。
  - GET /platform/tenants 的 `pageNum` 默认 `1`，`pageSize` 默认 `20`，最大 `100`。
  - GET /platform/tenants 的 `status` 正式取值仅为 `ACTIVE`、`DISABLED`。
  - GET /platform/tenants 的 `keywords` 至少匹配 `tenantCode`、`name`。
  - GET /platform/tenants 的 `sortBy` 白名单固定为 `tenantId`、`tenantCode`、`name`、`createdAt`、`updatedAt`；`sortOrder` 仅允许 `asc`、`desc`；默认排序固定为 `tenantId desc`。
  - GET /platform/tenants 不接受 `plan` 作为正式查询参数；`plan` 当前仅为响应中的只读预留字段，统一返回 `null`。
  - GET /platform/tenants/{tenantId} 的 path 参数固定为 `tenantId`，表示租户实体 ID。
  - POST /platform/tenants 的请求体固定字段为 `tenantCode`、`name` 必填，`remark` 选填。
  - POST /platform/tenants 的 `tenantCode`、`name` 不得为空字符串，且 `tenantCode` 必须全局唯一。
  - POST /platform/tenants 的 `tenantCode` 如已存在，统一返回 `409 + C0101`。
  - POST /platform/tenants 不接受 `tenantId`、`code`、`status`、`plan`、`createdAt`、`updatedAt`。
  - POST /platform/tenants 创建成功后，服务端固定写入 `status=ACTIVE`，并返回 `plan=null`。
  - POST /platform/tenants/{tenantId}/enable 与 POST /platform/tenants/{tenantId}/disable 的请求体固定为空对象或无请求体。
  - POST /platform/tenants/{tenantId}/enable 与 POST /platform/tenants/{tenantId}/disable 不接受任何业务字段。
  - POST /platform/tenants/{tenantId}/initialize-admin 的请求体固定字段为 `userId` 必填，`displayName` 选填。
  - POST /platform/tenants/{tenantId}/initialize-admin 不接受 `tenantId`、`status`、`roleCodes`、`roles`、`qualifications`。
  - POST /platform/tenants/{tenantId}/initialize-admin 的目标租户必须为 `ACTIVE`，且目标租户当前不存在任何 `ACTIVE` 状态且拥有 `tenant_admin` 角色的成员。
  - POST /platform/tenants/{tenantId}/initialize-admin 的 `userId` 必须指向已存在且可入租的正式 IAM 业务账号；若未命中“可入租账号”集合，统一返回 `404 + C0404`。
  - POST /platform/tenants/{tenantId}/initialize-admin 固定授予 `tenant_admin`；如目标用户已是该租户成员，则复用该成员关系、补齐角色并将成员状态恢复或保持为 `ACTIVE`，否则创建新的成员关系。
  - POST /platform/tenants/{tenantId}/initialize-admin 只作为“当前租户没有任何 `ACTIVE tenant_admin` 时”的 bootstrap / break-glass 修复接口；若当前已存在 `ACTIVE tenant_admin`，统一返回 `409 + C0203`。
  - 重构后的正式 API 不提供 `PUT /tenant/members/{memberId}`；成员属性更新统一使用 `PATCH /tenant/members/{memberId}`。
  - PATCH /tenant/members/{memberId} 的请求体只允许 `displayName`。
  - PATCH /tenant/members/{memberId} 不接受 `tenantId`、`tenant_id`、`userId`、`user_id`、`status`、`roleCodes`、`role_codes`、`roles`、`username`、`tenantCode`、`createdAt`、`updatedAt`、`joinedAt`、`qualifications`。
  - PATCH /tenant/members/{memberId} 中，`displayName` 允许空字符串，显式传 `""` 表示清空显示名。
  - GET /tenant/members 不再接收 tenantId 作为当前租户筛选参数，也不接受 tenant_id 旧参数。
  - GET /tenant/members 支持 `status` 查询参数，正式取值仅为 `ACTIVE`、`DISABLED`。
  - GET /tenant/members 如需多值状态筛选，使用重复 query 参数表达，例如 `?status=ACTIVE&status=DISABLED`。
  - GET /tenant/members 支持 `keywords` 查询参数，至少匹配 `username`、`displayName`。
  - GET /tenant/members 支持 `userId` 查询参数，表示按当前租户内全局用户 ID 做单值精确筛选，不支持多值。
  - GET /tenant/members 支持 `sortBy`、`sortOrder` 查询参数；`sortBy` 白名单固定为 `displayName`、`username`、`status`、`createdAt`、`updatedAt`；`sortOrder` 仅允许 `asc`、`desc`。
  - GET /tenant/members 在未传 `status` 时，默认返回 `ACTIVE + DISABLED` 两类正式成员。
  - GET /tenant/members 默认排序固定为 `displayName asc`；主排序字段相同的情况下，服务端必须再按 `memberId asc` 做稳定次排序。
  - PATCH /tenant/members/{memberId} 对未出现在请求体中的可写字段不做隐式清空，保持原值不变。
  - 成员资质能力本期不纳入重构后的正式 API；OpenAPI、Serializer 与验收用例中都不再出现 `qualifications`。

  如果出现 path 中的租户 ID 与 `X-TENANT-CODE` 同时表达同一租户上下文的错误请求，统一返回 `400 + B0001`，而不是“悄悄取其中一个”。

  6.9 删除与状态流转规范
  规范 9.2 明确指出普通 JSON 接口原则上不使用 204 No Content。规范 17.4 又建议统一软删除能力。

  结合当前 access 实现，本轮规则如下：

  - 成员资源在重构后的正式 API 中只暴露 `ACTIVE`、`DISABLED` 两种正式状态。
  - 成员不在重构后的正式 API 中开放硬删除。
  - 成员移除统一用状态动作。
  - 邀请流程不再进入重构后的正式 API，`disable` 只承担成员停用语义。
  - `POST /tenant/members/{memberId}/enable` 与 `POST /tenant/members/{memberId}/disable` 遇到重复或非法状态流转时，统一返回 `409 + C0203`，不再复用删除语义的 `C0201`。
  - 如后续真的需要“删除成员”概念，应先引入统一软删除基类，再决定是否提供 DELETE。

  这既符合规范，也避免把当前内部硬删除直接变成长期对外 API 约束。

  6.10 分层实现规范
  规范 17.5 要求 View / Serializer / Service / Model 分层。当前 apps/access/views.py 已经比较大，正式改造不建议继续在一个 views.py 里堆所有
  逻辑。

  建议的模块组织：

  - apps/access/api_v1/urls.py
  - apps/access/api_v1/views/session.py
  - apps/access/api_v1/views/me.py
  - apps/access/api_v1/views/tenant.py
  - apps/access/api_v1/views/platform.py
  - apps/access/api_v1/serializers/session.py
  - apps/access/api_v1/serializers/me.py
  - apps/access/api_v1/serializers/tenant.py
  - apps/access/api_v1/serializers/platform.py
  - apps/access/api_v1/base.py
  - apps/access/services/tenant_member_service.py
  - apps/access/services/directory_service.py
  - apps/access/services/tenant_service.py
  - apps/access/services/audit_service.py

  职责要求：

  - View 层只做接收请求、调用 service、返回统一响应。
  - Serializer 层只做参数校验和序列化。
  - Service 层集中实现成员状态流转、角色授予、租户管理等业务逻辑。
  - Model 层只负责持久化与约束。
  - 多租户上下文解析与权限判断应尽量下沉到基类或公共方法，不要在每个视图重复手写。

  6.11 OpenAPI 与文档规范
  规范 18 要求每个接口具备完整文档要素。正式 IAM 改造后，文档层统一按以下口径整理：

  约束口径：

  - 路由作用域、调用主体与禁止暴露的路径边界优先继承 4.1 通用约束。
  - 分页写法优先继承 6.6 分页规范。
  - 请求字段、查询参数、写接口边界优先继承 6.8 请求参数规范。
  - 本节只规定 OpenAPI 必须如何表达，不再重复定义第二套业务语义。

  通用文档要求：

  - 正式 IAM 文档统一进入 `/api/v1/docs/*`。
  - 每个正式接口都必须标注权限标识、请求示例、成功响应示例、错误码示例。
  - `session/login`、`session/refresh`、`session/register`、`session/register-by-phone` 的 OpenAPI `security` 必须显式声明为空；`session/logout`、`me/*`、`tenant/*`、`platform/*` 必须显式声明 Bearer Token。
  - `me/*` 不声明 `X-TENANT-CODE`；`tenant/*` 必须显式标注 `X-TENANT-CODE`；`platform/*` 中带 path 参数的接口必须显式标注对应 path 参数；不带 path 参数的平台接口不得额外声明不存在的目标资源 path 参数，也不得要求调用方通过 header、query 或 body 传租户 ID 代替 path。
  - 4.1 已定义的固定目录接口必须显式标注为非分页接口，响应 `data` 直接为数组；除这些固定目录接口外，其余列表接口都必须按 6.6 标注分页请求参数与 `data.list`、`data.total` 响应结构；`pageNum`、`pageSize` 不回显到响应体。
  - OpenAPI 中不得出现不属于重构后正式 API 的路径，包括全局用户目录、未入租用户批量检索、平台工作态账号管理、邀请流程、`GET /api/v1/iam/me/permissions`、`POST /api/v1/iam/platform/tenants/{tenantId}/set-plan`、`/platform/tenants/{tenantId}/members/*`、`POST /api/v1/iam/tenant/members/{memberId}/roles`、`PUT /api/v1/iam/tenant/members/{memberId}`。

  Session 与 me 文档：

  - `session/login`、`session/refresh`、`session/logout` 必须分别给出请求示例与响应示例；其中登录与刷新响应必须明确 `accessToken`、`refreshToken`、`tokenType`、`expiresIn`、`refreshExpiresIn`。
  - `session/login` 的响应 schema 必须声明 `user` 至少包含 `userId`、`username`、`status`、`isPlatformAdmin`；其中 `status` 正式取值固定为 `ACTIVE`、`DISABLED`；`isPlatformAdmin` 只作为当前会话自省字段出现；如返回全局人员档案，遵循 4.1 中 `staffProfile` 的统一定义；并给出“账号不属于正式 IAM 账号集合或账号状态为 `DISABLED`”时 `401 + A0401` 的错误示例。
  - `session/register` 与 `session/register-by-phone` 必须分别给出请求示例与响应示例，并明确成功后不自动登录、不返回 token；成功响应至少包含 `userId`、`username`、`status`、`staffProfile`，其中 `status` 固定为 `ACTIVE`；并在文档说明中明确注册后初始运行态为 `unassigned`。
  - `GET /api/v1/iam/me/profile` 的响应 schema 至少包含 `userId`、`username`、`status`、`createdAt`、`updatedAt`；其中 `status` 正式取值固定为 `ACTIVE`、`DISABLED`；如返回全局人员档案，遵循 4.1 中 `staffProfile` 的统一定义；并明确该接口只允许 `unassigned`、`tenant_member` 调用，`platform_operator` 调用时必须返回 `403 + A0403`。
  - `GET /api/v1/iam/me/tenants` 必须按 6.6、6.8 显式标注分页请求参数、默认排序和过滤边界；响应列表项最少字段至少包含 `tenantId`、`tenantCode`、`name`、`memberId`、`roleCodes`；并明确该接口只返回“租户状态为 `ACTIVE` 且当前用户在该租户下成员状态也为 `ACTIVE`”的租户成员关系；`unassigned` 调用时返回空列表，`platform_operator` 调用时必须返回 `403 + A0403`。

  平台接口文档：

  - `GET /api/v1/iam/platform/permissions` 必须明确标注为固定目录型非分页接口；列表项最少字段至少包含 `permissionId`、`code`、`name`、`module`、`resourceCode`、`status`；其中 `status` 正式取值固定为 `ACTIVE`、`DISABLED`。
  - `GET /api/v1/iam/platform/roles` 必须明确标注为固定目录型非分页接口；列表项最少字段至少包含 `roleId`、`code`、`name`、`description`、`status`、`createdAt`、`updatedAt`、`permissionGrants`；其中 `status` 正式取值固定为 `ACTIVE`、`DISABLED`；`permissionGrants` 每项至少包含 `permission`、`scopeType`，且 `permission` 固定表示权限 `code`，`scopeType` 正式取值固定为 `ALL`、`OWN`、`ASSIGNED`。
  - `GET /api/v1/iam/platform/roles/{roleId}` 的响应 schema 至少包含 `roleId`、`code`、`name`、`description`、`status`、`createdAt`、`updatedAt`、`permissionGrants`；其中 `status` 正式取值固定为 `ACTIVE`、`DISABLED`；`permissionGrants` 每项至少包含 `permission`、`scopeType`，且 `permission` 固定表示权限 `code`，`scopeType` 正式取值固定为 `ALL`、`OWN`、`ASSIGNED`。
  - `GET /api/v1/iam/platform/tenants` 的查询参数、排序白名单、默认分页值、最大 `pageSize` 与 `plan` 只读预留语义，必须按 6.6、6.8 显式写出；列表项最少字段至少包含 `tenantId`、`tenantCode`、`name`、`status`、`plan`、`createdAt`、`updatedAt`，其中 `plan` 示例值固定为 `null`。
  - `POST /api/v1/iam/platform/tenants` 的请求 schema、禁止字段与 `tenantCode` 冲突时返回 `409 + C0101` 的错误示例，必须按 6.8 显式写出；成功响应至少包含 `tenantId`、`tenantCode`、`name`、`status`、`plan`、`remark`、`createdAt`、`updatedAt`，并明确 `status=ACTIVE`、`plan=null`。
  - `GET /api/v1/iam/platform/tenants/{tenantId}` 的响应 schema 至少包含 `tenantId`、`tenantCode`、`name`、`status`、`plan`、`remark`、`createdAt`、`updatedAt`，并明确 `plan` 为只读预留字段。
  - `POST /api/v1/iam/platform/tenants/{tenantId}/enable` 与 `POST /api/v1/iam/platform/tenants/{tenantId}/disable` 必须明确声明请求体为空、成功响应至少包含 `tenantId`、`tenantCode`、`name`、`status`、`plan`、`remark`、`createdAt`、`updatedAt`，并分别给出 `409 + C0203` 的状态冲突错误示例。
  - `POST /api/v1/iam/platform/tenants/{tenantId}/initialize-admin` 的请求 schema、前置校验、复用成员关系语义、只允许在“当前不存在任何 `ACTIVE tenant_admin`”时执行的约束、`409 + C0203` 冲突场景与成功响应，必须按 6.8 完整声明；成功响应至少包含 `tenantId`、`memberId`、`userId`、`displayName`、`status`、`roleCodes`，其中 `status` 必须为 `ACTIVE`，`roleCodes` 必须包含 `tenant_admin`。
  - `GET /api/v1/iam/platform/audit-logs` 的查询参数、默认排序与分页响应必须按 6.6、6.8 显式写出；列表项最少字段至少包含 `auditLogId`、`tenantId`、`tenantCode`、`action`、`targetType`、`targetId`、`operatorUserId`、`operatorDisplayName`、`createdAt`、`summary`。

  租户接口文档：

  - `GET /api/v1/iam/tenant/me` 的响应 schema 必须拆成 `tenant` 与 `member` 两个对象；其中 `tenant` 至少包含 `tenantId`、`tenantCode`、`name`、`status`、`plan`、`remark`、`createdAt`、`updatedAt`，`member` 至少包含 `memberId`、`userId`、`displayName`、`status`、`roleCodes`。
  - `GET /api/v1/iam/tenant/members` 的筛选参数、排序白名单、默认状态集合、分页规则、`userId` 单值精确筛选语义与“仅 `tenant_admin` 可调用”的权限约束，必须按 6.6、6.8 显式写出；列表项最少字段至少包含 `memberId`、`userId`、`username`、`displayName`、`status`、`roleCodes`。
  - `GET /api/v1/iam/tenant/members/{memberId}` 的响应 schema 至少包含 `memberId`、`userId`、`username`、`displayName`、`status`、`roleCodes`；其中与全局账号相关的只读投影仅收口为 `userId` 与 `username`，不得扩展成独立的全局用户详情资源；并明确该接口仅 `tenant_admin` 可调用。
  - `POST /api/v1/iam/tenant/members` 的请求 schema、禁止字段、冲突码、入租前置校验与“仅 `tenant_admin` 可调用”的权限约束，必须按 6.8 完整声明；成功响应至少包含 `memberId`、`userId`、`username`、`displayName`、`status`、`roleCodes`。
  - `GET /api/v1/iam/tenant/roles` 必须明确标注为固定目录型非分页接口；列表项最少字段至少包含 `roleId`、`code`、`name`、`description`、`status`；其中 `status` 正式取值固定为 `ACTIVE`、`DISABLED`；并明确该接口仅 `tenant_admin` 可调用。
  - `PUT /api/v1/iam/tenant/members/{memberId}/roles` 的请求 schema、全量覆盖语义、空数组清空语义、成功响应、仅 `tenant_admin` 可调用的权限约束，以及“不得移除当前租户最后一个 `ACTIVE tenant_admin`”的 `409 + C0203` 错误示例，必须按 6.8 完整声明；成功响应至少包含 `memberId`、`userId`、`username`、`displayName`、`status`、`roleCodes`。
  - `PATCH /api/v1/iam/tenant/members/{memberId}` 的可写字段、空字符串清空语义、输入/输出 schema 与“仅 `tenant_admin` 可调用”的权限约束，必须按 6.8 完整声明；正式可写字段只允许 `displayName`；成功响应至少包含 `memberId`、`userId`、`username`、`displayName`、`status`、`roleCodes`。
  - `POST /api/v1/iam/tenant/members/{memberId}/enable` 与 `POST /api/v1/iam/tenant/members/{memberId}/disable` 必须明确声明请求体为空、成功响应至少包含 `memberId`、`userId`、`username`、`displayName`、`status`、`roleCodes`，并分别给出 `409 + C0203` 的状态冲突错误示例；其中 `disable` 还必须给出“不得停用当前租户最后一个 `ACTIVE tenant_admin`”的冲突示例，并明确两条接口本期都仅 `tenant_admin` 可调用。
  - `GET /api/v1/iam/tenant/audit-logs` 的查询参数、默认排序与分页响应必须按 6.6、6.8 显式写出；列表项最少字段至少包含 `auditLogId`、`action`、`targetType`、`targetId`、`operatorUserId`、`operatorDisplayName`、`createdAt`、`summary`。

  现有 apps/api_v1/openapi_hooks.py 已会自动把 /api/v1/* 响应 schema 包成 code/msg/data。所以 IAM 正式进入 /api/v1/iam 后，文档 schema 也能
  和现有业务 API 保持一致。

  6.12 版本与兼容规范
  规范 19 指出破坏性变更原则上通过新版本发布。这里需要明确一下本次处理口径：

  - 旧 /internal/auth/* 不属于现有正式 /api/v1 API 体系。
  - 本次不是在 /api/v1 里偷偷覆盖旧语义，而是把内部接口归一化迁入正式 /api/v1/iam/*。
  - 因此可以直接以 /api/v1/iam/* 作为重构后的正式 API 发布。
  - 旧 /internal/auth/* 不做兼容保留。

  也就是说，这次切换是“内部历史接口收口进正式 V1 API”，而不是“已经稳定运行的正式 V1 接口被原地破坏”。

  6.13 测试与验收规范
  正式切换前至少应具备以下验收项。本节只验运行时行为；请求/响应字段名、最少返回字段、静态 schema 与文档示例是否完整，统一按 6.11 做静态校验。

  发布与认证：

  - 新路径全部可访问，旧 `/internal/auth/*` 全部下线，不保留兼容层。
  - 所有需要认证的 `/api/v1/iam/*` 接口只接受 `Authorization: Bearer <token>`；Session/Cookie 与 BasicAuthentication 不再作为正式 IAM 认证方式。
  - 未携带 Token、Token 无效或 Token 过期时，统一返回 `401 + A0401`。
  - 所有正式响应统一返回 `code`、`msg`、`data`；所有对外 JSON 字段、query 参数、具名 path 参数统一使用 camelCase。
  - 固定目录接口不分页，响应 `data` 直接为数组；其余列表接口统一按 6.6 返回 `data.list`、`data.total`。

  会话与当前用户：

  - `POST /api/v1/iam/session/login` 对不属于正式 IAM 账号集合的主体，或账号状态为 `DISABLED` 的主体，统一返回 `401 + A0401`；成功后返回可用的 accessToken、refreshToken，并可继续访问正式 API。
  - `POST /api/v1/iam/session/refresh` 必须返回新的 accessToken、refreshToken；旧 refreshToken 在刷新成功后立即失效；若所属账号状态已变为 `DISABLED`，统一返回 `401 + A0401`。
  - `POST /api/v1/iam/session/logout` 调用后，当前认证会话下尚未过期的 accessToken 与 refreshToken 立即失效。
  - `POST /api/v1/iam/session/register` 与 `POST /api/v1/iam/session/register-by-phone` 成功后都必须返回 `status=ACTIVE`，且账号初始运行态固定为 `unassigned`；不自动登录、不返回 token。
  - 账号状态转为 `DISABLED` 后，服务端必须撤销该账号现有认证会话。
  - `GET /api/v1/iam/me/profile` 可在登录后稳定返回当前用户自己的 `userId` 与基础资料；仅 `unassigned`、`tenant_member` 两种正式 IAM 业务账号运行态可调用；`platform_operator` 调用时必须返回 `403 + A0403`。
  - `GET /api/v1/iam/me/tenants` 必须稳定返回当前登录用户可进入的租户上下文分页列表；只包含“租户状态为 `ACTIVE` 且当前用户在该租户下成员状态也为 `ACTIVE`”的租户成员关系；`unassigned` 调用时返回空列表；`platform_operator` 调用时必须返回 `403 + A0403`。
  - `GET /api/v1/iam/tenant/me` 可在携带正确 `X-TENANT-CODE` 时稳定返回当前租户资料，以及当前登录用户在该租户下的 `member.roleCodes`。

  路由边界与作用域：

  - 正式 API 不暴露全局用户目录、未入租用户批量检索、平台工作态账号管理、邀请流程、`GET /api/v1/iam/me/permissions`、`POST /api/v1/iam/platform/tenants/{tenantId}/set-plan`、`/platform/tenants/{tenantId}/members/*`、`POST /api/v1/iam/tenant/members/{memberId}/roles`、`PUT /api/v1/iam/tenant/members/{memberId}`。
  - `tenant/*` 缺少 `X-TENANT-CODE` 或 header 格式非法时，统一返回 `400 + B0001`。
  - `unassigned` 账号访问任何 `tenant/*` 或 `platform/*` 正式接口时，必须返回 `403 + A0403`。
  - `platform_operator` 访问任何 `me/*` 或 `tenant/*` 正式接口必须返回 `403 + A0403`；`tenant_member` 访问 `platform/*` 也必须返回 `403 + A0403`。
  - `tenant/*` 中，`X-TENANT-CODE` 命中的租户不存在、已停用，或当前用户在该租户下不存在 `ACTIVE` 状态的成员关系时，必须返回 `403 + A0403`。
  - 租户 A 管理员把 `X-TENANT-CODE` 切到自己无权访问的租户时，必须返回 `403 + A0403`。
  - 在当前已生效租户上下文内查不到目标成员、角色或其他资源时，必须返回 `404 + C0404`。
  - `platform/*` 只接受平台工作态账号调用，不通过 header 切换租户上下文，也不提供通用成员管理接口；`POST /api/v1/iam/platform/tenants/{tenantId}/initialize-admin` 仅作为“当前租户不存在任何 `ACTIVE tenant_admin` 时”的 bootstrap / break-glass 唯一正式例外保留。
  - `tenant_member` 与 `platform_operator` 互斥；同一账号不能同时处于这两种已赋权运行态。

  租户成员：

  - `POST /api/v1/iam/tenant/members` 只能绑定已存在且可入租的 `userId`，不能隐式创建全局用户；未命中返回 `404 + C0404`，重复入租返回 `409 + C0103`。
  - `GET /api/v1/iam/tenant/members`、`GET /api/v1/iam/tenant/members/{memberId}`、`GET /api/v1/iam/tenant/roles`、`POST /api/v1/iam/tenant/members`、`PATCH /api/v1/iam/tenant/members/{memberId}`、`PUT /api/v1/iam/tenant/members/{memberId}/roles`、`POST /api/v1/iam/tenant/members/{memberId}/enable`、`POST /api/v1/iam/tenant/members/{memberId}/disable` 本期都必须对非 `tenant_admin` 返回 `403 + A0403`。
  - `POST /api/v1/iam/tenant/members` 允许 `roleCodes` 省略或传空数组；为空时可先创建为无角色成员，后续再通过 `PUT /api/v1/iam/tenant/members/{memberId}/roles` 分配角色。
  - `GET /api/v1/iam/tenant/members` 默认返回 `ACTIVE + DISABLED`；按 `status`、`keywords`、`userId`、`sortBy`、`sortOrder` 过滤或排序时，语义必须与 6.8 一致；客户侧若只看可用成员，必须显式传 `status=ACTIVE`。
  - `GET /api/v1/iam/tenant/members` 默认排序必须为 `displayName asc`，并以 `memberId asc` 作为稳定次排序。
  - `GET /api/v1/iam/tenant/members/{memberId}` 必须稳定返回 `memberId`、`userId`、`username`、`displayName`、`status`、`roleCodes`；不得把它扩展成独立的全局用户详情接口。
  - `PUT /api/v1/iam/tenant/members/{memberId}/roles` 的语义必须是全量覆盖；空数组表示清空角色；重复提交幂等。
  - `PUT /api/v1/iam/tenant/members/{memberId}/roles` 如会移除当前租户最后一个 `ACTIVE tenant_admin`，必须返回 `409 + C0203`。
  - `PATCH /api/v1/iam/tenant/members/{memberId}` 只做局部更新；正式可写字段只允许 `displayName`；显式传空字符串时清空 `displayName`；未传则保持原值；成功响应至少返回 `memberId`、`userId`、`username`、`displayName`、`status`、`roleCodes`。
  - 成员资质能力本期不纳入重构后的正式 API；`tenant/members` 相关正式请求与响应都不得出现 `qualifications`。
  - `POST /api/v1/iam/tenant/members/{memberId}/enable` 对已是 `ACTIVE` 的成员返回 `409 + C0203`；`POST /api/v1/iam/tenant/members/{memberId}/disable` 对已是 `DISABLED` 的成员返回 `409 + C0203`。
  - `POST /api/v1/iam/tenant/members/{memberId}/disable` 如会停用当前租户最后一个 `ACTIVE tenant_admin`，必须返回 `409 + C0203`。
  - `POST /api/v1/iam/tenant/members/{memberId}/enable` 与 `POST /api/v1/iam/tenant/members/{memberId}/disable` 必须使用空请求体，并返回更新后的成员对象。
  - `GET /api/v1/iam/tenant/roles` 只返回允许分配给租户成员的角色，且固定为非分页目录接口；目录 `status` 只能取 `ACTIVE`、`DISABLED`；响应 `data` 必须直接为数组。

  平台租户与审计：

  - `GET /api/v1/iam/platform/permissions`、`GET /api/v1/iam/platform/roles` 必须固定为非分页目录接口；`GET /api/v1/iam/platform/roles/{roleId}` 必须稳定返回角色详情与 `permissionGrants`；其中目录 `status` 只能取 `ACTIVE`、`DISABLED`，`permissionGrants[].permission` 固定表示权限 `code`，`permissionGrants[].scopeType` 只能取 `ALL`、`OWN`、`ASSIGNED`；两个目录列表接口的响应 `data` 都必须直接为数组。
  - `GET /api/v1/iam/platform/tenants`、`GET /api/v1/iam/platform/audit-logs`、`GET /api/v1/iam/tenant/audit-logs` 必须按 6.6、6.8 支持分页、筛选和排序。
  - `GET /api/v1/iam/platform/tenants` 的列表项与 `GET /api/v1/iam/platform/tenants/{tenantId}` 的详情响应，都必须稳定返回 `tenantCode`，不得继续暴露租户实体字段名 `code`。
  - `GET /api/v1/iam/platform/tenants` 不接受 `plan` 作为正式筛选参数；响应中的 `plan` 仅作为只读预留字段，当前统一返回 `null`。
  - `POST /api/v1/iam/platform/tenants` 只接受 `tenantCode`、`name`、`remark` 这组正式字段；如传旧字段 `code` 必须按参数非法处理；`tenantCode` 已存在时必须返回 `409 + C0101`；创建成功后必须返回 `status=ACTIVE`、`plan=null`。
  - `POST /api/v1/iam/platform/tenants/{tenantId}/enable` 对已是 `ACTIVE` 的租户返回 `409 + C0203`；`POST /api/v1/iam/platform/tenants/{tenantId}/disable` 对已是 `DISABLED` 的租户返回 `409 + C0203`；两条接口都必须使用空请求体并返回更新后的租户对象。
  - `POST /api/v1/iam/platform/tenants/{tenantId}/initialize-admin` 只能用于“当前租户不存在任何 `ACTIVE tenant_admin` 时”的 bootstrap / break-glass 修复；如目标用户已是该租户成员，必须复用现有成员关系、补齐 `tenant_admin` 角色，并确保最终 `status=ACTIVE`，不得重复创建成员；如当前已存在 `ACTIVE tenant_admin`，必须返回 `409 + C0203`。

  7. 迁移表

  说明：

  - 本表中“拆分为”表示旧接口的混合语义被拆成多条新接口，由资源语义和调用场景决定各自归属，不表示客户端可在运行时任选一条替代旧接口。

  | 现状接口 | 目标接口 | 处理方式 | 备注 |
  | --- | --- | --- | --- |
  | GET /internal/auth/session-status | 下线 | 删除重构后的正式 API | 仅内部 helper，不再公开 |
  | POST /internal/auth/login | POST /api/v1/iam/session/login | 改挂载并改认证语义 | 从 Django Session 登录改为创建认证会话并签发 accessToken / refreshToken |
  | 新增 | POST /api/v1/iam/session/refresh | 新增接口 | 使用 refreshToken 刷新 accessToken；正式 API 固定启用 refresh token rotation |
  | POST /internal/auth/logout | POST /api/v1/iam/session/logout | 改挂载并改认证语义 | 从 Django Session 退出改为撤销当前认证会话，使当前认证会话下的 accessToken / refreshToken 失效 |
  | POST /internal/auth/users/register | POST /api/v1/iam/session/register | 改挂载并清洗响应语义 | 从 users 归位到 session；注册成功不自动登录，不返回 token |
| POST /internal/auth/users/register/by-phone | POST /api/v1/iam/session/register-by-phone | 改挂载并清洗请求体 | 从 users 归位到 session；正式请求体改为 `phone`、`smsCode`、`password`；注册成功不自动登录 |
  | 新增 | GET /api/v1/iam/me/profile | 新增接口 | 当前登录用户读取自己的全局账号资料与 `userId`，用于入租流程 |
  | GET /internal/auth/me/tenants | GET /api/v1/iam/me/tenants | 改挂载并清洗响应语义 | 旧接口中的“当前用户有效租户 membership 列表”语义保留为正式 API；新接口只返回“租户状态为 `ACTIVE` 且当前用户在该租户下成员状态也为 `ACTIVE`”的租户上下文列表，用于让业务账号读取可切换的 `tenantCode` |
  | GET /internal/auth/me/permissions | 下线 | 删除重构后的正式 API | 不再对外提供统一当前会话权限矩阵接口 |
  | GET /internal/auth/me/invitations | 下线 | 删除重构后的正式 API | 邀请流程整体下线 |
  | POST /internal/auth/me/invitations/reject | 下线 | 删除重构后的正式 API | 邀请流程整体下线 |
  | POST /internal/auth/tenant-members/confirm-invitation | 下线 | 删除重构后的正式 API | 邀请流程整体下线 |
  | GET /internal/auth/users | 拆分为：GET /api/v1/iam/tenant/members；`admin/后台` 或内部工具 | 拆接口并收口资源 | 旧接口中的“当前租户用户目录”语义并入 `tenant/members`；全局用户检索、未入租用户查询与平台工作态账号查询不纳入正式 API，改由 `admin/后台` 或内部工具处理 |
| POST /internal/auth/users | 下线 | 迁出正式 API | 全局用户或平台工作态账号创建不再通过正式 API 提供；客户自助注册统一走 `POST /api/v1/iam/session/register` 或 `POST /api/v1/iam/session/register-by-phone`；其他场景改由 `admin/后台` 或内部工具处理 |
  | GET /internal/auth/users/{id} | 拆分为：GET /api/v1/iam/tenant/members?userId={userId}；`admin/后台` 或内部工具 | 拆接口并收口资源 | 租户侧如需按全局 `userId` 定位当前租户成员，先调用 `GET /api/v1/iam/tenant/members?userId={userId}`，如需完整成员详情，再使用返回的 `memberId` 调用 `GET /api/v1/iam/tenant/members/{memberId}`；全局用户详情与平台工作态账号详情不纳入正式 API |
  | PUT /internal/auth/users/{id} | 下线 | 迁出正式 API | 正式 API 不再提供全局用户或平台工作态账号更新能力；原有维护能力改由 `admin/后台` 或内部工具承担 |
  | PATCH /internal/auth/users/{id} | 下线 | 迁出正式 API | 正式 API 不再提供全局用户或平台工作态账号局部更新能力；原有维护能力改由 `admin/后台` 或内部工具承担 |
  | GET /internal/auth/permissions | GET /api/v1/iam/platform/permissions | 改挂载 | 平台目录接口 |
  | GET /internal/auth/roles | GET /api/v1/iam/platform/roles | 改挂载 | 平台目录接口 |
  | GET /internal/auth/roles/{id} | GET /api/v1/iam/platform/roles/{roleId} | 改挂载 | 平台目录接口 |
  | 新增 | GET /api/v1/iam/tenant/roles | 新增接口 | 租户管理员读取当前租户可分配角色目录；仅返回允许分配给租户成员的角色 |
  | GET /internal/auth/audit-logs | 拆分为：GET /api/v1/iam/platform/audit-logs；GET /api/v1/iam/tenant/audit-logs | 拆接口 | 旧接口中的平台审计日志和租户审计日志两种语义分别迁移 |
  | GET /internal/auth/tenant-audit-logs | GET /api/v1/iam/tenant/audit-logs | 改挂载 | 租户审计正式路径 |
  | GET /internal/auth/tenants | 拆分为：GET /api/v1/iam/tenant/me；GET /api/v1/iam/platform/tenants | 拆接口 | 旧接口中的“当前租户上下文”和“平台租户列表”两种语义分别迁移 |
  | POST /internal/auth/tenants | POST /api/v1/iam/platform/tenants | 改挂载 | 平台创建租户 |
  | GET /internal/auth/tenants/{id} | GET /api/v1/iam/platform/tenants/{tenantId} | 收口到平台侧 | 平台侧租户实体详情统一走该接口；租户工作侧当前租户上下文统一改走 `GET /api/v1/iam/tenant/me` |
  | POST /internal/auth/tenants/{id}/enable | POST /api/v1/iam/platform/tenants/{tenantId}/enable | 改挂载 | 平台租户动作 |
  | POST /internal/auth/tenants/{id}/disable | POST /api/v1/iam/platform/tenants/{tenantId}/disable | 改挂载 | 平台租户动作 |
  | POST /internal/auth/tenants/{id}/initialize-admin | POST /api/v1/iam/platform/tenants/{tenantId}/initialize-admin | 改挂载 | 平台租户动作 |
  | POST /internal/auth/tenants/{id}/set-plan | 下线 | 删除重构后的正式 API | 套餐能力属于后续特性；`plan` 当前仅作为租户实体响应中的只读预留字段存在，统一返回 `null` |
  | GET /internal/auth/tenant-members | GET /api/v1/iam/tenant/members | 改挂载 | 保留租户工作侧 |
  | POST /internal/auth/tenant-members | POST /api/v1/iam/tenant/members | 改挂载并清洗请求体 | 不再接收 tenantId，也不接受 tenant_id 旧别名；仅 tenant_admin 可调用 |
  | POST /internal/auth/tenant-members/invite | 下线 | 删除重构后的正式 API | 邀请流程整体下线 |
  | GET /internal/auth/tenant-members/{id} | GET /api/v1/iam/tenant/members/{memberId} | 改挂载 | 保留租户工作侧 |
  | PUT /internal/auth/tenant-members/{id} | PATCH /api/v1/iam/tenant/members/{memberId} | 改挂载并统一方法语义 | 重构后的正式 API 不再保留 PUT；成员属性更新统一收口到 PATCH |
  | PATCH /internal/auth/tenant-members/{id} | PATCH /api/v1/iam/tenant/members/{memberId} | 改挂载 | 保留租户工作侧 |
  | DELETE /internal/auth/tenant-members/{id} | 下线 | 删除重构后的正式 API | 改由 disable 表达状态流转 |
  | POST /internal/auth/tenant-members/{id}/roles | PUT /api/v1/iam/tenant/members/{memberId}/roles | 改挂载并统一方法语义 | 按规范 v2 改为对子资源角色集合的全量更新 |
  | POST /internal/auth/tenant-members/{id}/enable | POST /api/v1/iam/tenant/members/{memberId}/enable | 改挂载 | 保留租户工作侧 |
  | POST /internal/auth/tenant-members/{id}/disable | POST /api/v1/iam/tenant/members/{memberId}/disable | 改挂载 | 保留租户工作侧 |

  8. 结论
  这次重构的最终目标不是“把 /internal/auth 换成一个新前缀”这么简单，而是把 access 从历史内部接口形态，收敛成正式、可文档化、可约束、可长期
  维护的 /api/v1/iam 正式 API。

  最终边界应固定为：

  - session/* 处理登录、刷新令牌、登出、注册。
  - me/* 只处理正式 IAM 业务账号当前登录用户自己的全局账号资料与自有租户上下文，并提供 `userId` 获取入口与可切换的 `tenantCode` 读取入口；平台工作态账号不进入这一层。
  - tenant/* 处理当前租户上下文内工作；`tenant/me` 返回当前租户资料与当前登录用户在该租户下的业务角色信息；当前租户用户目录与成员管理统一通过 `tenant/members/*` 提供；`tenant/*` 访问前提固定为目标租户状态为 `ACTIVE`，且当前用户在该租户下存在 `ACTIVE` 状态的成员关系。
  - tenant/roles 提供当前租户可分配角色目录，供租户管理员分配成员角色。
  - platform/* 处理平台层面的租户实体、平台目录和平台审计。
  - 平台工作态账号管理、全局用户检索和未入租用户批量处理统一留在 `admin/后台` 或内部工具。
  - 邀请流程相关 API 不进入重构后的正式 API，成员加入只保留“租户管理员将已存在的全局用户加入当前租户”。
  - `tenant_member` 与 `platform_operator` 互斥，一个账号不能同时处于这两种已赋权运行态。
  - 租户管理员可以管理自己当前租户内的成员。
  - 平台侧不提供按租户 ID 显式指定目标租户的通用成员管理 HTTP 接口；`POST /api/v1/iam/platform/tenants/{tenantId}/initialize-admin` 是平台侧唯一允许写入租户成员关系的正式例外，只能在目标租户当前不存在任何 `ACTIVE` 状态且拥有 `tenant_admin` 角色的成员时，用于租户管理员 `bootstrap / break-glass` 修复，不承接日常成员管理。
  - 旧 /internal/auth/* 直接退出，不做兼容。
