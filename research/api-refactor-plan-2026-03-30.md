# Django API 重构实施计划

## 概述

根据设计文档（DJI适配接入边界设计.md、DJI权限与租户隔离设计.md）重构当前 Django API，确立 `apps/dji_bff` 为唯一 DJI 感知边界。

**约束条件：**
- 不考虑数据迁移，直接删库重造
- DJI 深度：边界先落地（`apps/dji_bff` 搭建完整边界，上游网关保持 mock 可替换）
- 范围：全量按文档执行（IAM + drone + route + mission + media_file + 相关 assignment/waypoint/flight_record）
- 删除策略：直接删除冲突旧接口/字段/测试，不保留兼容层
- 测试策略：构建 Mock DJI Server 作为独立测试辅助层，支持端到端 HTTP 测试

---

## 测试基础设施：Mock DJI Server

### 策略

利用现有的 `LiveIamApiTestCase`（继承自 `LiveServerTestCase`）构建一个内嵌 Mock DJI Server：
- 新建 `apps/dji_mock/` 作为独立 Django app（不依赖业务模型）
- 该 server 是 DJI 上游 API 的 mock，响应格式对标 DJI 实测接口
- `settings.DJI_UPSTREAM_BASE_URL` 默认指向 `http://localhost`（测试时由 LiveServerTestCase 动态分配）
- `apps/dji_bff/gateway.py` 的 `DjiGateway` 通过 `settings.DJI_UPSTREAM_BASE_URL` 发起 HTTP 调用
- 测试无需 patch，直接用 `setUp` 创建 Mock DJI 设备/文件数据 fixture
- Mock server 支持模拟 DJI 故障（超时、401、无设备等），覆盖错误路径测试

### 关键设计

| 组件 | 职责 |
|------|------|
| `apps/dji_mock/server.py` | Mock DJI HTTP server（Flask/FastAPI，单文件即可） |
| `apps/dji_mock/views.py` | Mock endpoints：login, devices, waylines, jobs, files, media 等 |
| `apps/dji_mock/fixtures.py` | 测试数据 builder（创建设备快照、wayline、job、media 等） |
| `apps/dji_mock/urls.py` | URL 路由挂载为 `/mock-dji/` |
| `config/settings.py` | `DJI_UPSTREAM_BASE_URL` 环境变量配置 |
| `apps/dji_bff/gateway.py` | `DjiGateway` 从 `settings.DJI_UPSTREAM_BASE_URL` 读取 base URL |

### Mock Server 端点设计

```
POST /mock-dji/v1/auth/login                    # DJI 登录，返回 access_token
GET  /mock-dji/v1/devices                       # 设备列表
GET  /mock-dji/v1/devices/{device_sn}/live/capacity
POST /mock-dji/v1/devices/{device_sn}/live/start
POST /mock-dji/v1/devices/{device_sn}/live/stop
POST /mock-dji/v1/devices/{device_sn}/live/video-quality
POST /mock-dji/v1/devices/{device_sn}/live/video-source
POST /mock-dji/v1/waylines                      # 上传 wayline，返回 wayline_id
GET  /mock-dji/v1/waylines/{wayline_id}/download
POST /mock-dji/v1/jobs                          # 创建任务，返回 job_id
GET  /mock-dji/v1/jobs                          # 查询任务列表
DELETE /mock-dji/v1/jobs/{job_id}               # 取消任务
GET  /mock-dji/v1/files                         # 媒体文件列表
GET  /mock-dji/v1/files/{file_id}/download
```

---

## 实施阶段

### Phase 0：测试基础设施（Mock DJI Server + DJI BFF 边界层）

#### 0.1 创建 `apps/dji_mock/` Django app

**新建文件：**
- `apps/dji_mock/__init__.py`
- `apps/dji_mock/apps.py`
- `apps/dji_mock/server.py` -- Flask/FastAPI mock server，响应格式对标 DJI 实测接口
- `apps/dji_mock/views.py` -- Mock endpoints handlers
- `apps/dji_mock/fixtures.py` -- 测试数据 builder
- `apps/dji_mock/urls.py`

**关键文件: `apps/dji_mock/server.py`**
```python
# Mock DJI upstream API
# 响应格式: { "code": 0, "data": {...} }
# 支持: 登录、设备列表、live流、wayline上传/下载、job创建/取消、文件列表/下载
```

#### 0.2 更新 `config/settings.py`

```python
DJI_UPSTREAM_BASE_URL = os.getenv("DJI_UPSTREAM_BASE_URL", "http://localhost:8000")
DJI_MOCK_SERVER_URL = os.getenv("DJI_MOCK_SERVER_URL", "http://localhost:9000")
```

添加到 `INSTALLED_APPS`:
```python
"apps.dji_mock",
```

#### 0.3 更新 `apps/dji_bff/gateway.py` -- 接入 Mock Server

将 `DjiGateway` 从纯 placeholder 改造为通过 `requests` 调用 `DJI_UPSTREAM_BASE_URL`：

```python
import os
import requests
from django.conf import settings

class DjiGateway:
    def __init__(self):
        self.base_url = getattr(settings, 'DJI_UPSTREAM_BASE_URL', 'http://localhost:8000')
        self.access_token = self._get_token_from_config()

    def _get_token_from_config(self):
        # 从 DjiWorkspaceConfig 读取或返回 mock token
        return "mock-token"

    def _headers(self):
        return {"x-auth-token": self.access_token}

    def list_available_devices(self):
        resp = requests.get(f"{self.base_url}/mock-dji/v1/devices", headers=self._headers(), timeout=5)
        resp.raise_for_status()
        return resp.json().get("data", [])

    def upload_wayline(self, *, route_name: str, file_content: bytes = None):
        resp = requests.post(
            f"{self.base_url}/mock-dji/v1/waylines",
            headers=self._headers(),
            json={"name": route_name},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("data", {})

    def get_wayline_url(self, dji_wayline_id: str):
        resp = requests.get(
            f"{self.base_url}/mock-dji/v1/waylines/{dji_wayline_id}/download",
            headers=self._headers(),
            timeout=5,
            allow_redirects=False,
        )
        resp.raise_for_status()
        return resp.headers.get("Location", "")

    def delete_wayline(self, dji_wayline_id: str):
        resp = requests.delete(
            f"{self.base_url}/mock-dji/v1/waylines/{dji_wayline_id}",
            headers=self._headers(),
            timeout=5,
        )
        resp.raise_for_status()

    def create_flight_task(self, *, mission_name: str, dock_sn: str | None = None, **kwargs):
        resp = requests.post(
            f"{self.base_url}/mock-dji/v1/jobs",
            headers=self._headers(),
            json={"name": mission_name, "dock_sn": dock_sn},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("data", {})

    def get_jobs(self):
        resp = requests.get(f"{self.base_url}/mock-dji/v1/jobs", headers=self._headers(), timeout=5)
        resp.raise_for_status()
        return resp.json().get("data", [])

    def delete_job(self, dji_job_id: str):
        resp = requests.delete(
            f"{self.base_url}/mock-dji/v1/jobs/{dji_job_id}",
            headers=self._headers(),
            timeout=5,
        )
        resp.raise_for_status()

    def get_media_files(self, **kwargs):
        resp = requests.get(f"{self.base_url}/mock-dji/v1/files", headers=self._headers(), timeout=5)
        resp.raise_for_status()
        return resp.json().get("data", [])

    def get_media_url(self, dji_file_id: str):
        resp = requests.get(
            f"{self.base_url}/mock-dji/v1/files/{dji_file_id}/download",
            headers=self._headers(),
            timeout=5,
            allow_redirects=False,
        )
        resp.raise_for_status()
        return resp.headers.get("Location", "")

    def get_live_capacity(self, device_sn: str):
        resp = requests.get(
            f"{self.base_url}/mock-dji/v1/devices/{device_sn}/live/capacity",
            headers=self._headers(),
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json().get("data", {})

    def start_live(self, device_sn: str, **kwargs):
        resp = requests.post(
            f"{self.base_url}/mock-dji/v1/devices/{device_sn}/live/start",
            headers=self._headers(),
            json=kwargs,
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json().get("data", {})

    def stop_live(self, device_sn: str, **kwargs):
        resp = requests.post(
            f"{self.base_url}/mock-dji/v1/devices/{device_sn}/live/stop",
            headers=self._headers(),
            json=kwargs,
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json().get("data", {})

    def set_live_video_quality(self, device_sn: str, **kwargs):
        resp = requests.post(
            f"{self.base_url}/mock-dji/v1/devices/{device_sn}/live/video-quality",
            headers=self._headers(),
            json=kwargs,
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json().get("data", {})

    def set_live_video_source(self, device_sn: str, **kwargs):
        resp = requests.post(
            f"{self.base_url}/mock-dji/v1/devices/{device_sn}/live/video-source",
            headers=self._headers(),
            json=kwargs,
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json().get("data", {})
```

#### 0.4 添加 `apps/dji_bff/serializers.py`

```python
# 索引表序列化器
from apps.dji_bff.models import DjiDeviceIndex, TenantRouteIndex, TenantMissionIndex, TenantMediaIndex
from rest_framework import serializers

class DjiDeviceIndexSerializer(serializers.ModelSerializer):
    class Meta:
        model = DjiDeviceIndex
        fields = ["device_sn", "last_payload", "last_seen_at", "created_at", "updated_at"]

class TenantRouteIndexSerializer(serializers.ModelSerializer):
    class Meta:
        model = TenantRouteIndex
        fields = ["id", "tenant", "route", "dji_wayline_id", "sync_status", "last_sync_at"]

class TenantMissionIndexSerializer(serializers.ModelSerializer):
    class Meta:
        model = TenantMissionIndex
        fields = ["id", "tenant", "mission", "dji_job_id", "execution_status", "last_sync_at"]

class TenantMediaIndexSerializer(serializers.ModelSerializer):
    class Meta:
        model = TenantMediaIndex
        fields = ["id", "tenant", "media_file", "dji_file_id", "device_sn", "mission", "sync_status", "last_sync_at"]
```

#### 0.5 添加 `requirements.txt` 依赖

```
Django==5.1.6
djangorestframework==3.15.2
drf-spectacular==0.27.2
psycopg[binary]==3.2.3
requests==2.32.3
flask==3.1.0
```

---

### Phase 1：Drone 模块 -- Claim 语义 + Live 代理

#### 1.1 清理 `apps/drone/views.py` -- 删除本地状态动作

**删除 `permission_map` 条目：**
```python
# 删除这些
"enable": "drone.change_drone_status",
"disable": "drone.change_drone_status",
"maintenance": "drone.change_drone_status",
"retire": "drone.change_drone_status",
```

**删除 `@extend_schema_view` 中对应的 decorator：**
- `enable`, `disable`, `maintenance`, `retire` 的 `extend_schema` 装饰器

**删除 action 方法：**
- `DroneViewSet.enable()` -- 全部删除
- `DroneViewSet.disable()` -- 全部删除
- `DroneViewSet.maintenance()` -- 全部删除
- `DroneViewSet.retire()` -- 全部删除
- `DroneViewSet._change_status()` -- 全部删除

**删除 `@extend_schema` 装饰器和 `DRONE_STATE_CONFLICT_RESPONSE` 中关于 enable/disable/maintenance/retire 的示例**

#### 1.2 添加新 Live 端点到 `apps/drone/views.py`

**添加 `permission_map` 条目：**
```python
permission_map = {
    # ... 保留现有的 list/retrieve/create/update/partial_update/destroy
    "available": "drone.view_drone",
    "live_capacity": "drone.view_drone",
    "live_start": "drone.manage_drone",
    "live_stop": "drone.manage_drone",
    "live_video_quality": "drone.manage_drone",
    "live_video_source": "drone.manage_drone",
    "history": "drone.view_drone",
    "active_assignments": "drone.view_drone",
    "latest_assignment": "drone.view_drone",
}
```

**添加 `@action` 方法：**

```python
@action(detail=False, methods=["get"])
def available(self, request, *args, **kwargs):
    """查询共享设备池中未被认领的设备列表。"""
    gateway = DjiGateway()
    devices = gateway.list_available_devices()
    # 返回 DjiDeviceIndex 中不在 Drone 表中的 device_sn
    claimed_sns = set(Drone.objects.values_list("device_sn", flat=True))
    unclaimed = [d for d in devices if d.get("device_sn") not in claimed_sns]
    return Response(standard_success_payload(unclaimed))

@action(detail=True, methods=["get"], url_path="live/capacity")
def live_capacity(self, request, *args, **kwargs):
    """查询设备直播能力。"""
    drone = self.get_object()
    gateway = DjiGateway()
    data = gateway.get_live_capacity(drone.device_sn)
    return Response(standard_success_payload(data))

@action(detail=True, methods=["post"], url_path="live/start")
def live_start(self, request, *args, **kwargs):
    """启动设备直播。"""
    drone = self.get_object()
    gateway = DjiGateway()
    data = gateway.start_live(drone.device_sn, **(request.data or {}))
    return Response(standard_success_payload(data))

@action(detail=True, methods=["post"], url_path="live/stop")
def live_stop(self, request, *args, **kwargs):
    """停止设备直播。"""
    drone = self.get_object()
    gateway = DjiGateway()
    data = gateway.stop_live(drone.device_sn)
    return Response(standard_success_payload(data))

@action(detail=True, methods=["post"], url_path="live/video-quality")
def live_video_quality(self, request, *args, **kwargs):
    """设置直播视频质量。"""
    drone = self.get_object()
    gateway = DjiGateway()
    data = gateway.set_live_video_quality(drone.device_sn, **(request.data or {}))
    return Response(standard_success_payload(data))

@action(detail=True, methods=["post"], url_path="live/video-source")
def live_video_source(self, request, *args, **kwargs):
    """设置直播视频源。"""
    drone = self.get_object()
    gateway = DjiGateway()
    data = gateway.set_live_video_source(drone.device_sn, **(request.data or {}))
    return Response(standard_success_payload(data))
```

#### 1.3 更新 `apps/drone/serializers.py`

**清理 DJI 耦合 -- 移除直接导入：**
```python
# 删除: from apps.dji_bff.models import DjiDeviceIndex

# 改为通过 DjiGateway 或 TenantVisibilityService 访问
```

**保留 `AvailableDroneReadSerializer`**（已存在，无需修改）

#### 1.4 更新 `apps/drone/models.py`

- `Drone.status` 默认值改为 `ENABLED`（认领即启用）
- 添加 `DJI_UPSTREAM_BASE_URL` 相关注释

#### 1.5 更新 `apps/drone/tests.py`

- 删除 `test_drone_enable`, `test_drone_disable`, `test_drone_maintenance`, `test_drone_retire`
- 添加 `test_drone_claim_from_pool`, `test_drone_live_capacity`, `test_drone_live_start_stop`

---

### Phase 2：Route 模块 -- DJI Wayline 集成

#### 2.1 清理 `apps/route/views.py` -- 删除 enable/disable

**删除：**
- `permission_map` 中的 `"enable"` 和 `"disable"` 条目
- `enable()` 和 `disable()` action 方法
- `@extend_schema_view` 中的 `enable` 和 `disable` decorators
- `ROUTE_ENABLE_RESPONSE` 和 `ROUTE_DISABLE_RESPONSE` 定义

#### 2.2 更新 `RouteViewSet.perform_create()` 和 `perform_destroy()`

**`perform_create()` -- 添加 DJI 上传：**
```python
from apps.dji_bff.gateway import DjiGateway
from apps.dji_bff.models import TenantRouteIndex

@transaction.atomic
def perform_create(self, serializer):
    route = serializer.save(tenant=self.get_current_tenant())

    gateway = DjiGateway()
    dji_result = gateway.upload_wayline(route_name=route.name)
    dji_wayline_id = dji_result.get("dji_wayline_id")

    if dji_wayline_id:
        TenantRouteIndex.objects.create(
            tenant=route.tenant,
            route=route,
            dji_wayline_id=dji_wayline_id,
            sync_status="SYNCED",
        )

    return route
```

**`perform_destroy()` -- 添加 DJI 删除：**
```python
@transaction.atomic
def perform_destroy(self, instance):
    # 先删除 DJI wayline
    try:
        index = TenantRouteIndex.objects.get(route=instance)
        gateway = DjiGateway()
        gateway.delete_wayline(index.dji_wayline_id)
        index.delete()
    except TenantRouteIndex.DoesNotExist:
        pass

    # 再删除本地记录
    instance.delete()
```

#### 2.3 添加 download action

```python
@action(detail=True, methods=["get"], url_path="download")
def download(self, request, *args, **kwargs):
    """获取航线下载 URL（302 重定向到 DJI）。"""
    route = self.get_object()
    try:
        index = TenantRouteIndex.objects.get(route=route)
        gateway = DjiGateway()
        url = gateway.get_wayline_url(index.dji_wayline_id)
        return redirect(url)
    except TenantRouteIndex.DoesNotExist:
        raise NotFound("航线尚未同步到 DJI，无法下载")
```

#### 2.4 更新 `apps/route/tests.py`

- 删除 `test_enable_disable_lifecycle` 相关测试
- 添加 `test_route_create_syncs_to_dji`, `test_route_download_redirects`

---

### Phase 3：Mission 模块 -- DJI Job 边界

#### 3.1 清理 `apps/mission/views.py` -- 删除状态动作

**删除所有 `@extend_schema_view` 中 start/pause/resume/complete/fail 的装饰器**

**删除 `permission_map` 中这些条目的映射**

**删除 action 方法：**
- `MissionViewSet.start()`
- `MissionViewSet.pause()`
- `MissionViewSet.resume()`
- `MissionViewSet.complete()`
- `MissionViewSet.fail()`

**保留 `cancel()` 并更新其实现**

#### 3.2 添加 `dji_job_id` 到 `apps/mission/models.py`

```python
class Mission(models.Model):
    # ... 现有字段
    dji_job_id = models.CharField("DJI Job ID", max_length=128, blank=True, default="")
    dji_job_status = models.CharField("DJI Job 状态", max_length=32, blank=True, default="")
```

**创建 migration**

#### 3.3 更新 `MissionViewSet.perform_create()` -- 同步创建 DJI job

```python
from apps.dji_bff.gateway import DjiGateway
from apps.dji_bff.models import TenantMissionIndex

@transaction.atomic
def perform_create(self, serializer):
    mission = serializer.save(tenant=self.get_current_tenant(), status=MissionStatus.PENDING)

    gateway = DjiGateway()
    dji_result = gateway.create_flight_task(
        mission_name=mission.name,
        dock_sn=mission.dock_sn,
        route_id=mission.route.dji_index.dji_wayline_id if hasattr(mission.route, 'dji_index') else None,
    )
    dji_job_id = dji_result.get("dji_job_id", "")

    if dji_job_id:
        mission.dji_job_id = dji_job_id
        mission.save(update_fields=["dji_job_id", "updated_at"])

        TenantMissionIndex.objects.create(
            tenant=mission.tenant,
            mission=mission,
            dji_job_id=dji_job_id,
            execution_status="PENDING",
        )

    return mission
```

#### 3.4 更新 `cancel()` action

```python
@action(detail=True, methods=["post"])
@transaction.atomic
def cancel(self, request, *args, **kwargs):
    mission = self.get_object()

    if mission.dji_job_id:
        gateway = DjiGateway()
        gateway.delete_job(mission.dji_job_id)

        try:
            index = TenantMissionIndex.objects.get(mission=mission)
            index.execution_status = "CANCELED"
            index.save(update_fields=["execution_status", "updated_at"])
        except TenantMissionIndex.DoesNotExist:
            pass

    mission.status = MissionStatus.CANCELED
    mission.save(update_fields=["status", "dji_job_status", "updated_at"])

    return self._status_response(mission)
```

#### 3.5 更新 `apps/mission/models.py` -- 简化 `clean()`

**删除 `DroneStatus.ENABLED` 检查**（因为 DJI 管理设备状态）：
```python
# 删除这行：
if self.drone_id and self.drone.status != DroneStatus.ENABLED:
    raise ValidationError({"drone": "仅允许绑定启用状态无人机"})
```

#### 3.6 更新 `apps/mission/serializers.py`

- `MissionReadSerializer` 添加 `dji_job_id`, `dji_job_status` 字段
- `MissionWriteSerializer` 不写 `dji_job_id`（自动生成）

#### 3.7 更新 `apps/mission/tests.py`

- 删除 `test_mission_start`, `test_mission_pause`, `test_mission_resume`, `test_mission_complete`, `test_mission_fail`
- 添加 `test_mission_create_creates_dji_job`, `test_mission_cancel_deletes_dji_job`

---

### Phase 4：MediaFile 模块 -- 只读 + Download

#### 4.1 清理 `apps/media_file/views.py` -- 移除写操作

**从 `MediaFileViewSet` 继承链中删除：**
```python
# 删除
mixins.CreateModelMixin,
mixins.UpdateModelMixin,
mixins.DestroyModelMixin,
```

**删除 `permission_map` 中的：**
```python
"create": "media_file.manage_media_file",
"update": "media_file.manage_media_file",
"partial_update": "media_file.manage_media_file",
"destroy": "media_file.manage_media_file",
```

**删除 `@extend_schema_view` 中的：**
- `create`, `update`, `partial_update`, `destroy` decorators

**删除方法：**
- `perform_create()` -- 删除
- `perform_update()` -- 删除
- `perform_destroy()` -- 删除

**保留：**
- `list` 和 `retrieve` mixin 及其 decorators
- `get_queryset()` 及相关逻辑
- `ScopedQuerysetMixin` 的 ASSIGNED scope 过滤

#### 4.2 添加 download action

```python
from django.shortcuts import redirect

@action(detail=True, methods=["get"], url_path="download")
def download(self, request, *args, **kwargs):
    """302 重定向到 DJI 媒体文件下载地址。"""
    media_file = self.get_object()
    try:
        index = TenantMediaIndex.objects.get(media_file=media_file)
        gateway = DjiGateway()
        url = gateway.get_media_url(index.dji_file_id)
        return redirect(url)
    except TenantMediaIndex.DoesNotExist:
        raise NotFound("媒体文件尚未同步到本地，无法下载")
```

#### 4.3 添加 `device_sn` 过滤到 `get_queryset()`

```python
device_sn = params.get("device_sn")
if device_sn:
    queryset = queryset.filter(dji_index__device_sn=device_sn)
```

#### 4.4 更新 `apps/media_file/models.py`

- 移除 `is_deleted`, `deleted_at` 字段（媒体由后端同步管理，不再需要逻辑删除）
- 添加 `sync_status`, `last_sync_at` 字段
- 创建 migration

#### 4.5 更新 `apps/media_file/tests.py`

- 删除 `test_create_media_file`, `test_update_media_file`, `test_delete_media_file`
- 添加 `test_media_file_list_device_sn_filter`, `test_media_file_download_redirect`

---

### Phase 5：DroneAssignment 模块 -- 简化

#### 5.1 清理 `apps/drone_assignment/views.py` -- 删除 reactivate

**删除：**
- `permission_map` 中的 `"reactivate"` 条目
- `@extend_schema_view` 中 `reactivate` 的 decorator
- `DroneAssignmentViewSet.reactivate()` 方法

**删除 `DRONE_ASSIGNMENT_INVALID_PARAMS_RESPONSE` 中 reactivate 相关示例**

#### 5.2 更新 `apps/drone_assignment/tests.py`

- 删除 `test_reactivate`, `test_reactivate_should_reject_body`
- 保留 create/cancel 相关测试

---

### Phase 6：FlightRecord 模块 -- 无需改动

无需修改，按照设计文档。

---

### Phase 7：Schema / Contract 测试重构

#### 7.1 更新 `apps/api_v1/tests.py`

| 测试 | 操作 |
|------|------|
| `test_drone_available_and_live_actions_should_be_documented` | 已存在，验证 |
| `test_mission_cancel_should_be_documented_and_legacy_actions_removed` | 已存在，验证 |
| `test_media_file_list_should_document_device_sn_filter_and_be_read_only` | 已存在，验证 |
| 新增：`test_route_download_endpoint_documented` | 验证 `/routes/{id}/download` 存在 |
| 新增：`test_drone_legacy_status_actions_not_documented` | 验证 enable/disable/maintenance/retire 不存在 |
| 新增：`test_mission_legacy_transition_actions_not_documented` | 验证 start/pause/resume/complete/fail 不存在 |
| 新增：`test_drone_assignment_reactivate_not_documented` | 验证 reactivate 不存在 |
| 新增：`test_media_file_write_endpoints_not_documented` | 验证 post/put/patch/delete 不存在 |

#### 7.2 更新 `apps/access/test_live_schema_api.py`

已存在的断言全部验证通过即可：
- 新路径（available, live/*, download, cancel）应存在
- 旧路径（enable, start, reactivate）应不存在

---

### Phase 8：Live API 测试重构

按以下顺序重建测试（每批重建后可运行验证）：

#### 8.1 `apps/drone/test_live_api.py`

**删除测试：**
- `test_drone_lifecycle_should_follow_live_http_contract` 中 enable/disable/maintenance/retire 相关断言

**修改 `test_drone_lifecycle_should_follow_live_http_contract`：**
```python
def test_drone_lifecycle_should_follow_live_http_contract(self):
    # Mock DJI: 创建设备快照
    self.mock_dji.create_device(device_sn="DL-DRONE-SN-001", name="实时巡检无人机", model="Mavic 3E")

    # Claim 设备
    create_response = self.client.post("/api/v1/drones", {...})
    self.assertEqual(create_response.status_code, 201)
    # 认领后状态应为 ENABLED（不再需要 enable action）
    self.assertEqual(create_data["status"], DroneStatus.ENABLED)

    # 删除 enable/disable/maintenance/retire 动作断言

    # 验证 DJI 索引写入
    self.assertTrue(DjiDeviceIndex.objects.filter(device_sn="DL-DRONE-SN-001").exists())

    # 新增 live 流测试
    drone_id = create_data["id"]
    live_capacity = self.client.get(f"/api/v1/drones/{drone_id}/live/capacity")
    self.assertEqual(live_capacity.status_code, 200)

    live_start = self.client.post(f"/api/v1/drones/{drone_id}/live/start")
    self.assertEqual(live_start.status_code, 200)
```

**新增测试：**
- `test_drone_claim_requires_device_in_pool` -- 设备不在池中返回 400
- `test_drone_available_lists_unclaimed_devices` -- 查询可用设备
- `test_drone_live_endpoints_proxy_to_dji` -- live 流端到端测试
- `test_drone_cross_tenant_claim_rejected` -- 跨租户认领拒绝

#### 8.2 `apps/route/test_live_api.py`

**删除：**
- enable/disable 相关测试和断言

**新增：**
- `test_route_create_syncs_to_dji` -- 验证 TenantRouteIndex 写入
- `test_route_download_returns_dji_url` -- 验证 download 返回 302
- `test_route_delete_calls_dji_delete` -- 验证 DJI wayline 删除

#### 8.3 `apps/mission/test_live_api.py`

**删除：**
- `test_create_and_transition_mission_should_follow_live_http_contract` 中 start/pause/resume/complete 相关断言
- `test_mission_action_should_reject_body_over_live_http` -- 改为仅测试 cancel

**修改 `test_create_and_transition_mission_should_follow_live_http_contract`：**
```python
# 创建任务时 Mock DJI 返回 job_id
self.mock_dji.on_create_job(lambda req: {"dji_job_id": "mock-job-001", "dock_sn": None})

create_response = self.client.post("/api/v1/missions", {...})
self.assertEqual(create_response.status_code, 201)
# 验证 DJI job 创建
mission_id = create_response.json()["data"]["id"]
self.assertTrue(TenantMissionIndex.objects.filter(mission_id=mission_id).exists())

# cancel 测试保留（这是唯一保留的动作）
cancel_response = self.client.post(f"/api/v1/missions/{mission_id}/cancel")
self.assertEqual(cancel_response.status_code, 200)
self.assertEqual(cancel_response.json()["data"]["status"], MissionStatus.CANCELED)
```

**新增：**
- `test_mission_cancel_calls_dji_delete_job` -- 验证 DJI job 删除
- `test_mission_without_dji_job_cannot_cancel` -- 无 DJI job 时 cancel 失败

#### 8.4 `apps/media_file/test_live_api.py`

**删除：**
- `test_media_file_lifecycle_should_follow_live_http_contract` 中的 create/update/patch/delete 部分
- `test_create_media_file`, `test_update_media_file`, `test_patch_media_file`, `test_delete_media_file` 等独立测试

**新增：**
- `test_media_file_read_only` -- 验证 POST/PUT/PATCH/DELETE 返回 405 或 404
- `test_media_file_download_returns_dji_redirect`
- `test_media_file_device_sn_filter`

#### 8.5 `apps/drone_assignment/test_live_api.py`

**删除：**
- `test_reactivate_should_reject_body`
- `test_assignment_lifecycle_should_follow_live_http_contract` 中的 reactivate 部分

---

### Phase 9：Schema 重新生成与验证

```bash
python manage.py spectacular --file /tmp/openapi.yaml --urlconf config.business_api_urlconf
```

**验证清单：**

✅ 新路径存在：
- `/api/v1/drones/available`
- `/api/v1/drones/{id}/live/capacity`
- `/api/v1/drones/{id}/live/start`
- `/api/v1/drones/{id}/live/stop`
- `/api/v1/drones/{id}/live/video-quality`
- `/api/v1/drones/{id}/live/video-source`
- `/api/v1/routes/{id}/download`
- `/api/v1/missions/{id}/cancel`
- `/api/v1/media-files/{id}/download`

❌ 旧路径不存在：
- `/api/v1/drones/{id}/enable`
- `/api/v1/drones/{id}/disable`
- `/api/v1/drones/{id}/maintenance`
- `/api/v1/drones/{id}/retire`
- `/api/v1/routes/{id}/enable`
- `/api/v1/routes/{id}/disable`
- `/api/v1/missions/{id}/start`
- `/api/v1/missions/{id}/pause`
- `/api/v1/missions/{id}/resume`
- `/api/v1/missions/{id}/complete`
- `/api/v1/missions/{id}/fail`
- `/api/v1/drone-assignments/{id}/reactivate`
- `/api/v1/media-files` POST
- `/api/v1/media-files/{id}` PUT/PATCH/DELETE

---

## 文件变更总表

### 新建文件

| 文件 | 说明 |
|------|------|
| `apps/dji_mock/__init__.py` | DJI Mock app 初始化 |
| `apps/dji_mock/apps.py` | Django app 配置 |
| `apps/dji_mock/server.py` | Flask mock DJI server |
| `apps/dji_mock/views.py` | Mock endpoints |
| `apps/dji_mock/fixtures.py` | 测试数据 builder |
| `apps/dji_mock/urls.py` | URL 路由 |
| `apps/dji_bff/serializers.py` | DJI 索引表序列化器 |

### 修改文件

| 文件 | 变更 |
|------|------|
| `config/settings.py` | 添加 DJI_UPSTREAM_BASE_URL, INSTALLED_APPS |
| `requirements.txt` | 添加 requests, flask |
| `apps/dji_bff/gateway.py` | 改造为真实 HTTP 调用 + mock fallback |
| `apps/drone/views.py` | 删除 enable/disable/maintenance/retire + 添加 live actions |
| `apps/drone/serializers.py` | 移除 DjiDeviceIndex 直接导入 |
| `apps/drone/models.py` | status 默认改为 ENABLED |
| `apps/drone/tests.py` | 删除旧测试，添加新测试 |
| `apps/route/views.py` | 删除 enable/disable + 添加 download + DJI 集成 |
| `apps/route/tests.py` | 删除旧测试，添加新测试 |
| `apps/mission/views.py` | 删除 5 个状态动作 + 添加 DJI job 集成 |
| `apps/mission/models.py` | 添加 dji_job_id, dji_job_status; 简化 clean() |
| `apps/mission/serializers.py` | 添加 DJI 字段 |
| `apps/mission/tests.py` | 删除旧测试，添加新测试 |
| `apps/media_file/views.py` | 移除写操作 + 添加 download |
| `apps/media_file/models.py` | 移除 is_deleted/deleted_at, 添加 sync_status |
| `apps/media_file/serializers.py` | 删除 write serializer |
| `apps/media_file/tests.py` | 删除旧测试，添加新测试 |
| `apps/drone_assignment/views.py` | 删除 reactivate |
| `apps/drone_assignment/tests.py` | 删除旧测试 |
| `apps/api_v1/tests.py` | 添加新 schema 断言 |
| `apps/access/test_live_schema_api.py` | 验证已有断言 |

### 无需修改的文件

- `apps/api_v1/urls.py` -- URL 结构不变
- `apps/api_v1/business_response.py` -- 响应 envelope 不变
- `apps/api_v1/tenant_scope.py` -- 租户作用域不变
- `apps/api_v1/schema.py` -- schema helpers 不变
- `apps/access/middleware.py` -- 租户解析不变
- `apps/access/services.py` -- 授权逻辑不变
- `apps/access/drf_permissions.py` -- 权限检查不变
- `apps/access/api_v1/` -- IAM 端点不变
- `apps/waypoint/views.py` -- waypoint 无需改动
- `apps/waypoint/tests.py` -- 无需改动
- `apps/flight_record/views.py` -- 无需改动
- `apps/flight_record/tests.py` -- 无需改动

---

## 风险与依赖分析

### 关键依赖顺序

```
Phase 0 (Mock DJI + Gateway)
    ↓
Phase 1 (Drone) -- 依赖 Phase 0 的 gateway
    ↓
Phase 2 (Route) -- 依赖 Phase 0 的 gateway + Drone (route 创建时需要已认领的 drone)
    ↓
Phase 3 (Mission) -- 依赖 Phase 2 (route 有 DJI index)
    ↓
Phase 4 (Media) -- 依赖 Phase 3 (mission 有 DJI index)
    ↓
Phase 5 (Assignment) -- 无 DJI 依赖，并行
    ↓
Phase 7-9 (测试 + Schema)
```

### 高风险项

| 风险 | 缓解 |
|------|------|
| DJI Gateway HTTP 调用失败导致业务 API 报错 | Gateway 内置超时和降级，测试用 Mock Server |
| Mission 创建依赖 Route 有 DJI wayline_id | Route.perform_create 先写入 DJI，再创建 index |
| Media download 依赖 TenantMediaIndex | 后台 sync 任务写入 index；download 时若不存在返回 404 而非 500 |
| MediaFile 移除逻辑删除字段影响现有测试 | 测试数据 fixture 清理时无需再设置 is_deleted |

### 测试运行策略

每完成一个 Phase 即运行对应测试：
```bash
# Phase 0 后
python manage.py test apps.dji_bff

# Phase 1 后
python manage.py test apps.drone.test_live_api apps.api_v1.tests

# Phase 3 后
python manage.py test apps.mission.test_live_api

# 所有 Phase 后
python manage.py test apps.api_v1.tests apps.access.test_live_schema_api
python manage.py spectacular --file /tmp/openapi.yaml --urlconf config.business_api_urlconf
```
