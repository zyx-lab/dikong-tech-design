# DJI 适配接入边界设计（单上游资源池 / BFF 模式）

## 1. 结论
1. tenant 隔离只在我方系统内实现；DJI 的 `workspace`、`user` 只作为 `DjiGateway` 内部托管的上游实现细节，不作为 Django 系统的租户边界，也不作为前端要理解的业务概念。
2. `DjiGateway` 对 Django 系统只暴露“单上游资源池”语义：Django 只感知直播系统拥有的一组无人机、航线、任务、媒体资源，不感知上游到底有几个 `workspace`、几个 `user`。
3. `Drone` 在业务上定义为“上游设备映射后的租户资源”，不是纯本地资产台账；`POST /api/v1/drones` 的语义是认领，而不是手工创建设备主记录。
4. 前端永远只调用我方 `/api/v1/*` 业务接口，不直接调用 DJI；前端不感知上游认证、`workspace` / `user` 拓扑，但在直播子域允许复用上游参数和返回语义。
5. 对于 DJI 已经承接的设备拓扑、航线文件、任务执行、媒体存储、直播能力，我方不重复实现；我方只做 BFF 代理、tenant 可见性过滤、权限、审计和最小映射。
6. 对外接口继续使用业务语义：`drones`、`routes`、`missions`、`media_files`、`drone-assignments`、`health`；不对前端暴露 `integrations/dji/*`。
7. 首批最简单方案：按“单 user / 单 workspace / 单资源池”落地；设备采用“上游共享池 + 按 `device_sn` 本地认领”；航线、任务、媒体采用“我方业务 API + 上游执行/存储 + 本地最小索引”，不预埋额外多 workspace / 多 user 抽象。

---

## 2. 最简单隔离方案（Django 只看单上游资源池）
1. 平台侧只维护一个 `DjiWorkspaceConfig`，保存当前上游 `workspace_id`、会话刷新配置等内部信息；这些信息只在 `DjiGateway` 内部使用，不进入 Django 业务语义。
2. `DjiGateway` 后台定时从当前上游资源池同步设备快照到共享索引表 `DjiDeviceIndex(device_sn, last_payload, last_seen_at)`；即便上游未来调整 `workspace` / `user` 结构，Django 侧仍只消费统一资源池结果。
3. 当前按单 user / 单 workspace 的最简单方案落地，不为未来假设场景提前引入多资源池编排、多账号路由、多 workspace 映射等抽象。
4. tenant 隔离只依赖我方最小本地映射，不依赖 DJI 多 workspace：
   - Drone 本身即为认领绑定：`Drone(tenant_id, device_sn, code, org_id, ...)`
   - `TenantRouteIndex(tenant_id, route_id, dji_wayline_id, is_published)`
   - `TenantMissionIndex(tenant_id, mission_id, dji_job_id, execution_status, sync_status, last_sync_at, error_msg)`
   - `TenantMediaIndex(tenant_id, media_file_id, dji_file_id, device_sn, mission_id, sync_status, last_sync_at, error_msg)`
5. tenant 可见性规则：
   - 设备：只有已认领的 Drone 记录对 tenant 可见（`tenant_id + device_sn` 组合唯一）。
   - 航线：只有本 tenant 通过我方 API 创建出来并落了 `TenantRouteIndex` 的航线可见。
   - 任务：只有本 tenant 通过我方 API 创建出来并落了 `TenantMissionIndex` 的任务可见。
   - 媒体：只有能通过 `mission_id` 或 `device_sn` 反查到当前 tenant 的媒体可见。
6. 设备认领最简方案：
   - 平台侧先同步 DJI 设备快照。
   - tenant 管理员直接提交 `device_sn` 发起认领。
   - 我方校验该 `device_sn` 是否存在于共享设备池、是否已被其他 tenant 认领。
   - 校验通过后直接写入 Drone 表（`tenant_id + device_sn` 组合唯一）。
   - DJI 的 `bind_code` 不作为 tenant 认领凭据，只可作为平台内部辅助信息。
7. dock 与 drone 无映射关系：DJI dock 列表和 drone 列表独立，dockSn 在创建任务时由前端传入，我方不建立 dock→device 映射表。
8. 本地不保存 DJI 完整主数据；本地只保存 tenant 归属、最小映射、审计、同步状态和最近一次同步摘要。

---

## 3. 哪些能力必须经由我方系统
1. 上游登录态与工作空间信息
   - DJI：`POST /api/v1/manage/login`、`POST /api/v1/manage/token/refresh`、`GET /api/v1/manage/workspaces/current`
2. 设备与拓扑
   - DJI：`GET /api/v1/manage/workspaces/{workspace_id}/devices`、`GET /api/v1/manage/workspaces/{workspace_id}/devices/{device_sn}`、`GET /api/v1/manage/workspaces/{workspace_id}/docks`、`POST /api/v1/manage/devices/{device_sn}/binding`、`POST /api/v1/manage/devices/{device_sn}/unbinding`
3. 航线
   - DJI：`GET /api/v1/wayline/workspaces/{workspace_id}/waylines`、`GET /api/v1/wayline/workspaces/{workspace_id}/waylines/{wayline_id}/url`、`GET /api/v1/wayline/workspaces/{workspace_id}/waylines/duplicate-names`、`POST /api/v1/wayline/workspaces/{workspace_id}/waylines/files/upload`、`DELETE /api/v1/wayline/workspaces/{workspace_id}/waylines/{wayline_id}`
4. 任务
   - DJI：`POST /api/v1/wayline/workspaces/{workspace_id}/flight-tasks`、`GET /api/v1/wayline/workspaces/{workspace_id}/jobs`、`PUT /api/v1/wayline/workspaces/{workspace_id}/jobs/{job_id}`、`DELETE /api/v1/wayline/workspaces/{workspace_id}/jobs`
5. 媒体
   - DJI：`GET /api/v1/media/workspaces/{workspace_id}/files`、`GET /api/v1/media/workspaces/{workspace_id}/files/{file_id}/url`、`POST /api/v1/storage/workspaces/{workspace_id}/sts`、`POST /api/v1/media/workspaces/{workspace_id}/fast-upload`、`POST /api/v1/media/workspaces/{workspace_id}/upload-callback`、`POST /api/v1/media/workspaces/{workspace_id}/group-upload-callback`
6. 直播
   - DJI：`GET /api/v1/manage/live/capacity`、`POST /api/v1/manage/live/streams/start`、`POST /api/v1/manage/live/streams/stop`、`POST /api/v1/manage/live/streams/update`、`POST /api/v1/manage/live/streams/switch`

原因：这些能力都依赖 `x-auth-token`、`workspace_id`、对象存储临时凭证或上游真实资源 ID，必须先经过我方 tenant 过滤、权限校验、审计和代理；当前系统不涉及 DRC、遥控权、控制权抢占等控制面能力。

---

## 4. 当前 API 修改计划

### 4.1 `/api/v1/`、`/api/v1/iam/*`、`/api/v1/health`
1. `GET /api/v1/`
   - 保持业务入口，不新增任何 `integrations/dji` 发现入口。
   - 继续暴露：`drones`、`drone_assignments`、`routes`、`missions`、`media_files`、`health`。
2. `/api/v1/iam/*`
   - 不改接口合同。
   - 只在服务端增加 DJI 会话保活、权限校验和审计联动。
3. `GET /api/v1/health`
   - 保持不变。
   - 只检查我方业务平面是否存活，不把 DJI 连通性混入同一个健康结果，避免误导调用方。

### 4.2 `/api/v1/drones*`

#### 模型变更
| 变更项 | 说明 |
|--------|------|
| 设备标识统一 | 当前代码已经统一使用 `Drone.device_sn`；不再保留 `serial_no` 业务字段 |
| 状态字段保留 | `Drone.status` 保留，但只由后台同步更新（DJI 设备在线状态），不开放写接口 |
| 状态枚举收敛 | `Drone.status` 只保留 `ENABLED` / `DISABLED`；删除 `MAINTENANCE` / `RETIRED` 两个旧台账状态及对应代码、校验、测试 |
| 删除写 action | 当前实现已删除 `enable`、`disable`、`maintenance`、`retire` 四个 action 接口 |
| 新增共享池表 | `DjiDeviceIndex(device_sn, last_payload, last_seen_at)`：全局共享设备池 |

#### 对外 API
| 接口 | 方法 | 说明 |
|------|------|------|
| `GET /api/v1/drones/available` | GET | 获取可认领设备列表（来自 DjiDeviceIndex 未被认领的设备） |
| `GET /api/v1/drones` | GET | 返回当前 tenant 已认领设备列表 |
| `GET /api/v1/drones/{id}` | GET | 返回单台设备详情 |
| `POST /api/v1/drones` | POST | 认领设备：`{"device_sn": "...", "code": "..."}` |
| `PUT/PATCH /api/v1/drones/{id}` | PUT/PATCH | 只更新本地管理字段（code 等） |
| `GET /api/v1/drones/{id}/live/capacity` | GET | 返回设备直播能力 |
| `POST /api/v1/drones/{id}/live/start` | POST | 启动设备直播 |
| `POST /api/v1/drones/{id}/live/stop` | POST | 停止设备直播 |
| `POST /api/v1/drones/{id}/live/video-quality` | POST | 调整直播画质 |
| `POST /api/v1/drones/{id}/live/video-source` | POST | 切换直播视频源 |

#### 现有接口改动
- `POST /api/v1/drones`：语义改为"按 `device_sn` 认领共享池设备"，请求体为 `{"device_sn": "...", "code": "..."}`
- `code` 只是本地展示字段，不承担稳定唯一标识语义；设备硬标识使用 `device_sn`
- `GET /api/v1/drones`、`GET /api/v1/drones/{id}`：从纯本地台账改为"认领绑定 + 上游同步摘要"的聚合查询
- `Drone.status` 当前只表达“本轮同步是否在线”：同步命中设备记为 `ENABLED`，未命中设备记为 `DISABLED`
- 明确删除对外 `POST /api/v1/drones/{id}/enable`
- 明确删除对外 `POST /api/v1/drones/{id}/disable`
- 明确删除对外 `POST /api/v1/drones/{id}/maintenance`
- 明确删除对外 `POST /api/v1/drones/{id}/retire`

#### 认领流程
1. 平台侧后台定时同步 DJI 设备快照到 `DjiDeviceIndex`
2. 前端调用 `GET /api/v1/drones/available` 获取可认领设备列表
3. 前端提交 `POST /api/v1/drones` 发起认领：`{"device_sn": "...", "code": "...", "name": "..."}`
4. 我方校验 `device_sn` 是否存在于 DjiDeviceIndex 且未被其他 tenant 认领
5. 校验通过后写入 Drone 表（`tenant_id + device_sn` 组合唯一）

#### GET /api/v1/drones/available 实现

```python
def get_available_drones(request):
    # 1. 获取共享池所有设备
    shared_sn_set = set(DjiDeviceIndex.objects.values_list('device_sn', flat=True))

    # 2. 获取已认领设备 SN
    claimed_sn_set = set(Drone.objects.values_list('device_sn', flat=True))

    # 3. 差集 = 可认领
    available_sns = shared_sn_set - claimed_sn_set

    # 4. 返回详情
    return DjiDeviceIndex.objects.filter(device_sn__in=available_sns)
```

- 不分在线过滤，保持简单
- 已下线设备也能认领

#### 直播接口详情
- `GET /api/v1/drones/{id}/live/capacity` 先调用 DJI `GET /api/v1/manage/live/capacity`，再按当前 Drone 的 `device_sn` 过滤，只返回当前 `{id}` 对应设备的能力对象，不透传全量数组。
- `video_id` 只保留实测格式：`{drone_sn}/{camera.index}/{video.index}`。
- `POST /api/v1/drones/{id}/live/video-source` 直接接收 `video_id` 和 `videoType`。
- `POST /api/v1/drones/{id}/live/start`、`stop`、`video-quality`、`video-source` 的 `data` 原样透传 DJI `LiveDTO` 或上游原始成功响应，不做字段裁剪和重命名。
- `GET /api/v1/drones/{id}/live/capacity` 使用 `drone.view_drone` 权限。
- `POST /api/v1/drones/{id}/live/start|stop|video-quality|video-source` 使用 `drone.manage_drone` 权限。

#### 内部调用 DJI
- 后台设备同步：`GET /api/v1/manage/workspaces/{workspace_id}/devices`
- `POST /api/v1/drones`：不调用 DJI 创建设备；只做本地校验和落库
- `GET /api/v1/drones/{id}/live/capacity`：调用 DJI `GET /api/v1/manage/live/capacity` 后按 `device_sn` 过滤
- `POST /api/v1/drones/{id}/live/start`：调用 DJI `POST /api/v1/manage/live/streams/start`
- `POST /api/v1/drones/{id}/live/stop`：调用 DJI `POST /api/v1/manage/live/streams/stop`
- `POST /api/v1/drones/{id}/live/video-quality`：调用 DJI `POST /api/v1/manage/live/streams/update`
- `POST /api/v1/drones/{id}/live/video-source`：调用 DJI `POST /api/v1/manage/live/streams/switch`

### 4.3 `/api/v1/routes*`

#### 模型变更
| 变更项 | 说明 |
|--------|------|
| 聚合边界收敛 | `Route` 作为公开聚合根；`waypoints[]` 只作为内部编辑结构，不再单独暴露 `/api/v1/waypoints*` |
| 新增索引表 | `TenantRouteIndex(tenant_id, route_id, dji_wayline_id, is_published)` |

#### 对外 API
| 接口 | 方法 | 说明 |
|------|------|------|
| `GET /api/v1/routes` | GET | 查询当前 tenant 可见航线列表 |
| `GET /api/v1/routes/{id}` | GET | 查询单条航线详情 |
| `POST /api/v1/routes` | POST | 创建本地 route 草稿，可带完整 `waypoints[]` |
| `PUT/PATCH /api/v1/routes/{id}` | PUT/PATCH | 更新本地 route 草稿；提交 `waypoints[]` 时整条航线全量替换 |
| `POST /api/v1/routes/{id}/publish` | POST | 显式把当前 route 草稿发布到 DJI |
| `DELETE /api/v1/routes/{id}` | DELETE | 删除航线（同步删除 DJI + 清理 TenantRouteIndex） |
| `GET /api/v1/routes/{id}/download` | GET | 下载当前已发布航线文件 |

#### 现有接口改动
- 保留 `routes` 的核心 CRUD 路径，但把“本地编辑”和“发布到 DJI”明确拆开
- 明确删除对外 `POST /api/v1/routes/{id}/sync`
- 明确删除对外 `DELETE /api/v1/routes/{id}/sync`
- 明确删除对外 `POST /api/v1/routes/{id}/favorite`
- 明确删除对外 `DELETE /api/v1/routes/{id}/favorite`

#### 当前航线草稿流程
1. 前端调用 `POST /api/v1/routes` 创建本地 route 草稿。
2. 请求体可以带完整 `waypoints[]`；写入时按整条航线全量落内部 waypoint 行。
3. 系统自动创建 `TenantRouteIndex(dji_wayline_id=\"\", is_published=false)`。
4. 之后前端可通过 `PUT / PATCH /api/v1/routes/{id}` 持续编辑本地草稿。
5. 任意本地编辑后，`is_published` 都会被置回 `false`。

#### 发布流程
1. 前端调用 `POST /api/v1/routes/{id}/publish`。
2. 系统从当前内部 waypoint 行生成 KMZ。
3. 调用 DJI `POST /api/v1/wayline/workspaces/{workspace_id}/waylines/files/upload` 上传。
4. 上传成功后把新的 `dji_wayline_id` 写回 `TenantRouteIndex`，并设置 `is_published=true`。
5. 若此前已有旧的已发布 DJI 航线，则在新航线上传成功后删除旧航线。

#### 内部调用 DJI
- `POST /api/v1/routes`：只落本地库，不调用 DJI
- `PUT/PATCH /api/v1/routes/{id}`：只更新本地草稿，不调用 DJI
- `POST /api/v1/routes/{id}/publish`：生成 KMZ → 上传 DJI → 回写 `dji_wayline_id` / `is_published`
- `DELETE /api/v1/routes/{id}`：调用 `DELETE /api/v1/wayline/workspaces/{workspace_id}/waylines/{wayline_id}`
- `GET /api/v1/routes/{id}/download`：
  1. 校验 `is_published=true` 且 `dji_wayline_id` 非空
  2. 调用 DJI 下载地址：`GET /api/v1/wayline/workspaces/{workspace_id}/waylines/{wayline_id}/url`
  3. 透传 302 重定向，让前端直接访问 DJI 地址
  4. 不做代理下载

#### 航线下载实现

```python
def download_route(request, route_id):
    route = Route.objects.get(id=route_id)
    tenant_route = TenantRouteIndex.objects.get(route=route)
    if not tenant_route.is_published or not tenant_route.dji_wayline_id:
        raise ValidationError("航线尚未发布")

    # 获取 DJI 下载地址
    gateway = DjiGateway()
    url = gateway.get_wayline_url(tenant_route.dji_wayline_id)

    # 透传 302，让前端直接访问 DJI 地址
    return redirect(url)
```

### 4.4 `/api/v1/missions*`

#### 模型变更
| 变更项 | 说明 |
|--------|------|
| 新增索引表 | `TenantMissionIndex(tenant_id, mission_id, dji_job_id, execution_status, last_sync_at)` |
| 状态来源变更 | `Mission.status` 由后台同步任务更新，不再由 action 变更 |
| 删除校验 | 删除 `clean()` 中的 `drone.status == ENABLED` 校验（DJI 设备在线状态由后台同步） |

#### 对外 API
| 接口 | 方法 | 说明 |
|------|------|------|
| `GET /api/v1/missions` | GET | 查询任务列表 |
| `GET /api/v1/missions/{id}` | GET | 查询单条任务详情 |
| `POST /api/v1/missions` | POST | 创建任务（立即同步创建 DJI job） |
| `PUT/PATCH /api/v1/missions/{id}` | PUT/PATCH | 只更新本地管理字段（名称、备注等） |
| `POST /api/v1/missions/{id}/cancel` | POST | 取消任务 |

#### 现有接口改动
- 只保留 `cancel` 动作接口
- 明确删除对外 `POST /api/v1/missions/{id}/start`
- 明确删除对外 `POST /api/v1/missions/{id}/pause`
- 明确删除对外 `POST /api/v1/missions/{id}/resume`
- 明确删除对外 `POST /api/v1/missions/{id}/complete`
- 明确删除对外 `POST /api/v1/missions/{id}/fail`
- `Mission.status` 由后台同步任务更新，不开放写接口

#### dockSn 透传
- 创建任务时前端传入 `dock_sn`
- 我方透传给 DJI API，不做映射解析
- 不对外暴露 dock 相关 API

#### 内部调用 DJI
- `POST /api/v1/missions`：
  1. 创建本地 Mission（status=PENDING）
  2. 调用 `POST /api/v1/wayline/workspaces/{workspace_id}/flight-tasks`
  3. 回查获取 job_id（GET /jobs 按 name 匹配）
  4. 写入 `Mission.dji_job_id` 和 `TenantMissionIndex`
  5. 同一事务，成功则全成功，失败则全回滚
- `PUT/PATCH /api/v1/missions/{id}`：只更新本地字段，不调用 DJI
- `POST /api/v1/missions/{id}/cancel`：
  1. 校验 `dji_job_id` 不为空
  2. 调用 `DELETE /api/v1/wayline/workspaces/{workspace_id}/jobs?job_id=xxx`
  3. DJI 成功后更新本地 Mission.status=CANCELED
- 后台同步任务：调用 `GET /api/v1/wayline/workspaces/{workspace_id}/jobs`，刷新 `execution_status`、`last_sync_at`

#### Mission 创建与取消详细流程

**创建任务流程**：
```python
@transaction.atomic
def create_mission_and_sync(request):
    with transaction.atomic():
        # 1. 创建本地记录
        mission = Mission.objects.create(
            name=request.data["name"],
            status=MissionStatus.PENDING
        )

        # 2. 调 DJI 创建
        gateway = DjiGateway()
        gateway.create_job(
            name=mission.name,
            file_id=request.data["file_id"],
            dock_sn=request.data["dock_sn"],
            ...
        )

        # 3. 回查获取 job_id
        job_id = gateway.get_job_id_by_name(mission.name)
        if not job_id:
            # 回查失败，事务回滚
            raise ServiceError("D0001", "任务同步失败，请重试")

        # 4. 写入 dji_job_id
        mission.dji_job_id = job_id
        mission.save()

        # 5. 创建索引
        TenantMissionIndex.objects.create(
            mission=mission,
            dji_job_id=job_id,
            sync_status=SyncStatus.SYNCED
        )
```

**取消任务流程**：
```python
def cancel(self, request, *args, **kwargs):
    mission = self.get_object()

    if not mission.dji_job_id:
        raise ValidationError({"detail": "任务尚未同步到 DJI，无法取消"})

    # 1. 先调 DJI DELETE
    gateway = DjiGateway()
    gateway.delete_job(mission.dji_job_id)

    # 2. DJI 成功后再改本地状态
    mission.status = MissionStatus.CANCELED
    mission.save()
```

**失败处理**：
| 场景 | 处理 |
|------|------|
| DJI DELETE 失败 | 返回 502，不改本地状态 |
| DJI 返回 404（job 已删除） | 本地标记 CANCELED（幂等） |
| dji_job_id 为空 | 返回 400，提示"任务尚未同步" |
| Mission 创建失败 | 事务回滚，不留半成功状态 |

**Mission.name 与 DJI job name**：
- 直接透传，不做重名校验
- DJI 重名错误 → 返回 400 + DJI 错误消息
- DJI 重名成功 → 不管（DJI 允许重名）

### 4.5 `/api/v1/media_files*`

#### 模型变更
| 变更项 | 说明 |
|--------|------|
| 新增索引表 | `TenantMediaIndex(tenant_id, media_id, dji_file_id, device_sn, mission_id, sync_status)` |

#### 对外 API（只读）
| 接口 | 方法 | 说明 |
|------|------|------|
| `GET /api/v1/media_files` | GET | 查询当前 tenant 可见媒体列表 |
| `GET /api/v1/media_files/{id}` | GET | 查询单条媒体详情 |
| `GET /api/v1/media_files/{id}/download` | GET | 下载媒体文件 |

#### 现有接口改动
- 只保留 GET 和 download 两个读接口
- 明确删除对外 `POST /api/v1/media_files`
- 明确删除对外 `PUT/PATCH /api/v1/media_files/{id}`
- 明确删除对外 `DELETE /api/v1/media_files/{id}`
- 媒体不由前端手工驱动，由后台同步和上游存储事实驱动

#### 媒体归属链路
1. 优先按 `job_id -> mission_id` 归属链路
2. 若上游媒体列表无法稳定给出任务关联，退回 `device_sn + 时间窗口` 推断
3. 通过 TenantMediaIndex 关联 tenant

#### 内部调用 DJI
- 后台同步任务：调用 `GET /api/v1/media/workspaces/{workspace_id}/files`，更新 TenantMediaIndex
- `GET /api/v1/media_files/{id}/download`：
  1. 获取 DJI 下载地址：`GET /api/v1/media/workspaces/{workspace_id}/files/{file_id}/url`
  2. 透传 302 重定向，让前端直接访问 DJI 地址
  3. 不做代理下载，不做 URL 缓存（DJI URL 可能有时效）

### 4.6 `/api/v1/drone-assignments/*`
1. 对外 API
   - `GET /api/v1/drone-assignments`
   - `GET /api/v1/drone-assignments/{id}`
   - `POST /api/v1/drone-assignments`
   - `POST /api/v1/drone-assignments/{id}/cancel`
2. 现有接口改动
   - 保留查询、创建、取消三类必要动作。
   - 明确删除对外 `POST /api/v1/drone-assignments/{id}/reactivate`
   - 新增校验：只允许对当前 tenant 已认领设备建立分配关系。
3. 内部调用 DJI
   - 当前不调用 DJI。
   - 若后续需要设备占用校验，只通过定义好的内部接口读取设备归属和任务状态，不直接把 DJI 接口引入 `drone-assignments` 模块。
4. 边界
   - `drone-assignments` 是独立本地模块，只处理“tenant 成员 - 已认领设备”的业务分配关系，不引入额外状态恢复动作。

---

## 5. 首批新增对外 API

### 新增接口
| # | 接口 | 说明 |
|---|------|------|
| 1 | `GET /api/v1/drones/available` | 获取可认领设备列表（来自 DjiDeviceIndex 未被认领的设备） |
| 2 | `GET /api/v1/routes/{id}/download` | 下载航线文件 |
| 3 | `GET /api/v1/drones/{id}/live/capacity` | 获取设备直播能力 |
| 4 | `POST /api/v1/drones/{id}/live/start` | 启动设备直播 |
| 5 | `POST /api/v1/drones/{id}/live/stop` | 停止设备直播 |
| 6 | `POST /api/v1/drones/{id}/live/video-quality` | 调整直播画质 |
| 7 | `POST /api/v1/drones/{id}/live/video-source` | 切换直播视频源 |

### 删除接口
| # | 接口 | 说明 |
|---|------|------|
| 1 | `POST /api/v1/drones/{id}/enable` | 已废弃，设备状态由后台同步更新 |
| 2 | `POST /api/v1/drones/{id}/disable` | 已废弃 |
| 3 | `POST /api/v1/drones/{id}/maintenance` | 已废弃 |
| 4 | `POST /api/v1/drones/{id}/retire` | 已废弃 |
| 5 | `POST /api/v1/routes/{id}/sync` | 已废弃 |
| 6 | `DELETE /api/v1/routes/{id}/sync` | 已废弃 |
| 7 | `POST /api/v1/routes/{id}/favorite` | 已废弃 |
| 8 | `DELETE /api/v1/routes/{id}/favorite` | 已废弃 |
| 9 | `POST /api/v1/missions/{id}/start` | 已废弃，任务状态由后台同步更新 |
| 10 | `POST /api/v1/missions/{id}/pause` | 已废弃 |
| 11 | `POST /api/v1/missions/{id}/resume` | 已废弃 |
| 12 | `POST /api/v1/missions/{id}/complete` | 已废弃 |
| 13 | `POST /api/v1/missions/{id}/fail` | 已废弃 |
| 14 | `POST /api/v1/media_files` | 已废弃，媒体由后台同步创建 |
| 15 | `PUT/PATCH /api/v1/media_files/{id}` | 已废弃 |
| 16 | `DELETE /api/v1/media_files/{id}` | 已废弃 |
| 17 | `POST /api/v1/drone-assignments/{id}/reactivate` | 已废弃 |

说明：
- 首批不新增任何 `integrations/dji/*` 风格对外接口，避免让用户感知 DJI 集成模型
- DJI 直播能力纳入首批，采用"业务路径 + 上游直播语义透传"模式
- 首批不新增本地直播会话、控制会话、DRC 等额外接口和概念

---

## 6. 模块划分（Modularity）
1. `DjiGateway`
   - 定位是 Django 管理系统到 DJI/直播系统之间的中间层，对 Django 业务模块只暴露一个统一的上游资源池。
   - 负责 `login`、`token refresh`、`workspace` 获取、统一签名和 HTTP 调用。
   - 所有 DJI 的设备、直播、媒体、航线、任务接口都要求服务端先持有并维护 `x-auth-token`；前端不直接登录 DJI。
   - 业务模块不直接拼接 DJI URL，也不直接处理 `workspace`、`user`、`x-auth-token` 等上游会话细节。
2. `TenantVisibilityService`
   - 负责 tenant 归属、索引映射、`device_sn` 认领校验、可见性过滤。
3. `DeviceModule`
   - 负责设备索引同步、认领、设备详情聚合、直播控制。
4. `RouteModule`
   - 负责航线 CRUD 的 BFF 代理、下载代理、同步状态维护。
5. `MissionModule`
   - 负责任务创建、状态流转、执行状态聚合；`dockSn` 由前端在创建请求中传入，我方透传给 DJI。
6. `MediaModule`
   - 负责媒体索引同步、下载代理、媒体可见性过滤。
7. `AssignmentModule`
   - 负责 tenant 内设备分配，不直接依赖 DJI。

模块约束：所有业务模块只通过 `DjiGateway` 和 `TenantVisibilityService` 与上游和隔离规则交互，不跨模块直接读写彼此内部表。

### 6.1 DJI 上游认证与资源池托管方案
1. 资源池语义
   - `DjiGateway` 对 Django 业务模块暴露的是统一资源池，而不是 `workspace` / `user` 管理界面。
   - 对 Django 来说，只存在“当前可用上游无人机资源、航线资源、任务资源、媒体资源”；上游账号结构是 `DjiGateway` 内部实现细节。
2. 登录入口
   - 由服务端 `DjiGateway` 调用 DJI `POST /api/v1/manage/login` 获取 `access_token`、`workspace_id`、`mqtt_username`、`mqtt_password`、`mqtt_addr`。
   - 登录凭据只保存在服务端配置中，不下发给浏览器或前端应用。
3. token 与 workspace 托管
   - `DjiGateway` 在内存或服务端受控存储中缓存当前 `access_token` 和 `workspace_id`。
   - 所有业务模块调用 DJI 时都只能向 `DjiGateway` 传业务参数，由 `DjiGateway` 统一补齐 `x-auth-token` 和 `workspace_id`。
4. 刷新与重登
   - 服务端优先调用 DJI `POST /api/v1/manage/token/refresh` 续期登录态。
   - 若 refresh 失败、token 失效或上游返回未登录，则 `DjiGateway` 使用服务端托管凭据重新调用 `POST /api/v1/manage/login`，再重试原请求一次。
5. 单 user / 单 workspace 的当前策略
   - 当前实现按单 user / 单 workspace 落地，直接托管一套上游登录态即可。
   - 在没有真实业务需求前，不增加多 user 切换、多 workspace 映射、多资源池路由等设计。
6. 与直播的关系
   - `GET /api/v1/drones/{id}/live/capacity`、`POST /api/v1/drones/{id}/live/start|stop|video-quality|video-source` 调用前，都默认依赖 `DjiGateway` 已持有有效 `x-auth-token`。
   - 直播子域采用“业务路径 + 上游直播语义透传”模式，但 `live/capacity` 只返回当前设备能力对象，不返回 DJI capacity 全量数组。
   - 直播 `data` 保持透传 DJI `LiveDTO` 或上游原始成功响应，不做字段裁剪和重命名。
7. 失败处理
   - 若重新登录后仍失败，则业务请求失败，按我方统一错误格式返回，不要求前端单独登录 DJI。
   - 不允许把 DJI 原始登录页、账号口令、`x-auth-token`、`mqtt_*` 暴露给前端。

---

## 7. 接口设计约束（POLA / KISS）
1. 对外只保留业务路径，不暴露 DJI 登录态、`workspace` / `user` 拓扑和真实上游 URL。
2. Django 业务层和前端都只感知统一资源池，不感知上游 `workspace`、`user` 拓扑。
3. 不对前端暴露 `sync`、`workspace`、`wayline`、`job`、`file` 等上游管理概念；但直播子域允许复用上游参数与返回语义。
4. 一个业务动作只对应一个业务接口，不让调用方理解“两阶段创建 + 手动同步”。
5. 上游失败则业务请求失败，不制造“本地成功、上游失败”的半成功状态。
6. 后台同步只用于修复摘要、状态和索引，不改变前端接口语义。
7. DJI 登录、token 刷新、`workspace_id`、`mqtt_*`、`x-auth-token` 统一由服务端 `DjiGateway` 托管；浏览器和前端应用永远不直接登录 DJI。
8. 当 DJI token 失效时，业务模块不得要求前端重试登录 DJI；应由服务端自动刷新或重新登录后再继续调用。
9. 当前按单 user / 单 workspace 直接实现，不为尚未出现的多账号、多工作空间场景预埋额外接口和抽象。

---

## 8. 涉及文件
1. 现有文件
   - `apps/api_v1/views.py`
   - `apps/api_v1/urls.py`
   - `apps/drone/views.py`
   - `apps/route/views.py`
   - `apps/mission/views.py`
   - `apps/media_file/views.py`
   - `apps/drone_assignment/views.py`
   - `apps/access/management/commands/seed_role_permissions.py`
2. 新增内部模块
   - `apps/dji_bff/gateway.py`
   - `apps/dji_bff/models.py`
   - `apps/dji_bff/device_module/*`
   - `apps/dji_bff/route_module/*`
   - `apps/dji_bff/mission_module/*`
   - `apps/dji_bff/media_module/*`
   - `apps/dji_bff/visibility/*`

---

## 10. 设计决策确认清单

> 本章节记录设计评审中确认的关键决策，供实现时参考。

### 10.1 设备认领与 DJI Binding API 关系

| 决策项 | 说明 |
|--------|------|
| **核心原则** | 认领是纯本地 tenant 隔离机制，不调用 DJI binding API |
| **原因** | DJI binding 是将设备绑定到 workspace（目前所有设备已在同一 workspace），我方"认领"是 `Drone(tenant_id + device_sn)` 本地组合唯一约束 |
| **行为** | DJI 设备对所有 tenant 可见，但只有认领者能操作；不需要传 tenant 信息给 binding 接口 |

### 10.2 任务状态同步

| 决策项 | 说明 |
|--------|------|
| **同步方向** | 单向同步，`Mission.status` 由后台同步任务驱动 |
| **同步来源** | 调用 `GET /api/v1/wayline/workspaces/{workspace_id}/jobs` 同步状态 |
| **保留接口** | 只保留 `cancel` 接口（对应 DJI `DELETE /jobs`），因为 cancel 是业务终止而非状态机流转 |
| **删除接口** | 明确删除 `start`、`pause`、`resume`、`complete`、`fail` 五个 action 接口 |
| **同步周期** | 建议 30s~1min，前端轮询或 websocket 推送 |
| **禁止事项** | 不提供本地强制状态变更（如本地设为"完成"） |

### 10.3 dock_sn 获取

| 决策项 | 说明 |
|--------|------|
| **接口** | 新增 `GET /api/v1/docks` 接口 |
| **实现** | 透传 DJI `GET /api/v1/manage/workspaces/{workspace_id}/docks` |
| **映射** | 不建立 dock→device 映射表，dockSn 作为任务创建参数直接透传给 DJI |
| **原因** | 前端需要知道可用 dock_sn 才能创建任务 |

### 10.4 video_id 组装格式

| 决策项 | 说明 |
|--------|------|
| **实测格式** | `1581F7FVC252A00CJ5TT/88-0-0/normal-0` |
| **组装规则** | `video_id = {drone_sn}/{camera.index}/{video.index}` |
| **后端输入** | `live/start` 由后端基于 capacity 结果中的 `camera.index` 和 `video.index` 组装 |
| **切源接口** | `live/video-source` 直接接收 `video_id` 和 `videoType` |
| **简化** | 不保留其他推测格式，只按实测格式实现 |

### 10.5 KMZ 上传回查机制

| 决策项 | 说明 |
|--------|------|
| **上传响应** | `POST /api/v1/wayline/workspaces/{workspace_id}/waylines/files/upload` 返回空 `data: object`，**无 wayline_id** |
| **结论** | **必须回查**！无法从上传响应直接获取 wayline_id |
| **流程** | 1. `duplicate-names` 校验重名 → 2. 上传 KMZ → 3. 等待 1-2s → 4. 按文件名回查 waylines 列表 → 5. 取最近创建的那条 |
| **匹配** | 按文件名 + 时间窗口匹配，取最近创建的那条 |

### 10.6 媒体归属

| 决策项 | 说明 |
|--------|------|
| **归属链路** | 优先 `job_id → mission_id` 关联 |
| **兜底策略** | 若 DJI media API 无 job_id，按 `device_sn + 时间窗口` 推断最近任务 |
| **未知项** | MediaFileDTO 字段定义缺失，无法确认是否有 `job_id`；需实测确认 |
| **兜底行为** | 无法关联的媒体仍入库，但 `mission_id=null`，通过 `device_sn` 归属 tenant |

### 10.7 直播 URL 和下载 URL 代理策略

| 决策项 | 说明 |
|--------|------|
| **策略** | 302 重定向透传，不做代理下载 |
| **直播 URL** | 直接透传 DJI 返回的 rtmp_url/webrtc_url 等 |
| **航线/媒体下载** | 获取 DJI 地址后 302 重定向 |
| **标注** | 响应中标注"内部网络可用" |
| **不做缓存** | DJI URL 可能有时效，透传最简单 |
| **未来** | 若未来需要公网访问，再引入域名代理层 |

### 10.8 同步状态表设计

| 同步状态枚举 | 说明 |
|-------------|------|
| `PENDING` | 同步中 |
| `SYNCED` | 同步成功 |
| `ERROR` | 同步失败 |

#### DjiDeviceIndex

| 字段 | 类型 | 说明 |
|------|------|------|
| device_sn | string (PK) | 设备序列号 |
| last_payload | JSON | 设备快照摘要（在线状态、固件版本等关键字段） |
| last_seen_at | datetime | 最近同步时间 |

#### TenantRouteIndex / TenantMissionIndex / TenantMediaIndex

| 对象 | 字段 | 说明 |
|------|------|------|
| `TenantRouteIndex` | `dji_wayline_id` | 当前已发布 DJI 航线 ID |
| `TenantRouteIndex` | `is_published` | 当前本地 route 草稿是否已与最近一次成功发布结果一致 |
| `TenantMissionIndex` | `sync_status / last_sync_at / error_msg / execution_status` | 任务同步摘要 |
| `TenantMediaIndex` | `sync_status / last_sync_at / error_msg` | 媒体同步摘要 |

### 10.9 DjiWorkspaceConfig

| 决策项 | 说明 |
|--------|------|
| **当前设计** | 单实例：`DjiWorkspaceConfig(workspace_id, dji_user_id, dji_username, dji_user_type, access_token, mqtt_username, mqtt_password, mqtt_addr, expires_at)` |
| **扩展预留** | 若未来支持多 workspace，改为 `DjiWorkspaceConfig(code, workspace_id, ...)` 一对多 |
| **原则** | 不提前抽象，保持当前最简 |

### 10.10 权限继承

| 决策项 | 说明 |
|--------|------|
| **直播读权限** | `GET /api/v1/drones/{id}/live/capacity` 使用 `drone.view_drone` |
| **直播写权限** | `POST /api/v1/drones/{id}/live/start\|stop\|video-quality\|video-source` 使用 `drone.manage_drone` |
| **读权限** | 航线、任务、媒体的读操作沿用各自的 view 权限 |
| **细化** | 不新增 `live.start` 等细粒度权限，当前粒度足够 |
| **边界** | drone-assignments 独立权限 `drone_assignment.manage` |

---

## 11. 设计解决方案

> 本章节记录设计评审中提出的不确定设计点的解决方案。

### 11.1 设备认领与 DJI Binding API

**结论**：纯本地认领，不调用 DJI binding 接口

| 决策项 | 说明 |
|--------|------|
| **当前状态** | 所有设备已在同一 workspace，无需 binding |
| **认领机制** | `Drone(tenant_id, device_sn)` 唯一约束 |
| **未来扩展** | 多 workspace 时再重新考虑 |

---

### 11.2 Mission.drone_id 与 DJI 语义差异

**结论**：保留本地字段，不透传给 DJI

| 字段 | 用途 |
|------|------|
| `Mission.drone_id` | 本地业务管理、tenant 归属、分配关系 |
| `Mission.pilot_id` | 本地业务管理 |
| `dockSn` | 前端传入，我方透传 DJI，Mission 不存储 |

---

### 11.3 任务状态枚举映射

**结论**：建立映射表 + 实测补充

| DJI 状态（待实测） | 本地 MissionStatus |
|--------------------|--------------------|
| 待实测 | PENDING (0) |
| 待实测 | RUNNING (1) |
| 待实测 | PAUSED (2) |
| 待实测 | COMPLETED (3) |
| 待实测 | CANCELED (4) |
| 待实测 | FAILED (5) |

未知状态暂存原始字符串，不丢失信息。

---

### 11.4 KMZ 上传回查机制

**结论**：UUID 前缀 + 等待回查

| 步骤 | 说明 |
|------|------|
| 1 | 上传前加 UUID：`{uuid}_{original_name}.kmz` |
| 2 | 上传后等待 2s |
| 3 | 按 `name LIKE '{uuid}_%'` 精确匹配 |
| 4 | 回查失败重试 2-3 次，间隔 2s/4s |

---

### 11.5 任务创建两阶段

**结论**：同一事务，失败则全回滚

| 步骤 | 说明 |
|------|------|
| 1 | 创建本地 Mission（status=PENDING） |
| 2 | 调用 DJI 创建 job |
| 3 | 回查获取 job_id（按 name + 时间窗口） |
| 4 | 写入 Mission.dji_job_id + TenantMissionIndex |
| 5 | 同一事务，原子性 |

---

### 11.6 dock_sn 获取

**结论**：前端手动输入，不提供 dock 列表接口

| 决策项 | 说明 |
|--------|------|
| **前端行为** | 前端让用户扫码或手动输入 dockSn |
| **接口策略** | 不做 `GET /api/v1/docks` 接口（文档无此 API） |

---

### 11.7 video_id 组装格式

**结论**：按实测格式

格式：`video_id = {drone_sn}/{camera.index}/{video.index}`

实测示例：`1581F7FVC252A00CJ5TT/88-0-0/normal-0`

---

### 11.8 媒体归属链路

**结论**：device_sn 为主，job_id 可选

| 归属方式 | 说明 |
|----------|------|
| 主要 | device_sn → tenant |
| 可选 | 有 job_id 则关联 mission_id |
| 兜底 | 无 job_id 则 mission_id=null |

---

### 11.9 直播 URL 内网访问

**结论**：原样透传 DJI `LiveDTO`

| 决策项 | 说明 |
|--------|------|
| **响应策略** | `data` 原样透传 DJI `LiveDTO` 或上游原始成功响应 |
| **字段处理** | 不做字段裁剪和重命名 |
| **URL 语义** | 保持 DJI 返回的 `url`、`rtmp_url`、`webrtc_url`、`play_url`、`whep_url`、`hls_url` 原样返回 |

---

### 11.10 DjiDeviceIndex 表

**结论**：新建独立表

```
DjiDeviceIndex
├── device_sn (PK)
├── last_payload (JSON)
├── last_seen_at
├── firmware_version
└── firmware_status
```

无 tenant_id（全局共享池）。

---

### 11.11 同步任务幂等性

**结论**：只标记 ERROR，不主动清理

| 场景 | 处理 |
|------|------|
| DJI 存在 | 更新我方记录 |
| DJI 不存在 | 标记 `sync_status=ERROR` + `error_msg` |
| 清理 | 不做自动清理 |

---

### 11.12 token 失效处理

**结论**：刷新优先，失败则重新登录

| 步骤 | 说明 |
|------|------|
| 1 | 优先 `POST /token/refresh` |
| 2 | 失败则 `POST /login` 重新登录 |
| 3 | 重试原请求一次 |
| 4 | 仍失败返回 D0001 |

---

### 11.13 Mission.dji_job_id 字段

**结论**：Mission 表和 TenantMissionIndex 都存

| 存储位置 | 用途 |
|----------|------|
| `Mission.dji_job_id` | cancel 时使用 |
| `TenantMissionIndex.dji_job_id` | 同步索引主键 |

---

### 11.14 航线删除业务约束

**结论**：检查活跃任务

禁止删除条件：
- `Mission.route_id == target_route_id`
- `AND Mission.status IN (PENDING, RUNNING)`

返回 `B0001`："航线正在被任务使用，无法删除"。

---

### 11.15 Pilot 媒体回调

**结论**：当前不接入，依赖轮询同步

| 决策项 | 说明 |
|--------|------|
| **同步模式** | 媒体同步采用拉模式（定时轮询） |
| **未来扩展** | 有实时性需求时再接入回调 |

---

## 12. 待实现清单

| 优先级 | 事项 | 说明 |
|-------|------|------|
| P0 | 删除废弃接口 | `enable/disable/maintenance/retire`、`start/pause/resume/complete/fail`、`route sync/favorite`、`media_files 写接口`、`drone-assignments reactivate` |
| P0 | 新增同步状态表 | `DjiDeviceIndex`、`TenantRouteIndex`、`TenantMissionIndex`、`TenantMediaIndex` |
| P1 | dockSn 前端手动输入 | 不提供 docks 接口，前端让用户手动输入 dockSn |
| P1 | 新增 available 接口 | `GET /api/v1/drones/available` 获取可认领设备 |
| P1 | 媒体归属简化 | 采用 device_sn 归属，不依赖 job_id；`MediaFile.flight_record_id` 改为 nullable |
| P1 | 设备状态简化 | 当前仅保留 `Drone.status=ENABLED/DISABLED` 作为在线摘要；不再保留 `MAINTENANCE/RETIRED`，也不新增 `DjiDeviceIndex.is_online` |
| P1 | 媒体查询扩展 | `GET /api/v1/media_files` 增加 `device_sn` 过滤参数 |
| P1 | Mission.dji_job_id | 新增字段保存 DJI job_id，用于 cancel 操作 |
| P1 | 航线删除约束 | 检查 Mission.route_id 关联，存在活跃任务时禁止删除 |
| P1 | 直播 url_type 默认值 | 服务端设默认值 url_type=1（RTMP） |
| P1 | 无人机 SN 提取 | 认领时存储 children.device_sn，回退兼容 device_sn |
| P1 | DjiDeviceIndex 固件字段 | 新增 firmware_version、firmware_status 字段 |
| P1 | domain 字段过滤 | 同步时按 children.domain=0 过滤无人机 |
| P1 | 媒体时间戳格式 | 统一用 captured_at 字段，ISO 8601 + UTC |
| P1 | DjiGateway workspace_id | 单 workspace，登录后获取并缓存 |
| P1 | 任务状态枚举映射表 | `DJI_JOB_STATUS_MAP`，实测补充枚举值 |
| P2 | KMZ 上传 UUID 前缀 | 上传前生成 UUID 前缀，回查时精确匹配 |
| P2 | 任务创建两阶段回查 | 按 Mission.name + 时间窗口匹配 job_id |
| P2 | 同步任务调度增强 | 当前先用 Django management command `run_dji_sync_scheduler` 作为独立进程调度；需要更复杂重试、分布式调度或锁时再评估 Celery/beat |
| P2 | 同步失败监控 | 记录 error_msg，可选增加告警任务 |
| P2 | 媒体下载 302 处理 | 透传 302 让前端直接访问 DJI 存储地址 |
| P1 | Mission 创建原子事务 | 同一事务创建 Mission + 同步 DJI + 写入 dji_job_id |
| P1 | Mission.cancel 调用 DJI | 先删 DJI 再改本地状态，失败则整体失败 |
| P1 | available 接口实现 | 差集 = 共享池 - 已认领，不做在线过滤 |
| P2 | 同步幂等性策略 | 只标记 ERROR，不做自动清理 |
| P2 | 设备在线状态展示规则 | 前端按 last_seen_at 阈值（3min/10min）展示在线状态 |
| P2 | Pilot 媒体回调 | 当前不接入，依赖轮询同步 |
| P2 | 媒体上传 STS 凭证 | 当前不代理，不做凭证缓存 |
| P3 | 地图模块 | 未来扩展范围 |
| P3 | 控制模块 | 安全红线，不代理 |

---

## 13. 一句话结论
DJI 只有一个 workspace，tenant 隔离必须完全由我方本地最小映射来实现；我方系统应被定义为 BFF/中介层，对外继续暴露业务 API，对内代理 DJI 现有能力，并负责权限、隔离、审计和最小同步，而不是重复实现 DJI 已有主数据与执行能力。
