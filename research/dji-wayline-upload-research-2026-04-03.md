# DJI Server 航线上传研究笔记

更新时间：2026-04-03
测试环境：`DJI_UPSTREAM_BASE_URL=http://8.129.135.140`

> 历史记录说明
>
> 本文是 2026-04-03 的现场调研记录，不是当前生效结论。
> 其中“`files/upload` 不返回 `wayline_id`，必须走 STS + `upload-callback` + waylines 列表回查”的旧结论，
> 已被 2026-04-07 对真实上游的再次验证推翻。
> 当前正式发布路径是：登录 -> 当前 workspace -> `files/upload` -> 直接读取 `wayline_id` / `download_url`。

---

## 一、API 路径对比

| 用途 | dikong-web 前端 | backend DjiGateway |
|---|---|---|
| 登录 | `/manage/api/v1/login` | `/api/v1/manage/login` |
| 当前工作区 | `/manage/api/v1/workspaces/current` | `/api/v1/manage/workspaces/current` |
| 航线上传 | `/wayline/api/v1/.../waylines/file/upload` (singular) | `/api/v1/wayline/.../waylines/files/upload` (plural) |

两者登录接口返回格式一致，可以互通。

---

## 二、航线上传接口行为

### 2.1 直接上传（`/api/v1/wayline/workspaces/{id}/waylines/files/upload`）

- **请求**：multipart/form-data，字段 `name` + `file`
- **响应**：永远只返回 `{"code": "00000", "msg": "success"}`
- **没有返回 wayline_id**，没有 `data` 字段
- dikong-web 前端判断成功的方式：`response.data.code === 0`，不看任何 ID

### 2.2 STS 路径（`publish_route_via_sts`）

完整流程：
1. `POST /api/v1/storage/workspaces/{id}/sts` → 获取 MinIO 凭证
2. `PUT {endpoint}/{bucket}/{object_key}` → 直传 KMZ 到 MinIO
3. `POST /api/v1/wayline/workspaces/{id}/upload-callback` → 上报上传结果
4. `GET /api/v1/wayline/workspaces/{id}/waylines?...` → 从列表取 wayline_id
5. `GET /api/v1/wayline/workspaces/{id}/waylines/{wayline_id}/url` → 下载地址

**注意**：`upload-callback` 的 `object_key` 对应文件必须已在 bucket 中存在，否则报错 `E0001 The file ... does not exist in the bucket`

---

## 三、获取 wayline_id 的途径

wayline_id 格式为 UUID（`^[0-9a-f]{8}-[0-9a-f]{4}-...$`），来源只有一个：

**`GET /api/v1/wayline/workspaces/{id}/waylines?...` 返回 `list[].id`**

OpenAPI spec 定义的参数（但这个 DJI 实例有 bug，详见下文）：
```
orderBy.column: required, enum=["name", "update_time", "create_time"]
orderBy.desc: boolean
page: int (default 1)
page_size: int (default 10)
favorited: boolean
key: string (航线名称模糊匹配)
template_type: array
drone_model_keys: string
payload_model_key: string
action_type: string
```

---

## 四、这个 DJI 实例的已知问题（`8.129.135.140`）

### 4.1 waylines 列表 API 有 bug

**现象**：所有 `orderBy` 参数格式都报错

| 格式 | 参数 | 结果 |
|---|---|---|
| gateway 默认 | `orderBy=create_time` | E0001: "Invalid parameter" |
| OpenAPI spec | `orderBy.column=create_time&orderBy.desc=true` | E0001: "Invalid property 'orderBy' of bean class" |
| 不带 orderBy | — | B0001: "orderBy must not be null" |

错误信息暴露了 Java 层实现问题：后端接收了 `orderBy` 参数但对应 Java Bean 没有这个属性字段。

### 4.2 MinIO 存储满

STS 路径 PUT 到 MinIO 时报错：
```
XMinioStorageFull: Storage backend has reached its minimum free drive threshold.
```

需运维清理该 DJI Server 的 MinIO 存储。

---

## 五、OpenAPI 关键 Schema

### 5.1 CreateJobParam（创建任务）

```json
{
  "required": ["dockSn", "fileId", "name", "outOfControlAction", "rthAltitude", "taskType", "waylineType"],
  "properties": {
    "name": "string",
    "fileId": "string (UUID, 即 wayline_id)",
    "dockSn": "string",
    "waylineType": "int (0=waypoint, 1=mapping2d, 2=mapping3d, 3=mappingStrip)",
    "taskType": "string enum ['0','1','2']",
    "rthAltitude": "int",
    "outOfControlAction": "string enum ['0','1','2']",
    "minBatteryCapacity": "int",
    "minStorageCapacity": "int",
    "taskDays": "array[int]",
    "taskPeriods": "array[array[int]]"
  }
}
```

### 5.2 GetWaylineListResponse（航线列表项）

```json
{
  "required": ["create_time", "drone_model_key", "favorited", "id", "name", "object_key", "payload_model_keys", "template_types", "update_time", "user_name"],
  "properties": {
    "id": "string (UUID format)",
    "name": "string (pattern: ^[^<>:\"/|?*._]+$)",
    "object_key": "string",
    "drone_model_key": "string",
    "payload_model_keys": "array",
    "template_types": "array",
    "favorited": "boolean",
    "user_name": "string",
    "create_time": "int64 (millisecond)",
    "update_time": "int64 (millisecond)",
    "sign": "string"
  }
}
```

---

## 六、WPML/KMZ 格式要求

- 必须符合 DJI WPML 规范，不是普通 XML/KML
- 最小结构：`wpmz/template.kml` + `wpmz/waylines.wpml`
- 文件名和目录名必须严格遵循规范，否则上传被拒绝
- 拒绝错误：`E0001 The file format is incorrect.`

---

## 七、当前 DjiGateway 实现状态

- `publish_route_via_sts()`：已按标准 STS 流程实现，但受上述 bug 影响无法获取 wayline_id
- `upload_route()`：保留作为兼容辅助方法
- `list_waylines()` 中的 fallback 逻辑（`orderBy.column` 格式）在该实例上不工作

---

## 八、dikong-web 前端行为

- `src/api/flight/wayline.ts`：只有 `uploadKmz()` 一个方法，没有 listWaylines
- `src/views/route/detail.vue`：`handleDispatchRoute()` 在 `uploadKmz()` 返回 `code === 0` 后即认为成功，不验证 wayline_id
- 航线列表数据来源：**localStorage 本地存储**，与 DJI 无关

---

## 九、Docker 后续开发注意事项

1. **先确认 DJI 实例的 waylines 列表 API 是否修复**，否则无法走通完整链路
2. 如果列表 API 持续有 bug，可以考虑：
   - 让 `upload_route()` 在 `files/upload` 响应中直接返回某种可用的 wayline 标识
   - 或者用 `upload-callback` 之后直接假设成功（不验证 ID），但这样有风险
3. **MinIO 存储满的问题需运维处理**
4. **区分 Pilot 2 本地 Web 服务**（`192.168.3.26:6789`）和 **DJI Cloud Server**（`8.129.135.140`）— 两者 API 行为可能不同
5. 参考文档：`/项目总体概览/DJI_backend_api.md`、`/docs/dji-server-wayline-reference.md`
6. 参考前端实现：`/tmp/dikong-web-route/src/views/route/wayline-kmz.ts`（KMZ 构建）、`/tmp/dikong-web-route/src/api/flight/wayline.ts`（上传接口）
