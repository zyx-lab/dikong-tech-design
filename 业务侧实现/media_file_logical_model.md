# 媒体文件逻辑模型

- updated_at: 2026-03-31
- entity: media_file

## 实体主表

- table: media_files
- 主键: id (BigAutoField)

## 当前边界

- `MediaFile` 当前是索引驱动的只读模型。
- 公开业务接口只保留列表、详情、下载。
- 媒体记录主要由 DJI 同步任务写入，不由前端手工创建或编辑。

## 状态机

- `MediaFile` 无独立业务状态机
- 删除语义仍保留在表结构中：`is_deleted=true` 表示逻辑删除

## 关系与约束

- `media_files.tenant_id -> tenants.id`
- `media_files.flight_record_id -> flight_records.id`
- `tenant_media_indexes.media_file_id -> media_files.id`（一对一）
- `tenant_media_indexes.mission_id -> missions.id`（可为空）
- 对外查询固定过滤：`is_deleted=false` 且 `dji_index` 存在

## 生命周期入口

| 操作 | 路径 | 说明 |
|-----|------|------|
| 列表 | GET /api/v1/media-files | 查询当前 tenant 可见媒体列表 |
| 详情 | GET /api/v1/media-files/{id} | 查询媒体详情 |
| 下载 | GET /api/v1/media-files/{id}/download | 下载 DJI 媒体文件 |

## 接口语义

### 查询媒体列表 GET /api/v1/media-files

- 功能：按本地读模型查询媒体
- 可用筛选：`flight_record_id`、`mission_id`、`device_sn`、`media_type`、`file_name`
- 业务码：`00000`, `A0401 / A0403`

### 查询媒体详情 GET /api/v1/media-files/{id}

- 功能：读取单条媒体详情
- 约束：已删除记录或未建立 DJI 映射的记录不对外暴露
- 业务码：`00000`, `C0404`, `A0401 / A0403`

### 下载媒体文件 GET /api/v1/media-files/{id}/download

- 功能：跳转到 DJI 提供的下载地址
- 行为：返回 `302`，不做代理下载
- 业务码：`302`, `C0404`, `A0401 / A0403`
