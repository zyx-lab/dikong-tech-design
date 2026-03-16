# 媒体文件实现说明

- generated_at: 2026-03-08T11:18:06.896282Z
- updated_at: 2026-03-09
- entity: media_file

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

### MediaFile 表 (media_files)

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | BigAutoField | 主键 |
| flight_record | ForeignKey | 关联飞行记录（可选） |
| media_type | PositiveSmallIntegerField | 媒体类型：1=照片, 2=视频 |
| file_name | CharField(255) | 文件名 |
| file_url | CharField(500) | 文件URL |
| thumbnail_url | CharField(500) | 缩略图URL（可选） |
| file_size | BigIntegerField | 文件大小（字节，可选） |
| latitude | DecimalField(12,8) | 拍摄位置-纬度（可选） |
| longitude | DecimalField(12,8) | 拍摄位置-经度（可选） |
| captured_at | DateTimeField | 拍摄时间（可选） |
| is_deleted | BooleanField | 是否已删除，默认 False |
| deleted_at | DateTimeField | 删除时间（可选） |
| created_at | DateTimeField | 创建时间 |

### MediaType 枚举
- PHOTO = 1, "照片"
- VIDEO = 2, "视频"

---

## API 实现 (/api/v1/media-files)

统一响应契约：
- 成功：`code=00000`，`msg=success`
- 失败：统一返回 `code / msg / data`，常见错误码为 `A0401`、`A0403`、`B0001`、`C0404`

### 1. GET /api/v1/media-files
- 功能：媒体文件列表查询
- 筛选参数：flight_record_id, media_type, mission_id, drone_id, file_name
- 过滤：仅返回 is_deleted=False 的记录
- 说明：若调用方角色命中 `media_file.* = ASSIGNED`，则仅返回关联飞行记录属于当前飞手的媒体文件
- 权限：media_file.view_media_file
- 业务码：`00000`, `A0401 / A0403`

### 2. POST /api/v1/media-files
- 功能：创建媒体文件记录
- 必填：media_type, file_name, file_url
- 可选：flight_record, thumbnail_url, file_size, latitude, longitude, captured_at
- 默认：is_deleted=False, deleted_at=None
- 约束：若调用方角色命中 `media_file.manage_media_file = ASSIGNED`，则只能写入当前飞手自己飞行记录下的媒体文件
- 权限：media_file.manage_media_file
- 业务码：`00000`, `B0001`, `A0401 / A0403`

### 3. GET /api/v1/media-files/{id}
- 功能：媒体文件详情
- 过滤：仅返回未删除记录，已删除返回 404
- 说明：若调用方角色命中 `media_file.view_media_file = ASSIGNED`，则只能读取当前飞手自己的媒体文件
- 权限：media_file.view_media_file
- 业务码：`00000`, `C0404`, `A0401 / A0403`

### 4. PUT / PATCH /api/v1/media-files/{id}
- 功能：全量或局部更新媒体文件
- 可写字段：flight_record, media_type, file_name, file_url, thumbnail_url, file_size, latitude, longitude, captured_at
- 约束：
  - PATCH 请求体必须至少包含一个可写字段
  - 已逻辑删除记录不参与更新，按资源不存在处理
  - 若调用方角色命中 `media_file.manage_media_file = ASSIGNED`，则不能把媒体改绑到其他飞手的飞行记录下
- 权限：media_file.manage_media_file
- 业务码：`00000`, `B0001`, `C0404`, `A0401 / A0403`
- 审计：MEDIA_FILE_UPDATE

### 5. DELETE /api/v1/media-files/{id}
- 功能：逻辑删除媒体文件
- 策略：更新 is_deleted=True, deleted_at=当前时间
- 约束：不做物理删除
- 权限：media_file.manage_media_file
- 业务码：`00000`, `C0404`, `A0401 / A0403`

---

## 审计动作

- MEDIA_FILE_CREATE
- MEDIA_FILE_UPDATE
- MEDIA_FILE_DELETE

---

## 关键实现文件

- apps/media_file/models.py
- apps/media_file/serializers.py
- apps/media_file/views.py
- apps/media_file/urls.py
- apps/media_file/tests.py
- apps/access/management/commands/seed_role_permissions.py
- apps/api_v1/urls.py
