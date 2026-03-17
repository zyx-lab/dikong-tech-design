
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
  - apps/access/management/commands/seed_role_permissions.py 中 tenant_admin 具备的是租户成员管理相关权限，而不是平台租户管理权限。

  所以这次改造不能只改前缀，必须连同接口分层、资源边界、响应规范、文档挂载一起收口。

  2. 已确认的业务边界
  以下边界已按你的口径固定，作为本方案的前提：

  - session-status 不作为正式对客户公开的 API 保留。
  - 客户侧需要“当前租户用户目录”接口。
  - 客户侧需要“当前租户 profile”接口。
  - 平台侧不提供正式“跨租户成员管理 HTTP 接口”。
  - 这里的“不提供跨租户成员管理”是指：不提供通过路径显式指定某个租户后，再去管理该租户成员的接口。
  - 邀请流程相关 API 不纳入重构后的正式 API，成员加入只保留“租户管理员直接添加成员”这一条路径。
  - 租户 A 的管理员可以管理租户 A 当前租户上下文内的成员。
  - 租户 A 的管理员不能管理租户 B 的成员。
  - 旧 /internal/auth/* 不保留兼容期，直接切换。

  为避免歧义，正式表述应固定为：

  “平台侧不提供以租户 ID 显式指定目标租户的成员管理 HTTP 接口；租户成员管理统一通过租户工作侧当前租户接口提供，租户管理员仅可管理自己当前租
  户内的成员。”

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

  正式 API 采用四段式分层：

  | 分层 | 路由前缀 | 租户表达方式 | 说明 |
  | --- | --- | --- | --- |
  | 会话层 | /api/v1/iam/session/* | 无 | 登录、登出、注册 |
  | 当前用户层 | /api/v1/iam/me/* | 无或隐式 | 当前用户自己的租户、权限、邀请 |
  | 租户工作侧 | /api/v1/iam/tenant/* | X-TENANT-CODE | 当前租户上下文内工作 |
  | 平台管理侧 | /api/v1/iam/platform/* | path {id} | 平台层面管理租户实体、平台账号、平台目录 |

  核心原则：

  - 路径决定接口作用域，不能再靠“调用者是什么身份”动态切换同一路径的语义。
  - tenant/* 只处理当前租户工作侧问题。
  - platform/* 只处理平台层问题。
  - 不提供 /platform/tenants/{id}/members/* 这类平台侧成员管理接口。

  4. 目标路由结构

  4.1 Session

  | 路由 | 功能描述 | 调用限制 |
  | --- | --- | --- |
  | POST /api/v1/iam/session/login | 用户名密码登录，创建当前会话，返回当前账号基础登录态信息。 | 匿名可调用；不依赖租户上下文；当前阶段按 Session/Cookie 方式建立登录态。 |
  | POST /api/v1/iam/session/logout | 退出当前登录会话，清理当前 Session。 | 仅已登录用户可调用；不依赖 X-TENANT-CODE。 |
  | POST /api/v1/iam/session/register | 用户名方式自助注册账号，并创建基础用户档案。 | 匿名可调用；不依赖租户上下文；仅用于自助注册，不负责加入租户。 |
  | POST /api/v1/iam/session/register/by-phone | 手机号方式自助注册账号，并创建基础用户档案。 | 匿名可调用；不依赖租户上下文；仅用于自助注册，不负责加入租户。 |

  4.2 Me

  | 路由 | 功能描述 | 调用限制 |
  | --- | --- | --- |
  | GET /api/v1/iam/me/tenants | 返回当前平台管理员可切换的租户列表，用于平台管理员进入某个租户工作上下文。 | 仅 platform_admin 可调用；不依赖 X-TENANT-CODE；普通租户用户不可调用。 |
  | GET /api/v1/iam/me/permissions | 返回当前会话的有效权限矩阵。租户用户查看的是当前租户权限，平台用户查看的是平台权限。 | 仅已登录用户可调用；租户用户调用时必须提供 X-TENANT-CODE；platform_admin 和 superuser 可在无租户上下文下调用。 |

  4.3 Tenant

  | 路由 | 功能描述 | 调用限制 |
  | --- | --- | --- |
  | GET /api/v1/iam/tenant/profile | 返回当前租户的基础资料，用于客户侧展示当前租户信息。 | 仅已登录且属于当前租户的用户可调用；必须提供 X-TENANT-CODE；不允许通过 path 指定租户；platform_admin 不作为正式调用主体。 |
  | GET /api/v1/iam/tenant/users | 返回当前租户用户目录，展示当前租户内已有成员关系的账号列表。 | 仅具备当前租户用户目录查看权限的租户用户可调用；必须提供 X-TENANT-CODE；只返回当前租户范围内账号；platform_admin 不作为正式调用主体。 |
  | GET /api/v1/iam/tenant/users/{id} | 返回当前租户用户目录中的单个用户详情。这里的 `{id}` 是全局用户 ID。 | 仅具备当前租户用户目录查看权限的租户用户可调用；必须提供 X-TENANT-CODE；目标用户必须属于当前租户；platform_admin 不作为正式调用主体。 |
  | GET /api/v1/iam/tenant/members | 返回当前租户成员列表，用于成员管理页。 | 仅具备当前租户成员查看权限的租户用户可调用；必须提供 X-TENANT-CODE；只允许查看当前租户成员；platform_admin 不作为正式调用主体。 |
  | POST /api/v1/iam/tenant/members | 直接为当前租户添加成员，并可写入成员属性与初始角色，创建后立即生效。 | 仅 tenant_admin 可调用；必须提供 X-TENANT-CODE；请求体不再接收 tenantId；只能添加当前租户成员；platform_admin 不作为正式调用主体。 |
  | GET /api/v1/iam/tenant/members/{id} | 返回当前租户单个成员详情。这里的 `{id}` 是租户成员 ID。 | 仅具备当前租户成员查看权限的租户用户可调用；必须提供 X-TENANT-CODE；目标成员必须属于当前租户；platform_admin 不作为正式调用主体。 |
  | PUT /api/v1/iam/tenant/members/{id} | 全量更新当前租户成员的展示名、memberNo、资质等租户内属性。 | 仅具备当前租户成员管理权限的租户用户可调用；必须提供 X-TENANT-CODE；目标成员必须属于当前租户；不用于修改全局用户账号；platform_admin 不作为正式调用主体。 |
  | PATCH /api/v1/iam/tenant/members/{id} | 局部更新当前租户成员的展示名、memberNo、资质等租户内属性。 | 仅具备当前租户成员管理权限的租户用户可调用；必须提供 X-TENANT-CODE；目标成员必须属于当前租户；不用于修改全局用户账号；platform_admin 不作为正式调用主体。 |
  | POST /api/v1/iam/tenant/members/{id}/roles | 重设当前租户成员的角色列表。 | 仅具备当前租户成员角色分配权限的租户用户可调用；必须提供 X-TENANT-CODE；目标成员必须属于当前租户；只能授予当前合同允许的租户角色；platform_admin 不作为正式调用主体。 |
  | POST /api/v1/iam/tenant/members/{id}/enable | 重新启用当前租户内已停用成员。 | 仅具备当前租户成员管理权限的租户用户可调用；必须提供 X-TENANT-CODE；目标成员必须属于当前租户，且状态必须允许启用；platform_admin 不作为正式调用主体。 |
  | POST /api/v1/iam/tenant/members/{id}/disable | 停用当前租户内已生效成员。 | 仅具备当前租户成员管理权限的租户用户可调用；必须提供 X-TENANT-CODE；目标成员必须属于当前租户，且状态必须允许停用；platform_admin 不作为正式调用主体。 |
  | GET /api/v1/iam/tenant/audit-logs | 返回当前租户范围内的审计日志。 | 仅具备当前租户审计日志查看权限的租户用户可调用；必须提供 X-TENANT-CODE；只返回当前租户日志；platform_admin 不作为正式调用主体。 |

  4.4 Platform

  | 路由 | 功能描述 | 调用限制 |
  | --- | --- | --- |
  | GET /api/v1/iam/platform/users | 返回平台范围内的全局账号列表。 | 仅 platform_admin 或 superuser 可调用；不依赖 X-TENANT-CODE；租户用户不可调用。 |
  | POST /api/v1/iam/platform/users | 创建全局账号，并可写入 StaffProfile。 | 仅 platform_admin 或 superuser 可调用；不依赖 X-TENANT-CODE；租户用户不可调用。 |
  | GET /api/v1/iam/platform/users/{id} | 返回平台范围内单个全局账号详情。 | 仅 platform_admin 或 superuser 可调用；不依赖 X-TENANT-CODE；`{id}` 为全局用户 ID；租户用户不可调用。 |
  | PUT /api/v1/iam/platform/users/{id} | 全量更新全局账号及 StaffProfile。 | 仅 platform_admin 或 superuser 可调用；不依赖 X-TENANT-CODE；租户用户不可调用。 |
  | PATCH /api/v1/iam/platform/users/{id} | 局部更新全局账号及 StaffProfile。 | 仅 platform_admin 或 superuser 可调用；不依赖 X-TENANT-CODE；租户用户不可调用。 |
  | GET /api/v1/iam/platform/permissions | 返回平台固定权限目录。 | 仅 platform_admin 或 superuser 可调用；不依赖 X-TENANT-CODE；租户用户不可调用。 |
  | GET /api/v1/iam/platform/roles | 返回平台固定角色目录及其权限模板。 | 仅 platform_admin 或 superuser 可调用；不依赖 X-TENANT-CODE；租户用户不可调用。 |
  | GET /api/v1/iam/platform/roles/{id} | 返回平台固定角色的详情。 | 仅 platform_admin 或 superuser 可调用；不依赖 X-TENANT-CODE；`{id}` 为角色 ID；租户用户不可调用。 |
  | GET /api/v1/iam/platform/audit-logs | 返回平台维度审计日志。 | 仅 platform_admin 或 superuser 可调用；不依赖 X-TENANT-CODE；租户用户不可调用。 |
  | GET /api/v1/iam/platform/tenants | 返回平台租户列表。 | 仅 platform_admin 或 superuser 可调用；不依赖 X-TENANT-CODE；租户用户不可调用。 |
  | POST /api/v1/iam/platform/tenants | 创建新的租户实体。 | 仅 platform_admin 或 superuser 可调用；不依赖 X-TENANT-CODE；租户用户不可调用。 |
  | GET /api/v1/iam/platform/tenants/{id} | 返回单个租户实体详情。 | 仅 platform_admin 或 superuser 可调用；目标租户以 path `{id}` 为准；不依赖 X-TENANT-CODE；租户用户不可调用。 |
  | POST /api/v1/iam/platform/tenants/{id}/enable | 启用指定租户。 | 仅 platform_admin 或 superuser 可调用；目标租户以 path `{id}` 为准；不依赖 X-TENANT-CODE；租户用户不可调用。 |
  | POST /api/v1/iam/platform/tenants/{id}/disable | 停用指定租户。 | 仅 platform_admin 或 superuser 可调用；目标租户以 path `{id}` 为准；不依赖 X-TENANT-CODE；租户用户不可调用。 |
  | POST /api/v1/iam/platform/tenants/{id}/initialize-admin | 为指定租户初始化首个租户管理员。 | 仅 platform_admin 或 superuser 可调用；目标租户以 path `{id}` 为准；不依赖 X-TENANT-CODE；租户用户不可调用。 |
  | POST /api/v1/iam/platform/tenants/{id}/set-plan | 为指定租户设置或调整套餐。 | 仅 platform_admin 或 superuser 可调用；目标租户以 path `{id}` 为准；不依赖 X-TENANT-CODE；租户用户不可调用。 |

  4.5 明确不提供

  | 路由 | 功能描述 | 调用限制 |
  | --- | --- | --- |
  | POST /api/v1/iam/tenant/members/invitations | 当前租户内发起成员邀请。 | 不纳入重构后的正式 API；成员加入统一通过 `POST /api/v1/iam/tenant/members` 由租户管理员直接添加。 |
  | GET /api/v1/iam/me/invitations | 当前用户查看待处理邀请。 | 不纳入重构后的正式 API；邀请流程整体下线。 |
  | POST /api/v1/iam/me/invitations/accept | 当前用户接受邀请。 | 不纳入重构后的正式 API；邀请流程整体下线。 |
  | POST /api/v1/iam/me/invitations/reject | 当前用户拒绝邀请。 | 不纳入重构后的正式 API；邀请流程整体下线。 |
  | GET /api/v1/iam/platform/tenants/{id}/members | 平台侧按租户 ID 查看成员列表。 | 不纳入重构后的正式 API；成员列表统一通过 `GET /api/v1/iam/tenant/members` 在当前租户上下文下查看。 |
  | POST /api/v1/iam/platform/tenants/{id}/members | 平台侧按租户 ID 创建成员。 | 不纳入重构后的正式 API；成员创建统一通过 `POST /api/v1/iam/tenant/members` 在当前租户上下文下执行。 |
  | GET /api/v1/iam/platform/tenants/{id}/members/{memberId} | 平台侧按租户 ID 查看单个成员详情。 | 不纳入重构后的正式 API；成员详情统一通过 `GET /api/v1/iam/tenant/members/{id}` 在当前租户上下文下查看。 |
  | POST /api/v1/iam/platform/tenants/{id}/members/{memberId}/roles | 平台侧按租户 ID 修改成员角色。 | 不纳入重构后的正式 API；成员角色调整统一通过 `POST /api/v1/iam/tenant/members/{id}/roles` 在当前租户上下文下执行。 |
  | 任何通过 path 显式指定租户后管理成员的正式接口 | 平台侧按租户 path 直接管理成员。 | 不纳入重构后的正式 API；租户管理员只能管理自己当前租户内成员，不能跨租户管理。 |

  5. API 拆分方案

  5.1 Session 与 Me 的归位
  当前 apps/access/urls.py 中会话与当前用户接口分散在 login/logout/users/register/me/* 等多个位置。正式合
  同应归位为两类：

  - session/* 只负责创建或销毁会话、注册账号。
  - me/* 只负责当前会话相关的上下文数据。

  具体调整：

  - session-status 下线，不再进入正式 API。
  - users/register、users/register/by-phone 改挂到 session/*。
  - me/tenants 作为特例保留在 me/* 分组下，但在重构后的正式 API 中仅对 platform_admin 开放，用于平台管理员获取可切换租户列表。
  - 邀请流程相关 API 全部下线，不再保留 `GET /me/invitations`、`POST /me/invitations/accept`、`POST /me/invitations/reject`、`POST /tenant-members/confirm-invitation` 这些重构后的正式 API。

  5.2 Users 拆分为租户用户目录和平台全局账号管理
  当前 UserListCreateView 与 UserDetailView 在 apps/access/views.py 中有明显混合语义：

  - GET /users、GET /users/{id} 会根据调用者角色决定作用域。
  - POST /users、PUT/PATCH /users/{id} 实际在操作全局 User + StaffProfile。

  重构后的正式 API 必须拆开。

  拆分后的重构后的正式 API 以第 4 节为准，这里只强调边界：

  - `tenant/users` 表示“当前租户用户目录”，是客户侧正式使用的只读资源。
  - `platform/users` 表示“平台全局账号管理”，负责全局账号与 StaffProfile 的增删改查能力。
  - `tenant/users/{id}` 的 `{id}` 是全局用户 ID，不是成员 ID。
  - `tenant/users/*` 只做当前租户范围内的目录查询，不承担全局账号创建和修改。
  - `platform/users/*` 不再因为调用者身份不同而退化成“当前租户用户目录”。

  这一步的实质是把“全局账号资源”和“当前租户用户目录”分成两套只读/可写边界清晰的资源。

  5.3 Tenants 拆分为当前租户资料和平台租户实体
  当前 tenants 路由混合了两种需求：

  - 平台层面管理租户实体。
  - 租户侧读取“自己当前租户”的资料。

  重构后的正式 API 必须拆开。

  拆分后的重构后的正式 API 以第 4 节为准，这里只强调边界：

  - `tenant/profile` 表示“当前租户资料”，只解决客户侧查看当前租户信息的问题。
  - `platform/tenants/*` 表示“平台租户实体管理”，负责列表、详情、启停、套餐、初始化管理员等平台动作。
  - `tenant/profile` 不带 path 租户 ID，也不承担任何平台管理动作。
  - `platform/tenants/{id}` 中的目标租户必须以 path 为准，不能由 `X-TENANT-CODE` 替代。

  5.4 Audit Logs 拆分为平台审计和租户审计
  当前 /audit-logs 和 /tenant-audit-logs 并存，而 /audit-logs 本身又带混合作用域。

  重构后的正式 API 只保留两类固定语义资源：

  - `platform/audit-logs` 只表达平台维度审计日志。
  - `tenant/audit-logs` 只表达当前租户审计日志。
  - 不再保留“同一路径根据调用者不同返回不同日志范围”的合同。

  5.5 Tenant Members 保留在租户工作侧，但要做协议清洗
  tenant-members/* 当前的方向是对的，因为它已经通过 platform_admin_forbidden 明确限制在租户工作侧。但重构后的正式 API 仍需做四类清洗。

  第一类是路径规范化与下线范围确认：

  - `tenant-members` 规范化为 `tenant/members`
  - 邀请流程相关接口不再迁移，直接下线，包括 `tenant-members/invite`、`tenant-members/confirm-invitation` 以及 `me/invitations/*`

  第二类是请求体去租户参数化：

  当前 apps/access/serializers.py 中 TenantMemberCreateSerializer 和 TenantMemberInviteSerializer 仍接受 tenant_id。这只是旧接口遗留兼容，
  不应进入重构后的正式 API。

  在重构后的正式 API 里，成员创建接口不再接收 tenantId 或 tenant_id。目标租户只能从 X-TENANT-CODE 解析。

  第三类是资源语义固定：

  - tenant/members/{id} 的 {id} 是租户成员 ID。
  - 它对应的是租户成员关系资源，不是全局用户。
  - 成员接口可管理角色、资质、状态、展示名、memberNo 等租户内属性。
  - 用户目录接口与成员管理接口不能再混用。
  - `POST /tenant/members` 是“直接添加成员”，不是“发邀请”；创建后成员立即生效。
  - `POST /tenant/members` 在重构后的正式 API 中仅允许租户管理员调用。

  第四类是删除策略调整：

  当前 TenantMemberDetailView 仍有 DELETE，是硬删除。重构后的正式 API 不建议继续保留，原因有两个：

  - 规范不建议普通 JSON 接口使用 204 No Content 作为常规删除返回。
  - 成员对象更适合走状态流转，而不是直接硬删除。

  因此在重构后的正式 API 中：

  - 不提供 `DELETE /tenant/members/{id}`
  - ACTIVE 成员移除使用 `POST /tenant/members/{id}/disable`
  - DISABLED 成员恢复使用 `POST /tenant/members/{id}/enable`

  这里再强调一次边界：

  - 成员管理统一留在 `tenant/members/*`
  - 不提供 `/platform/tenants/{id}/members/*`
  - 也就是说，允许“同租户内跨成员管理”，不允许“按 path 显式指定任意租户后管理该租户成员”

  如果未来真的要支持“删除”概念，也应基于统一软删除能力重新设计，而不是把当前硬删除直接公开为重构后的正式 API。

  5.6 Platform 目录能力归位
  当前权限目录和角色目录本质上都是平台级只读目录，不属于租户工作侧。

  拆分后的重构后的正式 API 以第 4 节为准，这里只强调边界：

  - `platform/permissions`、`platform/roles`、`platform/roles/{id}` 都属于平台目录接口。
  - 它们解决的是“平台固定目录配置”的读取问题，不是“当前租户有效权限”的读取问题。
  - 租户用户如果要知道自己当前租户能做什么，应通过 `GET /me/permissions` 查看当前会话的有效权限矩阵，而不是读取平台目录。

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
  规范 15.1 推荐 Bearer Token，但当前工程实际配置见 config/settings.py：

  - SessionAuthentication
  - BasicAuthentication

  而当前 apps/access/views.py 的 LoginView/LogoutView 也是标准 Django Session 流程。

  因此本次重构建议分开处理：

  - 本轮路由重构不强制同步切到 Bearer Token。
  - 正式文档必须明确当前认证方式就是 Session/Cookie。
  - 如果前端是浏览器调用，所有需要认证的写操作必须同时满足 Session 与 CSRF 约束。
  - 如果后续要支持移动端、第三方系统或纯 API SDK，再单独立 Bearer Token 改造，不要把认证体系改造和本次路由分层重构绑在一起。

  也就是说，本轮重点是把“路径合同、作用域合同、响应合同”先收敛，认证机制可以单独演进。

  6.3 多租户上下文规范
  按照规范 6.6 和 17.6，正式实现必须统一租户上下文解析方式。

  租户工作侧：

  - 统一从 X-TENANT-CODE 解析 effectiveTenant
  - 统一校验当前登录用户是否属于该租户且具有对应权限
  - 统一禁止在 path、query、body 中再次传入租户标识表达同一上下文

  平台管理侧：

  - 统一从 path {id} 解析 targetTenant
  - 统一校验当前登录用户是否具有平台管理权限
  - 不允许用 X-TENANT-CODE 替代或覆盖目标租户

  落地要求：

  - get_queryset
  - serializer 校验
  - service 层逻辑
  - 批量任务
  - 审计日志

  都必须使用统一解析后的 effectiveTenant 或 targetTenant，不能再各处手写一套不同的租户判断。

  当前 apps/access/middleware.py 已能把 X-TENANT-CODE 解析到 request.tenant_context。新合同应以此为基础继续下沉统一逻辑，而不是在各个视图里
  继续散落判断。

  6.4 权限校验规范
  权限控制以后端校验为准，不依赖前端展示层。新合同要求路径和权限边界一一对应：

  - session/*：登录、注册使用 AllowAny，登出使用已登录校验。
  - me/*：默认要求当前用户已登录；其中 `GET /me/tenants` 是特例，仅 `platform_admin` 可调用。
  - tenant/*：要求当前用户在 effectiveTenant 中具备租户侧权限。
  - platform/*：要求当前用户具备平台侧权限。

  关键约束：

  - 不允许同一路径根据调用者身份不同切换成不同语义。
  - 平台管理员不作为租户成员接口的正式调用主体。
  - `POST /tenant/members` 作为重构后的正式 API 中的“添加成员”接口，仅 `tenant_admin` 可调用。
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
  | 409 | C0101 或 C0201 | 重复或状态冲突 |
  | 500 | E0001 | 系统异常 |

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

  也不允许一部分列表接口分页，一部分接口直接返回整数组。

  如果某个目录接口确实不分页，也应在文档中明确说明不分页原因，而不是随意返回不同结构。

  6.7 字段命名规范
  规范 5.3 推荐对外使用 camelCase，但也允许项目层统一为 snake_case，前提是“全系统一致，不得混用”。

  这一点对 IAM 改造非常关键，因为当前 access 大量使用 tenant_id、user_id、member_no 等 snake_case 字段。重构后的正式 API 必须做统一决策：

  - 如果项目正式 API 准备统一成 camelCase，则 IAM 新合同应全部转成 tenantId、userId、memberNo、pageNum、pageSize。
  - 如果项目 /api/v1 其余正式业务接口已经稳定使用 snake_case，则 IAM 可以统一跟随现有风格。
  - 无论最终选哪一种，IAM 新合同内部都不能再混用。

  本文件建议：

  - 路径和响应 envelope 本轮必须统一。
  - 字段命名也应在本轮一并统一拍板。
  - 至少新正式接口不能再继续暴露 tenant_id 与 tenantId 混搭的情况。

  6.8 请求参数规范
  重构后的正式 API 中，租户工作侧接口禁止在 query/body/path 中重复表达租户上下文。

  明确要求：

  - POST /tenant/members 不再接收 tenantId
  - GET /tenant/members 不再依赖 tenant_id 筛选当前租户
  - GET /tenant/profile 不接收 path 租户 ID
  - platform/* 路由不声明 X-TENANT-CODE

  如果客户端同时传了 path 租户 ID 和 header 租户 code 来表达同一件事，建议直接返回 400 + B0001，而不是“悄悄取其中一个”。

  6.9 删除与状态流转规范
  规范 9.2 明确指出普通 JSON 接口原则上不使用 204 No Content。规范 17.4 又建议统一软删除能力。

  结合当前 access 实现，本轮建议如下：

  - 成员不在重构后的正式 API 中开放硬删除。
  - 成员移除统一用状态动作。
  - 邀请流程不再进入重构后的正式 API，`disable` 只承担成员停用语义。
  - 如后续真的需要“删除成员”概念，应先引入统一软删除基类，再决定是否提供 DELETE。

  这既符合规范，也避免把当前内部硬删除直接变成长期对外合同。

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
  - apps/access/services/platform_user_service.py
  - apps/access/services/tenant_service.py
  - apps/access/services/audit_service.py

  职责要求：

  - View 层只做接收请求、调用 service、返回统一响应。
  - Serializer 层只做参数校验和序列化。
  - Service 层集中实现成员状态流转、角色授予、租户管理等业务逻辑。
  - Model 层只负责持久化与约束。
  - 多租户上下文解析与权限判断应尽量下沉到基类或公共方法，不要在每个视图重复手写。

  6.11 OpenAPI 与文档规范
  规范 18 要求每个接口具备完整文档要素。正式 IAM 改造后，文档层要同步调整：

  - 正式 IAM 接口进入 /api/v1/docs/*
  - 每个接口都必须标注权限标识
  - tenant/* 必须明确标注 X-TENANT-CODE
  - platform/* 必须明确标注 path 中的目标租户或目标资源 ID
  - 列表接口必须标注 pageNum/pageSize
  - 每个接口都应附成功响应示例与错误码示例

  现有 apps/api_v1/openapi_hooks.py 已会自动把 /api/v1/* 响应 schema 包成 code/msg/data。所以 IAM 正式进入 /api/v1/iam 后，文档 schema 也能
  和现有业务 API 保持一致。

  6.12 版本与兼容规范
  规范 19 指出破坏性变更原则上通过新版本发布。这里需要明确一下本次处理口径：

  - 旧 /internal/auth/* 不属于现有正式 /api/v1 合同体系。
  - 本次不是在 /api/v1 里偷偷覆盖旧语义，而是把内部接口归一化迁入正式 /api/v1/iam/*。
  - 因此可以直接以 /api/v1/iam/* 作为重构后的正式 API 发布。
  - 旧 /internal/auth/* 不做兼容保留。

  也就是说，这次切换是“内部历史接口收口进正式 V1 合同”，而不是“已经稳定运行的正式 V1 接口被原地破坏”。

  6.13 测试与验收规范
  正式切换前至少应具备以下验收项：

  - 新路径全部可访问，旧路径全部下线。
  - 所有新接口返回 code/msg/data。
  - 所有分页接口返回 pageNum/pageSize 入参和 data.list/data.total 出参。
  - tenant/* 缺少 X-TENANT-CODE 时返回明确错误。
  - 租户 A 管理员无法访问租户 B 数据。
  - 平台路由不提供成员管理接口。
  - platform/* 无法通过 header 指定目标租户成员进行管理。
  - 邀请流程相关 API 已从重构后的正式 API 移除。
  - tenant/members 创建不再接收 tenantId，且仅 tenant_admin 可调用。
  - tenant/users 只读，platform/users 才可增改。
  - tenant/profile 可正常返回当前租户资料。
  - OpenAPI 文档进入 /api/v1/docs/*，不再以 internal/docs 作为正式入口。

  7. 迁移表

  说明：

  - 本表中“拆分为”表示旧接口的混合语义被拆成多条新接口，由资源语义和调用场景决定各自归属，不表示客户端可在运行时任选一条替代旧接口。

  | 现状接口 | 目标接口 | 处理方式 | 备注 |
  | --- | --- | --- | --- |
  | GET /internal/auth/session-status | 下线 | 删除重构后的正式 API | 仅内部 helper，不再公开 |
  | POST /internal/auth/login | POST /api/v1/iam/session/login | 平移 | 会话接口归位 |
  | POST /internal/auth/logout | POST /api/v1/iam/session/logout | 平移 | 会话接口归位 |
  | POST /internal/auth/users/register | POST /api/v1/iam/session/register | 改挂载 | 从 users 归位到 session |
  | POST /internal/auth/users/register/by-phone | POST /api/v1/iam/session/register/by-phone | 改挂载 | 从 users 归位到 session |
  | GET /internal/auth/me/tenants | GET /api/v1/iam/me/tenants | 改挂载 | 平台管理员可切换租户列表 |
  | GET /internal/auth/me/permissions | GET /api/v1/iam/me/permissions | 改挂载 | 当前会话权限矩阵 |
  | GET /internal/auth/me/invitations | 下线 | 删除重构后的正式 API | 邀请流程整体下线 |
  | POST /internal/auth/me/invitations/reject | 下线 | 删除重构后的正式 API | 邀请流程整体下线 |
  | POST /internal/auth/tenant-members/confirm-invitation | 下线 | 删除重构后的正式 API | 邀请流程整体下线 |
  | GET /internal/auth/users | 拆分为：GET /api/v1/iam/tenant/users；GET /api/v1/iam/platform/users | 拆接口 | 旧接口中的“当前租户用户目录”和“平台全局账号列表”两种语义分别迁移 |
  | POST /internal/auth/users | POST /api/v1/iam/platform/users | 保留平台侧 | 全局账号创建 |
  | GET /internal/auth/users/{id} | 拆分为：GET /api/v1/iam/tenant/users/{id}；GET /api/v1/iam/platform/users/{id} | 拆接口 | 旧接口中的“当前租户用户详情”和“平台全局账号详情”两种语义分别迁移 |
  | PUT /internal/auth/users/{id} | PUT /api/v1/iam/platform/users/{id} | 收口到平台侧 | 租户侧不改全局账号 |
  | PATCH /internal/auth/users/{id} | PATCH /api/v1/iam/platform/users/{id} | 收口到平台侧 | 租户侧不改全局账号 |
  | GET /internal/auth/permissions | GET /api/v1/iam/platform/permissions | 改挂载 | 平台目录接口 |
  | GET /internal/auth/roles | GET /api/v1/iam/platform/roles | 改挂载 | 平台目录接口 |
  | GET /internal/auth/roles/{id} | GET /api/v1/iam/platform/roles/{id} | 改挂载 | 平台目录接口 |
  | GET /internal/auth/audit-logs | 拆分为：GET /api/v1/iam/platform/audit-logs；GET /api/v1/iam/tenant/audit-logs | 拆接口 | 旧接口中的平台审计日志和租户审计日志两种语义分别迁移 |
  | GET /internal/auth/tenant-audit-logs | GET /api/v1/iam/tenant/audit-logs | 改挂载 | 租户审计正式路径 |
  | GET /internal/auth/tenants | 拆分为：GET /api/v1/iam/tenant/profile；GET /api/v1/iam/platform/tenants | 拆接口 | 旧接口中的“当前租户资料”和“平台租户列表”两种语义分别迁移 |
  | POST /internal/auth/tenants | POST /api/v1/iam/platform/tenants | 改挂载 | 平台创建租户 |
  | GET /internal/auth/tenants/{id} | GET /api/v1/iam/platform/tenants/{id} | 收口到平台侧 | 租户侧当前资料改走 tenant/profile |
  | POST /internal/auth/tenants/{id}/enable | POST /api/v1/iam/platform/tenants/{id}/enable | 改挂载 | 平台租户动作 |
  | POST /internal/auth/tenants/{id}/disable | POST /api/v1/iam/platform/tenants/{id}/disable | 改挂载 | 平台租户动作 |
  | POST /internal/auth/tenants/{id}/initialize-admin | POST /api/v1/iam/platform/tenants/{id}/initialize-admin | 改挂载 | 平台租户动作 |
  | POST /internal/auth/tenants/{id}/set-plan | POST /api/v1/iam/platform/tenants/{id}/set-plan | 改挂载 | 平台租户动作 |
  | GET /internal/auth/tenant-members | GET /api/v1/iam/tenant/members | 改挂载 | 保留租户工作侧 |
  | POST /internal/auth/tenant-members | POST /api/v1/iam/tenant/members | 改挂载并清洗请求体 | 不再接收 tenantId；仅 tenant_admin 可调用 |
  | POST /internal/auth/tenant-members/invite | 下线 | 删除重构后的正式 API | 邀请流程整体下线 |
  | GET /internal/auth/tenant-members/{id} | GET /api/v1/iam/tenant/members/{id} | 改挂载 | 保留租户工作侧 |
  | PUT /internal/auth/tenant-members/{id} | PUT /api/v1/iam/tenant/members/{id} | 改挂载 | 保留租户工作侧 |
  | PATCH /internal/auth/tenant-members/{id} | PATCH /api/v1/iam/tenant/members/{id} | 改挂载 | 保留租户工作侧 |
  | DELETE /internal/auth/tenant-members/{id} | 下线 | 删除重构后的正式 API | 改由 disable 表达状态流转 |
  | POST /internal/auth/tenant-members/{id}/roles | POST /api/v1/iam/tenant/members/{id}/roles | 改挂载 | 保留租户工作侧 |
  | POST /internal/auth/tenant-members/{id}/enable | POST /api/v1/iam/tenant/members/{id}/enable | 改挂载 | 保留租户工作侧 |
  | POST /internal/auth/tenant-members/{id}/disable | POST /api/v1/iam/tenant/members/{id}/disable | 改挂载 | 保留租户工作侧 |

  8. 结论
  这次重构的最终目标不是“把 /internal/auth 换成一个新前缀”这么简单，而是把 access 从历史内部接口形态，收敛成正式、可文档化、可约束、可长期
  维护的 /api/v1/iam 合同。

  最终边界应固定为：

  - session/* 处理登录、登出、注册。
  - me/* 处理当前会话相关的租户与权限上下文。
  - tenant/* 处理当前租户上下文内工作。
  - platform/* 处理平台层面的租户实体、平台账号、平台目录和平台审计。
  - 邀请流程相关 API 不进入重构后的正式 API，成员加入只保留“租户管理员直接添加成员”。
  - 租户管理员可以管理自己当前租户内的成员。
  - 平台侧不提供按租户 ID 显式指定目标租户的成员管理 HTTP 接口。
  - 旧 /internal/auth/* 直接退出，不做兼容。
