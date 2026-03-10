# 低空智能巡检平台 - 媒体文件

## 数据库

PostgreSQL

---

## 文档格式说明

本文档为 **数据字典** 类型文档，记录数据库表结构、字段定义、约束和业务规则。

### 更新本文档的指南（大模型用）

当需要更新此文档时，请遵循以下格式：

```
## N. {表名中文名}

**说明**：{表用途简述}

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| {字段名} | {PostgreSQL类型} | {约束} | {默认值} | {字段说明} |

**{某字段} 状态值**：

| 值 | 含义 |
|----|------|
| {枚举值} | {含义} |

**业务规则**：
1. {规则1}
2. {规则2}
```

---

## 阅读说明

本数据字典覆盖当前已落地的业务表：`media_files`。

---

## 1. media_files（媒体文件表）

**说明**：存储飞行任务产生的媒体文件元数据。

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 主键 |
| flight_record_id | bigint | FK | - | 关联飞行记录 ID |
| media_type | smallint | NOT NULL | - | 媒体类型 |
| file_name | varchar(255) | NOT NULL | - | 文件名 |
| file_url | varchar(500) | NOT NULL | - | 文件URL |
| thumbnail_url | varchar(500) | - | "" | 缩略图URL |
| file_size | bigint | - | - | 文件大小（字节） |
| latitude | decimal(12,8) | - | - | 拍摄位置-纬度 |
| longitude | decimal(12,8) | - | - | 拍摄位置-经度 |
| captured_at | timestamp | - | - | 拍摄时间 |
| is_deleted | boolean | - | false | 是否已删除 |
| deleted_at | timestamp | - | - | 删除时间 |
| created_at | timestamp | NOT NULL | now() | 创建时间 |

**media_type 枚举值**：

| 值 | 含义 |
|----|------|
| 1 | 照片 |
| 2 | 视频 |

**业务规则**：
1. 列表查询默认过滤 is_deleted=false 的记录。
2. 删除采用逻辑删除（is_deleted=true），不物理删除。

---

## 2. 与实现对应

1. 模型：`apps/media_file/models.py`
2. 序列化与校验：`apps/media_file/serializers.py`
3. 接口：`apps/media_file/views.py`

---

## 3. 业务响应码字典（Business API）

说明：业务 API 响应体包含 `business_code`（主业务码）与 `business_detail_code`（细分原因码）。

| business_code | 典型 HTTP | 语义 |
| ------ | ------ | ------ |
| SUCCESS | 200 / 201 | 业务处理成功 |
| INVALID_PARAMS | 400 | 请求参数校验失败 |
| PERMISSION_DENIED | 401 / 403 | 身份或权限不足 |
| RESOURCE_NOT_FOUND | 404 | 目标资源不存在 |

| business_detail_code | 语义 |
| ------ | ------ |
| OK | 成功 |
| NOT_AUTHENTICATED | 未登录或认证信息缺失 |
| FORBIDDEN | 已登录但无权限 |
| NOT_FOUND | 资源不存在 |
| VALIDATION_ERROR | 参数校验失败 |
