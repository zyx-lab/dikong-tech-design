# 媒体文件逻辑模型

- generated_at: 2026-03-08T11:18:06.896282Z
- entity: media_file

## 数据库

PostgreSQL

---

## 文档格式说明

本文档为 **逻辑模型** 类型文档，记录实体关系、状态机、生命周期、接口语义等。

### 更新本文档的指南（大模型用）

当需要更新此文档时，请遵循以下格式：

```
## 实体主表
- table: {表名}
- 主键: {主键定义}

## 状态机
- {状态字段}: {状态值列表}

## 关系与约束
- {外键关系}
- {业务约束}

## 生命周期入口
- {HTTP方法} {路径}: {功能描述}

## 接口语义
### {API名称}
- 功能：{功能描述}
- 路径：{API路径}
- 方法：{HTTP方法}
- 状态流转：{状态变化}
- 有效状态：{允许执行该操作的状态}
- 无效状态：{禁止执行该操作的状态列表}
- 业务码：{返回的业务码}
```

---

## 实体主表

- table: media_files
- 主键: id (BigAutoField)

## 状态机

- N/A（媒体文件无状态机）

## 关系与约束

- flight_record -> flight_record.FlightRecord
- 逻辑删除：is_deleted=true 表示已删除

## 生命周期入口

| 操作 | 路径 | 说明 |
|-----|------|------|
| 创建 | POST /api/v1/media-files | 新增媒体文件 |
| 列表 | GET /api/v1/media-files | 媒体文件列表查询 |
| 详情 | GET /api/v1/media-files/{id} | 媒体文件详情 |
| 更新 | PUT / PATCH /api/v1/media-files/{id} | 全量或局部更新媒体文件 |
| 删除 | DELETE /api/v1/media-files/{id} | 逻辑删除媒体文件 |

## 接口语义

### 创建媒体文件 POST /api/v1/media-files
- 功能：创建媒体文件元数据记录
- 必填：media_type, file_name, file_url
- 约束：is_deleted 默认为 false
- 业务码：`00000`, `B0001`, `A0401 / A0403`

### 更新媒体文件 PUT / PATCH /api/v1/media-files/{id}
- 功能：全量或局部更新媒体文件元数据
- 可写字段：flight_record, media_type, file_name, file_url, thumbnail_url, file_size, latitude, longitude, captured_at
- 约束：已逻辑删除的记录不参与更新
- 业务码：`00000`, `B0001`, `C0404`, `A0401 / A0403`

### 删除媒体文件 DELETE /api/v1/media-files/{id}
- 功能：逻辑删除媒体文件
- 约束：执行 is_deleted=true, deleted_at=当前时间，不做物理删除
- 业务码：`00000`, `C0404`, `A0401 / A0403`
