# 低空智能巡检平台 - 航点

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

**业务规则**：
1. {规则1}
2. {规则2}
```

---

## 阅读说明

本数据字典覆盖当前已落地的业务表：`waypoints`。

---

## 1. waypoints（航点表）

**说明**：存储航点坐标信息，隶属于航线。

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 主键 |
| route_id | bigint | FK, NOT NULL | - | 所属航线 ID |
| sequence | int | NOT NULL | - | 航点序号 |
| latitude | decimal(12,8) | NOT NULL | - | 纬度 |
| longitude | decimal(12,8) | NOT NULL | - | 经度 |
| altitude | decimal(10,2) | NOT NULL | - | 飞行高度（米） |
| created_at | timestamp | NOT NULL | now() | 创建时间 |

**约束**：
- 同一航线下 sequence 唯一

**业务规则**：
1. 创建/更新航点时所属航线必须为 ACTIVE 状态。
2. 删除航点后自动更新 route.waypoint_count。

---

## 2. 与实现对应

1. 模型：`apps/waypoint/models.py`
2. 序列化与校验：`apps/waypoint/serializers.py`
3. 接口：`apps/waypoint/views.py`

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
