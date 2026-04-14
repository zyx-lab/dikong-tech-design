# 媒体文件逻辑模型

- updated_at: 2026-04-14
- entity: media_file

## 实体主表

- table: media_files
- 主键: id (BigAutoField)

## 当前边界

- `MediaFile` 当前是索引驱动的只读模型，写入主链路来自 DJI 同步任务。
- 公开业务接口只保留列表、详情、下载、软删除和 `bind-mission`。
- 当前阶段 mission 是媒体归档的唯一业务锚点；`flight_record` 不参与自动归档判断。

## 状态机

- `MediaFile` 无独立业务状态机。
- 删除语义仍保留在表结构中：`is_deleted=true` 表示逻辑删除。

## 关系与约束

- `media_files.tenant_id -> tenants.id`
- `media_files.flight_record_id -> flight_records.id`
- `media_files.mission_id -> missions.id`
- `tenant_media_indexes.media_file_id -> media_files.id`（一对一）
- `tenant_media_indexes.mission_id -> missions.id`（可为空）
- 对外查询固定过滤：`is_deleted=false` 且 `dji_index` 存在

## 生命周期入口

| 操作 | 路径 | 说明 |
|-----|------|------|
| 列表 | GET /api/v1/media-files | 查询当前 tenant 可见媒体列表 |
| 详情 | GET /api/v1/media-files/{id} | 查询媒体详情 |
| 下载 | GET /api/v1/media-files/{id}/download | 下载 DJI 媒体文件 |
| 删除 | DELETE /api/v1/media-files/{id} | 软删除媒体记录 |
| 手工绑定 | POST /api/v1/media-files/bind-mission | 批量把媒体绑定到 mission |

## 自动归档语义

### DJI 媒体同步

- 租户归属：按 `device_sn -> claimed drone -> tenant`
- mission 匹配：按 `device_sn + captured_at` 命中同租户内唯一 mission 时间窗
- 冲突策略：
  - 命中唯一 mission：自动绑定
  - 命中多个 mission：不自动绑定
  - 媒体已人工绑定 mission：保留人工绑定

### 手工绑定 POST /api/v1/media-files/bind-mission

- 功能：覆盖或补齐 media 的 mission 归属
- 约束：
  - mission 必须存在且具备 `device_sn`
  - media 的 `device_sn` 必须与 mission 一致
  - 若 `flight_record.mission_id` 已锁定到其他 mission，则拒绝覆盖
- 副作用：
  - 同步更新 `media_files.mission_id`
  - 同步更新 `tenant_media_indexes.mission_id`

## 下载语义

### GET /api/v1/media-files/{id}/download

- 功能：跳转到 DJI 提供的下载地址
- 行为：返回 `302`，不做代理下载
- 依赖：`tenant_media_indexes.dji_file_id`
