# 低空智能巡检平台 - 业务数据字典（当前实现）

## 数据库

PostgreSQL

---

## 阅读说明

本数据字典只覆盖当前已落地的业务表：`drones`、`drone_assignments`。

---

## 1. drones（无人机台账表）

**说明**：存储无人机基础信息与当前状态。

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 主键 |
| code | varchar(64) | NOT NULL, UNIQUE | - | 业务编码 |
| name | varchar(128) | NOT NULL | - | 无人机名称 |
| model | varchar(128) | NOT NULL | - | 型号 |
| serial_no | varchar(128) | NOT NULL, UNIQUE | - | 出厂序列号 |
| status | varchar(16) | NOT NULL | DISABLED | 状态 |
| org_id | bigint | - | - | 组织 ID（预留） |
| created_by_staff_id | bigint | - | - | 创建人 staff ID |
| created_at | timestamp | NOT NULL | now() | 创建时间 |
| updated_at | timestamp | NOT NULL | now() | 更新时间 |

**status 状态值**：

| 值 | 含义 |
|----|------|
| ENABLED | 启用 |
| DISABLED | 停用 |
| MAINTENANCE | 维护中 |
| RETIRED | 已退役 |

**业务规则**：
1. `serial_no` 全局唯一。  
2. 已退役（`RETIRED`）状态不可逆。

---

## 2. drone_assignments（无人机分配表）

**说明**：存储无人机与飞手的分配关系，支撑 `ASSIGNED` 范围鉴权。

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 主键 |
| drone_id | bigint | FK, NOT NULL | - | 关联无人机 ID |
| staff_id | bigint | FK, NOT NULL | - | 关联飞手 staff ID |
| status | varchar(16) | NOT NULL | ACTIVE | 分配状态 |
| start_at | timestamp | NOT NULL | now() | 分配生效时间 |
| end_at | timestamp | - | - | 分配结束时间 |
| created_by_staff_id | bigint | - | - | 操作人 staff ID |
| created_at | timestamp | NOT NULL | now() | 创建时间 |
| updated_at | timestamp | NOT NULL | now() | 更新时间 |

**status 状态值**：

| 值 | 含义 |
|----|------|
| ACTIVE | 生效中 |
| INACTIVE | 已失效 |

**约束与规则**：
1. 唯一约束：同一 `(drone_id, staff_id)` 在 `ACTIVE` 状态下唯一。  
2. 取消分配采用软失效（`ACTIVE -> INACTIVE`），不物理删除。

---

## 3. 关系与外键

1. `drone_assignments.drone_id -> drones.id`  
2. `drone_assignments.staff_id -> staff_profiles.id`

---

## 4. 与实现对应

1. 模型：`apps/drone/models.py`  
2. 序列化与校验：`apps/drone/serializers.py`  
3. 接口：`apps/drone/views.py`
