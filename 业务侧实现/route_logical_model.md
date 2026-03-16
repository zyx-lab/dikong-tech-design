# 航线逻辑模型

- updated_at: 2026-03-15T14:35:00Z
- entity: route

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

- table: routes
- 主键: id (BigAutoField)
- 排序规则: `ordering = ['-id']`

## 状态机

| 状态字段 | 值 | 含义 |
|---------|-----|------|
| status | 0 | 禁用 |
| status | 1 | 正常 |
| route_type | 0 | 待扩展 |

## 关系与约束

- tenant -> access.Tenant
- 当前模型无其他显式业务 ForeignKey（`drone_type_id` 为外部 ID 引用位）
- 唯一约束: N/A
- 冗余字段约束：`waypoint_count` 为系统维护字段，只能由航点创建/删除链回写，不允许业务接口直写

## 生命周期入口

| 操作 | 路径 | 说明 |
|-----|------|------|
| 创建 | POST /api/v1/routes | 新增航线 |
| 列表 | GET /api/v1/routes | 航线列表查询 |
| 详情 | GET /api/v1/routes/{id} | 航线详情 |
| 更新 | PUT / PATCH /api/v1/routes/{id} | 全量或局部更新航线 |
| 删除 | DELETE /api/v1/routes/{id} | 删除航线 |
| 启用 | POST /api/v1/routes/{id}/enable | 启用航线 |
| 禁用 | POST /api/v1/routes/{id}/disable | 禁用航线 |

## 接口语义

### 更新航线 PUT / PATCH /api/v1/routes/{id}
- 功能：全量或局部更新航线元数据
- 可写字段：name, route_type, drone_type_id, total_distance, estimated_duration
- 约束：status、creator_name、waypoint_count 不可通过 PATCH 修改
- 业务码：`00000`, `B0001`, `C0404`, `A0401 / A0403`

### 删除航线 DELETE /api/v1/routes/{id}
- 功能：删除航线
- 约束：若被任务引用则软禁用(status=0)，否则物理删除
- 业务码：`00000`, `B0001`, `C0404`, `A0401 / A0403`

### 启用航线 POST /api/v1/routes/{id}/enable
- 状态流转：DISABLED -> ACTIVE
- 有效状态：DISABLED
- 无效状态：ACTIVE
- 业务码：`00000`, `B0001`, `C0404`, `A0401 / A0403`

### 禁用航线 POST /api/v1/routes/{id}/disable
- 功能：按 route 主键显式执行禁用状态动作，与 DELETE 删除语义分离
- 路径：/api/v1/routes/{id}/disable
- 方法：POST
- 状态流转：ACTIVE -> DISABLED
- 有效状态：ACTIVE
- 无效状态：N/A（DISABLED 按幂等成功返回当前状态）
- 约束：请求体必须为空；不承担航点删除、任务解绑或批量停用编排
- 业务码：`00000`, `B0001`, `C0404`, `A0401 / A0403`
