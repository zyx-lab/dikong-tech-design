
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
  - 这些接口里已有客户侧正式使用场景，再带 internal 会和“正式合同”语义冲突。
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

  - POST /api/v1/iam/session/login
  - POST /api/v1/iam/session/logout
  - POST /api/v1/iam/session/register
  - POST /api/v1/iam/session/register/by-phone

  4.2 Me

  - GET /api/v1/iam/me/tenants
  - GET /api/v1/iam/me/permissions
  - GET /api/v1/iam/me/invitations
  - POST /api/v1/iam/me/invitations/accept
  - POST /api/v1/iam/me/invitations/reject

  4.3 Tenant

  - GET /api/v1/iam/tenant/profile
  - GET /api/v1/iam/tenant/users
  - GET /api/v1/iam/tenant/users/{id}
  - GET /api/v1/iam/tenant/members
  - POST /api/v1/iam/tenant/members
  - POST /api/v1/iam/tenant/members/invitations
  - GET /api/v1/iam/tenant/members/{id}
  - PUT /api/v1/iam/tenant/members/{id}
  - PATCH /api/v1/iam/tenant/members/{id}
  - POST /api/v1/iam/tenant/members/{id}/roles
  - POST /api/v1/iam/tenant/members/{id}/enable
  - POST /api/v1/iam/tenant/members/{id}/disable
  - GET /api/v1/iam/tenant/audit-logs

  4.4 Platform

  - GET /api/v1/iam/platform/users
  - POST /api/v1/iam/platform/users
  - GET /api/v1/iam/platform/users/{id}
  - PUT /api/v1/iam/platform/users/{id}
  - PATCH /api/v1/iam/platform/users/{id}
  - GET /api/v1/iam/platform/permissions
  - GET /api/v1/iam/platform/roles
  - GET /api/v1/iam/platform/roles/{id}
  - GET /api/v1/iam/platform/audit-logs
  - GET /api/v1/iam/platform/tenants
  - POST /api/v1/iam/platform/tenants
  - GET /api/v1/iam/platform/tenants/{id}
  - POST /api/v1/iam/platform/tenants/{id}/enable
  - POST /api/v1/iam/platform/tenants/{id}/disable
  - POST /api/v1/iam/platform/tenants/{id}/initialize-admin
  - POST /api/v1/iam/platform/tenants/{id}/set-plan

  4.5 明确不提供

  - GET /api/v1/iam/platform/tenants/{id}/members
  - POST /api/v1/iam/platform/tenants/{id}/members
  - GET /api/v1/iam/platform/tenants/{id}/members/{memberId}
  - POST /api/v1/iam/platform/tenants/{id}/members/{memberId}/roles
  - 任何通过 path 显式指定租户后管理成员的正式接口

  5. API 拆分方案

  5.1 Session 与 Me 的归位
  当前 apps/access/urls.py 中会话与当前用户接口分散在 login/logout/users/register/me/*/tenant-members/confirm-invitation 等多个位置。正式合
  同应归位为两类：

  - session/* 只负责创建或销毁会话、注册账号。
  - me/* 只负责当前登录用户自己的上下文数据和个人动作。

  具体调整：

  - session-status 下线，不再进入正式 API。
  - users/register、users/register/by-phone 改挂到 session/*。
  - tenant-members/confirm-invitation 改挂到 me/invitations/accept，因为这不是“租户管理员管理成员”，而是“当前登录用户接受自己的邀请”。

  5.2 Users 拆分为租户用户目录和平台全局账号管理
  当前 UserListCreateView 与 UserDetailView 在 apps/access/views.py 中有明显混合语义：

  - GET /users、GET /users/{id} 会根据调用者角色决定作用域。
  - POST /users、PUT/PATCH /users/{id} 实际在操作全局 User + StaffProfile。

  正式合同必须拆开。

  租户工作侧：

  - GET /tenant/users
  - GET /tenant/users/{id}

  语义：

  - 这是“当前租户用户目录”，是客户侧正式需要的接口。
  - 它是只读接口。
  - 它按当前 X-TENANT-CODE 过滤，只返回当前租户内有关联成员关系的用户。
  - {id} 使用全局用户 ID，而不是成员 ID。
  - 不允许通过租户侧接口创建、修改全局用户账号。

  平台管理侧：

  - GET /platform/users
  - POST /platform/users
  - GET /platform/users/{id}
  - PUT /platform/users/{id}
  - PATCH /platform/users/{id}

  语义：

  - 这是平台级全局账号管理接口。
  - 保留当前 User + StaffProfile 的管理能力。
  - 不再因为调用者是租户管理员而“顺带变成租户用户目录接口”。

  这一步的实质是把“全局账号资源”和“当前租户用户目录”分成两套只读/可写边界清晰的资源。

  5.3 Tenants 拆分为当前租户资料和平台租户实体
  当前 tenants 路由混合了两种需求：

  - 平台层面管理租户实体。
  - 租户侧读取“自己当前租户”的资料。

  正式合同必须拆开。

  租户工作侧：

  - GET /tenant/profile

  语义：

  - 这是客户侧正式需要的“当前租户 profile”接口。
  - 只返回当前 X-TENANT-CODE 对应租户资料。
  - 不带 path 租户 ID。
  - 不能用于启停、设套餐、初始化管理员。

  平台管理侧：

  - GET /platform/tenants
  - POST /platform/tenants
  - GET /platform/tenants/{id}
  - POST /platform/tenants/{id}/enable
  - POST /platform/tenants/{id}/disable
  - POST /platform/tenants/{id}/initialize-admin
  - POST /platform/tenants/{id}/set-plan

  语义：

  - 这是平台层面对租户实体的管理。
  - 目标租户由 path {id} 明确指定。
  - 即使请求里带了 X-TENANT-CODE，也不能用它替代 path 中的目标租户。

  5.4 Audit Logs 拆分为平台审计和租户审计
  当前 /audit-logs 和 /tenant-audit-logs 并存，而 /audit-logs 本身又带混合作用域。

  正式合同应只保留两类固定语义接口：

  - GET /platform/audit-logs
  - GET /tenant/audit-logs

  约束：

  - platform/audit-logs 只表达平台维度审计日志。
  - tenant/audit-logs 只表达当前租户审计日志。
  - 不再保留“同一路径根据调用者不同返回不同日志范围”的合同。

  5.5 Tenant Members 保留在租户工作侧，但要做协议清洗
  tenant-members/* 当前的方向是对的，因为它已经通过 platform_admin_forbidden 明确限制在租户工作侧。但正式合同仍需做四类清洗。

  第一类是路径规范化：

  - tenant-members -> tenant/members
  - tenant-members/invite -> tenant/members/invitations
  - tenant-members/confirm-invitation -> me/invitations/accept

  第二类是请求体去租户参数化：

  当前 apps/access/serializers.py 中 TenantMemberCreateSerializer 和 TenantMemberInviteSerializer 仍接受 tenant_id。这只是旧接口遗留兼容，
  不应进入新正式合同。

  正式合同要求：

  - POST /tenant/members
  - POST /tenant/members/invitations

  都不再接收 tenantId 或 tenant_id。目标租户只能从 X-TENANT-CODE 解析。

  第三类是资源语义固定：

  - tenant/members/{id} 的 {id} 是租户成员 ID。
  - 它对应的是租户成员关系资源，不是全局用户。
  - 成员接口可管理角色、资质、状态、展示名、memberNo 等租户内属性。
  - 用户目录接口与成员管理接口不能再混用。

  第四类是删除策略调整：

  当前 TenantMemberDetailView 仍有 DELETE，是硬删除。正式合同不建议继续保留，原因有两个：

  - 规范不建议普通 JSON 接口使用 204 No Content 作为常规删除返回。
  - 成员对象更适合走状态流转，而不是直接硬删除。

  因此正式合同中：

  - 不提供 DELETE /tenant/members/{id}
  - ACTIVE 成员移除使用 POST /tenant/members/{id}/disable
  - INVITED 邀请撤销也使用 POST /tenant/members/{id}/disable
  - DISABLED 成员恢复使用 POST /tenant/members/{id}/enable

  如果未来真的要支持“删除”概念，也应基于统一软删除能力重新设计，而不是把当前硬删除直接公开为正式合同。

  5.6 Platform 目录能力归位
  当前权限目录和角色目录本质上都是平台级只读目录，不属于租户工作侧。

  正式合同固定为：

  - GET /platform/permissions
  - GET /platform/roles
  - GET /platform/roles/{id}

  租户管理员如果需要知道自己当前租户能做什么，应通过 GET /me/permissions 获取当前会话的有效权限矩阵，而不是直接读取平台目录。

  6. 实现规范
  本节把 项目总体概览/api设计规范v2.md 的协议规范与当前工程实现约束合并成落地规则。

  6.1 路由挂载规范
  正式挂载统一改为：

  path("api/v1/iam/", include(...))

  约束：

  - /api/v1/iam/* 是正式合同。
  - /internal/auth/* 直接下线，不做兼容。
  - 如果还有真正内部 helper，才单独保留在内部前缀下，但不能继续和正式合同混挂。

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
  - me/*：只要求当前用户已登录。
  - tenant/*：要求当前用户在 effectiveTenant 中具备租户侧权限。
  - platform/*：要求当前用户具备平台侧权限。

  关键约束：

  - 不允许同一路径根据调用者身份不同切换成不同语义。
  - 平台管理员不作为租户成员接口的正式调用主体。
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

  这一点对 IAM 改造非常关键，因为当前 access 大量使用 tenant_id、user_id、member_no 等 snake_case 字段。正式合同必须做统一决策：

  - 如果项目正式 API 准备统一成 camelCase，则 IAM 新合同应全部转成 tenantId、userId、memberNo、pageNum、pageSize。
  - 如果项目 /api/v1 其余正式业务接口已经稳定使用 snake_case，则 IAM 可以统一跟随现有风格。
  - 无论最终选哪一种，IAM 新合同内部都不能再混用。

  本文件建议：

  - 路径和响应 envelope 本轮必须统一。
  - 字段命名也应在本轮一并统一拍板。
  - 至少新正式接口不能再继续暴露 tenant_id 与 tenantId 混搭的情况。

  6.8 请求参数规范
  租户工作侧正式接口禁止在 query/body/path 中重复表达租户上下文。

  明确要求：

  - POST /tenant/members 不再接收 tenantId
  - POST /tenant/members/invitations 不再接收 tenantId
  - GET /tenant/members 不再依赖 tenant_id 筛选当前租户
  - GET /tenant/profile 不接收 path 租户 ID
  - platform/* 路由不声明 X-TENANT-CODE

  如果客户端同时传了 path 租户 ID 和 header 租户 code 来表达同一件事，建议直接返回 400 + B0001，而不是“悄悄取其中一个”。

  6.9 删除与状态流转规范
  规范 9.2 明确指出普通 JSON 接口原则上不使用 204 No Content。规范 17.4 又建议统一软删除能力。

  结合当前 access 实现，本轮建议如下：

  - 成员正式合同不开放硬删除。
  - 成员移除统一用状态动作。
  - 邀请撤销与成员停用统一落在 disable 动作上。
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
  - 因此可以直接以 /api/v1/iam/* 作为新正式合同发布。
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
  - tenant/members 创建与邀请不再接收 tenantId。
  - tenant/users 只读，platform/users 才可增改。
  - tenant/profile 可正常返回当前租户资料。
  - me/invitations/accept、me/invitations/reject 行为与当前业务语义一致。
  - OpenAPI 文档进入 /api/v1/docs/*，不再以 internal/docs 作为正式入口。

  7. 迁移表

  | 现状接口 | 目标接口 | 处理方式 | 备注 |
  | --- | --- | --- | --- |
  | GET /internal/auth/session-status | 下线 | 删除正式合同 | 仅内部 helper，不再公开 |
  | POST /internal/auth/login | POST /api/v1/iam/session/login | 平移 | 会话接口归位 |
  | POST /internal/auth/logout | POST /api/v1/iam/session/logout | 平移 | 会话接口归位 |
  | POST /internal/auth/users/register | POST /api/v1/iam/session/register | 改挂载 | 从 users 归位到 session |
  | POST /internal/auth/users/register/by-phone | POST /api/v1/iam/session/register/by-phone | 改挂载 | 从 users 归位到 session |
  | GET /internal/auth/me/tenants | GET /api/v1/iam/me/tenants | 改挂载 | 当前用户租户列表 |
  | GET /internal/auth/me/permissions | GET /api/v1/iam/me/permissions | 改挂载 | 当前会话权限矩阵 |
  | GET /internal/auth/me/invitations | GET /api/v1/iam/me/invitations | 改挂载 | 当前用户待处理邀请 |
  | POST /internal/auth/me/invitations/reject | POST /api/v1/iam/me/invitations/reject | 改挂载 | 当前用户拒绝邀请 |
  | POST /internal/auth/tenant-members/confirm-invitation | POST /api/v1/iam/me/invitations/accept | 改语义挂载 | 当前用户接受邀请 |
  | GET /internal/auth/users | GET /api/v1/iam/tenant/users 或 GET /api/v1/iam/platform/users | 拆接口 | 旧接口混合租户目录和平台账号视角 |
  | POST /internal/auth/users | POST /api/v1/iam/platform/users | 保留平台侧 | 全局账号创建 |
  | GET /internal/auth/users/{id} | GET /api/v1/iam/tenant/users/{id} 或 GET /api/v1/iam/platform/users/{id} | 拆接口 | 按资源语义拆分 |
  | PUT /internal/auth/users/{id} | PUT /api/v1/iam/platform/users/{id} | 收口到平台侧 | 租户侧不改全局账号 |
  | PATCH /internal/auth/users/{id} | PATCH /api/v1/iam/platform/users/{id} | 收口到平台侧 | 租户侧不改全局账号 |
  | GET /internal/auth/permissions | GET /api/v1/iam/platform/permissions | 改挂载 | 平台目录接口 |
  | GET /internal/auth/roles | GET /api/v1/iam/platform/roles | 改挂载 | 平台目录接口 |
  | GET /internal/auth/roles/{id} | GET /api/v1/iam/platform/roles/{id} | 改挂载 | 平台目录接口 |
  | GET /internal/auth/audit-logs | GET /api/v1/iam/platform/audit-logs 或 GET /api/v1/iam/tenant/audit-logs | 拆接口 | 旧接口为混合日志接
  口 |
  | GET /internal/auth/tenant-audit-logs | GET /api/v1/iam/tenant/audit-logs | 改挂载 | 租户审计正式路径 |
  | GET /internal/auth/tenants | GET /api/v1/iam/tenant/profile 或 GET /api/v1/iam/platform/tenants | 拆接口 | 旧接口混合当前租户和平台租户
  列表语义 |
  | POST /internal/auth/tenants | POST /api/v1/iam/platform/tenants | 改挂载 | 平台创建租户 |
  | GET /internal/auth/tenants/{id} | GET /api/v1/iam/platform/tenants/{id} | 收口到平台侧 | 租户侧当前资料改走 tenant/profile |
  | POST /internal/auth/tenants/{id}/enable | POST /api/v1/iam/platform/tenants/{id}/enable | 改挂载 | 平台租户动作 |
  | POST /internal/auth/tenants/{id}/disable | POST /api/v1/iam/platform/tenants/{id}/disable | 改挂载 | 平台租户动作 |
  | POST /internal/auth/tenants/{id}/initialize-admin | POST /api/v1/iam/platform/tenants/{id}/initialize-admin | 改挂载 | 平台租户动作 |
  | POST /internal/auth/tenants/{id}/set-plan | POST /api/v1/iam/platform/tenants/{id}/set-plan | 改挂载 | 平台租户动作 |
  | GET /internal/auth/tenant-members | GET /api/v1/iam/tenant/members | 改挂载 | 保留租户工作侧 |
  | POST /internal/auth/tenant-members | POST /api/v1/iam/tenant/members | 改挂载并清洗请求体 | 不再接收 tenantId |
  | POST /internal/auth/tenant-members/invite | POST /api/v1/iam/tenant/members/invitations | 改挂载并规范命名 | 不再接收 tenantId |
  | GET /internal/auth/tenant-members/{id} | GET /api/v1/iam/tenant/members/{id} | 改挂载 | 保留租户工作侧 |
  | PUT /internal/auth/tenant-members/{id} | PUT /api/v1/iam/tenant/members/{id} | 改挂载 | 保留租户工作侧 |
  | PATCH /internal/auth/tenant-members/{id} | PATCH /api/v1/iam/tenant/members/{id} | 改挂载 | 保留租户工作侧 |
  | DELETE /internal/auth/tenant-members/{id} | 下线 | 删除正式合同 | 改由 disable 表达状态流转 |
  | POST /internal/auth/tenant-members/{id}/roles | POST /api/v1/iam/tenant/members/{id}/roles | 改挂载 | 保留租户工作侧 |
  | POST /internal/auth/tenant-members/{id}/enable | POST /api/v1/iam/tenant/members/{id}/enable | 改挂载 | 保留租户工作侧 |
  | POST /internal/auth/tenant-members/{id}/disable | POST /api/v1/iam/tenant/members/{id}/disable | 改挂载 | 保留租户工作侧 |

  8. 结论
  这次重构的最终目标不是“把 /internal/auth 换成一个新前缀”这么简单，而是把 access 从历史内部接口形态，收敛成正式、可文档化、可约束、可长期
  维护的 /api/v1/iam 合同。

  最终边界应固定为：

  - session/* 处理登录、登出、注册。
  - me/* 处理当前用户自己的租户、权限、邀请。
  - tenant/* 处理当前租户上下文内工作。
  - platform/* 处理平台层面的租户实体、平台账号、平台目录和平台审计。
  - 租户管理员可以管理自己当前租户内的成员。
  - 平台侧不提供按租户 ID 显式指定目标租户的成员管理 HTTP 接口。
  - 旧 /internal/auth/* 直接退出，不做兼容。
