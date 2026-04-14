# 低空智能巡检平台 - 媒体文件

## 阅读说明

本数据字典覆盖当前 media_file 业务域已落地的两张核心表：

- `media_files`
- `tenant_media_indexes`

## 1. media_files（媒体文件表）

**说明**：存储当前 tenant 可见媒体的本地读模型。

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 主键 |
| tenant_id | bigint | FK, NOT NULL | - | 所属租户 |
| flight_record_id | bigint | FK | - | 关联飞行记录 ID |
| mission_id | bigint | FK | - | 关联任务 ID |
| device_sn | varchar(128) | NOT NULL | '' | 设备序列号（冗余） |
| media_type | smallint | NOT NULL | - | 媒体类型 |
| file_name | varchar(255) | NOT NULL | - | 文件名 |
| file_url | varchar(500) | NOT NULL | - | 文件 URL（内部保存 `dji://...` 协议占位） |
| thumbnail_url | varchar(500) | NOT NULL | '' | 缩略图 URL |
| file_size | bigint | - | - | 文件大小（字节） |
| latitude | decimal(12,8) | - | - | 拍摄位置纬度 |
| longitude | decimal(12,8) | - | - | 拍摄位置经度 |
| captured_at | timestamp | - | - | 拍摄时间 |
| is_deleted | boolean | NOT NULL | false | 是否已删除 |
| deleted_at | timestamp | - | - | 删除时间 |
| created_at | timestamp | NOT NULL | now() | 创建时间 |

**media_type 枚举值**：

| 值 | 含义 |
|----|------|
| 1 | 照片 |
| 2 | 视频 |

**业务规则**：
1. 当前对外查询只返回 `is_deleted=false` 且已建立 `tenant_media_indexes` 映射的记录。
2. `media_files` 主要由后台同步任务写入，不开放前端手工创建或编辑。
3. 媒体同步按 `device_sn -> claimed drone -> tenant` 归属租户，不依赖 DJI `job_id`。
4. 同步时若 `device_sn + captured_at` 命中同租户下唯一 mission 时间窗，则自动回填 `mission_id`。
5. 若媒体已被人工绑定 mission，则后续同步保留人工绑定结果。
6. 当前阶段 `flight_record_id` 不参与自动归档判断。

## 2. tenant_media_indexes（媒体同步索引表）

**说明**：存储本地媒体记录与 DJI 文件的映射，以及最近同步摘要。

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 主键 |
| tenant_id | bigint | FK, NOT NULL | - | 所属租户 |
| media_file_id | bigint | FK, NOT NULL, UNIQUE | - | 对应媒体文件 |
| dji_file_id | varchar(128) | NOT NULL | - | DJI 文件 ID |
| device_sn | varchar(128) | NOT NULL | '' | 设备序列号 |
| mission_id | bigint | FK | - | 关联任务 ID（镜像 `media_files.mission_id`） |
| sync_status | varchar(32) | NOT NULL | PENDING | 同步状态 |
| last_sync_at | timestamp | - | - | 最近同步时间 |
| error_msg | varchar(255) | NOT NULL | '' | 同步错误 |
| created_at | timestamp | NOT NULL | now() | 创建时间 |
| updated_at | timestamp | NOT NULL | now() | 更新时间 |

**sync_status 枚举值**：

| 值 | 含义 |
|----|------|
| PENDING | 同步中 |
| SYNCED | 同步成功 |
| ERROR | 同步失败 |

**业务规则**：
1. `(tenant_id, dji_file_id)` 唯一。
2. 下载媒体时，使用 `tenant_media_indexes.dji_file_id` 获取 DJI 下载地址。
3. `tenant_media_indexes.mission_id` 与 `media_files.mission_id` 一起维护；自动同步和人工绑定都会同步更新这两个位置。

## 3. 与实现对应

1. 模型：`apps/media_file/models.py`
2. DJI 索引：`apps/dji_bff/models.py`
3. 同步任务：`apps/dji_bff/tasks.py`
4. 接口：`apps/media_file/views.py`
