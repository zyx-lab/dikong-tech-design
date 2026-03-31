# 媒体文件实现说明

- updated_at: 2026-03-31
- entity: media_file

## 数据模型

### MediaFile 表 (media_files)

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | BigAutoField | 主键 |
| tenant | ForeignKey | 所属租户 |
| flight_record | ForeignKey | 关联飞行记录（可选） |
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

## API 实现 (/api/v1/media-files)

统一响应契约：

- 成功：`code=00000`，`msg=success`
- 失败：统一返回 `code / msg / data`，常见错误码为 `A0401`、`A0403`、`C0404`

### 1. GET /api/v1/media-files

- 功能：媒体文件列表查询
- 筛选参数：`flight_record_id`、`mission_id`、`device_sn`、`media_type`、`file_name`
- 关键行为：
  - 只返回 `is_deleted=false` 且存在 `dji_index` 的记录
  - 返回字段包含 `mission_id`、`device_sn`、`dji_file_id`、`sync_status`、`last_sync_at`
- Scope：若调用方命中 `media_file.view_media_file = ASSIGNED`，则仅返回当前飞手自己的媒体文件
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

## 审计动作

当前对外媒体接口只暴露只读查询和下载，不新增业务写审计动作；媒体落库主要来自系统同步任务。

## 关键实现文件

- apps/media_file/models.py
- apps/media_file/serializers.py
- apps/media_file/views.py
- apps/media_file/urls.py
- apps/dji_bff/models.py
- apps/dji_bff/tasks.py
