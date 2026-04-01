# DJI 集成调研文档

## 概述

本项目通过 `apps/dji_bff` (Backend-For-Frontend) 层实现与 DJI Pilot 平台的对接。设计核心是**租户隔离本地资源池 + DJI 作为统一上游资源池**的混合模型。`apps/dji_mock` 提供完整的测试桩，支持本地开发和 CI 测试。

---

## 1. 架构概览

### 1.1 核心组件

| 组件 | 路径 | 职责 |
|------|------|------|
| `dji_bff.gateway` | `apps/dji_bff/gateway.py` | HTTP 客户端，封装 DJI Pilot API 认证和请求 |
| `dji_bff.models` | `apps/dji_bff/models.py` | 本地索引/映射表（设备/航线/任务/媒体） |
| `dji_bff.tasks` | `apps/dji_bff/tasks.py` | 同步函数（设备/任务/媒体拉取） |
| `dji_bff.views` | `apps/dji_bff/views.py` | 内部回调端点 |
| `dji_bff.services` | `apps/dji_bff/services.py` | 回调处理逻辑 |
| `dji_mock` | `apps/dji_mock/` | 测试桩模拟 DJI Pilot API |
| 调度命令 | `apps/dji_bff/management/commands/run_dji_sync_scheduler.py` | 定时同步入口 |

### 1.2 数据流向

```
DJI Pilot 平台
    │
    │ (1) DjiGateway HTTP 请求
    ▼
apps/dji_bff.gateway (认证 + 请求封装)
    │
    ├── 设备列表 ─────────────► DjiDeviceIndex
    ├── 任务列表 ─────────────► TenantMissionIndex ──► Mission.status
    └── 媒体列表 ─────────────► TenantMediaIndex ──► MediaFile
    │
    ▼
业务层 (Drone/Mission/MediaFile)
```

---

## 2. DJI Gateway (`apps/dji_bff/gateway.py`)

### 2.1 核心类

**`DjiGateway`** (line 40-528): Mock-friendly HTTP 客户端

```python
class DjiGateway:
    DEFAULT_WAYLINE_TYPE = 0
    DEFAULT_TASK_TYPE = 0
    DEFAULT_RTH_ALTITUDE = 30
    DEFAULT_OUT_OF_CONTROL_ACTION = 0
```

### 2.2 认证机制 (line 204-298)

1. **令牌存储**: `DjiWorkspaceConfig` 单行表存储 `access_token`、`workspace_id`
2. **自动登录**: 无 token 时调用 `/api/v1/manage/login` 获取
3. **自动续期**: token 过期前调用 `/api/v1/manage/token/refresh`
4. **自动重试**: 收到 401 后重新认证并重试请求一次

### 2.3 设备管理方法

| 方法 | API 路径 | 功能 |
|------|----------|------|
| `list_devices()` (line 66) | `GET /api/v1/manage/workspaces/{id}/devices/bound?domain=0` | 列出已绑定设备 |
| `get_live_capacity(device_sn)` (line 52) | `GET /api/v1/manage/live/capacity` | 查询设备直播能力 |
| `start_live(device_sn, **kwargs)` (line 73) | `POST /api/v1/manage/live/streams/start` | 启动直播 |
| `stop_live(device_sn, **kwargs)` (line 78) | `POST /api/v1/manage/live/streams/stop` | 停止直播 |
| `set_live_video_quality(...)` (line 83) | `POST /api/v1/manage/live/streams/update` | 调整画质 |
| `set_live_video_source(...)` (line 88) | `POST /api/v1/manage/live/streams/switch` | 切换视频源 |

### 2.4 航线管理方法

| 方法 | API 路径 | 功能 |
|------|----------|------|
| `upload_route(route_name, file_obj)` (line 93) | `POST /waylines/files/upload` | 上传 KMZ 航线文件 |
| `get_duplicate_route_names(names)` (line 107) | `GET /waylines/duplicate-names` | 检查重名航线 |
| `get_route_download_url(dji_wayline_id)` (line 126) | `GET /waylines/{id}/url` | 获取航线下载地址 |
| `delete_route(dji_wayline_id)` (line 143) | `DELETE /waylines/{id}` | 删除航线 |

### 2.5 任务管理方法

| 方法 | API 路径 | 功能 |
|------|----------|------|
| `create_mission(mission_name, file_id, dock_sn)` (line 150) | `POST /flight-tasks` | 创建 DJI 任务 |
| `cancel_mission(dji_job_id)` (line 172) | `DELETE /jobs?job_id=` | 取消任务 |
| `list_jobs()` (line 179) | `GET /jobs` | 列出所有任务 |

### 2.6 媒体管理方法

| 方法 | API 路径 | 功能 |
|------|----------|------|
| `list_media_files()` (line 200) | `GET /media/files` | 列出媒体文件 |
| `get_media_url(dji_file_id)` (line 183) | `GET /media/files/{id}/url` | 获取媒体下载地址 |

---

## 3. 数据模型 (`apps/dji_bff/models.py`)

### 3.1 `DjiWorkspaceConfig` (line 11-26)

平台级配置表，存储 DJI 认证信息（单行）：

```python
workspace_id      # DJI workspace 唯一标识
access_token      # OAuth 访问令牌
expires_at        # 令牌过期时间
mqtt_username/password/addr  # MQTT 连接信息
```

### 3.2 `DjiDeviceIndex` (line 29-40)

全局设备索引（不区分租户）：

```python
device_sn         # 设备序列号（唯一索引）
last_payload      # 最近一次 DJI 原始载荷
last_seen_at      # 最近见到时间
firmware_version  # 固件版本
firmware_status   # 固件状态
```

### 3.3 `TenantRouteIndex` (line 43-60)

租户航线索引（tenant + dji_wayline_id 唯一约束）：

```python
tenant            # 所属租户
route             # OneToOne → Route
dji_wayline_id    # DJI 航线 ID
is_published      # 是否已发布
```

### 3.4 `TenantMissionIndex` (line 63-77)

租户任务索引（tenant + dji_job_id 唯一约束）：

```python
tenant            # 所属租户
mission           # OneToOne → Mission
dji_job_id        # DJI 任务 ID
execution_status  # 上游执行状态
sync_status       # 同步状态 (PENDING/SYNCED/ERROR)
```

### 3.5 `TenantMediaIndex` (line 80-101)

租户媒体索引（tenant + dji_file_id 唯一约束）：

```python
tenant            # 所属租户
media_file        # OneToOne → MediaFile
dji_file_id       # DJI 文件 ID
device_sn         # 关联设备 SN
mission           # 可选关联 Mission
sync_status       # 同步状态
```

---

## 4. 同步任务 (`apps/dji_bff/tasks.py`)

### 4.1 `sync_device_indexes()` (line 151-191)

**功能**: 从 DJI 拉取设备列表，更新本地 `DjiDeviceIndex`，同步 `Drone.status`

**逻辑**:
1. 调用 `gateway.list_devices()` 获取所有已绑定设备
2. 过滤 `domain=0`（仅无人机设备）
3. `update_or_create` 逐条更新 `DjiDeviceIndex`
4. 删除本地有但上游不再有的设备索引
5. 同步 `Drone.status`: 上游有的 → `ENABLED`，上游没有的 → `DISABLED`

### 4.2 `sync_mission_indexes()` (line 194-233)

**功能**: 同步 `TenantMissionIndex` 状态到本地 `Mission`

**DJI 状态映射** (`DJI_JOB_STATUS_MAP`, line 19-37):
```python
READY/PENDING/QUEUED  → PENDING (0)
RUNNING/IN_PROGRESS/EXECUTING → RUNNING (1)
PAUSED              → PAUSED (2)
FINISHED/COMPLETED/SUCCESS/DONE → COMPLETED (3)
CANCELED/CANCELLED/STOPPED/ABORTED → CANCELED (4)
FAILED/ERROR        → FAILED (5)
```

### 4.3 `sync_media_indexes()` (line 236-332)

**功能**: 从 DJI 拉取媒体文件，创建/更新 `TenantMediaIndex` 和 `MediaFile`

**租户归属推断**:
1. 按 `job_id` 关联 `TenantMissionIndex` → 获取 `tenant`
2. 若无 `job_id`，按 `device_sn` 查找已认领的 `Drone` → 获取 `tenant`
3. 若仍无 `tenant`，跳过该媒体文件

---

## 5. 内部回调端点 (`apps/dji_bff/views.py`)

### 5.1 URL 路由 (`apps/dji_bff/urls.py`)

```
POST /api/v1/__internal__/dji/sync/devices
POST /api/v1/__internal__/dji/sync/missions
POST /api/v1/__internal__/dji/sync/media
POST /api/v1/__internal__/dji/callbacks/wayline-upload
POST /api/v1/__internal__/dji/callbacks/media-upload
POST /api/v1/__internal__/dji/callbacks/media-group-upload
```

### 5.2 认证

所有端点需要 `X-DJI-Internal-Token` header（配置于 `DJI_INTERNAL_API_TOKEN`）

### 5.3 回调处理 (`apps/dji_bff/services.py`)

| 函数 | 功能 |
|------|------|
| `handle_wayline_upload_callback()` | 标记航线为已发布 (`is_published=True`) |
| `handle_media_upload_callback()` | 标记媒体索引为已同步 |
| `handle_media_group_upload_callback()` | 仅记录日志 |

---

## 6. 调度器 (`apps/dji_bff/management/commands/run_dji_sync_scheduler.py`)

### 6.1 命令行参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--once` | False | 单轮执行后退出 |
| `--interval-seconds` | 60 | 循环模式每轮间隔 |
| `--max-cycles` | 0 | 受控运行轮次上限 |

### 6.2 用法

```bash
# 单轮执行
python manage.py run_dji_sync_scheduler --once

# 循环执行（默认 60 秒间隔）
python manage.py run_dji_sync_scheduler

# 自定义间隔（10 秒）且最多 5 轮
python manage.py run_dji_sync_scheduler --interval-seconds 10 --max-cycles 5
```

---

## 7. 业务层集成

### 7.1 `Drone` 与 DJI 设备关联

**文件**: `apps/drone/views.py:319-460`

- **认领设备** (`Drone.create`): 本地记录，关联 `device_sn`
- **可认领设备查询** (`/drones/available`): 从 `DjiDeviceIndex` 排除已认领的
- **直播控制**: 直接透传 `device_sn` 到 `DjiGateway` 方法

### 7.2 `Route` 与 DJI 航线关联

**文件**: `apps/route/views.py:241-326`

- **发布航线** (`/routes/{id}/publish`):
  1. `build_route_kmz_from_xml()` 将 XML 转为 KMZ
  2. `gateway.upload_route()` 上传到 DJI
  3. 保存 `dji_wayline_id` 到 `TenantRouteIndex`
- **删除航线**: 调用 `gateway.delete_route()` 删除上游
- **回调机制**: DJI 处理完成后回调 `wayline-upload`，标记 `is_published=True`

### 7.3 `Mission` 与 DJI 任务关联

**文件**: `apps/mission/views.py:197-345`

- **创建任务** (`Mission.perform_create`):
  1. 验证航线已发布 (`is_published=True`)
  2. 调用 `gateway.create_mission()` 创建 DJI 任务
  3. 保存 `dji_job_id` 到 `Mission` 和 `TenantMissionIndex`
- **取消任务** (`/missions/{id}/cancel`): 调用 `gateway.cancel_mission()`
- **状态同步**: 定时任务 `sync_mission_indexes()` 拉取状态更新本地

### 7.4 `MediaFile` 与 DJI 媒体关联

**文件**: `apps/media_file/views.py:121`

- **下载媒体**: 通过 `gateway.get_media_url()` 获取下载地址

---

## 8. 测试桩 (`apps/dji_mock`)

### 8.1 `MockDjiState` (`apps/dji_mock/state.py`)

内存状态存储，包含：
- `devices`: 设备字典（预置 `MOCK-DRONE-001/002`）
- `waylines`: 航线字典
- `jobs`: 任务字典
- `media_files`: 媒体字典（预置 `mock-file-001`）

### 8.2 `MockDjiUpstreamTestMixin` (`apps/dji_mock/test_support.py`)

测试基类，自动启动内嵌 WSGI 服务器：

```python
class MyTests(MockDjiUpstreamTestMixin, TestCase):
    def setUp(self):
        # 自动注入 mock URL，设置 ENABLE_DJI_MOCK_SERVER=True
        # 创建 mock workspace config
```

### 8.3 Mock 服务器路由

| 路由 | 功能 |
|------|------|
| `POST /api/v1/manage/login` | 返回 mock 用户/workspace 信息 |
| `POST /api/v1/manage/token/refresh` | 刷新并返回新 token |
| `GET /api/v1/manage/workspaces/{id}/devices/bound` | 返回设备列表 |
| `POST /api/v1/wayline/workspaces/{id}/waylines/files/upload` | 创建航线记录 |
| `POST /api/v1/wayline/workspaces/{id}/flight-tasks` | 创建任务记录 |
| `GET /api/v1/wayline/workspaces/{id}/jobs` | 返回任务列表 |
| `GET /api/v1/media/workspaces/{id}/files` | 返回媒体列表 |

---

## 9. 配置项

| 配置项 | 环境变量 | 说明 |
|--------|----------|------|
| `DJI_UPSTREAM_BASE_URL` | `DJI_UPSTREAM_BASE_URL` | DJI Pilot API 根地址 |
| `DJI_UPSTREAM_USERNAME` | `DJI_UPSTREAM_USERNAME` | 登录用户名 |
| `DJI_UPSTREAM_PASSWORD` | `DJI_UPSTREAM_PASSWORD` | 登录密码 |
| `DJI_UPSTREAM_LOGIN_FLAG` | `DJI_UPSTREAM_LOGIN_FLAG` | 登录标识 (默认 1) |
| `DJI_UPSTREAM_TIMEOUT_SECONDS` | `DJI_UPSTREAM_TIMEOUT_SECONDS` | 请求超时 (默认 10) |
| `ENABLE_DJI_MOCK_SERVER` | `ENABLE_DJI_MOCK_SERVER` | 启用测试桩 (默认 false) |
| `DJI_INTERNAL_API_TOKEN` | `DJI_INTERNAL_API_TOKEN` | 内部回调认证 token |

---

## 10. 关键设计决策

### 10.1 租户隔离模型

- DJI 作为**统一上游资源池**，不区分租户
- 本地通过 `TenantXxxIndex` 表建立 **租户 → DJI 资源** 的映射
- 租户只能看到自己"认领"的设备/任务/媒体

### 10.2 异步同步模式

- **拉取同步**: 定时任务 `run_dji_sync_scheduler` 主动拉取 DJI 数据
- **推送回调**: DJI 处理完成后回调内部端点，触发局部更新
- 两者结合确保数据最终一致

### 10.3 Mock-First 开发

- `dji_mock` 提供完整的功能桩
- 所有业务测试继承 `MockDjiUpstreamTestMixin`
- 支持本地开发、CI 测试无需真实 DJI 环境

---

## 11. 相关文件索引

| 文件 | 行号范围 | 说明 |
|------|----------|------|
| `apps/dji_bff/gateway.py` | 1-537 | HTTP 客户端实现 |
| `apps/dji_bff/models.py` | 1-102 | 数据模型定义 |
| `apps/dji_bff/tasks.py` | 1-333 | 同步任务函数 |
| `apps/dji_bff/views.py` | 1-123 | 内部 API 视图 |
| `apps/dji_bff/services.py` | 1-113 | 回调处理服务 |
| `apps/dji_bff/urls.py` | 1-14 | URL 路由 |
| `apps/dji_mock/state.py` | 1-276 | Mock 状态存储 |
| `apps/dji_mock/views.py` | 1-336 | Mock API 视图 |
| `apps/dji_mock/urls.py` | 1-42 | Mock URL 路由 |
| `apps/dji_mock/test_support.py` | 1-88 | 测试基类 |
| `apps/dji_bff/management/commands/run_dji_sync_scheduler.py` | 1-77 | 调度命令 |
| `config/settings.py` | 138-144 | DJI 配置项 |
