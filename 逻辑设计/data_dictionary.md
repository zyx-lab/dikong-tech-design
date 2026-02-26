# 低空智能巡检平台 - 数据字典

## 数据库

PostgreSQL

---

## 1. drone_types（无人机类型表）

**说明**：存储无人机型号信息

| 字段名       | 类型         | 约束     | 默认值 | 说明     |
| ------------ | ------------ | -------- | ------ | -------- |
| id           | bigserial    | PK       | 自增   | 类型ID   |
| name         | varchar(100) | NOT NULL | -      | 型号名称 |
| manufacturer | varchar(100) | -        | -      | 制造商   |

**示例数据**：
| name | manufacturer |
|------|--------------|
| 大疆Mavic 3E | 大疆 |
| CW-15 | 成都纵横 |

---

## 2. drones（无人机表）

**说明**：存储具体的无人机设备

| 字段名        | 类型         | 约束     | 默认值 | 说明         |
| ------------- | ------------ | -------- | ------ | ------------ |
| id            | bigserial    | PK       | 自增   | 无人机ID     |
| name          | varchar(100) | NOT NULL | -      | 名称         |
| sn            | varchar(100) | UNIQUE   | -      | 设备序列号   |
| drone_type_id | bigint       | FK       | -      | 无人机类型ID |
| status        | smallint     | -        | 1      | 状态         |
| created_at    | timestamp    | -        | now()  | 创建时间     |
| updated_at    | timestamp    | -        | -      | 更新时间     |

**status 状态值**：
| 值 | 含义 |
|----|------|
| 1 | 在线 |
| 2 | 离线 |
| 3 | 故障 |
| 4 | 维护中 |
| 9 | 已报废 |

**示例数据**：
| name | sn | drone_type_id |
|------|----|---------------|
| 应急测绘-01 | DJI3E001 | 1 |
| 河道巡检-03 | CW15002 | 2 |

---

## 3. pilots（飞手表）

**说明**：存储飞行操作员信息

| 字段名           | 类型         | 约束     | 默认值 | 说明     |
| ---------------- | ------------ | -------- | ------ | -------- |
| id               | bigserial    | PK       | 自增   | 飞手ID   |
| username         | varchar(50)  | NOT NULL | -      | 用户账号 |
| name             | varchar(50)  | NOT NULL | -      | 姓名     |
| phone            | varchar(20)  | -        | -      | 联系电话 |
| cert_type        | smallint     | -        | -      | 证件类型 |
| cert_no          | varchar(100) | -        | -      | 证件编号 |
| cert_issue_date  | date         | -        | -      | 发证日期 |
| cert_expiry_date | date         | -        | -      | 有效期限 |
| account_status   | smallint     | -        | 1      | 账号状态 |
| created_at       | timestamp    | -        | now()  | 创建时间 |
| updated_at       | timestamp    | -        | -      | 更新时间 |

**cert_type 证件类型**：
| 值 | 含义 |
|----|------|
| 1 | AOPA合格证 |
| 2 | ALPA合格证 |
| 3 | CAAC执照 |

**account_status 账号状态**：
| 值 | 含义 |
|----|------|
| 0 | 关闭（含离职/已删除） |
| 1 | 开启 |

**示例数据**：
| username | name | cert_type |
|----------|------|-----------|
| wangwei01 | 王伟 | 1 |
| lina_jiaotong | 李娜 | 1 |

---

## 4. routes（航线表）

**说明**：存储航线规划信息

| 字段名             | 类型          | 约束     | 默认值 | 说明               |
| ------------------ | ------------- | -------- | ------ | ------------------ |
| id                 | bigserial     | PK       | 自增   | 航线ID             |
| name               | varchar(100)  | NOT NULL | -      | 航线名称           |
| route_type         | smallint      | NOT NULL | -      | 航线类型           |
| drone_type_id      | bigint        | FK       | -      | 适用无人机类型ID   |
| total_distance     | numeric(12,2) | -        | -      | 航线总长度（米）   |
| estimated_duration | integer       | -        | -      | 预计飞行时长（秒） |
| waypoint_count     | integer       | -        | -      | 航点数量           |
| creator_name       | varchar(50)   | -        | -      | 创建人姓名         |
| status             | smallint      | -        | 1      | 状态               |
| created_at         | timestamp     | -        | now()  | 创建时间           |
| updated_at         | timestamp     | -        | -      | 更新时间           |

**status 状态值**:
| 值 | 含义 |
|----|------|
| 0 | 禁用（已删除） |
| 1 | 正常 |

> **删除策略说明**：
>
> - 如果航线已被任务引用，禁止物理删除，只能设置为 `0-禁用`
> - 未被使用的草稿航线可以物理删除

**route_type 航线类型**：
| 值 | 含义 |
|----|------|
| 1 | 点状航线 |
| 2 | 环状航线 |
| 3 | 面状航线 |

**示例数据**：
| name | route_type | department | total_distance |
|------|------------|------------|----------------|
| 珠海岸线巡查航线 | 1 | 市应急局 | 2063 |
| 紫金山核心区交通要道航线 | 2 | 市交通局 | 5120 |

---

## 5. waypoints（航点表）

**说明**：存储航线中的航点详情

| 字段名     | 类型          | 约束         | 默认值 | 说明           |
| ---------- | ------------- | ------------ | ------ | -------------- |
| id         | bigserial     | PK           | 自增   | 航点ID         |
| route_id   | bigint        | FK, NOT NULL | -      | 所属航线ID     |
| sequence   | integer       | NOT NULL     | -      | 航点序号       |
| latitude   | numeric(12,8) | NOT NULL     | -      | 纬度           |
| longitude  | numeric(12,8) | NOT NULL     | -      | 经度           |
| altitude   | numeric(10,2) | NOT NULL     | -      | 飞行高度（米） |
| created_at | timestamp     | -            | now()  | 创建时间       |

**索引**：
| 索引名 | 字段 | 类型 |
|--------|------|------|
| waypoints_route_seq_unique | (route_id, sequence) | UNIQUE |

---

## 6. missions（任务表）

**说明**：存储巡检任务信息

| 字段名       | 类型         | 约束     | 默认值 | 说明               |
| ------------ | ------------ | -------- | ------ | ------------------ |
| id           | bigserial    | PK       | 自增   | 任务ID             |
| name         | varchar(100) | NOT NULL | -      | 任务名称           |
| route_id     | bigint       | FK       | -      | 任务航线ID         |
| route_name   | varchar(100) | -        | -      | 航线名称（冗余）   |
| drone_id     | bigint       | FK       | -      | 执行无人机ID       |
| drone_name   | varchar(100) | -        | -      | 无人机名称（冗余） |
| pilot_id     | bigint       | FK       | -      | 执行飞手ID         |
| pilot_name   | varchar(50)  | -        | -      | 飞手姓名（冗余）   |
| scheduled_at | timestamp    | -        | -      | 计划执行时间       |
| remark       | varchar(500) | -        | -      | 任务备注           |
| status       | smallint     | -        | 0      | 状态               |
| created_at   | timestamp    | -        | now()  | 创建时间           |
| updated_at   | timestamp    | -        | -      | 更新时间           |

**status 状态值**：
| 值 | 含义 |
|----|------|
| 0 | 待执行 |
| 1 | 执行中 |
| 2 | 已暂停 |
| 3 | 已完成 |
| 4 | 已取消 |
| 5 | 执行失败 |

---

## 7. flight_records（飞行记录表）

**说明**：存储每次飞行的执行记录（架次）

| 字段名          | 类型         | 约束             | 默认值 | 说明               |
| --------------- | ------------ | ---------------- | ------ | ------------------ |
| id              | bigserial    | PK               | 自增   | 记录ID             |
| flight_no       | varchar(50)  | UNIQUE, NOT NULL | -      | 架次编号           |
| mission_id      | bigint       | FK               | -      | 所属任务ID         |
| mission_name    | varchar(100) | -                | -      | 任务名称（冗余）   |
| route_name      | varchar(100) | -                | -      | 航线名称（冗余）   |
| airport_name    | varchar(100) | -                | -      | 执行机场名称       |
| drone_id        | bigint       | FK               | -      | 执行无人机ID       |
| drone_name      | varchar(100) | -                | -      | 无人机名称（冗余） |
| pilot_id        | bigint       | FK               | -      | 执行飞手ID         |
| pilot_name      | varchar(50)  | -                | -      | 飞手姓名（冗余）   |
| start_time      | timestamp    | -                | -      | 开始时间           |
| end_time        | timestamp    | -                | -      | 结束时间           |
| flight_duration | integer      | -                | -      | 飞行时长（秒）     |
| photo_count     | integer      | -                | 0      | 拍摄照片数量       |
| video_count     | integer      | -                | 0      | 录制视频数量       |
| status          | smallint     | -                | 0      | 状态               |
| created_at      | timestamp    | -                | now()  | 创建时间           |
| updated_at      | timestamp    | -                | -      | 更新时间           |

**status 状态值**：
| 值 | 含义 |
|----|------|
| 0 | 飞行中 |
| 1 | 已完成 |
| 2 | 异常终止 |

**示例数据**：
| flight_no | mission_name | flight_duration |
|-----------|--------------|-----------------|
| YJ202511163281 | 前山河汛前岸线勘察 | 201 |

---

## 8. media_files（媒体文件表）

**说明**：存储飞行拍摄的照片和视频

| 字段名           | 类型          | 约束     | 默认值 | 说明             |
| ---------------- | ------------- | -------- | ------ | ---------------- |
| id               | bigserial     | PK       | 自增   | 媒体ID           |
| flight_record_id | bigint        | FK       | -      | 关联飞行记录ID   |
| media_type       | smallint      | NOT NULL | -      | 媒体类型         |
| file_name        | varchar(255)  | NOT NULL | -      | 文件名           |
| file_url         | varchar(500)  | NOT NULL | -      | 文件URL          |
| thumbnail_url    | varchar(500)  | -        | -      | 缩略图URL        |
| file_size        | bigint        | -        | -      | 文件大小（字节） |
| latitude         | numeric(12,8) | -        | -      | 拍摄位置-纬度    |
| longitude        | numeric(12,8) | -        | -      | 拍摄位置-经度    |
| captured_at      | timestamp     | -        | -      | 拍摄时间         |
| is_deleted       | boolean       | -        | false  | 是否已删除       |
| deleted_at       | timestamp     | -        | -      | 删除时间         |
| created_at       | timestamp     | -        | now()  | 创建时间         |

> **删除策略说明**：
>
> - 支持逻辑删除（回收站机制），删除后设置 `is_deleted=true`
> - 建议定期清理（如30天后）物理删除文件和记录
> - 物理删除时需同步删除云存储（OSS）上的文件

**media_type 媒体类型**：
| 值 | 含义 |
|----|------|
| 1 | 照片 |
| 2 | 视频 |
