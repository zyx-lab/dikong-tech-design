# KMZ 航线导入与航线任务参数实现规格

## 1. 目标与范围

在现有 V2 巡检链路上完成两件事：

1. 允许前端导入 DJI WPML KMZ，并将导入后的航线用于任务创建。
2. 创建机场自动航线任务时，支持截图中本期可以由 DJI Cloud API 直接表达的任务参数。

本期坚持复用现有接口，不增加重复的“导入航线”或“建立航线任务”入口：

```text
导入 KMZ     POST /api/v2/inspection/routes
替换 KMZ     PUT  /api/v2/inspection/routes/{id}
创建任务     POST /api/v2/inspection/missions
编辑任务     PUT  /api/v2/inspection/missions/{id}
开始任务     POST /api/v2/inspection/missions/{id}/start
```

本期只支持截图中的“普通任务 + 立即执行”。单次定时、重复定时、连续执行、自动断点续飞和多模态识别不纳入本次实现，原因见第 9 节。

## 2. 现有链路

### 2.1 KMZ 导入

现有接口已经完整覆盖导入动作：

```text
multipart KMZ
  -> RouteCreateSerializer
  -> parse_route_kmz
  -> WaypointRoute + Waypoint
  -> DjiConnectionGateway.upload_route
  -> Java /waylines/files/upload
  -> WaypointRouteCloudFile
```

`WaypointRouteCloudFile` 已持久化任务启动所需的 `dji_file_id`、`wayline_type` 和 DJI 连接，不再建立另一张“导入记录”表。

### 2.2 任务创建与启动

现有任务创建接口保存本地 `InspectionMission`，启动时才调用 Java：

```text
POST /missions
  -> InspectionMission(PENDING)

POST /missions/{id}/start
  -> start_mission
  -> DjiConnectionGateway.create_dock_flight_task
  -> Java POST /flight-tasks
  -> MQTT flighttask_prepare + flighttask_execute
```

新增参数沿这条链路透传，不增加 Django 到 Java 的第二种调用方式。

## 3. 本期字段

### 3.1 API 字段

给 `MissionWriteSerializer` 增加以下 camelCase 字段：

| 字段 | 类型 | 必填 | 默认值 | 范围/枚举 | DJI 字段 |
|---|---|---|---|---|---|
| `waylinePrecisionType` | int | 否 | `1` | `0` GNSS/GPS，`1` RTK | `wayline_precision_type` |
| `rthMode` | int | 否 | `1` | `0` 最佳高度，`1` 预设高度 | `rth_mode` |
| `rthAltitude` | int | 否 | `30` | `20..500` 米 | `rth_altitude` |
| `exitWaylineWhenRcLost` | int | 否 | `1` | `0` 继续航线，`1` 执行失联动作 | `exit_wayline_when_rc_lost` |
| `outOfControlAction` | int | 否 | `0` | `0` 返航，`1` 悬停，`2` 降落 | `out_of_control_action` |

默认值采用截图当前选择及现有任务行为：RTK、预设高度 30m、航线失联后执行返航。其中精度字段是对当前 Java 未设置 DJI 必填字段的补齐；其余默认值保持现有行为。

### 3.2 不作为任务字段的页面项

| 页面项 | 处理 |
|---|---|
| 任务类型“普通任务” | 本期唯一任务类型，不增加只有一个值的数据库字段 |
| 执行航线 | 继续使用 `routeId` |
| 选择设备 | 继续使用 `droneId`、`dockId` |
| 完成动作 | 继续由 KMZ `wpml:finishAction` 唯一决定 |
| 智能规划最佳返航路线 | 后端字段命名为 `rthMode`，避免误称为服务端路径规划 |

## 4. 请求与响应契约

### 4.1 导入航线

```http
POST /api/v2/inspection/routes
Content-Type: multipart/form-data
```

```text
name=园区航线
djiConnectionId=1
kmzFile=@route.kmz
```

成功返回既有 `RouteReadSerializer`。前端使用响应 `id` 作为任务的 `routeId`，使用 `djiFile.djiFileId` 判断 DJI 文件同步成功。

接口继续执行以下校验：

- 文件扩展名必须是 `.kmz`，内容必须是 ZIP。
- 必须包含 `wpmz/template.kml` 和 `wpmz/waylines.wpml`。
- 必须能解析受支持的 `templateType` 和至少一个航点。
- DJI 上传失败时不得留下可用的本地航线；已上传的孤儿文件 best-effort 删除。

### 4.2 创建任务

```json
{
  "name": "园区巡检",
  "routeId": 12,
  "droneId": 3,
  "dockId": 2,
  "pilotAccountProfileId": 8,
  "scheduledAt": null,
  "waylinePrecisionType": 1,
  "rthMode": 1,
  "rthAltitude": 60,
  "exitWaylineWhenRcLost": 1,
  "outOfControlAction": 0,
  "remark": "普通任务"
}
```

成功响应在既有任务字段外返回同名五个任务参数。任务详情、列表、编辑响应保持一致。

### 4.3 校验规则

1. 新字段只对 `dockId` 模式有效。使用 `executorId` 的 Pilot2 任务如果显式提交任一新字段，返回 `400`，避免“保存但不生效”。Serializer 不设置字段默认值，以便区分“显式提交”和“未提交”。
2. `rthMode=1` 时必须有有效 `rthAltitude`；省略时使用默认 30m。
3. `rthMode=0` 时仍保存 `rthAltitude` 作为设备回退值并正常下发，不建立条件可空逻辑。
4. 本期 `scheduledAt` 只作为本地计划展示字段，不改变 Java `task_type=0` 的立即启动语义；只有用户调用 `/start` 才开始任务。
5. 任务只有在 `PENDING` 状态可编辑；启动后参数不可修改。

## 5. 数据模型

在 `InspectionMission` 增加五个非空小整数字段：

```python
wayline_precision_type = models.PositiveSmallIntegerField(default=1)
rth_mode = models.PositiveSmallIntegerField(default=1)
rth_altitude = models.PositiveSmallIntegerField(default=30)
exit_wayline_when_rc_lost = models.PositiveSmallIntegerField(default=1)
out_of_control_action = models.PositiveSmallIntegerField(default=0)
```

枚举和取值校验放在 Django 枚举/serializer 与 Java enum 中。数据库不增加 JSON 配置字段，也不增加独立任务设置表。

迁移只添加带默认值字段，历史任务按现有固定行为回填。

任务启动后，已有 `MissionCloudExecution.raw_request` 保存实际下发参数，作为该次执行快照；无需在 execution 表重复建五列。

## 6. Django 实现

### 6.1 Serializer 和视图

`MissionWriteSerializer`：

- 增加五个字段及枚举/范围校验。
- 保持 `StrictSerializer`，未知字段仍返回错误。
- 在对象级校验中限制新字段只能用于 `dockId`。

`MissionReadSerializer` 返回五个 camelCase 字段。

`MissionListCreateView.post` 和 `MissionDetailView.put` 将 validated data 保存到 `InspectionMission`：

- 创建时省略新字段，由模型默认值生效。
- 编辑时省略新字段，保留任务原值；老版本前端更新任务不能把已设置参数重置。
- 每个服务内的默认值只定义在模型枚举/常量这一处，避免 serializer、view、service 各写一份不同数字；Django 与 Java 用契约测试保证一致。

### 6.2 启动服务

`start_mission` 的 Dock 分支从 mission 取值，构造：

```python
request_payload = {
    "mission_name": mission.name,
    "file_id": route_cloud_file.dji_file_id,
    "dock_sn": mission.dock_sn,
    "wayline_type": int(route_cloud_file.wayline_type),
    "task_type": 0,
    "rth_altitude": mission.rth_altitude,
    "out_of_control_action": mission.out_of_control_action,
    "rth_mode": mission.rth_mode,
    "wayline_precision_type": mission.wayline_precision_type,
    "exit_wayline_when_rc_lost": mission.exit_wayline_when_rc_lost,
}
```

同一 payload 用于调用 gateway 和保存 `MissionCloudExecution.raw_request`，避免审计快照与真实请求不一致。

`DjiConnectionGateway.create_dock_flight_task` 和兼容包装 `create_mission` 增加三个当前缺失参数，并输出 Java REST 使用的 snake_case 字段。

## 7. Java 实现

### 7.1 REST 参数和持久化

`CreateJobParam` 增加：

```java
@NotNull private RthModeEnum rthMode;
@NotNull private WaylinePrecisionTypeEnum waylinePrecisionType;
@NotNull private ExitWaylineWhenRcLostEnum exitWaylineWhenRcLost;
```

现有 `rthAltitude` 和 `outOfControlAction` 继续复用。

虽然本期只创建立即任务，Java 会先落 `wayline_job` 再 prepare，因此三个字段必须加入：

- `WaylineJobEntity`
- `WaylineJobDTO`
- `WaylineJobServiceImpl` entity/DTO 映射
- `mysql/init.sql`、`mysql/cloud_sample.sql`、`sql/cloud_sample.sql`

列名：

```sql
rth_mode                  int NOT NULL DEFAULT 1
wayline_precision_type    int NOT NULL DEFAULT 1
exit_wayline_when_rc_lost int NOT NULL DEFAULT 1
```

不要只修改初始化 SQL。现有部署需要提供一份幂等或有版本标识的增量 SQL，由部署步骤在发布 Java 前执行。

### 7.2 MQTT prepare

`FlightTaskServiceImpl.prepareFlightTask` 改为：

```java
new FlighttaskPrepareRequest()
    // 既有字段
    .setRthMode(waylineJob.getRthMode())
    .setWaylinePrecisionType(waylineJob.getWaylinePrecisionType())
    .setExitWaylineWhenRcLost(waylineJob.getExitWaylineWhenRcLost());
```

删除当前写死的：

```java
.setExitWaylineWhenRcLost(ExitWaylineWhenRcLostEnum.EXECUTE_RC_LOST_ACTION)
```

### 7.3 返回 job ID

当前 Java `publishFlightTask` 返回空 success，而 Django 要求响应中包含 `dji_job_id`。本期同时修正立即任务响应，可直接返回单键 `Map`，不增加一次性响应类：

```json
{
  "dji_job_id": "生成的 job UUID"
}
```

由于本期一个请求只创建一个立即任务，响应只返回一个 ID，不设计批量 IDs。

## 8. 测试与验收

### 8.1 Django 自动化

1. 现有 KMZ 创建接口继续通过，返回 route、航点和 DJI file ID。
2. 创建 Dock 任务时五个默认值正确持久化并返回。
3. 创建/编辑任务时自定义值正确持久化。
4. 非法枚举及 `rthAltitude < 20`、`> 500` 返回 `400`。
5. Pilot2 显式提交 Dock 参数返回 `400`。
6. 启动任务时 gateway 收到五个任务参数。
7. `MissionCloudExecution.raw_request` 与 gateway 实际参数一致。
8. 既有未传新字段的任务测试不回归。

运行：

```bash
.venv/bin/python manage.py test apps.dji_cloud.test_gateway_logging --keepdb
.venv/bin/python manage.py test apps.inspection_v2.tests.InspectionV2ApiTests --keepdb
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py check
```

### 8.2 Java 自动化

1. Controller 接受三个新增枚举字段。
2. entity/DTO 往返映射不丢字段。
3. `flighttask_prepare` 请求包含五个任务参数。
4. 立即任务 REST 响应返回创建的 `dji_job_id`。
5. 无效枚举和高度被 Bean Validation/反序列化拒绝。

运行项目现有 Maven 测试，并至少包含聚焦测试：

```bash
cd feature/DJI-Cloud-API-Demo-main
./mvnw test
```

如果仓库没有 `mvnw`，使用已安装的 `mvn test`。

### 8.3 真机验收

使用 Dock 2 或 Dock 3 + 配对飞行器分别验证：

1. 导入真实 KMZ 后可以启动普通任务。
2. GNSS 和 RTK 两种精度均能在 `flighttask_prepare` 抓包中看到正确值。
3. 预设高度与最佳高度两种返航模式正确下发。
4. 预设返航高度正确下发。
5. 航线失联“继续执行”和“执行失联动作”正确下发。
6. 执行失联动作选择返航时正确下发。

真机验收不得通过实际断网制造不受控飞行；优先使用 DJI 调试环境、地面安全条件或抓包确认。

## 9. 后续阶段

### 9.1 单次定时、重复定时、连续执行

Java 已能表达定时和多日期任务，但 Django 当前 `/start` 会立即把 mission 标记为 `RUNNING`、创建 `FlightSession` 并启动直播。这与“未来才起飞”的定时任务语义不一致。本期不把 `scheduledAt` 草率映射为 `task_type=1`。

后续实现需先增加 `SCHEDULED/PREPARING` 等状态，处理任务占用、直播启动时间、取消和 Java 多 job ID 映射。重复定时和连续执行还需要一对多执行实例，不能继续使用当前 mission 与 cloud execution 的 OneToOne 关系。

### 9.2 自动断点续飞

完整闭环是：

```text
flighttask_progress.break_point
  -> 持久化断点
  -> 创建后继 job
  -> flighttask_prepare.break_point
  -> 跟踪 parent job / child job
```

当前 Django 只保存 `raw_last_event`，Java 虽有 breakpoint SDK 类型但 REST 未暴露。只增加 `autoResume=true` 不会产生续飞行为，因此作为独立阶段实现。

### 9.3 多模态识别

该字段不属于 DJI 航线任务协议。必须先确定媒体输入、识别服务、触发时机、结果模型和失败重试，再决定是否在 mission 上保存开关，作为独立识别功能实现。

### 9.4 完成动作

完成动作已经由 KMZ `wpml:finishAction` 定义。任务接口不再提供同义字段，避免同一任务出现两个互相冲突的来源。

## 10. 实施顺序

1. Django 测试先行：任务字段校验、持久化、读取和启动透传。
2. Django 模型、迁移、serializer、view、service、gateway 最小改动。
3. Java 测试先行：参数映射、MQTT prepare、job ID 响应。
4. Java DTO/entity/service/SQL 最小改动。
5. 跑 Django 和 Java 回归测试、迁移检查及接口文档同步检查。
6. 联调上传 KMZ、创建任务、启动任务，检查 Django `raw_request`、Java 数据库记录及 MQTT payload 三者一致。
7. 最后进行安全的 Dock 2/Dock 3 真机验收。
8. 再按第 9 节依次设计单次定时、重复/连续执行、断点续飞和多模态识别；每个阶段独立验收，不在普通立即任务中预埋空字段。

## 11. 完成标准

1. 不新增重复 KMZ 或任务接口。
2. 现有 KMZ 导入接口可直接生成任务可引用的 `routeId`。
3. 五个本期任务参数能创建、编辑、读取和持久化。
4. 五个参数从 Django 透传至 Java，再进入 `flighttask_prepare`。
5. Java 创建立即任务后向 Django 返回唯一 `dji_job_id`。
6. 历史调用方不传新字段时保持现有执行行为。
7. Django/Java 自动化、迁移和接口文档检查通过。
8. 后续阶段能力未以无效字段或空实现混入本期。
