# 低空平台 - 数据字典

## 数据库

PostgreSQL

---

## 1. auth_users（账号表）

**说明**：平台登录账号主体

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 账号 ID |
| username | varchar(150) | NOT NULL, UNIQUE | - | 登录账号 |
| is_staff | boolean | NOT NULL | false | 是否可登录 Django Admin |
| is_active | boolean | NOT NULL | true | Django 账户启用状态 |
| is_superuser | boolean | NOT NULL | false | 技术 Root 标记 |
| is_platform_admin | boolean | NOT NULL | false | 是否平台工作态账号 |
| status | smallint | NOT NULL | 1 | 业务账号状态 |
| last_login | timestamp | - | - | 最近登录时间 |
| created_at | timestamp | - | now() | 创建时间 |
| updated_at | timestamp | - | now() | 更新时间 |

**status 状态值**：
| 值 | 含义 |
|----|------|
| 0 | disabled |
| 1 | active |

---

## 2. auth_sessions（认证会话表）

**说明**：正式 Bearer Token 会话

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 会话 ID |
| user_id | bigint | FK, NOT NULL | - | 关联账号 |
| session_type | varchar(16) | NOT NULL | - | 会话类型 |
| access_token_hash | varchar(64) | NOT NULL, UNIQUE | - | access token 哈希 |
| refresh_token_hash | varchar(64) | NOT NULL, UNIQUE | - | refresh token 哈希 |
| access_token_expires_at | timestamp | NOT NULL | - | access token 过期时间 |
| refresh_token_expires_at | timestamp | NOT NULL | - | refresh token 过期时间 |
| revoked_at | timestamp | - | - | 撤销时间 |
| last_refreshed_at | timestamp | - | - | 最近刷新时间 |
| last_used_at | timestamp | - | - | 最近使用时间 |
| created_ip | varchar / inet | - | - | 创建 IP |
| last_used_ip | varchar / inet | - | - | 最近使用 IP |
| user_agent | varchar(255) | NOT NULL | '' | 客户端标识 |
| created_at | timestamp | - | now() | 创建时间 |
| updated_at | timestamp | - | now() | 更新时间 |

**session_type 会话类型**：
| 值 | 含义 |
|----|------|
| BUSINESS | 业务账号工作态 |
| PLATFORM | 平台管理员工作态 |

---

## 3. staff_profiles（人员档案表）

**说明**：账号绑定的全局人员档案

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 人员档案 ID |
| user_id | bigint | FK, NOT NULL, UNIQUE | - | 关联账号（1:1） |
| name | varchar(64) | NOT NULL | - | 姓名 |
| phone | varchar(32) | - | - | 手机号 |
| email | varchar(254) | - | - | 邮箱 |
| employment_status | smallint | NOT NULL | 1 | 在职状态 |
| org_id | bigint | - | - | 组织 ID |
| created_at | timestamp | - | now() | 创建时间 |
| updated_at | timestamp | - | now() | 更新时间 |

**employment_status 状态值**：
| 值 | 含义 |
|----|------|
| 0 | inactive |
| 1 | active |

---

## 4. tenants（租户表）

**说明**：平台治理的租户主实体

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 租户 ID |
| code | varchar(64) | NOT NULL, UNIQUE | - | 租户编码 |
| name | varchar(128) | NOT NULL | - | 租户名称 |
| status | smallint | NOT NULL | 1 | 状态 |
| plan | varchar(64) | NOT NULL | '' | 套餐 |
| remark | varchar(500) | NOT NULL | '' | 备注 |
| created_at | timestamp | - | now() | 创建时间 |
| updated_at | timestamp | - | now() | 更新时间 |

**status 状态值**：
| 值 | 含义 |
|----|------|
| 0 | disabled |
| 1 | active |

---

## 5. roles（平台角色目录）

**说明**：平台统一角色目录

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 角色 ID |
| code | varchar(64) | NOT NULL, UNIQUE | - | 角色编码 |
| name | varchar(128) | NOT NULL | - | 角色名称 |
| description | varchar(500) | NOT NULL | '' | 角色描述 |
| status | smallint | NOT NULL | 1 | 目录状态 |
| created_at | timestamp | - | now() | 创建时间 |
| updated_at | timestamp | - | now() | 更新时间 |

**当前种子角色**：
| code | name |
|------|------|
| platform_admin | 平台管理员 |
| tenant_admin | 租户管理员 |
| business_admin | 业务管理员 |
| route_planner | 航线规划员 |
| dispatcher | 任务调度员 |
| pilot_operator | 飞手操作员 |
| auditor | 审计员 |

---

## 6. permissions（平台权限目录）

**说明**：正式授权使用的权限目录

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 权限 ID |
| code | varchar(128) | NOT NULL, UNIQUE | - | 权限编码 |
| name | varchar(128) | NOT NULL | - | 权限名称 |
| module | varchar(64) | NOT NULL | - | 模块名 |
| resource_code | varchar(64) | NOT NULL | '' | 资源编码 |
| description | varchar(500) | NOT NULL | '' | 描述 |
| status | smallint | NOT NULL | 1 | 目录状态 |
| created_at | timestamp | - | now() | 创建时间 |
| updated_at | timestamp | - | now() | 更新时间 |

---

## 7. role_permission_grants（角色权限映射表）

**说明**：角色到权限与 scope 的基线映射

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 映射 ID |
| role_id | bigint | FK, NOT NULL | - | 角色 ID |
| permission_id | bigint | FK, NOT NULL | - | 权限 ID |
| scope_type | varchar(16) | NOT NULL | ALL | 数据范围 |
| created_at | timestamp | - | now() | 创建时间 |
| updated_at | timestamp | - | now() | 更新时间 |

**scope_type 范围值**：
| 值 | 含义 |
|----|------|
| ALL | 当前作用域全部数据 |
| OWN | 当前成员自己创建/拥有的数据 |
| ASSIGNED | 当前成员被分配到的数据 |

---

## 8. qualification_types（资质类型表）

**说明**：平台统一资质类型字典

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 资质类型 ID |
| code | varchar(64) | NOT NULL, UNIQUE | - | 资质编码 |
| name | varchar(128) | NOT NULL | - | 资质名称 |
| description | varchar(500) | NOT NULL | '' | 资质描述 |
| requires_validity | boolean | NOT NULL | false | 是否要求有效期 |
| payload_schema_json | json | - | - | 扩展字段 schema |
| status | smallint | NOT NULL | 1 | 目录状态 |
| created_at | timestamp | - | now() | 创建时间 |
| updated_at | timestamp | - | now() | 更新时间 |

---

## 9. tenant_members（租户成员关系表）

**说明**：账号进入租户后的成员主体

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 成员关系 ID |
| tenant_id | bigint | FK, NOT NULL | - | 所属租户 |
| user_id | bigint | FK, NOT NULL | - | 账号 ID |
| member_no | varchar(64) | - | - | 租户内工号 |
| display_name | varchar(128) | NOT NULL | '' | 显示名称 |
| invitation_token | varchar(64) | - | - | 邀请令牌 |
| invited_by_user_id | bigint | FK | - | 邀请人账号 |
| invited_at | timestamp | - | - | 邀请时间 |
| expires_at | timestamp | - | - | 过期时间 |
| responded_at | timestamp | - | - | 响应时间 |
| joined_at | timestamp | - | - | 加入时间 |
| status | smallint | NOT NULL | 0 | 成员状态 |
| created_at | timestamp | - | now() | 创建时间 |
| updated_at | timestamp | - | now() | 更新时间 |

**status 状态值**：
| 值 | 含义 |
|----|------|
| 0 | invited |
| 1 | active |
| 2 | rejected |
| 3 | expired |
| 4 | revoked |
| 5 | disabled |

---

## 10. tenant_member_roles（成员角色绑定表）

**说明**：租户成员与平台角色的绑定

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 绑定 ID |
| tenant_member_id | bigint | FK, NOT NULL | - | 成员关系 ID |
| system_role_id | bigint | FK, NOT NULL | - | 角色 ID |
| status | smallint | NOT NULL | 1 | 绑定状态 |
| assigned_by_user_id | bigint | FK | - | 分配人账号 |
| assigned_at | timestamp | - | - | 分配时间 |
| created_at | timestamp | - | now() | 创建时间 |
| updated_at | timestamp | - | now() | 更新时间 |

**status 状态值**：
| 值 | 含义 |
|----|------|
| 0 | revoked |
| 1 | granted |

---

## 11. tenant_member_qualifications（成员资质记录表）

**说明**：租户成员资质信息

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 资质记录 ID |
| tenant_member_id | bigint | FK, NOT NULL | - | 成员关系 ID |
| qualification_type_id | bigint | FK, NOT NULL | - | 资质类型 ID |
| certificate_no | varchar(128) | NOT NULL | '' | 证书编号 |
| level | varchar(64) | NOT NULL | '' | 等级 |
| status | smallint | NOT NULL | 1 | 资质状态 |
| issued_at | date | - | - | 发证日期 |
| valid_from | date | - | - | 有效开始日期 |
| valid_until | date | - | - | 有效截止日期 |
| issuer | varchar(128) | NOT NULL | '' | 发证机构 |
| payload_json | json | NOT NULL | {} | 扩展信息 |
| created_at | timestamp | - | now() | 创建时间 |
| updated_at | timestamp | - | now() | 更新时间 |

**status 状态值**：
| 值 | 含义 |
|----|------|
| 1 | active |
| 2 | invalid |
| 3 | revoked |

---

## 12. auth_audit_logs（审计日志表）

**说明**：平台级 / 租户级关键动作审计

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 日志 ID |
| tenant_id | bigint | FK | - | 所属租户 |
| actor_user_id | bigint | FK | - | 操作人账号 |
| action | varchar(128) | NOT NULL | - | 动作码 |
| target_type | varchar(128) | NOT NULL | - | 目标类型 |
| target_id | varchar(64) | NOT NULL | '' | 目标主键 |
| before_data | json | - | - | 变更前快照 |
| after_data | json | - | - | 变更后快照 |
| ip | varchar(39) | - | - | 请求 IP |
| request_id | varchar(64) | NOT NULL | '' | 请求追踪 ID |
| created_at | timestamp | - | now() | 创建时间 |

---

## 13. drones（无人机表）

**说明**：租户内无人机台账

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 无人机 ID |
| tenant_id | bigint | FK, NOT NULL | - | 所属租户 |
| code | varchar(64) | NOT NULL | - | 租户内业务编码 |
| name | varchar(128) | NOT NULL | - | 无人机名称 |
| model | varchar(128) | NOT NULL | - | 型号 |
| device_sn | varchar(128) | NOT NULL | - | 设备序列号 |
| status | varchar(16) | NOT NULL | DISABLED | 状态 |
| org_id | bigint | - | - | 组织 ID |
| created_by_tenant_member_id | bigint | - | - | 创建人 TenantMember ID |
| created_at | timestamp | - | now() | 创建时间 |
| updated_at | timestamp | - | now() | 更新时间 |

**status 状态值**：
| 值 | 含义 |
|----|------|
| ENABLED | 启用 |
| DISABLED | 停用 |

---

## 14. drone_assignments（无人机分配表）

**说明**：无人机与飞手成员的分配关系

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 分配 ID |
| tenant_id | bigint | FK, NOT NULL | - | 所属租户 |
| drone_id | bigint | FK, NOT NULL | - | 无人机 ID |
| tenant_member_id | bigint | FK, NOT NULL | - | 飞手成员 ID |
| status | varchar(16) | NOT NULL | ACTIVE | 分配状态 |
| start_at | timestamp | - | now() | 开始时间 |
| end_at | timestamp | - | - | 结束时间 |
| created_by_tenant_member_id | bigint | - | - | 创建人 TenantMember ID |
| created_at | timestamp | - | now() | 创建时间 |
| updated_at | timestamp | - | now() | 更新时间 |

**status 状态值**：
| 值 | 含义 |
|----|------|
| ACTIVE | 生效中 |
| INACTIVE | 已失效 |

---

## 15. routes（航线表）

**说明**：租户内 route 主表

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 航线 ID |
| tenant_id | bigint | FK, NOT NULL | - | 所属租户 |
| name | varchar(100) | NOT NULL | - | 航线名称 |
| created_at | timestamp | - | now() | 创建时间 |
| updated_at | timestamp | - | now() | 更新时间 |

**业务规则**：
1. 当前设计不再使用 `Route.status`。
2. `Route` 是公开聚合根，写入链路只接受 `kmz_file` 直传并立即同步 DJI。
3. 删除航线时，若存在 `PENDING / RUNNING / PAUSED` 任务引用，则拒绝删除；否则删除 route 和残留 waypoint 行。

---

## 16. waypoints（航点表）

**说明**：Route 聚合内部航点表

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 航点 ID |
| route_id | bigint | FK, NOT NULL | - | 所属航线 |
| sequence | integer | NOT NULL | - | 航点序号 |
| latitude | numeric(12,8) | NOT NULL | - | 纬度 |
| longitude | numeric(12,8) | NOT NULL | - | 经度 |
| altitude | numeric(10,2) | NOT NULL | - | 飞行高度（米） |
| created_at | timestamp | - | now() | 创建时间 |

**索引**：
| 索引名 | 字段 | 类型 |
|--------|------|------|
| waypoints_route_seq_unique | (route_id, sequence) | UNIQUE |

**业务规则**：
1. `waypoints` 不再是独立业务资源。
2. 当前没有公开 `/api/v1/waypoints*` 接口。
3. 当前 route 创建与更新接口不再写入 `waypoints`；该表仅保留历史内部行。
4. route 删除接口会先清理残留 `waypoints` 行，再删除 route。

---

## 17. missions（任务表）

**说明**：巡检任务主记录

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 任务 ID |
| tenant_id | bigint | FK, NOT NULL | - | 所属租户 |
| name | varchar(100) | NOT NULL | - | 任务名称 |
| route_id | bigint | FK | - | 任务航线；route 删除后可为空 |
| route_name | varchar(100) | NOT NULL | '' | 航线名称（冗余） |
| drone_id | bigint | FK, NOT NULL | - | 执行无人机 |
| drone_name | varchar(100) | NOT NULL | '' | 无人机名称（冗余） |
| pilot_id | bigint | FK, NOT NULL | - | 执行飞手成员 |
| pilot_name | varchar(50) | NOT NULL | '' | 飞手姓名（冗余） |
| scheduled_at | timestamp | - | - | 计划执行时间 |
| remark | varchar(500) | NOT NULL | '' | 任务备注 |
| status | smallint | NOT NULL | 0 | 任务状态 |
| dji_job_id | varchar(128) | NOT NULL | '' | DJI 任务 ID |
| created_at | timestamp | - | now() | 创建时间 |
| updated_at | timestamp | - | now() | 更新时间 |

**status 状态值**：
| 值 | 含义 |
|----|------|
| 0 | 待执行 |
| 1 | 执行中 |
| 2 | 已暂停 |
| 3 | 已完成 |
| 4 | 已取消 |
| 5 | 执行失败 |

**业务规则**：
1. 创建任务时 `route`、`drone`、`pilot` 必须属于当前租户。
2. 创建任务时 `route` 必须已绑定最近一次成功上传的 DJI 航线；不再要求 `drone` 处于 `ENABLED`。
3. `pilot` 必须是当前租户下的 `ACTIVE TenantMember`，其账号需存在在职 `staff_profile`，且成员已绑定 `pilot_operator`。
4. `status` 不可通过 PATCH 直接修改；本地 `start / pause / resume / complete / fail` 动作接口已删除。

---

## 18. flight_records（飞行记录表）

**说明**：飞行执行过程记录

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 记录 ID |
| tenant_id | bigint | FK, NOT NULL | - | 所属租户 |
| flight_no | varchar(50) | NOT NULL | - | 架次编号 |
| mission_id | bigint | FK | - | 所属任务 |
| mission_name | varchar(100) | NOT NULL | '' | 任务名称（冗余） |
| route_name | varchar(100) | NOT NULL | '' | 航线名称（冗余） |
| airport_name | varchar(100) | NOT NULL | '' | 执行机场名称 |
| drone_id | bigint | FK | - | 执行无人机 |
| drone_name | varchar(100) | NOT NULL | '' | 无人机名称（冗余） |
| pilot_id | bigint | FK | - | 执行飞手成员 |
| pilot_name | varchar(50) | NOT NULL | '' | 飞手姓名（冗余） |
| start_time | timestamp | - | - | 开始时间 |
| end_time | timestamp | - | - | 结束时间 |
| flight_duration | integer | - | - | 飞行时长（秒） |
| photo_count | integer | NOT NULL | 0 | 拍摄照片数量 |
| video_count | integer | NOT NULL | 0 | 录制视频数量 |
| status | smallint | NOT NULL | 0 | 飞行状态 |
| created_at | timestamp | - | now() | 创建时间 |
| updated_at | timestamp | - | now() | 更新时间 |

**status 状态值**：
| 值 | 含义 |
|----|------|
| 0 | 飞行中 |
| 1 | 已完成 |
| 2 | 异常终止 |

---

## 19. media_files（媒体文件表）

**说明**：飞行过程产生的图片与视频

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 媒体 ID |
| tenant_id | bigint | FK, NOT NULL | - | 所属租户 |
| flight_record_id | bigint | FK | - | 关联飞行记录 |
| media_type | smallint | NOT NULL | - | 媒体类型 |
| file_name | varchar(255) | NOT NULL | - | 文件名 |
| file_url | varchar(500) | NOT NULL | - | 文件 URL |
| thumbnail_url | varchar(500) | NOT NULL | '' | 缩略图 URL |
| file_size | bigint | - | - | 文件大小（字节） |
| latitude | numeric(12,8) | - | - | 拍摄位置-纬度 |
| longitude | numeric(12,8) | - | - | 拍摄位置-经度 |
| captured_at | timestamp | - | - | 拍摄时间 |
| is_deleted | boolean | NOT NULL | false | 是否已删除 |
| deleted_at | timestamp | - | - | 删除时间 |
| created_at | timestamp | - | now() | 创建时间 |

**media_type 媒体类型**：
| 值 | 含义 |
|----|------|
| 1 | 照片 |
| 2 | 视频 |

---

## 20. dji_workspace_configs（DJI 工作空间配置表）

**说明**：系统托管的 DJI workspace / user 会话配置。

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 主键 |
| workspace_id | varchar(128) | NOT NULL, UNIQUE | - | DJI workspace ID |
| dji_user_id | varchar(128) | NOT NULL | '' | DJI user ID |
| dji_username | varchar(128) | NOT NULL | '' | DJI 用户名 |
| dji_user_type | varchar(64) | NOT NULL | '' | DJI 用户类型 |
| access_token | varchar(512) | NOT NULL | '' | 访问令牌 |
| mqtt_username | varchar(128) | NOT NULL | '' | MQTT 用户名 |
| mqtt_password | varchar(256) | NOT NULL | '' | MQTT 密码 |
| mqtt_addr | varchar(256) | NOT NULL | '' | MQTT 地址 |
| expires_at | timestamp | - | - | 访问令牌过期时间 |
| created_at | timestamp | NOT NULL | now() | 创建时间 |
| updated_at | timestamp | NOT NULL | now() | 更新时间 |

---

## 21. dji_device_indexes（DJI 设备索引表）

**说明**：共享设备池快照。

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 主键 |
| device_sn | varchar(128) | NOT NULL, UNIQUE | - | 设备序列号 |
| last_payload | json | NOT NULL | {} | 最近一次 DJI 原始载荷 |
| last_seen_at | timestamp | - | - | 最近见到时间 |
| firmware_version | varchar(128) | NOT NULL | '' | 固件版本 |
| firmware_status | varchar(64) | NOT NULL | '' | 固件状态 |
| created_at | timestamp | NOT NULL | now() | 创建时间 |
| updated_at | timestamp | NOT NULL | now() | 更新时间 |

---

## 22. tenant_route_indexes（航线发布索引表）

**说明**：route 与 DJI 航线的最小发布映射。

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 主键 |
| tenant_id | bigint | FK, NOT NULL | - | 所属租户 |
| route_id | bigint | FK, NOT NULL, UNIQUE | - | 对应 route |
| dji_wayline_id | varchar(128) | NOT NULL | '' | DJI 航线 ID |
| download_url | varchar(500) | NOT NULL | '' | 最近一次成功上传返回的 DJI 航线下载地址 |
| is_published | boolean | NOT NULL | false | 当前 route 是否已绑定最近一次成功上传的 DJI 航线 |
| created_at | timestamp | NOT NULL | now() | 创建时间 |
| updated_at | timestamp | NOT NULL | now() | 更新时间 |

---

## 23. tenant_mission_indexes（任务同步索引表）

**说明**：mission 与 DJI job 的一对一映射。

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 主键 |
| tenant_id | bigint | FK, NOT NULL | - | 所属租户 |
| mission_id | bigint | FK, NOT NULL, UNIQUE | - | 对应任务 |
| dji_job_id | varchar(128) | NOT NULL | - | DJI 任务 ID |
| execution_status | varchar(64) | NOT NULL | '' | 最近一次同步到的 DJI 执行状态原文 |
| sync_status | varchar(32) | NOT NULL | PENDING | 同步状态 |
| last_sync_at | timestamp | - | - | 最近同步时间 |
| error_msg | varchar(255) | NOT NULL | '' | 同步错误 |
| created_at | timestamp | NOT NULL | now() | 创建时间 |
| updated_at | timestamp | NOT NULL | now() | 更新时间 |

---

## 24. tenant_media_indexes（媒体同步索引表）

**说明**：media_file 与 DJI 文件的一对一映射。

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 主键 |
| tenant_id | bigint | FK, NOT NULL | - | 所属租户 |
| media_file_id | bigint | FK, NOT NULL, UNIQUE | - | 对应媒体文件 |
| dji_file_id | varchar(128) | NOT NULL | - | DJI 文件 ID |
| device_sn | varchar(128) | NOT NULL | '' | 设备序列号 |
| mission_id | bigint | FK | - | 关联任务 ID |
| sync_status | varchar(32) | NOT NULL | PENDING | 同步状态 |
| last_sync_at | timestamp | - | - | 最近同步时间 |
| error_msg | varchar(255) | NOT NULL | '' | 同步错误 |
| created_at | timestamp | NOT NULL | now() | 创建时间 |
| updated_at | timestamp | NOT NULL | now() | 更新时间 |
