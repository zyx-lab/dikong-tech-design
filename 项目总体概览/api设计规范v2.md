# 低空平台API 设计规范

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 文档名称 | API 设计规范 |
| 文档版本 | v2 |
| 适用范围 | Django REST Framework + youlai-element-admin |

## 2. 文档目的

本文档用于统一低空平台的接口设计方式，规范前后端在接口命名、请求方式、响应结构、分页规则、错误处理、权限控制等方面的实现标准，降低联调成本，提升系统可维护性与可扩展性。

## 3. 适用范围

本规范适用于以下场景：

- 基于 Django REST Framework 开发的后台业务接口
- 基于 youlai-element-admin 开发的后台管理前端
- 系统内所有新增、改造、重构的业务 API
- 与后台管理系统直接对接的业务模块、基础模块与通用能力模块

以下场景可参考本规范，但允许按专项设计文档单独约定：

- 对外开放平台 API
- 第三方集成接口
- 大文件处理或流式传输接口
- 异步任务中心、消息推送等非典型 CRUD 接口

## 4. 设计原则

### 4.1 一致性原则

系统内所有 API 应保持统一的命名风格、请求方式、响应格式与错误处理策略，避免各模块各自定义接口风格。

### 4.2 资源化原则

接口设计优先采用资源化 URL，普通数据操作使用标准 HTTP 动词表达，不滥用动词式路径。

### 4.3 前后端协同原则

接口设计应优先服务后台管理系统的开发效率与联调体验，保证 youlai-element-admin 可以按统一模式快速接入。

### 4.4 可扩展原则

规范在满足当前后台系统需求的同时，应为后续版本升级、权限扩展、审计追踪、文档生成等提供稳定基础。

### 4.5 安全性原则

所有涉及登录态、权限、数据范围控制的行为，必须以后端校验为准，不依赖前端展示层控制。

## 5. 基础约定

### 5.1 接口基础前缀

所有业务接口统一使用以下前缀：

```http
/api/v1
```

示例：

```http
/api/v1/system/users
/api/v1/system/roles
/api/v1/resource/aircrafts
```

说明：

- `/api` 表示统一接口入口
- `/v1` 表示接口主版本号
- 接口出现不兼容变更时，应通过版本升级解决，不应直接覆盖旧接口语义

### 5.2 数据交换格式

默认约定如下：

- 请求格式：`application/json`
- 响应格式：`application/json`
- 文件上传：`multipart/form-data`

### 5.3 字段命名约定

对前端暴露的 JSON 字段建议统一使用 `camelCase` 命名风格，例如：

- `pageNum`
- `pageSize`
- `createTime`
- `roleIds`

Python 内部命名可继续使用 `snake_case`，通过 Serializer 或转换层完成映射。

如项目决定统一对外使用 `snake_case`，应在全系统范围内保持一致，不得混用。

## 6. URL 设计规范

### 6.1 命名规则

URL 设计应遵循以下规则：

- 使用小写字母
- 多单词使用中划线 `-`
- 使用复数资源名词
- 普通 CRUD 场景中不使用动词路径
- 业务动作接口允许使用明确动词后缀

### 6.2 标准资源接口

以用户管理为例：

```http
GET    /api/v1/system/users
GET    /api/v1/system/users/{id}
POST   /api/v1/system/users
PUT    /api/v1/system/users/{id}
DELETE /api/v1/system/users/{id}
```

### 6.3 批量操作接口

批量操作应采用显式动作接口，不使用万能动作入口。

示例：

```http
POST /api/v1/system/users/batch-delete
POST /api/v1/system/users/batch-update-status
```

### 6.4 单资源业务动作接口

当资源存在明确业务动作时，允许使用“资源 + 动作”的路径形式。

示例：

```http
POST /api/v1/system/users/{id}/reset-password
POST /api/v1/system/users/{id}/restore
POST /api/v1/system/tasks/{id}/submit-approval
```

### 6.5 不推荐写法

以下写法不符合本规范：

```http
/getUserList
/deleteUser
/doAction
/updateStatus
/queryByCondition
```

### 6.6 多租户接口分层规范

本项目采用“租户工作侧用 header、平台管理侧用 path”的分层设计。判断依据不是操作者身份，而是本次请求是在当前租户上下文中工作，还是在平台层直接管理租户实体。

#### 6.6.1 术语定义

- 租户工作侧：在当前租户上下文中管理成员、角色、权限、租户内审计日志及租户内业务资源。
- 平台管理侧：在平台层面直接管理租户实体，如查看租户列表、查询租户详情、启停租户、设置套餐、初始化租户管理员等。

#### 6.6.2 租户工作侧接口规范

租户工作侧接口统一通过请求头传递当前租户上下文，推荐请求头为 `X-TENANT-CODE`。在同一项目中，租户上下文字段必须全局唯一，不得同时混用 `X-TENANT-CODE`、`X-TENANT-ID` 等多种表达方式。

示例：

```http
GET /api/v1/internal/auth/tenant-members?pageNum=1&pageSize=20
X-TENANT-CODE: tenant-a

GET /api/v1/internal/auth/tenant-audit-logs?pageNum=1&pageSize=20&action=INVITE_MEMBER
X-TENANT-CODE: tenant-a
```

约束：

- 该类接口的 URL 不再重复拼接租户标识。
- 普通租户用户不得通过伪造 header 访问其他租户。
- 平台管理员可在具备授权的前提下切换租户上下文，但仍须由后端校验可访问范围（暂不实现）。
- 该类接口的列表、详情、编辑、删除、批量操作均必须按当前租户上下文做强制过滤。

#### 6.6.3 平台管理侧接口规范

平台管理侧接口通过 URL path 显式指定目标租户，目标租户 ID 以路径参数为准，不得依赖 header 隐式指定。

示例：

```http
GET  /api/v1/internal/auth/tenants
GET  /api/v1/internal/auth/tenants/{id}
POST /api/v1/internal/auth/tenants/{id}/enable
POST /api/v1/internal/auth/tenants/{id}/set-plan
```

约束：

- 路径中的 `{id}` 用于唯一确定被管理的租户实体。
- 平台列表页、详情页、启停、套餐配置、初始化管理员等动作统一走 path 模式。
- 即使请求中附带 `X-TENANT-CODE`，也不得用其替代 path 中的目标租户标识。
- 平台管理侧接口仍须校验当前操作者是否具备该租户的管理权限。

#### 6.6.4 禁止混用的写法

以下写法不推荐或禁止：

```http
GET  /api/v1/internal/auth/tenants/{tenantId}/tenant-members

POST /api/v1/internal/auth/tenants/enable
X-TENANT-CODE: tenant-a
```

说明：

- 租户工作侧接口不应同时依赖 path 与 header 共同表达同一租户上下文。
- 平台管理侧接口不应把目标租户隐藏在 header 中。
- 同一类接口一旦确定表达方式，应在全项目保持一致。

## 7. 请求方式规范

### 7.1 标准 HTTP 方法语义

| 方法 | 语义 | 典型用途 |
| --- | --- | --- |
| GET | 查询资源 | 列表、详情、导出 |
| POST | 创建资源或触发明确动作 | 新增、导入、批量操作、业务动作 |
| PUT | 全量更新资源 | 编辑 |
| PATCH | 局部更新资源 | 更新状态、更新部分字段 |
| DELETE | 删除资源 | 软删除语义接口 |

### 7.2 列表查询规范

列表查询统一使用 `GET + query string`。

示例：

```http
GET /api/v1/system/users?pageNum=1&pageSize=20&keywords=admin&status=1
```

说明：

- 普通列表查询不得设计为 `POST /search`
- 简单查询参数应通过 URL 参数传递
- 若未来存在复杂高级检索需求，应另行编写专项规范

## 8. 统一响应结构规范

### 8.1 响应包裹结构

所有普通业务接口统一返回以下结构：

```json
{
  "code": "00000",
  "msg": "success",
  "data": {}
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| code | string | 是 | 业务码，前端以此判断业务是否成功 |
| msg | string | 是 | 响应消息，用于提示或调试 |
| data | object / array / null | 是 | 业务数据主体 |

推荐扩展字段：

```json
{
  "code": "00000",
  "msg": "success",
  "data": {},
  "traceId": "9c1b7d5b8f4f4d7f"
}
```

### 8.2 成功响应示例

详情查询：

```json
{
  "code": "00000",
  "msg": "success",
  "data": {
    "id": 1001,
    "username": "admin",
    "nickname": "系统管理员",
    "status": 1
  }
}
```

列表查询：

```json
{
  "code": "00000",
  "msg": "success",
  "data": {
    "list": [
      {
        "id": 1001,
        "username": "admin",
        "nickname": "系统管理员",
        "status": 1
      }
    ],
    "total": 1
  }
}
```

删除成功：

```json
{
  "code": "00000",
  "msg": "删除成功",
  "data": null
}
```

### 8.3 失败响应示例

参数错误：

```json
{
  "code": "B0001",
  "msg": "参数校验失败",
  "data": {
    "username": ["用户名不能为空"]
  }
}
```

业务冲突：

```json
{
  "code": "C0101",
  "msg": "用户名已存在",
  "data": null
}
```

未登录：

```json
{
  "code": "A0401",
  "msg": "登录状态已失效",
  "data": null
}
```

## 9. HTTP 状态码规范

HTTP 状态码负责协议层语义，业务码负责业务层语义。两者必须同时使用，不可互相替代。

### 9.1 状态码约定

| HTTP 状态码 | 含义 | 使用场景 |
| --- | --- | --- |
| 200 | 请求成功 | 查询、更新、删除、批量操作成功 |
| 201 | 创建成功 | 新增资源成功 |
| 400 | 请求错误 | 参数非法、字段校验失败 |
| 401 | 未认证 | 未登录、Token 无效、Token 过期 |
| 403 | 无权限 | 已登录但无操作权限 |
| 404 | 未找到 | 资源不存在 |
| 409 | 资源冲突 | 唯一键冲突、状态冲突 |
| 500 | 服务端异常 | 未预期系统异常 |

### 9.2 使用要求

- 不得将所有异常统一返回 HTTP 200
- 不得仅返回 HTTP 状态码而不返回业务码
- 文件下载等特殊接口可按实际情况返回文件流，不强制套用统一响应包裹
- 普通 JSON 接口原则上不使用 `204 No Content`

## 10. 分页与列表规范

### 10.1 分页参数

列表分页统一使用以下参数：

- `pageNum`：页码，从 `1` 开始
- `pageSize`：每页条数

推荐默认值：

```text
pageNum = 1
pageSize = 20
```

推荐上限：

```text
pageSize <= 100
```

### 10.2 分页响应结构

分页接口统一返回：

```json
{
  "code": "00000",
  "msg": "success",
  "data": {
    "list": [],
    "total": 0
  }
}
```

字段要求：

- `list` 必须为数组
- `total` 必须为整数
- 查询无结果时返回空数组与正确总数，不得返回 `null`

### 10.3 常用查询参数命名建议

| 参数名 | 含义 |
| --- | --- |
| keywords | 关键字搜索 |
| status | 状态筛选 |
| startTime | 开始时间 |
| endTime | 结束时间 |
| sortBy | 排序字段 |
| sortOrder | 排序方向，取值 `asc` 或 `desc` |

示例：

```http
GET /api/v1/system/users?pageNum=1&pageSize=20&keywords=zhang&status=1&sortBy=createTime&sortOrder=desc
```

## 11. CRUD 规范

### 11.1 新增接口

请求方式：

```http
POST /api/v1/system/users
```

请求体示例：

```json
{
  "username": "zhangsan",
  "nickname": "张三",
  "mobile": "13800000000",
  "status": 1,
  "roleIds": [1, 2]
}
```

要求：

- 仅传递业务可写字段
- 不得由前端传递系统生成字段，如 `id`、`createTime`、`updateTime`、`creator`

### 11.2 详情接口

请求方式：

```http
GET /api/v1/system/users/1001
```

要求：

- 按资源主键查询
- 若资源不存在，返回 `404`
- 若资源已软删除，默认按不存在处理

### 11.3 编辑接口

请求方式：

```http
PUT /api/v1/system/users/1001
```

请求体示例：

```json
{
  "nickname": "张三",
  "mobile": "13800000001",
  "status": 1,
  "roleIds": [1, 3]
}
```

说明：

- `PUT` 用于更新主要业务字段
- 若仅更新单个或少数字段，可使用 `PATCH`

### 11.4 局部更新接口

请求方式：

```http
PATCH /api/v1/system/users/1001
```

请求体示例：

```json
{
  "status": 0
}
```

适用场景：

- 状态切换
- 单字段更新
- 局部属性调整

### 11.5 删除接口

请求方式：

```http
DELETE /api/v1/system/users/1001
```

规范要求：

- 接口语义为删除
- 数据实现采用软删除
- 软删除数据默认不在普通列表与详情中返回
- 如需恢复能力，应单独设计恢复接口

恢复接口示例：

```http
POST /api/v1/system/users/1001/restore
```

## 12. 批量操作规范

批量操作统一采用显式动作接口，不使用聚合型万能命令接口。

### 12.1 批量删除

请求方式：

```http
POST /api/v1/system/users/batch-delete
```

请求体：

```json
{
  "ids": [1001, 1002, 1003]
}
```

响应体：

```json
{
  "code": "00000",
  "msg": "批量删除成功",
  "data": {
    "successCount": 3,
    "failCount": 0
  }
}
```

### 12.2 批量状态更新

请求方式：

```http
POST /api/v1/system/users/batch-update-status
```

请求体：

```json
{
  "ids": [1001, 1002, 1003],
  "status": 0
}
```

### 12.3 批量接口设计要求

- 接口名称必须显式表达动作含义
- 请求体必须包含目标数据集合
- 响应体应返回成功数、失败数，必要时返回失败明细
- 高风险操作必须记录审计日志

## 13. 文件上传、导入、导出规范

### 13.1 文件上传

请求方式：

```http
POST /api/v1/common/upload
```

请求格式：

```text
multipart/form-data
```

响应示例：

```json
{
  "code": "00000",
  "msg": "上传成功",
  "data": {
    "fileName": "avatar.png",
    "url": "https://example.com/files/avatar.png"
  }
}
```

### 13.2 数据导入

请求方式：

```http
POST /api/v1/system/users/import
```

说明：

- 文件字段统一命名为 `file`
- 请求格式为 `multipart/form-data`

响应示例：

```json
{
  "code": "00000",
  "msg": "导入完成",
  "data": {
    "successCount": 80,
    "failCount": 3,
    "failItems": [
      {
        "rowNum": 12,
        "reason": "手机号格式错误"
      }
    ]
  }
}
```

### 13.3 数据导出

请求方式：

```http
GET /api/v1/system/users/export?status=1&keywords=admin
```

要求：

- 导出筛选条件与列表查询条件保持一致
- 导出数据范围为“当前筛选结果”，而非“当前页数据”
- 如导出数据量巨大，应升级为异步导出任务方案

## 14. 错误码规范

### 14.1 业务码设计原则

业务码必须具备以下特征：

- 唯一
- 稳定
- 可检索
- 可文档化
- 可供前端统一处理

### 14.2 推荐业务码分层

| 业务码段 | 含义 |
| --- | --- |
| 00000 | 成功 |
| A0xxx | 认证与权限错误 |
| B0xxx | 请求参数错误 |
| C0xxx | 业务规则错误 |
| D0xxx | 依赖服务错误 |
| E0xxx | 系统内部错误 |

### 14.3 推荐业务码示例

| 业务码 | 说明 |
| --- | --- |
| 00000 | success |
| A0401 | 未登录或登录已失效 |
| A0403 | 无操作权限 |
| B0001 | 参数校验失败 |
| B0002 | 缺少必要参数 |
| B0003 | 参数格式错误 |
| C0101 | 用户名已存在 |
| C0102 | 手机号已存在 |
| C0201 | 当前状态不允许删除 |
| C0202 | 当前数据已被引用，无法删除 |
| D0001 | 第三方服务调用失败 |
| E0001 | 系统异常 |

## 15. 认证与权限规范

### 15.1 认证方式

推荐采用 Bearer Token 方式进行认证。

请求头示例：

```http
Authorization: Bearer {{token}}
```

### 15.2 认证失败处理

| 场景 | HTTP 状态码 | 业务码 |
| --- | --- | --- |
| 未携带 Token | 401 | A0401 |
| Token 无效或过期 | 401 | A0401 |
| 已登录但无权限 | 403 | A0403 |

### 15.3 权限控制要求

- 所有业务接口必须以后端权限校验结果为准
- 页面按钮权限与接口权限应保持一致，但页面控制不能替代后端校验
- 数据权限控制应由后端强制实现，不依赖前端筛选参数

### 15.4 多租户上下文与越权控制

- 租户工作侧接口必须校验 `X-TENANT-CODE` 对应租户是否合法，且属于当前用户可访问范围。
- 平台管理侧接口必须校验 path 中租户 `id` 是否在当前用户的可管理范围内。
- 批量操作中的全部资源 ID 必须属于当前生效租户或当前 path 指定租户，不得跨租户混用。
- 对跨租户越权请求，默认返回 `403`；如需隐藏资源存在性，可在专项规范中统一约定为 `404`，但全系统必须保持一致。
- 审计日志建议同时记录 `actorUserId`、`actorTenantId`、`effectiveTenantCode` 或 `effectiveTenantId`。

## 16. 前端对接约定

本节适用于 youlai-element-admin 的后台前端对接规范。

### 16.1 请求封装约定

前端请求层统一假设后端返回：

```json
{
  "code": "00000",
  "msg": "success",
  "data": {}
}
```

前端响应处理逻辑建议如下：

- 请求自动携带 Token
- 响应先判断 HTTP 状态码
- 再判断业务码 `code`
- 当 `code = "00000"` 时，返回 `data`
- 当 `code != "00000"` 时，统一按业务异常处理
- 对 `401` 统一清理登录态并跳转登录页
- 对 `403` 统一提示无权限
- 租户工作侧页面应在统一请求拦截器中自动注入 `X-TENANT-CODE`
- 平台管理侧页面应通过 path 参数调用 `/tenants/{id}`，不在 header 中重复声明目标租户

### 16.2 列表页对接约定

列表接口统一读取：

- `data.list` 作为表格数据源
- `data.total` 作为分页总数

查询参数统一为：

- `pageNum`
- `pageSize`

这样可保证各管理页使用一致的分页和搜索逻辑。

### 16.3 表单提交约定

- 新增页面调用 `POST`
- 编辑页面调用 `PUT` 或 `PATCH`
- 提交失败时优先读取字段级错误信息
- 普通错误统一展示 `msg`

### 16.4 删除与批量操作约定

统一交互流程建议如下：

- 用户确认操作
- 调用删除或批量动作接口
- 成功后提示操作成功
- 自动刷新列表
- 必要时回退空页码

### 16.5 多租户前端对接约定

- 当前租户切换应维护在全局状态中，并仅影响租户工作侧接口的 header 注入。
- 平台租户列表页、租户详情页、套餐配置页等平台管理页面，应始终以当前选中记录的租户 `id` 作为 path 参数来源。
- 前端不得将租户切换能力暴露给无权限角色，也不得允许用户手工篡改租户 header 后绕过页面限制。
- 当页面同时存在平台管理动作与租户工作动作时，应在 API 方法层明确拆分，不得复用同一个接口封装混合两种调用方式。

## 17. DRF 实现建议

本节为推荐实现方式，不属于协议强制项，但建议项目统一采用。

### 17.1 统一响应封装

建议后端提供统一响应工具：

- `success(data=None, msg="success")`
- `fail(code="E0001", msg="系统异常", data=None)`

要求：

- 所有 View 层统一走响应封装
- 禁止部分接口返回包裹结构、部分接口直接裸返回

### 17.2 全局异常处理

建议通过 DRF 全局异常处理器，将常见异常统一映射。

推荐映射关系：

- `ValidationError` -> `400 + B0001`
- `NotAuthenticated` -> `401 + A0401`
- `PermissionDenied` -> `403 + A0403`
- `NotFound` -> `404 + {{资源不存在业务码}}`
- 未捕获异常 -> `500 + E0001`

### 17.3 自定义分页类

建议自定义 DRF 分页类，实现以下统一能力：

- 接收 `pageNum`
- 接收 `pageSize`
- 返回 `list`
- 返回 `total`

避免直接暴露 DRF 默认结构：

```json
{
  "count": 100,
  "next": null,
  "previous": null,
  "results": []
}
```

### 17.4 软删除实现建议

建议通过模型基类或统一逻辑支持以下字段：

- `isDeleted` 或 `deletedAt`
- `deletedBy`
- `deletedTime`

实现要求：

- 默认查询过滤已删除数据
- 详情接口默认不可访问已删除数据
- 唯一索引设计需考虑软删除场景

### 17.5 分层建议

推荐采用以下职责分层：

- View 层：接收请求与返回响应
- Serializer 层：参数校验与序列化
- Service 层：业务逻辑实现
- Model 层：数据持久化

原则上不应将复杂业务逻辑直接堆积在 ViewSet 中。

### 17.6 多租户上下文解析建议

建议在 DRF 中统一实现当前租户解析与授权校验，避免在各个 ViewSet 中重复手写。

推荐做法：

- 租户工作侧接口：从 `X-TENANT-CODE` 与当前登录用户解析 `effectiveTenant`。
- 平台管理侧接口：从 path 参数 `{id}` 解析 `targetTenant`，并校验当前用户是否具备平台管理权限。
- `get_queryset`、Serializer 校验、Service 层、批量任务统一使用 `effectiveTenant` 或 `targetTenant` 做过滤与校验。
- 审计日志、操作日志、异步任务日志应显式记录当前操作者与生效租户，便于排查跨租户问题。

## 18. 接口文档要求

每个接口至少应包含以下内容：

- 接口名称
- 接口说明
- 请求路径
- 请求方法
- 权限标识
- 请求参数说明
- 请求示例
- 响应示例
- 错误码说明
- 备注信息

分页接口还应补充：

- 分页参数说明
- 支持的筛选字段
- 支持的排序字段
- 导出是否复用同一套筛选条件

## 19. 版本管理与兼容性要求

### 19.1 破坏性变更定义

以下情况视为破坏性变更：

- 修改已有字段名称
- 修改已有字段类型
- 修改已有业务码语义
- 修改分页返回结构
- 修改既有接口路径语义
- 将原本可选字段调整为必填字段

### 19.2 破坏性变更处理原则

破坏性变更原则上应通过新版本路径发布，例如：

```http
/api/v2/...
```

### 19.3 兼容性变更范围

以下情况可视为兼容性变更：

- 新增可选字段
- 新增非必填筛选参数
- 新增不影响旧逻辑的业务码
- 新增业务动作接口

## 20. 标准接口示例

### 20.1 通用资源接口示例

以“用户管理”为例，标准接口建议如下：

```http
GET    /api/v1/system/users
GET    /api/v1/system/users/{id}
POST   /api/v1/system/users
PUT    /api/v1/system/users/{id}
PATCH  /api/v1/system/users/{id}
DELETE /api/v1/system/users/{id}
POST   /api/v1/system/users/batch-delete
POST   /api/v1/system/users/batch-update-status
POST   /api/v1/system/users/{id}/reset-password
POST   /api/v1/system/users/import
GET    /api/v1/system/users/export
```

列表查询示例：

```http
GET /api/v1/system/users?pageNum=1&pageSize=20&keywords=admin&status=1
```

列表响应示例：

```json
{
  "code": "00000",
  "msg": "success",
  "data": {
    "list": [
      {
        "id": 1001,
        "username": "admin",
        "nickname": "系统管理员",
        "status": 1,
        "createTime": "2026-03-16 10:00:00"
      }
    ],
    "total": 1
  }
}
```

批量删除请求示例：

```json
{
  "ids": [1001, 1002]
}
```

### 20.2 多租户接口示例

租户工作侧接口示例：

```http
GET /api/v1/internal/auth/tenant-members?pageNum=1&pageSize=20&status=ACTIVE
X-TENANT-CODE: tenant-a

GET /api/v1/internal/auth/tenant-audit-logs?pageNum=1&pageSize=20&action=INVITE_MEMBER
X-TENANT-CODE: tenant-a
```

平台管理侧接口示例：

```http
GET  /api/v1/internal/auth/tenants?pageNum=1&pageSize=20&keywords=demo
GET  /api/v1/internal/auth/tenants/1001
POST /api/v1/internal/auth/tenants/1001/enable
POST /api/v1/internal/auth/tenants/1001/set-plan
```

设计说明：

- 租户工作侧接口表达的是“当前租户上下文中的工作”，因此使用 header 更适合统一前端请求封装。
- 平台管理侧接口表达的是“直接管理某个租户实体”，因此使用 path 更利于文档表达、权限校验和审计追踪。


