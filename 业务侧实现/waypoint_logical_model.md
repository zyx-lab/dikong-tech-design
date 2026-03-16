# 航点逻辑模型

- generated_at: 2026-03-09T02:39:31.679337Z
- entity: waypoint

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

- table: waypoints
- 主键: id (BigAutoField)

## 状态机

- N/A（航点无状态机）

## 关系与约束

- route -> "route.Route"（FK, PROTECT）
- 唯一约束：同一航线下 sequence 唯一

## 生命周期入口

| 操作 | 路径 | 说明 |
|-----|------|------|
| 创建 | POST /api/v1/waypoints | 新增航点 |
| 列表 | GET /api/v1/waypoints | 航点列表查询 |
| 详情 | GET /api/v1/waypoints/{id} | 航点详情 |
| 更新 | PUT / PATCH /api/v1/waypoints/{id} | 全量或局部更新航点 |
| 删除 | DELETE /api/v1/waypoints/{id} | 删除航点 |

## 接口语义

### 创建航点 POST /api/v1/waypoints
- 功能：向指定航线新增航点
- 必填：route, sequence, latitude, longitude, altitude
- 约束：仅允许写入 ACTIVE 航线
- 业务码：`00000`, `B0001`, `A0401 / A0403`

### 更新航点 PUT / PATCH /api/v1/waypoints/{id}
- 功能：全量或局部更新航点坐标或顺序
- 可写字段：sequence, latitude, longitude, altitude
- 约束：不支持切换所属航线，仅允许更新 ACTIVE 航线下的航点
- 业务码：`00000`, `B0001`, `C0404`, `A0401 / A0403`

### 删除航点 DELETE /api/v1/waypoints/{id}
- 功能：删除单条航点
- 约束：删除后自动更新 route.waypoint_count
- 业务码：`00000`, `C0404`, `A0401 / A0403`
