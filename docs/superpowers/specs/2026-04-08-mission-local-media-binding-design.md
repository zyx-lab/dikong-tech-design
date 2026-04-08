# 2026-04-08 Mission Local-Only and Media Binding Design

## 1. 背景

当前 `Mission` 创建链路会调用 DJI `flight-tasks`，并通过 `TenantMissionIndex` 与 `dji_job_id` 维护上游任务映射。当前 `media sync` 也会尝试基于 DJI `job_id` 自动回填 `Mission`。

本次调整确认以下约束：

- 任务由 Django 本地管理，不再调用 DJI `flight-tasks`
- 不引入 `FlightRecord` 作为 `media -> mission` 主关联锚点
- 不依赖实际飞行时间或时间窗口推断 mission
- `media` 必须可追溯，但 mission 关联不能靠猜
- `Mission`、`Media` 软删除不可恢复；`Drone` 仍为软删除，但允许重新认领恢复
- 不做旧表兼容；允许直接删除无效字段、表和运行路径

## 2. 目标

1. 把 `Mission` 收敛为纯本地任务单
2. 把 `Mission.status` 收敛为“是否绑定无人机”这一维度
3. 保持 `Route` 的 DJI KMZ 上传/下载链路不变
4. 保持无人机直播链路不变
5. 让 `media` 先稳定落库，再由本地业务显式绑定到 mission

## 3. 非目标

- 不实现 `media -> mission` 自动智能匹配
- 不接入新的上游任务执行状态同步
- 不引入 `FlightRecord` 自动创建或自动绑定
- 不改造无人机直播能力或 DJI live 协议
- 不为旧数据库结构增加兼容分支、回填逻辑或 fallback 读路径

## 4. 现状问题

### 4.1 Mission 语义混杂

当前 `Mission` 同时承担：

- 本地任务记录
- DJI 上游任务镜像
- 本地状态与上游执行状态混合表达

在不再调用 DJI `flight-tasks` 的前提下，这种语义会失效。

### 4.2 Media 自动关联依据不可靠

当前 `media sync` 主链路依赖 `job_id -> TenantMissionIndex -> mission`。当本地不再创建 DJI 任务后：

- `job_id` 不再是 Django 任务主关联键
- 上游 DJI 没有租户概念
- 也不存在稳定的飞行时间窗口用于自动匹配

继续自动猜 mission 只会放大误绑定风险。

### 4.3 Route 占用语义需要重定义

当前 route 删除阻塞规则使用旧的 mission 执行状态语义。Mission 改为纯本地任务单后，route 更新/删除应只反映“本地是否已被已绑定无人机的任务占用”。

## 5. 核心设计

### 5.1 Mission 改为纯本地任务单

`Mission` 仅表示 Django 本地任务，不再下发 DJI 任务。

保留：

- `Mission` 主表
- mission 的本地 CRUD
- mission 与 `route` / `drone` / `pilot` 的本地关联
- mission 软删除

移除：

- `MissionViewSet.create()` 中对 `DjiGateway().create_mission(...)` 的调用
- `MissionViewSet.destroy()` / `cancel()` 中对 `DjiGateway().cancel_mission(...)` 的调用
- “mission 创建时 route 必须已发布到 DJI”的校验
- `TenantMissionIndex` 整张表及其读写路径
- `missions.dji_job_id`
- `sync_mission_indexes()` 的运行职责

`cancel` 接口不再保留。原因是本次状态模型只有“是否绑定无人机”这一维度，取消语义由 mission 软删除承担。

### 5.2 Mission 状态收敛为无人机绑定状态

`Mission.status` 只保留两个状态：

- `DRONE_UNBOUND`
- `DRONE_BOUND`

状态含义：

- `DRONE_UNBOUND`：mission 当前未绑定无人机
- `DRONE_BOUND`：mission 当前已绑定无人机

状态维护规则：

- `drone is null` 时，`status = DRONE_UNBOUND`
- `drone is not null` 时，`status = DRONE_BOUND`

约束：

- 前端和外部调用方不直接编辑 `status`
- `status` 由 mission 写入逻辑根据 `drone` 是否为空自动维护
- `device_sn`、`drone_name` 继续作为 mission 冗余追溯字段
- 绑定无人机时自动写入 `device_sn`、`drone_name`
- 解绑无人机时清空 `device_sn`、`drone_name`，并同步切回 `DRONE_UNBOUND`

命名明确避免使用 `ASSIGNED`，因为该词在系统权限范围中已经用于“分配给飞手/成员”的语义，不能再复用到“绑定无人机”状态。

### 5.3 Mission 创建与更新规则

mission 仍然是本地业务数据，保留 route/pilot/drone 的租户校验与关系校验。

创建规则：

- route 仍为本地业务必填项
- 不再要求 route 已发布到 DJI
- drone 可为空
- 如果创建时传入 drone，则自动写成 `DRONE_BOUND`
- 如果创建时不传 drone，则自动写成 `DRONE_UNBOUND`

更新规则：

- 允许通过 mission 更新接口绑定或解绑 drone
- 每次 drone 变更时自动维护 `status`、`device_sn`、`drone_name`

删除规则：

- mission 继续软删除
- 软删除不可恢复
- 不提供恢复 API

### 5.4 Media 先落库，再显式绑定 Mission

`MediaFile` 继续保持：

- 一个 media 只能对应一个 mission
- `mission_id` 可为空

`media sync` 的目标改为“可靠落库”，而不是“自动猜测 mission”。

同步时保留：

- `tenant`
- `device_sn`
- `captured_at`
- `dji_file_id`
- `file_name`
- 其他现有媒体元数据

同步时移除：

- 基于 `job_id` 的 mission 自动匹配
- 基于时间窗口的 mission 自动匹配
- 基于最近一条 mission 的猜测绑定

tenant 归属规则：

- 同步时根据 `device_sn` 查找已认领的 `Drone`
- 若存在已认领无人机，则以其 `tenant` 作为 `media` 所属 tenant
- 若无法确定 tenant，则忽略该 media，不落业务数据

mission 绑定规则：

- 默认不自动写入 `MediaFile.mission_id`
- 默认不自动写入 `TenantMediaIndex.mission_id`
- 后续仅通过本地显式绑定接口建立 mission 关联

### 5.5 Media 显式绑定接口

新增本地业务接口：

- `POST /api/v1/media-files/bind-mission`

请求体：

```json
{
  "mission_id": 123,
  "media_file_ids": [10, 11, 12]
}
```

语义：

- 一次请求把多条 media 绑定到同一个 mission
- 关系仍然是 `MediaFile -> Mission` 单值外键
- 不是多对多

校验规则：

- mission 必须存在、属于当前 tenant、且未软删除
- 所有 media 必须存在、属于当前 tenant、且未软删除
- mission 必须处于 `DRONE_BOUND`
- mission 必须存在有效 `device_sn`
- 每条 media 的 `device_sn` 必须等于 mission 的 `device_sn`

写入规则：

- 更新 `MediaFile.mission_id`
- 同步更新 `TenantMediaIndex.mission_id`

覆盖语义：

- 若 media 尚未绑定 mission，则直接绑定
- 若 media 已绑定同一个 mission，则视为幂等成功
- 若 media 已绑定其他 mission，则允许覆盖到新的 mission，并写审计日志

选择允许覆盖的原因：

- 该接口承担显式人工修正职责
- 若禁止覆盖，错误绑定后只能依赖数据库人工修复，运维成本更高

### 5.6 Route 占用规则

route 的 DJI KMZ 上传、更新、下载链路保持不变。

受 mission 新语义影响的只有 route 占用校验：

- 若存在未软删除且 `status = DRONE_BOUND` 的 mission 引用该 route，则禁止更新 route
- 若存在未软删除且 `status = DRONE_BOUND` 的 mission 引用该 route，则禁止删除 route

以下 mission 不阻塞 route 更新/删除：

- `DRONE_UNBOUND`
- 已软删除 mission

这样 route 占用规则明确表达为：

- “已绑定无人机的任务占用 route”
- 而不是“上游 DJI 正在执行 route”

### 5.7 Live 功能不受影响

无人机直播链路不依赖 mission 下发逻辑。

保持不变的内容：

- `/api/v1/drones/{id}/live/capacity`
- `/api/v1/drones/{id}/live/start`
- `/api/v1/drones/{id}/live/stop`
- `/api/v1/drones/{id}/live/video-quality`
- `/api/v1/drones/{id}/live/video-source`

原因：

- live 链路依赖 `Drone`、`device_sn`、`video_id` 和 DJI live 接口
- 不依赖 `TenantMissionIndex`
- 不依赖 `dji_job_id`
- 不依赖 `flight-tasks`
- 不依赖 `media -> mission` 自动绑定

## 6. 数据与模型调整

### 6.1 Mission

保留：

- `tenant`
- `route`
- `drone`
- `pilot`
- `device_sn`
- `drone_name`
- `pilot_name`
- `is_deleted`
- `deleted_at`

调整：

- `status` 枚举改为 `DRONE_UNBOUND / DRONE_BOUND`
- `status` 改为受 `drone` 自动驱动

删除：

- `dji_job_id`

### 6.2 DJI Mission 索引

删除整张表：

- `TenantMissionIndex`

理由：

- mission 已不再与 DJI job 建立主映射关系
- 继续保留只会制造无效状态和无用同步职责

### 6.3 Media

保留：

- `MediaFile.mission` 可空外键
- `MediaFile.device_sn`
- `TenantMediaIndex.mission` 可空外键

不新增新的中间表或映射表。

## 7. API 影响面

### 7.1 Mission API

保留：

- `GET /api/v1/missions`
- `GET /api/v1/missions/{id}`
- `POST /api/v1/missions`
- `PUT /api/v1/missions/{id}`
- `PATCH /api/v1/missions/{id}`
- `DELETE /api/v1/missions/{id}`

移除：

- `POST /api/v1/missions/{id}/cancel`

### 7.2 Media API

保留：

- 现有 media 列表、详情、下载、删除接口

新增：

- `POST /api/v1/media-files/bind-mission`

### 7.3 Route API

保留：

- 现有 route CRUD
- `GET /api/v1/routes/{id}/kmz`

只调整 route 更新/删除时的 mission 占用校验。

### 7.4 DJI 侧能力

不再使用：

- `POST /api/v1/wayline/workspaces/{workspace_id}/flight-tasks`
- DJI job 同步作为 mission 主链路

继续使用：

- route 的 wayline 文件上传/下载/删除
- drone live 相关接口
- media 拉取和 media 下载

## 8. 软删除与追溯语义

- `Mission` 软删除后不可恢复
- `Media` 软删除后不可恢复
- mission/media 删除不提供恢复 API
- `Drone` 软删除保留现有可重新认领恢复逻辑

追溯要求保持：

- mission 记录保留 `device_sn`
- media 记录保留 `device_sn`
- media 通过 `mission_id` 建立显式任务归属

## 9. 测试要求

必须覆盖：

1. mission 创建不再调用 DJI `flight-tasks`
2. mission 创建不再要求 route 已发布到 DJI
3. mission 更新绑定/解绑 drone 时自动维护 `status` 与 `device_sn`
4. mission 删除仅做本地软删除
5. `/missions/{id}/cancel` 不再暴露
6. route 被 `DRONE_BOUND` mission 引用时禁止更新
7. route 被 `DRONE_BOUND` mission 引用时禁止删除
8. `DRONE_UNBOUND` mission 不阻塞 route 更新/删除
9. media sync 在缺少 mission 的情况下仍可按 `device_sn` 正常落库
10. media sync 不再按 `job_id` 自动绑定 mission
11. media 批量绑定 mission 成功路径
12. media 批量绑定跨租户失败
13. media 批量绑定到 `DRONE_UNBOUND` mission 失败
14. media `device_sn` 与 mission `device_sn` 不一致时失败
15. live 系列接口回归通过

## 10. 实现顺序

1. 重构 mission 模型、序列化器、视图和测试，去掉 DJI job 依赖
2. 删除 `TenantMissionIndex`、`dji_job_id` 和 `sync_mission_indexes()` 相关路径
3. 调整 route 更新/删除的 mission 占用规则
4. 收敛 media sync，只保留可靠落库逻辑
5. 新增 media 批量绑定 mission 接口与测试
6. 做 mission/route/media/live 回归验证

## 11. 取舍说明

### 11.1 为什么不自动关联 media 到 mission

因为当前不存在可靠信息源：

- 不再使用 DJI `job_id`
- 不引入 `FlightRecord`
- 不依赖飞行时间窗口

在这些前提下继续自动猜测 mission，只会提高误绑定概率。

### 11.2 为什么 mission 只保留两个状态

因为当前没有稳定执行信号。

把 mission 状态限定为“是否绑定无人机”，可以让状态表达真实、可解释、可维护，不再伪装成执行状态机。

### 11.3 为什么允许 media 绑定覆盖

因为显式绑定接口需要承担人工修正职责。绑定错误时必须能通过业务接口修正，否则只能直接改数据库，成本更高。
