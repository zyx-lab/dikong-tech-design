# 低空智能巡检平台 - 任务

## 阅读说明

本数据字典覆盖当前 mission 业务域已落地的核心表：

- `missions`

说明：历史上的 `dji_job_id`、`tenant_mission_indexes` 已移除；mission 当前完全是 Django 本地业务对象，不再与 DJI job 建立持久化映射。

## 1. missions（任务表）

**说明**：存储 tenant 内任务主记录、执行状态和执行时间窗。

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 主键 |
| tenant_id | bigint | FK, NOT NULL | - | 租户 ID |
| name | varchar(100) | NOT NULL | - | 任务名称 |
| route_id | bigint | FK | - | 航线 ID；DB 允许为空，创建接口仍要求必填 |
| route_name | varchar(100) | NOT NULL | '' | 航线名称（冗余） |
| drone_id | bigint | FK | - | 无人机 ID；DB 允许为空，创建接口仍要求必填 |
| device_sn | varchar(128) | NOT NULL | '' | 无人机 SN（冗余） |
| drone_name | varchar(100) | NOT NULL | '' | 无人机名称（冗余） |
| pilot_id | bigint | FK, NOT NULL | - | 飞手成员 ID（TenantMember） |
| pilot_name | varchar(50) | NOT NULL | '' | 飞手姓名（冗余） |
| scheduled_at | timestamp | - | - | 计划执行时间 |
| started_at | timestamp | - | - | 开始执行时间 |
| finished_at | timestamp | - | - | 执行完成时间 |
| remark | varchar(500) | NOT NULL | '' | 任务备注 |
| status | smallint | NOT NULL | 0 | 任务执行状态 |
| is_deleted | boolean | NOT NULL | false | 是否已软删除 |
| deleted_at | timestamp | - | - | 软删除时间 |
| created_at | timestamp | NOT NULL | now() | 创建时间 |
| updated_at | timestamp | NOT NULL | now() | 更新时间 |

**status 状态值**：

| 值 | 含义 |
|----|------|
| 0 | 待执行 |
| 1 | 执行中 |
| 2 | 执行完成 |

**业务规则**：
1. 创建接口要求 `route`、`drone`、`pilot` 必须属于当前租户。
2. 创建接口不再要求 `route` 已发布到 DJI；mission 当前是本地调度对象。
3. `pilot` 必须为当前租户 `ACTIVE` 成员，账号存在在职 `staff_profile`，并具备 `pilot_operator` 角色。
4. mission 创建后初始状态固定为 `待执行`，且 `started_at`、`finished_at` 必须为空。
5. `POST /api/v1/missions/{id}/advance` 负责推进 `待执行 -> 执行中 -> 执行完成`，分别写入 `started_at`、`finished_at`。
6. 同一无人机同一时刻只允许一个 mission 进入 `执行中`。
7. 只有 `待执行` mission 允许通过更新接口修改 `route`、`drone` 等绑定字段；进入 `执行中 / 执行完成` 后不允许再改。
8. mission 软删除不可恢复。
9. 当前阶段 mission 是媒体自动归档的唯一业务锚点；自动归档不依赖 `flight_record` 或 DJI `jobId`。

## 2. 与实现对应

1. 模型：`apps/mission/models.py`
2. 序列化：`apps/mission/serializers.py`
3. 接口：`apps/mission/views.py`
4. 路由：`apps/mission/urls.py`
5. 测试：`apps/mission/tests.py`
