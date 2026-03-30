# 无人机实现说明

- generated_at: 2026-03-08
- updated_at: 2026-03-09
- entity: drone / drone_assignment

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

## 数据模型

### Drone 表 (drones)

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | BigAutoField | 主键 |
| tenant | ForeignKey | 所属租户 |
| code | CharField(64) | 业务编码，唯一 |
| name | CharField(128) | 无人机名称 |
| model | CharField(128) | 型号 |
| device_sn | CharField(128) | 设备序列号，租户内唯一 |
| status | CharField(16) | 状态：ENABLED/DISABLED |
| org_id | BigIntegerField | 组织 ID（预留） |
| created_by_tenant_member_id | BigIntegerField | 创建人 TenantMember ID |
| created_at | DateTimeField | 创建时间 |
| updated_at | DateTimeField | 更新时间 |

### DroneStatus 枚举
- ENABLED = "ENABLED", "启用"
- DISABLED = "DISABLED", "停用"

### DroneAssignment 表 (drone_assignments)

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | BigAutoField | 主键 |
| drone | ForeignKey | 关联无人机 |
| tenant_member | ForeignKey | 关联租户成员 |
| status | CharField(16) | 分配状态：ACTIVE/INACTIVE |
| start_at | DateTimeField | 分配开始时间 |
| end_at | DateTimeField | 分配结束时间（可选） |
| created_by_tenant_member_id | BigIntegerField | 创建人 TenantMember ID |
| created_at | DateTimeField | 创建时间 |
| updated_at | DateTimeField | 更新时间 |

### DroneAssignmentStatus 枚举
- ACTIVE = "ACTIVE", "生效中"
- INACTIVE = "INACTIVE", "已失效"

### 约束
- `(tenant, code)`、`(tenant, device_sn)` 唯一
- 同一 `(drone, tenant_member)` 在 `ACTIVE` 状态下唯一
- `ASSIGNED` 范围统一按 `tenant_member_id` 命中
- `status` 不可由业务 API 直接写入，由后台同步任务维护

---

## API 实现

统一响应契约：
- 成功：`code=00000`，`msg=success`
- 失败：统一返回 `code / msg / data`，常见错误码为 `A0401`、`A0403`、`B0001`、`C0101`、`C0201`、`C0404`

### 无人机台账 (/api/v1/drones)

#### 1. GET /api/v1/drones
- 功能：无人机列表查询
- 筛选参数：code, name, model, device_sn
- Scope：`ASSIGNED` 用户只返回当前租户下分配给本人 `TenantMember` 的无人机
- 权限：drone.view_drone
- 业务码：`00000`, `A0401 / A0403`

#### 2. GET /api/v1/drones/available
- 功能：查询当前租户可认领的共享设备
- 返回：DJI 共享设备池中、且尚未被任何租户认领的设备
- 权限：drone.view_drone
- 业务码：`00000`, `A0401 / A0403`

#### 3. POST /api/v1/drones
- 功能：认领共享设备
- 必填：code, device_sn
- 可选：name, model, org_id
- 自动补全：若未传 name/model，则优先回填共享设备索引中的值
- 自动设置：`created_by_tenant_member_id=当前租户成员`
- 权限：drone.manage_drone
- 业务码：`00000`, `B0001`, `C0101`, `A0401 / A0403`

#### 4. GET /api/v1/drones/{id}
- 功能：无人机详情
- 权限：drone.view_drone
- 业务码：`00000`, `C0404`, `A0401 / A0403`

#### 5. PUT / PATCH /api/v1/drones/{id}
- 功能：全量或局部更新无人机
- 可写字段：code, name, model, org_id
- 约束：`device_sn`、`status` 不可直接修改
- 权限：drone.manage_drone
- 业务码：`00000`, `B0001`, `C0404`, `A0401 / A0403`

#### 6. GET /api/v1/drones/{id}/live/capacity
- 功能：查询设备直播能力
- 权限：drone.view_drone
- 业务码：`00000`, `C0404`, `A0401 / A0403`

#### 7. POST /api/v1/drones/{id}/live/start
- 功能：启动直播
- 请求体：`camera_index`、`video_index`，可选 `url_type`
- 权限：drone.manage_drone
- 业务码：`00000`, `B0001`, `C0404`, `A0401 / A0403`

#### 8. POST /api/v1/drones/{id}/live/stop
- 功能：停止直播
- 请求体：可选 `video_id`
- 权限：drone.manage_drone
- 业务码：`00000`, `B0001`, `C0404`, `A0401 / A0403`

#### 9. POST /api/v1/drones/{id}/live/video-quality
- 功能：调整直播画质
- 请求体：`quality`，可选 `video_id`
- 权限：drone.manage_drone
- 业务码：`00000`, `B0001`, `C0404`, `A0401 / A0403`

#### 10. POST /api/v1/drones/{id}/live/video-source
- 功能：切换直播视频源
- 请求体：`video_id`、`videoType`
- 权限：drone.manage_drone
- 业务码：`00000`, `B0001`, `C0404`, `A0401 / A0403`

---

### 分配关系 (/api/v1/drone-assignments)

#### 1. GET /api/v1/drone-assignments
- 功能：分配关系列表查询
- 筛选参数：drone_id, tenant_member_id, status
- 权限：drone_assignment.manage_drone_assignment
- 业务码：`00000`, `A0401 / A0403`

#### 2. POST /api/v1/drone-assignments
- 功能：创建分配关系
- 必填：drone, tenant_member
- 默认：status=ACTIVE, start_at=当前时间
- 约束：同一无人机与同一租户成员不得重复存在 ACTIVE 分配
- 权限：drone_assignment.manage_drone_assignment
- 业务码：`00000`, `B0001`, `C0101`, `A0401 / A0403`

#### 3. GET /api/v1/drone-assignments/{id}
- 功能：分配关系详情
- 权限：drone_assignment.manage_drone_assignment
- 业务码：`00000`, `C0404`, `A0401 / A0403`

#### 4. POST /api/v1/drone-assignments/{id}/cancel
- 功能：取消分配
- 约束：请求体必须为空
- 状态流转：ACTIVE -> INACTIVE
- 自动设置：end_at=当前时间
- 幂等：已 INACTIVE 返回当前状态
- 权限：drone_assignment.manage_drone_assignment
- 业务码：`00000`, `B0001`, `C0404`, `A0401 / A0403`
- 审计：DRONE_ASSIGNMENT_CANCEL

---

## 审计动作

### 无人机
- DRONE_CLAIM
- DRONE_UPDATE
- DRONE_LIVE_CAPACITY
- DRONE_LIVE_START
- DRONE_LIVE_STOP
- DRONE_LIVE_VIDEO_QUALITY
- DRONE_LIVE_VIDEO_SOURCE

### 分配关系
- DRONE_ASSIGNMENT_CREATE
- DRONE_ASSIGNMENT_CANCEL

---

## 关键实现文件

- apps/drone/models.py
- apps/drone/serializers.py
- apps/drone/views.py
- apps/drone/urls.py
- apps/drone_assignment/models.py
- apps/drone_assignment/serializers.py
- apps/drone_assignment/views.py
- apps/drone_assignment/urls.py
- apps/access/management/commands/seed_role_permissions.py
- apps/api_v1/urls.py
