# 低空智能巡检平台 - 租户

## 数据库

PostgreSQL

---

## 阅读说明

本数据字典覆盖当前已落地的多租户权限核心表：

- `tenants`
- `tenant_members`
- `roles`
- `permissions`
- `role_permission_grants`
- `qualification_types`
- `tenant_member_roles`
- `tenant_member_qualifications`

---

## 1. tenants

**说明**：租户主表，平台级数据。

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 主键 |
| code | varchar(64) | NOT NULL, UNIQUE | - | 租户编码 |
| name | varchar(128) | NOT NULL | - | 租户名称 |
| status | smallint | NOT NULL | 1 | 0=DISABLED, 1=ACTIVE |
| plan | varchar(64) | 可空字符串 | '' | 套餐标识 |
| remark | varchar(500) | 可空字符串 | '' | 备注 |
| created_at | timestamp | NOT NULL | now() | 创建时间 |
| updated_at | timestamp | NOT NULL | now() | 更新时间 |

**业务规则**：
1. `code` 全局唯一。
2. `status=DISABLED` 时，该租户所有租户态接口都不可通过 `X-Tenant-Code` 进入。

---

## 2. tenant_members

**说明**：某个 `User` 在某个租户中的成员关系，同时承载邀请生命周期。

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 主键 |
| tenant_id | bigint | NOT NULL, FK -> tenants.id | - | 所属租户 |
| user_id | bigint | NOT NULL, FK -> auth_users.id | - | 平台账号 |
| member_no | varchar(64) | 可空 | null | 租户内工号 |
| display_name | varchar(128) | 可空字符串 | '' | 租户内显示名 |
| invitation_token | varchar(64) | 可空，非空时唯一 | null | 邀请令牌 |
| invited_by_user_id | bigint | 可空, FK -> auth_users.id | null | 邀请人 |
| invited_at | timestamp | 可空 | null | 邀请发出时间 |
| expires_at | timestamp | 可空 | null | 邀请过期时间 |
| responded_at | timestamp | 可空 | null | 用户响应时间 |
| joined_at | timestamp | 可空 | null | 成员加入时间 |
| status | smallint | NOT NULL | 0 | 成员状态 |
| created_at | timestamp | NOT NULL | now() | 创建时间 |
| updated_at | timestamp | NOT NULL | now() | 更新时间 |

**status 状态值**：

| 值 | 含义 |
|----|------|
| 0 | INVITED |
| 1 | ACTIVE |
| 2 | REJECTED |
| 3 | EXPIRED |
| 4 | REVOKED |
| 5 | DISABLED |

**业务规则**：
1. `(tenant_id, user_id)` 唯一。
2. `(tenant_id, member_no)` 仅在 `member_no` 非空且非空字符串时唯一。
3. `invitation_token` 仅在非空且非空字符串时唯一。
4. `INVITED` 必须有 `invitation_token / invited_at / expires_at`。
5. `ACTIVE` 不保留邀请元数据，且可分配 `member_no`。

---

## 3. roles

**说明**：平台统一角色目录，统一承载业务岗位与管理职责。

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 主键 |
| code | varchar(64) | NOT NULL, UNIQUE | - | 角色编码 |
| name | varchar(128) | NOT NULL | - | 角色名称 |
| description | varchar(500) | 可空字符串 | '' | 描述 |
| status | smallint | NOT NULL | 1 | 0=DISABLED, 1=ACTIVE |
| created_at | timestamp | NOT NULL | now() | 创建时间 |
| updated_at | timestamp | NOT NULL | now() | 更新时间 |

**业务规则**：
1. `code` 发布后保持稳定。
2. 平台管理员维护角色目录，租户管理员不可修改。

---

## 4. permissions

**说明**：平台统一权限目录。

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 主键 |
| code | varchar(128) | NOT NULL, UNIQUE | - | 权限编码 |
| name | varchar(128) | NOT NULL | - | 权限名称 |
| module | varchar(64) | NOT NULL | - | 模块名 |
| resource_code | varchar(64) | 可空字符串 | '' | 细粒度资源编码 |
| description | varchar(500) | 可空字符串 | '' | 描述 |
| status | smallint | NOT NULL | 1 | 0=DISABLED, 1=ACTIVE |
| created_at | timestamp | NOT NULL | now() | 创建时间 |
| updated_at | timestamp | NOT NULL | now() | 更新时间 |

**业务规则**：
1. 目录只存平台级权限定义，不直接承载租户运行态授权结果。
2. 权限是否开放给某角色，由 `role_permission_grants` 决定。

---

## 5. role_permission_grants

**说明**：角色默认权限映射。

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 主键 |
| role_id | bigint | NOT NULL, FK -> roles.id | - | 角色 |
| permission_id | bigint | NOT NULL, FK -> permissions.id | - | 权限 |
| scope_type | varchar(16) | NOT NULL | ALL | 数据范围 |
| created_at | timestamp | NOT NULL | now() | 创建时间 |
| updated_at | timestamp | NOT NULL | now() | 更新时间 |

**scope_type 枚举**：

| 值 | 含义 |
|----|------|
| ALL | 全量范围 |
| OWN | 自建资源 |
| ASSIGNED | 分配到自己的资源 |

**业务规则**：
1. `(role_id, permission_id)` 唯一。
2. 多角色命中同一权限时按 `ALL > ASSIGNED > OWN` 合并。

---

## 6. qualification_types

**说明**：平台统一资质类型目录。

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 主键 |
| code | varchar(64) | NOT NULL, UNIQUE | - | 资质类型编码 |
| name | varchar(128) | NOT NULL | - | 资质类型名称 |
| description | varchar(500) | 可空字符串 | '' | 描述 |
| requires_validity | boolean | NOT NULL | false | 是否要求有效期 |
| payload_schema_json | jsonb | 可空 | null | 扩展字段 schema |
| status | smallint | NOT NULL | 1 | 0=DISABLED, 1=ACTIVE |
| created_at | timestamp | NOT NULL | now() | 创建时间 |
| updated_at | timestamp | NOT NULL | now() | 更新时间 |

---

## 7. tenant_member_roles

**说明**：成员绑定角色的实际运行数据。

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 主键 |
| tenant_member_id | bigint | NOT NULL, FK -> tenant_members.id | - | 成员 |
| system_role_id | bigint | NOT NULL, FK -> roles.id | - | 角色（当前代码字段名沿用 `system_role_id`） |
| status | smallint | NOT NULL | 1 | 0=REVOKED, 1=GRANTED |
| assigned_by_user_id | bigint | 可空, FK -> auth_users.id | null | 分配人 |
| assigned_at | timestamp | 可空 | null | 分配时间 |
| created_at | timestamp | NOT NULL | now() | 创建时间 |
| updated_at | timestamp | NOT NULL | now() | 更新时间 |

**业务规则**：
1. `(tenant_member_id, system_role_id)` 唯一。
2. `INVITED` 成员允许预绑定角色，但不参与实际授权。
3. 角色同步接口会把未出现在最新 `role_codes` 中的绑定改成 `REVOKED`。

**scope 规则补充**：
1. `OWN / ASSIGNED` 只允许用于代码已经实现资源判定的权限。
2. 不需要细粒度判定的权限统一使用 `ALL`。

---

## 8. tenant_member_qualifications

**说明**：成员实际持有的资质记录。

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 主键 |
| tenant_member_id | bigint | NOT NULL, FK -> tenant_members.id | - | 成员 |
| qualification_type_id | bigint | NOT NULL, FK -> qualification_types.id | - | 资质类型 |
| certificate_no | varchar(128) | 可空字符串 | '' | 证书编号 |
| level | varchar(64) | 可空字符串 | '' | 等级 |
| status | smallint | NOT NULL | 1 | 1=ACTIVE, 2=INVALID, 3=REVOKED |
| issued_at | date | 可空 | null | 发证日期 |
| valid_from | date | 可空 | null | 生效日期 |
| valid_until | date | 可空 | null | 到期日期 |
| issuer | varchar(128) | 可空字符串 | '' | 发证机构 |
| payload_json | jsonb | NOT NULL | `{}` | 扩展信息 |
| created_at | timestamp | NOT NULL | now() | 创建时间 |
| updated_at | timestamp | NOT NULL | now() | 更新时间 |

**业务规则**：
1. 允许同一成员同一资质类型存在多条记录。
2. `requires_validity=true` 时必须提供 `valid_from / valid_until`，且 `valid_from <= valid_until`。
3. 结构非法或缺少关键字段时应写成 `INVALID`，不参与业务资格判定。
