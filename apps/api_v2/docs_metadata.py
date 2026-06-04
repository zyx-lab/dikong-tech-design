API_V2_FRONTEND_GUIDE_DESCRIPTION = """
API v2 文档。当前业务开发入口统一收口到 `/api/v2/*`。

## 前端接入流程

1. 调用 `POST /api/v2/iam/session/login` 获取 `accessToken`，后续请求统一带 Bearer Token：`Authorization: Bearer <accessToken>`。
2. 调用 `/api/v2/iam/me/context` 和 `/api/v2/iam/me/profile` 初始化当前账号、部门、角色和资质。
3. 资源侧按“DJI 连接 -> 资源发现与绑定 -> 资源列表”使用；`discover` 返回的是发现结果，资源列表只返回已绑定资源。
4. 巡检侧按“航线 KMZ 上传 -> 创建任务 -> 启动任务 -> 活动飞行/遥测/直播 -> 完成或取消”使用。航线封面 `coverImageUrl` 是 MinIO 私有预签名 URL；`djiFile.downloadUrl` 是 DJI 上云侧可直接下载的 KMZ 绝对链接。
5. MQTT 实时数据由 worker 写入 v2，前端通过 `mqtt-health`、`mqtt-messages/latest` 或 WebSocket 读取，不直接连接 DJI broker。

## 标准响应

所有 v2 HTTP 接口返回标准 JSON envelope：`code`、`msg`、`data`。成功时 `code` 固定为 `00000`，业务数据在 `data` 内；列表数据通常是 `{ "list": [], "total": 0 }`。

## 资源发现与绑定

`djiConnectionId` 是 v2 本地 DJI 连接 ID，不是 DJI workspace ID。`resourceId` 是资源类型内 ID；前端混合展示资源时应使用 `resourceType + resourceId` 作为唯一 key。

## MQTT

DJI OSD、state、events、services_reply、status 会被 v2 worker 记录为最新消息。前端先看 `mqtt-health` 判断 worker 是否连接，再按 `deviceSn` 和 `topicKind` 查询最新消息。

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


def _domain_response(path: str) -> str:
    if path.endswith("/login") or path.endswith("/refresh"):
        return "成功后 `data.accessToken` 用于后续 Authorization，`data.refreshToken` 用于续期。"
    if "/dji-connections" in path and path.endswith("/discover"):
        return "`data.connectionId` 是当前连接 ID；各资源项内 `resourceType/resourceId/djiConnectionId` 可直接用于绑定。"
    if "/mqtt-health" in path:
        return "`data.list[]` 展示 worker 状态、MQTT 地址、订阅 topic、心跳时间和累计消息数。"
    if "/mqtt-messages/latest" in path:
        return "`data.list[]` 返回每个 topic/device 的最新透传消息，原始 DJI payload 在 `rawPayload`。"
    if "/resource/" in path and path.split("/")[-1] in {"drones", "docks", "gateways", "payloads"}:
        return "`data.list[]` 只包含当前账号可见且已绑定的资源，不包含未绑定的 discover 结果。"
    if "/routes" in path:
        return "返回航线基础信息、航点、MinIO 封面预签名 URL 和 DJI 云端 KMZ 文件字段；详情会按过期时间刷新 `djiFile.downloadUrl`。"
    if "/missions" in path:
        return "返回任务状态、航线快照、资源快照、pilot 账号摘要和云端执行字段，用于任务列表和详情页。"
    if "/active-flights" in path or "/telemetry" in path:
        return "返回运行中飞行和遥测快照；遥测由 MQTT worker 异步写入。"
    if "/live/" in path:
        return "返回 DJI 上游直播能力或操作结果；失败时 `msg` 可作为联调错误提示。"
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
    if "/missions" in path and path.endswith("/start"):
        return "请求体为空对象；后端会启动 DJI 任务、直播和本地飞行会话。"
    if "/missions/" in path and method.upper() == "POST":
        return "请求体可为空对象；取消/失败接口可传 `reason` 便于审计和前端展示。"
    if "/live/" in path:
        return "按 DJI 直播动作传 `deviceSn`、`videoId`、质量或镜头参数；字段为空时上游可能拒绝。"
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
        return "任务创建后调用 `POST /api/v2/inspection/missions/{id}/start`。"
    if path.endswith("/start"):
        return "轮询或订阅 `/active-flights`、`/telemetry/snapshots` 和 MQTT 消息展示执行过程。"
    if any(path.endswith(suffix) for suffix in ("/complete", "/cancel", "/fail", "/abort")):
        return "刷新任务列表、活动飞行列表和飞行记录。"
    if "/live/start" in path:
        return "将返回的播放信息展示到监控页；停止时调用 `/live/stop`。"
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
                f"- 响应要点：{_domain_response(path)}",
                f"- 下一步：{_next_step(method, path)}",
            ]
        )
    )
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
    if path.endswith("/start") or path.endswith("/complete") or path.endswith("/abort"):
        return {}
    if path.endswith("/cancel") or path.endswith("/fail"):
        return {"reason": "前端联调操作"}
    if path.endswith("/telemetry/snapshots"):
        return {"droneId": 1, "latitude": "22.25000000", "longitude": "113.52000000", "batteryPercent": 88}
    if path.endswith("/live/start"):
        return {"deviceSn": "1581F7FVC252A00CJ5TT", "videoId": "1581F7FVC252A00CJ5TT/88-0-0/normal-0", "quality": "HD"}
    if path.endswith("/live/stop"):
        return {"deviceSn": "1581F7FVC252A00CJ5TT", "videoId": "1581F7FVC252A00CJ5TT/88-0-0/normal-0"}
    if path.endswith("/live/update"):
        return {"deviceSn": "1581F7FVC252A00CJ5TT", "videoId": "1581F7FVC252A00CJ5TT/88-0-0/normal-0", "quality": "HD"}
    if path.endswith("/live/switch"):
        return {"deviceSn": "1581F7FVC252A00CJ5TT", "videoId": "1581F7FVC252A00CJ5TT/88-0-0/normal-0"}
    if path.endswith("/flight-records/{id}"):
        return {"status": "COMPLETED", "remark": "飞行记录确认"}
    if path.endswith("/refresh-media"):
        return {}
    return {"remark": "前端联调示例"}
