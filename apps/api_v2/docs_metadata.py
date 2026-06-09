API_V2_FRONTEND_GUIDE_DESCRIPTION = """
API v2 文档。前端只调用 `/api/v2/*`；旧 API v1 已移除。文档页是 `/api/v2/docs/`，OpenAPI JSON 是 `/api/v2/docs/schema/`。

## 前端接入流程（10 分钟接入流程）

1. 本地联调没有固定内置业务账号；需要账号时让后端先执行 `python manage.py bootstrap_v2_system --reset --username <super_username> --password '<strong_password>' --noinput`。
2. 调用 `POST /api/v2/iam/session/login` 获取 `accessToken` 和 `refreshToken`。
3. 后续业务请求统一带 Bearer Token：`Authorization: Bearer <accessToken>`。
4. 首屏初始化依次调用 `GET /api/v2/iam/me/context`、`GET /api/v2/system/menus/current`、`GET /api/v2/iam/me/profile`。
5. access token 过期时调用 `POST /api/v2/iam/session/refresh`，成功后替换本地 token 并重试原请求。

## 模块接入顺序

- IAM：部门、账号、角色、权限和菜单先完成；飞手不是独立资源，而是账号具备 `pilot` 角色、`pilot` 档案和有效 `pilot` 资质后的能力。
- 资源发现与绑定：按“创建 DJI 连接 -> discover 发现资源 -> bindings 绑定资源 -> drones/docks/gateways/payloads 列表展示”接入；`discover` 返回发现结果，资源列表只返回已绑定资源。
- 巡检：按“航线 KMZ 上传 -> 创建任务 -> preflight-check -> start -> active-flights/telemetry/live/camera -> complete/cancel/fail/abort -> flight-records/media-files”接入。
- 系统：`GET /api/v2/system/menus/current` 驱动当前用户导航和按钮；日志接口用于后台审计页。

## 标准响应

所有 v2 HTTP 接口返回标准 JSON envelope：`code`、`msg`、`data`。成功时 `code` 固定为 `00000`；失败时前端优先展示 `msg`。列表数据通常是 `{ "list": [], "total": 0 }`。

## 关键字段

- `djiConnectionId` 是本系统 v2 本地 DJI 连接 ID，不是 DJI workspace ID。
- `resourceId` 是资源类型内 ID；混合展示资源时用 `${resourceType}:${resourceId}` 做 key。
- `route.id` 是本地航线 ID，创建任务传它；`djiFile.djiFileId` 是 DJI wayline/file id，仅用于展示和排查。
- `coverImageUrl` 是本系统 MinIO 私有预签名 URL；`djiFile.downloadUrl` 是 DJI 上云侧 KMZ 下载 URL，可能过期，下载前推荐读取航线详情刷新。

## MQTT

DJI OSD、state、events、services_reply、status 会被 v2 worker 记录为最新消息。前端先看 `GET /api/v2/resource/dji-connections/mqtt-health`，再按 `deviceSn/topicKind` 查询 latest 消息，或通过 WebSocket `/ws/v2/dji/mqtt?token=<accessToken>` 订阅实时数据。

## DJI 上游边界

前端不要直接调用 DJI 上云 v1 manage、wayline、media 协议路径。这些是 DJI 上游协议，不是本系统业务 API。后端会通过 v2 接口代理登录、续期、资源发现、航线上传、任务下发、直播、相机控制和媒体 URL 刷新。

更完整的前端使用流程见仓库文档：`docs/api-v2-frontend-guide.md`。
""".strip()


PUBLIC_METHODS = {"get", "post", "put", "patch", "delete"}


def _operation_key(method: str, path: str) -> tuple[str, str]:
    return method.upper(), path


SUMMARY_OVERRIDES = {
    _operation_key("POST", "/api/v2/inspection/live/start"): "启动 DJI 直播",
    _operation_key("POST", "/api/v2/inspection/live/stop"): "停止 DJI 直播",
    _operation_key("POST", "/api/v2/inspection/live/update"): "更新 DJI 直播参数",
    _operation_key("POST", "/api/v2/inspection/live/switch"): "切换 DJI 直播源",
    _operation_key("POST", "/api/v2/inspection/camera/actions"): "控制 DJI 相机与云台",
}


CAMERA_ACTION_FRONTEND_DETAILS = """
### 相机动作补充说明

推荐调用流程：先完成登录、DJI 资源发现和资源绑定；从 `GET /api/v2/resource/drones` 选择本地 `droneId`，从 `GET /api/v2/resource/gateways` 选择本地 `executorId`；再调用 `GET /api/v2/inspection/live/capacity?droneId=<droneId>`，从 `data.cameras_list[].index` 取 `payloadIndex`，例如 `88-0-0`。

前端只调用本接口，不直接调用 DJI 上游。后端会按实现固定执行两步：先调用 DJI payload authority，上游请求体是 `{ "payload_index": payloadIndex }`；成功后再调用 DJI payload commands，上游请求体是 `{ "cmd": action, "data": ... }`。对外字段推荐 camelCase；后端会把 `payloadIndex/cameraMode/cameraType/zoomFactor/resetMode` 转成 DJI 需要的 snake_case。

字段含义：`droneId` 是本地无人机资源 ID，用于权限、在线状态和响应里的 `droneSn`；`executorId` 是本地执行端/网关资源 ID，会映射成上游路径里的 `{gatewaySn}`；`payloadIndex` 是 DJI payload index；`action` 是 DJI payload command 的 `cmd`。

前置限制：调用账号必须是平台超管、调度员或飞手；无人机和执行端都必须已绑定、当前账号可用、在线，并且属于同一个 DJI 连接。否则可能返回 `403/404/409`。

| action | 使用场景 | 额外字段 |
| --- | --- | --- |
| `camera_photo_take` | 拍照 | 无 |
| `camera_recording_start` | 开始录像 | 无 |
| `camera_recording_stop` | 停止录像 | 无 |
| `camera_mode_switch` | 切换拍照/录像等相机模式 | `cameraMode`：`0=拍照`、`1=录像`、`2=智能低光`、`3=全景` |
| `camera_focal_length_set` | 设置变焦倍率 | `cameraType=zoom|ir`；`zoomFactor` 在 `cameraType=zoom` 时为 `2..200`，在 `cameraType=ir` 时为 `2..20` |
| `camera_aim` | 点选瞄准/云台指向画面位置 | `cameraType=wide|zoom|ir`、`locked`、`x`、`y`；`x/y` 是直播画面归一化坐标，范围 `0..1` |
| `gimbal_reset` | 云台复位 | `resetMode`：`0=回中`、`1=朝下`、`2=偏航回中`、`3=俯仰朝下` |

`camera_aim.locked=true` 表示锁定云台，云台和无人机一起转动；`false` 表示只转动云台。拍照和录像接口只返回操作结果，不直接返回媒体文件；新媒体仍通过 DJI 媒体同步、回调或飞行记录媒体刷新流程进入系统。

成功响应的 `data.upstream.authority` 是抢占 payload 控制权的 DJI 结果，`data.upstream.command` 是 payload command 的 DJI 结果。上游失败通常返回 `502`，`msg` 可直接用于联调提示；后端会把本次 `CameraOperation` 记录为 `FAILED`。
""".strip()


CAMERA_ACTION_REQUEST_EXAMPLES = {
    "photoTake": {
        "summary": "拍照",
        "description": "只需要基础字段；生成的照片不在本接口响应里返回。",
        "value": {"droneId": 1, "executorId": 2, "payloadIndex": "88-0-0", "action": "camera_photo_take"},
    },
    "switchToVideo": {
        "summary": "切到录像模式",
        "description": "`cameraMode=1` 对应 DJI VIDEO。",
        "value": {"droneId": 1, "executorId": 2, "payloadIndex": "88-0-0", "action": "camera_mode_switch", "cameraMode": 1},
    },
    "recordingStart": {
        "summary": "开始录像",
        "description": "如需确保处于录像模式，可先调用 `camera_mode_switch`。",
        "value": {"droneId": 1, "executorId": 2, "payloadIndex": "88-0-0", "action": "camera_recording_start"},
    },
    "recordingStop": {
        "summary": "停止录像",
        "description": "停止录像后，视频文件仍走媒体同步/刷新流程。",
        "value": {"droneId": 1, "executorId": 2, "payloadIndex": "88-0-0", "action": "camera_recording_stop"},
    },
    "focalLengthSetZoom": {
        "summary": "设置变焦倍率",
        "description": "`cameraType=zoom` 时 `zoomFactor` 范围是 `2..200`。",
        "value": {
            "droneId": 1,
            "executorId": 2,
            "payloadIndex": "88-0-0",
            "action": "camera_focal_length_set",
            "cameraType": "zoom",
            "zoomFactor": 12,
        },
    },
    "cameraAim": {
        "summary": "点选瞄准",
        "description": "`x/y` 传直播画面上的归一化坐标，范围 `0..1`。",
        "value": {
            "droneId": 1,
            "executorId": 2,
            "payloadIndex": "88-0-0",
            "action": "camera_aim",
            "cameraType": "zoom",
            "locked": False,
            "x": 0.5,
            "y": 0.4,
        },
    },
    "gimbalReset": {
        "summary": "云台回中",
        "description": "`resetMode=0` 对应 DJI RECENTER。",
        "value": {"droneId": 1, "executorId": 2, "payloadIndex": "88-0-0", "action": "gimbal_reset", "resetMode": 0},
    },
}


DJI_UPSTREAM_OPERATION_DETAILS = {
    _operation_key("POST", "/api/v2/resource/dji-connections/{id}/discover"): """
### DJI 上游调用

该接口会用当前 `DjiConnection` 保存的 `baseUrl/username/password/loginFlag` 登录或续期 DJI 上云，再同步 DJI 工作空间里的设备。后端会读取已绑定设备列表中的无人机和机场、读取设备列表中的网关，并从设备 payload 信息里提取负载；前端拿响应里的 `resourceType/resourceId/djiConnectionId` 继续调用资源绑定接口。

前端不需要传 DJI workspace，也不需要处理 DJI token。DJI 登录、workspace、MQTT 账号和 token 会保存回本地连接。DJI 上游失败时返回标准错误 envelope，常见为 `502`，`msg` 用于展示或联调排查。
""".strip(),
    _operation_key("POST", "/api/v2/inspection/routes"): """
### DJI 上游调用

创建航线会把 `kmzFile` 上传到 DJI wayline 文件库，然后再获取 DJI 返回的航线下载地址并保存到 `data.djiFile`。前端必须用 `multipart/form-data` 上传真实 KMZ；`djiConnectionId` 决定使用哪一个 DJI 连接和 workspace。

成功后 `data.id` 是本地航线 ID，`data.djiFile.djiFileId` 是 DJI wayline/file id，`data.djiFile.downloadUrl` 是 DJI 上云侧可直接访问的 KMZ 下载地址。后续创建任务使用本地 `routeId`，不需要前端再传 DJI file id。若本地保存失败，后端会 best-effort 删除刚上传的 DJI 航线文件。
""".strip(),
    _operation_key("GET", "/api/v2/inspection/routes/{id}"): """
### DJI 上游调用

读取航线详情通常只读本地数据；当 `data.djiFile.downloadUrl` 缺失或快过期时，后端会向 DJI wayline 获取新的下载地址并刷新本地缓存。前端下载 KMZ 前推荐先读详情，不要直接使用列表页缓存的旧下载地址。

如果 DJI 下载地址刷新失败，本接口会返回 DJI 上游错误；前端应展示 `msg` 并保留当前详情页状态。
""".strip(),
    _operation_key("PUT", "/api/v2/inspection/routes/{id}"): """
### DJI 上游调用

如果请求体不包含 `kmzFile`，该接口只更新本地航线名称、状态、备注或封面，不调用 DJI。只要传了 `kmzFile`，后端会重新上传 KMZ 到 DJI wayline 文件库、获取新的下载地址、替换本地 `djiFile`，并 best-effort 删除旧 DJI 航线文件。

前端替换 KMZ 时必须使用 `multipart/form-data`，同时传 `djiConnectionId/waylineType/kmzFile`；不替换 KMZ 时使用 JSON，不能传执行相关字段。
""".strip(),
    _operation_key("DELETE", "/api/v2/inspection/routes/{id}"): """
### DJI 上游调用

删除航线会先校验本地航线没有被任何任务引用，再删除本地航线和航点，随后 best-effort 删除 DJI wayline 文件。DJI 删除失败不会阻断本地删除结果；前端收到成功后可以直接从列表移除该航线。
""".strip(),
    _operation_key("POST", "/api/v2/inspection/missions/{id}/start"): """
### DJI 上游调用

启动任务会连续调用 DJI 直播能力、启动直播和创建 wayline flight task。后端用本地任务绑定的 `route.cloudFile.djiFileId` 作为 DJI `file_id`，用本地执行端 `executor.deviceSn` 作为 DJI `dock_sn/gateway_sn`，并自动选择第一个可用 `liveVideoId` 启动直播。

前置要求：任务必须是 `PENDING`，航线已经上传 DJI，无人机和执行端在线，二者和航线属于同一个 DJI 连接，且相关资源没有被其他运行中任务占用。创建 DJI 任务失败时后端会尝试停止刚启动的直播，并把上游错误返回给前端。
""".strip(),
    _operation_key("POST", "/api/v2/inspection/missions/{id}/preflight-check"): """
### DJI 上游调用

该接口是任务启动前检查，不会启动直播，也不会创建 DJI wayline flight task。它先检查本地 mission、DJI 航线文件、资源绑定、在线状态、DJI 连接一致性和资源占用；本地条件通过后，只调用 DJI live capacity 这个只读能力，确认能否选出 `selectedLiveVideoId`。

响应里的 `canStart=false` 表示前端不应继续调用 `start`，应展示 `blockingReasons`。`executorId` 是本地执行端/网关资源 ID，不是飞手 ID；真正启动时会映射为 DJI `dock_sn/gateway_sn`。preflight 不下发真实航线任务，因此不能证明当前设备类型一定支持 wayline flight task。
""".strip(),
    _operation_key("POST", "/api/v2/inspection/missions/{id}/cloud-execution/refresh"): """
### DJI 上游调用

该接口按本地任务找到 `MissionCloudExecution.djiJobId`，再调用 DJI wayline jobs 列表并匹配同一个 job。前端不传 DJI job id，也不要直接调用上游 jobs 接口；后端会用任务上的 DJI 连接和 workspace 处理。

如果 DJI job 仍在 `PENDING/IN_PROGRESS/PAUSED`，后端只刷新 `data.cloudExecution.status/progressPercent/lastEventAt`，任务保持运行中。如果 DJI job 已 `SUCCESS/CANCEL/FAILED`，后端会复用事件闭环：更新任务和飞行会话、生成飞行记录、按 `djiJobId` 同步媒体，并尝试停止直播。
""".strip(),
    _operation_key("POST", "/api/v2/inspection/missions/{id}/complete"): """
### DJI 上游调用

完成任务主要更新本地任务、飞行会话和飞行记录；如果任务存在 DJI 执行记录，后端会尝试拉取 DJI 媒体列表并按 `djiJobId` 关联照片/视频，然后停止任务启动时创建的直播。媒体同步和停止直播失败不会阻断完成操作。

前端收到成功后刷新任务、活动飞行、飞行记录和媒体列表；如果媒体暂时没有出现，可再调用飞行记录的 `refresh-media` 接口。
""".strip(),
    _operation_key("POST", "/api/v2/inspection/missions/{id}/cancel"): """
### DJI 上游调用

取消任务在存在 DJI `djiJobId` 时会调用 DJI wayline job 删除/取消能力，然后停止任务直播并更新本地状态。DJI 取消失败会返回上游错误；停止直播失败只记录在本地云端执行状态，不阻断取消结果。

前端可传 `reason` 作为取消原因；成功后刷新任务列表、活动飞行列表和飞行记录。
""".strip(),
    _operation_key("POST", "/api/v2/inspection/missions/{id}/fail"): """
### DJI 上游调用

标记失败不主动取消 DJI wayline job，但如果任务启动过直播，后端会尝试停止直播并记录停止结果。停止直播失败不会阻断本地失败标记。

前端可传 `reason` 作为失败原因；成功后以本地任务状态为准刷新页面。
""".strip(),
    _operation_key("POST", "/api/v2/inspection/missions/{id}/abort"): """
### DJI 上游调用

安全中止走本地失败闭环，不主动取消 DJI wayline job；如果任务启动过直播，后端会尝试停止直播并记录停止结果。停止直播失败不会阻断本地中止结果。

该接口面向安全处置场景，权限按平台超管、部门管理员、调度员和资源归属关系裁决；前端可传 `reason` 作为中止原因。
""".strip(),
    _operation_key("GET", "/api/v2/inspection/live/capacity"): """
### DJI 上游调用

该接口会调用 DJI live capacity，按本地 `droneId` 映射出的 `drone.deviceSn` 过滤当前无人机的直播能力。响应基本透传 DJI 能力数据，常用字段是 `cameras_list[].index` 和 `videos_list[].index`。

前端用 capacity 组装 `videoId`：`{droneSn}/{payloadIndex}/{videoIndex}`；也用 `cameras_list[].index` 作为相机动作接口的 `payloadIndex`。
""".strip(),
    _operation_key("POST", "/api/v2/inspection/live/start"): """
### DJI 上游调用

该接口把本地 `droneId` 映射成 DJI `device_sn`，再调用 DJI live stream start。`videoId` 来自 capacity，`urlType/videoQuality` 会转成 DJI 需要的 snake_case 字段。

响应为 DJI 直播启动结果，可能包含 `url/rtmp_url/webrtc_url/play_url/hls_url` 等播放地址；前端按实际返回字段选择播放器地址。上游失败时返回标准错误 envelope。
""".strip(),
    _operation_key("POST", "/api/v2/inspection/live/stop"): """
### DJI 上游调用

该接口把 `droneId` 映射成 DJI `device_sn`，用前端传入的 `videoId` 调用 DJI live stream stop。成功后前端应停止播放器并清理直播状态。
""".strip(),
    _operation_key("POST", "/api/v2/inspection/live/update"): """
### DJI 上游调用

该接口把 `droneId` 映射成 DJI `device_sn`，用 `videoId/videoQuality` 调用 DJI live stream update。前端通常用于调整清晰度或码流质量；失败时保持当前播放状态并展示 `msg`。
""".strip(),
    _operation_key("POST", "/api/v2/inspection/live/switch"): """
### DJI 上游调用

该接口把 `droneId` 映射成 DJI `device_sn`，用 `videoId` 和 `videoType=wide|zoom|ir|normal` 调用 DJI live stream switch。前端切换镜头前应先从 capacity 获取可用 `payloadIndex/videoIndex`，并在切换成功后刷新播放器源。
""".strip(),
    _operation_key("POST", "/api/v2/inspection/camera/actions"): """
### DJI 上游调用

该接口会先调用 DJI payload authority 抢占 payload 控制权，再调用 DJI payload commands 下发 `action`。前端只传本地资源 ID 和 action 参数；后端负责把本地 `executorId` 映射成 DJI `gatewaySn`，把 camelCase 字段转成 DJI snake_case。
""".strip(),
    _operation_key("POST", "/api/v2/inspection/flight-records/{id}/refresh-media"): """
### DJI 上游调用

该接口会根据飞行记录关联的任务执行信息，调用 DJI media files 列表，按本次任务的 `djiJobId` 过滤照片/视频并写入本地媒体表。响应里的 `synced/photoCount/videoCount` 是本次刷新后的本地统计。

如果飞行记录没有 DJI 执行记录，则不会调用 DJI，只重新计算本地媒体数量。该接口不单独调用 DJI playback 或 preview URL；播放、预览、下载地址来自 DJI 媒体列表或回调中已保存的字段。
""".strip(),
    _operation_key("POST", "/api/v2/inspection/media-files/{id}/refresh-url"): """
### DJI 上游调用

该接口按本地 `CloudMediaFile.id` 刷新单个媒体文件的 signed URL，不接受任意 DJI file id。`urlType=download` 调 DJI media file URL；`urlType=preview` 调 preview URL；`urlType=playback` 调 playback URL。

后端会从媒体所属 mission 的云端执行记录、设备 SN 绑定或 workspace 唯一 DJI 连接解析上游连接。成功后只更新对应字段：`downloadUrl`、`previewUrl` 或 `playbackUrl`；前端刷新当前媒体项即可。
""".strip(),
}


def frontend_summary(method: str, path: str, operation: dict) -> str:
    summary = str(operation.get("summary") or "").strip()
    if summary:
        return summary
    override = SUMMARY_OVERRIDES.get(_operation_key(method, path))
    if override:
        return override
    if "/iam/" in path:
        return "v2 账号与部门接口"
    if "/resource/" in path:
        return "v2 资源管理接口"
    if "/inspection/" in path:
        return "v2 巡检业务接口"
    return "v2 接口"


def _domain_before(path: str) -> str:
    if "/iam/session/login" in path:
        return "无需 Bearer Token；账号必须已启用并有 v2 账号档案。"
    if "/iam/session/refresh" in path:
        return "需要有效 refreshToken；accessToken 过期时调用。"
    if "/iam/" in path:
        return "需要 Bearer Token；账号、部门、角色必须处于启用状态。"
    if "/resource/" in path:
        return "需要 Bearer Token；资源可见性由当前部门、角色和资源共享关系共同决定。"
    if "/inspection/" in path:
        return "需要 Bearer Token；巡检任务依赖已绑定资源、具备 pilot 档案和有效资质的账号、以及已上传的 DJI 航线文件。"
    return "需要 Bearer Token。"


def _domain_response(method: str, path: str) -> str:
    if path.endswith("/session/login") or path.endswith("/session/refresh"):
        return "成功后 `data.accessToken` 用于后续 Authorization，`data.refreshToken` 用于续期。"
    if "/dji-connections" in path and path.endswith("/discover"):
        return "`data.connectionId` 是当前连接 ID；各资源项内 `resourceType/resourceId/djiConnectionId` 可直接用于绑定。"
    if "/mqtt-health" in path:
        return "`data.list[]` 展示 worker 状态、MQTT 地址、订阅 topic、心跳时间和累计消息数。"
    if "/mqtt-messages/latest" in path:
        return "`data.list[]` 返回每个 topic/device 的最新透传消息，原始 DJI payload 在 `rawPayload`。"
    if "/resource/" in path and path.split("/")[-1] in {"drones", "docks", "gateways", "payloads"}:
        return "`data.list[]` 只包含当前账号可见且已绑定的资源，不包含未绑定的 discover 结果。"
    if "/routes/" in path and method.upper() == "DELETE":
        return "返回被删除的本地航线 ID 和 `deleted=true`；DJI 云端 KMZ 文件和封面文件由后端 best-effort 清理。"
    if "/routes" in path:
        return "返回航线基础信息、航点、MinIO 封面预签名 URL 和 DJI 云端 KMZ 文件字段；详情会按过期时间刷新 `djiFile.downloadUrl`。"
    if "/missions" in path:
        if path.endswith("/preflight-check"):
            return "返回 `canStart`、`blockingReasons`、`warnings`、逐项检查结果和执行端/航线/直播源映射信息；不会改变任务状态。"
        if path.endswith("/cloud-execution/refresh"):
            return "返回最新任务详情；前端重点读取 `data.status` 和 `data.cloudExecution.status/progressPercent/lastEventAt`。"
        return "返回任务状态、航线快照、资源快照、pilot 账号摘要和云端执行字段，用于任务列表和详情页。"
    if "/active-flights" in path or "/telemetry" in path:
        return "返回运行中飞行和遥测快照；遥测由 MQTT worker 异步写入。"
    if "/live/" in path:
        return "返回 DJI 上游直播能力或操作结果；失败时 `msg` 可作为联调错误提示。"
    if "/camera/" in path:
        return "返回本次相机操作记录 ID、`droneSn/gatewaySn/payloadIndex` 映射字段，以及 `upstream.authority/upstream.command` 两段 DJI 上游结果；拍照和录像媒体文件继续走媒体同步流程。"
    if path.endswith("/refresh-url"):
        return "返回更新后的媒体文件详情；按 `urlType` 刷新 `downloadUrl`、`previewUrl` 或 `playbackUrl`。"
    return "成功数据固定放在 `data`；列表接口返回 `list` 和 `total`。"


def _request_notes(method: str, path: str) -> str:
    if method.upper() == "GET":
        return "按文档中的 query 参数过滤；没有分页参数的列表一次返回当前可见范围。"
    if path.endswith("/discover"):
        return "请求体固定为空对象 `{}`；连接账号和 DJI baseUrl 来自 `dji-connections/{id}`。"
    if path.endswith("/bindings"):
        return "必须传 `resourceType`、`resourceId`、`djiConnectionId`；这些值可直接从 discover 响应资源项读取。"
    if "/routes" in path and method.upper() == "POST":
        return "使用 `multipart/form-data`，必须上传 `kmzFile`；`waypoints` 以 JSON 字符串传入。"
    if "/routes/" in path and method.upper() == "PUT":
        return "不替换 KMZ 时可用 JSON 更新基础信息；替换 KMZ 时用 `multipart/form-data`。"
    if "/routes/" in path and method.upper() == "DELETE":
        return "请求体固定为空；只有航线归属部门的调度员或平台超管可删除，且已被任何任务引用的航线不能删除。"
    if "/missions" in path and path.endswith("/start"):
        return "请求体为空对象；后端会启动 DJI 任务、直播和本地飞行会话。"
    if "/missions" in path and path.endswith("/preflight-check"):
        return "请求体为空对象；该接口只做启动前检查，条件满足时会只读查询 DJI live capacity，不会下发 DJI 航线任务。"
    if "/missions" in path and path.endswith("/cloud-execution/refresh"):
        return "请求体为空对象；后端按本地 `djiJobId` 查询 DJI jobs，不允许前端传任意 job id。"
    if "/missions/" in path and method.upper() == "POST":
        return "请求体可为空对象；取消/失败接口可传 `reason` 便于审计和前端展示。"
    if "/live/" in path:
        return "传本地 `droneId`；`videoId` 从 capacity 的镜头/视频能力组装，切换镜头时传 `videoType=wide|zoom|ir`。"
    if "/camera/actions" in path:
        return "传本地 `droneId/executorId` 和 capacity 中的 `payloadIndex`，`action` 选择 DJI payload command；额外字段按 action 传，前端主推 camelCase。"
    if path.endswith("/refresh-url"):
        return "`urlType` 可传 `download`、`preview` 或 `playback`；省略时默认刷新下载地址。"
    if "/session/logout" in path:
        return "请求体为空对象；前端随后清理本地 token。"
    return "按 request schema 传 JSON；未列出的字段会被严格校验器拒绝。"


def _next_step(method: str, path: str) -> str:
    if path.endswith("/session/login"):
        return "调用 `/api/v2/iam/me/context` 和 `/api/v2/iam/me/profile` 初始化页面权限。"
    if path.endswith("/session/refresh"):
        return "替换本地 token 后重试刚才失败的业务请求。"
    if path.endswith("/dji-connections") and method.upper() == "POST":
        return "调用 `POST /api/v2/resource/dji-connections/{id}/discover` 验证 DJI 连通性并同步资源。"
    if path.endswith("/discover"):
        return "对需要纳入业务的资源调用 `POST /api/v2/resource/bindings`，绑定后再查资源列表。"
    if path.endswith("/bindings"):
        return "刷新对应资源列表和资源总览；混合列表使用 `resourceType + resourceId` 做唯一 key。"
    if "/mqtt-health" in path:
        return "状态正常后按 `deviceSn` 调用 latest 消息接口，或打开 WebSocket 订阅实时消息。"
    if "/mqtt-messages/latest" in path:
        return "将 `rawPayload` 用于设备状态面板；需要实时推送时改用 WebSocket。"
    if path.endswith("/routes") and method.upper() == "POST":
        return "使用返回的 route id 创建任务；下载 KMZ 前先读详情获取最新 `djiFile.downloadUrl`。"
    if "/missions" in path and method.upper() == "POST" and path.endswith("/missions"):
        return "任务创建后先调用 `POST /api/v2/inspection/missions/{id}/preflight-check`，通过后再调用 `start`。"
    if path.endswith("/preflight-check"):
        return "`data.canStart=true` 时再启用 `POST /api/v2/inspection/missions/{id}/start`；否则展示 `blockingReasons` 并引导用户修正资源或设备状态。"
    if path.endswith("/cloud-execution/refresh"):
        return "用返回的 `cloudExecution` 刷新任务进度；若任务进入终态，再刷新飞行记录和媒体列表。"
    if path.endswith("/start"):
        return "轮询或订阅 `/active-flights`、`/telemetry/snapshots` 和 MQTT 消息展示执行过程。"
    if any(path.endswith(suffix) for suffix in ("/complete", "/cancel", "/fail", "/abort")):
        return "刷新任务列表、活动飞行列表和飞行记录。"
    if "/live/start" in path:
        return "将返回的播放信息展示到监控页；停止时调用 `/live/stop`。"
    if "/camera/actions" in path:
        return "根据 `data.status` 和 `data.upstream.command` 更新当前按钮状态；需要查看新照片或录像时走媒体同步或飞行记录媒体刷新接口。"
    if path.endswith("/refresh-url"):
        return "把返回的 URL 写回当前媒体项；如果 URL 仍不可访问，再展示错误并允许用户重试。"
    if method.upper() in {"PUT", "DELETE"}:
        return "刷新详情页或列表页，避免继续展示旧状态。"
    return "根据 `data` 刷新当前页面状态；失败时展示 `msg` 并保留用户输入。"


def frontend_description(method: str, path: str, operation: dict) -> str:
    existing = str(operation.get("description") or "").strip()
    purpose = frontend_summary(method, path, operation)
    parts = []
    if existing:
        parts.append(existing)
    parts.append(
        "\n".join(
            [
                "### 前端用法",
                f"- 用途：{purpose}。",
                f"- 前置：{_domain_before(path)}",
                f"- 请求要点：{_request_notes(method, path)}",
                f"- 响应要点：{_domain_response(method, path)}",
                f"- 下一步：{_next_step(method, path)}",
            ]
        )
    )
    if method.upper() == "POST" and path == "/api/v2/inspection/camera/actions":
        parts.append(CAMERA_ACTION_FRONTEND_DETAILS)
    dji_upstream_details = DJI_UPSTREAM_OPERATION_DETAILS.get(_operation_key(method, path))
    if dji_upstream_details:
        parts.append(dji_upstream_details)
    return "\n\n".join(parts)


def request_example_value(method: str, path: str, media_type: str):
    method = method.upper()
    if media_type == "multipart/form-data" and path == "/api/v2/inspection/routes":
        return {
            "name": "南区巡检航线",
            "djiConnectionId": 1,
            "waylineType": 0,
            "kmzFile": "<binary: route.kmz>",
            "waypoints": '[{"lat":22.25,"lng":113.52,"alt":80}]',
            "coverImage": "<binary: cover.png>",
            "remark": "前端联调示例",
        }
    if media_type == "multipart/form-data" and path == "/api/v2/inspection/routes/{id}":
        return {
            "name": "南区巡检航线-更新",
            "djiConnectionId": 1,
            "waylineType": 0,
            "kmzFile": "<binary: route.kmz>",
            "waypoints": '[{"lat":22.25,"lng":113.52,"alt":80}]',
        }

    if path.endswith("/session/login"):
        return {"username": "v2_frontend_admin", "password": "pass1234"}
    if path.endswith("/session/refresh"):
        return {"refreshToken": "<refreshToken>"}
    if path.endswith("/session/logout"):
        return {}
    if path.endswith("/departments"):
        return {"name": "巡检一队", "parentId": 1}
    if path.endswith("/departments/{id}"):
        return {"name": "巡检一队", "parentId": 1, "status": 1}
    if path.endswith("/accounts"):
        return {
            "username": "pilot01",
            "password": "pass1234",
            "name": "飞手一号",
            "phone": "13800000001",
            "email": "pilot01@example.test",
            "departmentId": 1,
            "roleCodes": ["pilot"],
        }
    if path.endswith("/accounts/{id}"):
        return {"name": "飞手一号", "phone": "13800000001", "email": "pilot01@example.test", "departmentId": 1, "status": 1}
    if path.endswith("/accounts/{id}/roles"):
        return {"roleCodes": ["pilot"]}
    if path.endswith("/profile-types"):
        return {"code": "maintenance_operator", "name": "维保员档案", "status": 1, "sort": 80, "remark": "前端联调示例"}
    if path.endswith("/profile-types/{code}"):
        return {"code": "maintenance_operator", "name": "维保员档案", "status": 1, "sort": 80, "remark": "前端联调示例"}
    if path.endswith("/accounts/{id}/profiles"):
        return {"profileType": "pilot", "displayName": "飞手一号", "level": "A", "status": 1, "remark": "前端联调示例"}
    if path.endswith("/accounts/{id}/profiles/{profile_type}"):
        return {"displayName": "飞手一号", "level": "A", "status": 1, "remark": "前端联调示例"}
    if path.endswith("/accounts/{id}/qualifications") or path.endswith("/accounts/{id}/qualifications/{qualification_id}"):
        return {
            "profileType": "pilot",
            "qualificationType": "多旋翼巡检",
            "certificateNo": "CERT-001",
            "issuedAt": "2026-01-01",
            "expiresAt": "2027-01-01",
            "status": 1,
            "remark": "前端联调示例",
        }
    if path.endswith("/dji-connections"):
        return {"name": "DJI 本地上云联调", "baseUrl": "http://example-dji-api:6789", "username": "admin", "password": "<password>", "loginFlag": 1}
    if path.endswith("/dji-connections/{id}"):
        return {"name": "DJI 本地上云联调", "baseUrl": "http://example-dji-api:6789", "username": "admin", "password": "<password>", "loginFlag": 1}
    if path.endswith("/discover"):
        return {}
    if path.endswith("/bindings"):
        return {"resourceType": "drone", "resourceId": 1, "djiConnectionId": 1}
    if path.endswith("/share-groups"):
        return {"name": "跨部门监控共享组", "ownerDepartmentId": 1}
    if path.endswith("/share-groups/{id}"):
        return {"name": "跨部门监控共享组", "status": 1}
    if path.endswith("/share-groups/{id}/departments"):
        return {"departmentId": 2}
    if path.endswith("/share-groups/{id}/resources"):
        return {"resourceType": "drone", "resourceId": 1, "permissions": ["view", "monitor"]}
    if path.endswith("/share-groups/{id}/resources/{resource_share_id}"):
        return {"permissions": ["view", "monitor"]}
    if path.endswith("/inspection/routes"):
        return {"name": "南区巡检航线", "djiConnectionId": 1, "waylineType": 0, "waypoints": [{"lat": 22.25, "lng": 113.52, "alt": 80}]}
    if path.endswith("/inspection/routes/{id}"):
        return {"name": "南区巡检航线-更新", "status": 1, "remark": "仅更新基础信息"}
    if path.endswith("/inspection/missions"):
        return {"name": "南区巡检任务", "routeId": 1, "droneId": 1, "pilotAccountProfileId": 1, "scheduledAt": "2026-06-04T10:00:00+08:00"}
    if path.endswith("/inspection/missions/{id}"):
        return {"name": "南区巡检任务", "status": "PENDING", "remark": "前端联调示例"}
    if path.endswith("/cloud-execution/refresh"):
        return {}
    if path.endswith("/start") or path.endswith("/complete") or path.endswith("/abort"):
        return {}
    if path.endswith("/cancel") or path.endswith("/fail"):
        return {"reason": "前端联调操作"}
    if path.endswith("/telemetry/snapshots"):
        return {"droneId": 1, "latitude": "22.25000000", "longitude": "113.52000000", "batteryPercent": 88}
    if path.endswith("/live/start"):
        return {"droneId": 1, "videoId": "1581F7FVC252A00CJ5TT/88-0-0/normal-0", "urlType": 1, "videoQuality": 1}
    if path.endswith("/live/stop"):
        return {"droneId": 1, "videoId": "1581F7FVC252A00CJ5TT/88-0-0/normal-0"}
    if path.endswith("/live/update"):
        return {"droneId": 1, "videoId": "1581F7FVC252A00CJ5TT/88-0-0/normal-0", "videoQuality": 1}
    if path.endswith("/live/switch"):
        return {"droneId": 1, "videoId": "1581F7FVC252A00CJ5TT/88-0-0/zoom-0", "videoType": "zoom"}
    if path.endswith("/camera/actions"):
        return {"droneId": 1, "executorId": 2, "payloadIndex": "88-0-0", "action": "camera_photo_take"}
    if path.endswith("/refresh-url"):
        return {"urlType": "download"}
    if path.endswith("/flight-records/{id}"):
        return {"status": "COMPLETED", "remark": "飞行记录确认"}
    if path.endswith("/refresh-media"):
        return {}
    return {"remark": "前端联调示例"}


def request_examples(method: str, path: str, media_type: str):
    if method.upper() == "POST" and path == "/api/v2/inspection/camera/actions" and "json" in media_type:
        return CAMERA_ACTION_REQUEST_EXAMPLES
    return {
        "frontend": {
            "summary": "前端调用示例",
            "description": "占位值仅用于说明字段形状；真实账号、密码、token、设备 SN 由运行环境提供。",
            "value": request_example_value(method, path, media_type),
        }
    }
