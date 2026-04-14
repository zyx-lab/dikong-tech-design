# 媒体文件实现说明

- updated_at: 2026-04-14
- entity: media_file

## 数据模型

### MediaFile 表 (media_files)

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | BigAutoField | 主键 |
| tenant | ForeignKey | 所属租户 |
| flight_record | ForeignKey | 关联飞行记录（可选） |
| mission | ForeignKey | 关联任务（可选） |
| device_sn | CharField(128) | 设备序列号冗余 |
| media_type | PositiveSmallIntegerField | 媒体类型：1=照片, 2=视频 |
| file_name | CharField(255) | 文件名 |
| file_url | CharField(500) | 文件 URL（内部保存 `dji://...` 协议占位） |
| thumbnail_url | CharField(500) | 缩略图 URL（可选） |
| file_size | BigIntegerField | 文件大小（字节，可选） |
| latitude | DecimalField(12,8) | 拍摄位置纬度（可选） |
| longitude | DecimalField(12,8) | 拍摄位置经度（可选） |
| captured_at | DateTimeField | 拍摄时间（可选） |
| is_deleted | BooleanField | 是否逻辑删除 |
| deleted_at | DateTimeField | 删除时间（可选） |
| created_at | DateTimeField | 创建时间 |

### TenantMediaIndex 表 (tenant_media_indexes)

| 字段 | 类型 | 说明 |
|-----|------|------|
| tenant | ForeignKey | 租户 |
| media_file | OneToOneField | 业务媒体文件 |
| dji_file_id | CharField(128) | DJI 文件 ID |
| device_sn | CharField(128) | 设备序列号 |
| mission | ForeignKey | 关联任务（可选） |
| sync_status | CharField(32) | 同步状态：`PENDING / SYNCED / ERROR` |
| last_sync_at | DateTimeField | 最近同步时间 |
| error_msg | CharField(255) | 同步错误 |
| created_at | DateTimeField | 创建时间 |
| updated_at | DateTimeField | 更新时间 |

### MediaType 枚举

- PHOTO = 1, "照片"
- VIDEO = 2, "视频"

## 自动同步归档规则

1. 同步任务从 DJI 拉取媒体列表后，先通过 `device_sn` 找到本地 `CLAIMED` 无人机，再据此确定 tenant。
2. 之后按 `device_sn + captured_at` 在同租户内匹配 mission 时间窗：
   - 命中唯一 mission：自动回填 mission
   - 命中 0 个或多个 mission：保持未绑定
3. 若本地媒体已经人工绑定了 mission，则同步不覆盖人工结果。
4. 当前阶段自动归档不依赖 `flight_record`，也不依赖 DJI `jobId`。

## API 实现 (/api/v1/media-files)

统一响应契约：

- 成功：`code=00000`，`msg=success`
- 失败：统一返回 `code / msg / data`，常见错误码为 `A0401`、`A0403`、`B0001`、`C0404`

### 1. GET /api/v1/media-files

- 功能：媒体文件列表查询
- 筛选参数：`flight_record_id`、`mission_id`、`device_sn`、`media_type`、`file_name`
- 关键行为：
  - 只返回 `is_deleted=false` 且存在 `dji_index` 的记录
  - 返回字段包含 `mission_id`、`device_sn`、`dji_file_id`、`sync_status`、`last_sync_at`
- Scope：若调用方命中 `media_file.view_media_file = ASSIGNED`，则返回当前飞手自己的媒体；优先看 `flight_record.pilot_id`，否则退回 `mission.pilot_id`
- 权限：`media_file.view_media_file`

### 2. GET /api/v1/media-files/{id}

- 功能：媒体文件详情
- 约束：只读；已删除记录或缺少 `dji_index` 的记录不对外暴露
- Scope：若命中 `ASSIGNED`，则只能读取当前飞手自己的媒体文件
- 权限：`media_file.view_media_file`

### 3. GET /api/v1/media-files/{id}/download

- 功能：下载媒体文件
- 关键行为：
  - 通过 `media_file.dji_index.dji_file_id` 获取 DJI 下载地址
  - 返回 `302` 重定向到 DJI URL
- 权限：`media_file.view_media_file`

### 4. DELETE /api/v1/media-files/{id}

- 功能：软删除媒体记录
- 请求体：必须为空
- 关键行为：
  - 写入 `is_deleted=true`
  - 写入 `deleted_at`
  - 删除后从业务查询中隐藏
- 权限：`media_file.manage_media_file`

### 5. POST /api/v1/media-files/bind-mission

- 功能：批量把媒体绑定到指定 mission
- 请求体：`mission_id` + `media_file_ids[]`
- 前置条件：
  - mission 存在、未删除，且 `device_sn` 非空
  - `media_file_ids` 对应媒体都存在、未删除、具备可见性且存在 `dji_index`
  - 所选媒体 `device_sn` 必须与 mission 一致
  - 若媒体已被某条 `flight_record` 锁定到其他 mission，则拒绝覆盖
  - `ASSIGNED` 范围下只能绑定到当前飞手自己的 mission
- 关键行为：
  - 批量更新 `media_files.mission_id`
  - 同步更新 `tenant_media_indexes.mission_id`
- 权限：`media_file.manage_media_file`

## 审计动作

- MEDIA_FILE_DELETE
- MEDIA_FILE_BIND_MISSION
- DJI_MEDIA_SYNC（系统同步审计）

## 关键实现文件

- apps/media_file/models.py
- apps/media_file/serializers.py
- apps/media_file/views.py
- apps/media_file/urls.py
- apps/dji_bff/models.py
- apps/dji_bff/tasks.py
- apps/media_file/tests.py
