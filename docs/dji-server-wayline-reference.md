# DJI 真实服务航线对接速查（Wayline）

更新时间：2026-04-07  
适用环境：`DJI_UPSTREAM_BASE_URL=http://8.129.135.140`

## 1. 目标

沉淀 DJI 真实服务（非 mock）在“航线上传/查询/执行前置”链路上的实际用法、URL、关键字段与常见坑，供后续排查和开发直接复用。

## 2. 一手文档入口

- 真实服务 OpenAPI: `http://8.129.135.140/v3/api-docs`
- DJI 官方 Cloud API 文档主页: `https://developer.dji.com/doc/cloud-api-tutorial/en/`
- Pilot Wayline 管理（流程图）: `https://developer.dji.com/doc/cloud-api-tutorial/en/feature-set/pilot-feature-set/wayline-management.html`
- WPML 规范总览: `https://developer.dji.com/doc/cloud-api-tutorial/en/api-reference/dji-wpml/overview.html`
- Waypoint/Wayline HTTPS API（STS、查重、回调等）:
  `https://developer.dji.com/doc/cloud-api-tutorial/en/api-reference/pilot-to-cloud/https/waypoint-management/obtain-waypointfile-list.html`

## 3. 真实服务标准流程（当前直接上传）

1. 登录获取 `x-auth-token`  
   `POST /api/v1/manage/login`
2. 获取当前 `workspace_id`  
   `GET /api/v1/manage/workspaces/current`
3. 通过 `POST /api/v1/wayline/workspaces/{workspace_id}/waylines/files/upload` 上传 KMZ，表单字段仅包含 `name` 与 `file`
4. 直接从上传响应 `data` 中读取 `wayline_id` 与 `download_url`（响应同时会包含 `name` 与 `workspace_id`，其中 `download_url` 目前是相对路径）
5. 创建任务时把 `fileId` 设为返回的 `wayline_id`，无需额外申请 STS、上报 `upload-callback` 或按名字回查 `waylines` 列表
6. 如需清理旧航线，调用 `DELETE /api/v1/wayline/workspaces/{workspace_id}/waylines/{wayline_id}`，该接口保持幂等，在目标已经不存在时仍可能返回成功

## 4. 本地可复用 curl 模板

先准备环境变量（只存变量名，不落库明文密码）：

```bash
export DJI_BASE_URL="http://8.129.135.140"
export DJI_UPSTREAM_USERNAME="<your-username>"
export DJI_UPSTREAM_PASSWORD="<your-password>"
export DJI_UPSTREAM_LOGIN_FLAG=1
```

### 4.1 登录 + workspace

```bash
LOGIN_JSON=$(curl -sS -X POST "$DJI_BASE_URL/api/v1/manage/login" \
  -H 'Content-Type: application/json' \
  --data "{\"username\":\"$DJI_UPSTREAM_USERNAME\",\"password\":\"$DJI_UPSTREAM_PASSWORD\",\"flag\":$DJI_UPSTREAM_LOGIN_FLAG}")

TOKEN=$(echo "$LOGIN_JSON" | jq -r '.data.access_token')
WORKSPACE_ID=$(curl -sS -H "x-auth-token: $TOKEN" \
  "$DJI_BASE_URL/api/v1/manage/workspaces/current" | jq -r '.data.workspace_id')
```

### 4.2 直接上传 KMZ

```bash
UPLOAD_JSON=$(curl -sS -X POST \
  "$DJI_BASE_URL/api/v1/wayline/workspaces/$WORKSPACE_ID/waylines/files/upload" \
  -H "x-auth-token: $TOKEN" \
  -F "name=test_route" \
  -F "file=@/abs/path/to/test_route.kmz")

echo "$UPLOAD_JSON" | jq .
```

### 4.3 读取 `wayline_id` 与 `download_url`

```bash
WAYLINE_ID=$(echo "$UPLOAD_JSON" | jq -r '.data.wayline_id')
DOWNLOAD_URL=$(echo "$UPLOAD_JSON" | jq -r '.data.download_url')

printf 'wayline_id=%s\ndownload_url=%s\n' "$WAYLINE_ID" "$DOWNLOAD_URL"
```

### 4.4 查重（可选）

```bash
curl -sS -H "x-auth-token: $TOKEN" \
  "$DJI_BASE_URL/api/v1/wayline/workspaces/$WORKSPACE_ID/waylines/duplicate-names?name=test_route" | jq .
```

### 4.5 删除航线（幂等）

```bash
curl -sS -X DELETE \
  "$DJI_BASE_URL/api/v1/wayline/workspaces/$WORKSPACE_ID/waylines/$WAYLINE_ID" \
  -H "x-auth-token: $TOKEN" | jq .
```

## 5. 关键契约与已验证行为

1. 航线主键以上传响应 `data.wayline_id` 为准；后续任务创建时 `fileId` 直接使用该值。
2. `POST /waylines/files/upload` 当前返回的关键字段为 `name`、`wayline_id`、`workspace_id`、`download_url`。
3. `download_url` 当前是相对路径，调用方如需完整地址应自行拼接 `DJI_BASE_URL`。
4. `DELETE /waylines/{wayline_id}` 在目标不存在时，真实服务可能返回成功（幂等风格）。
5. `GET waylines` 的排序参数在“文档描述”和“该实例实测”存在差异：  
   - OpenAPI 文档可见 `orderBy.column`、`orderBy.desc`  
   - 该实例实测 `orderBy=create_time` 可用，`orderBy.column=...` 会报错

## 6. WPML/KMZ 注意事项

1. 真实服务对文件格式校验严格，普通 XML/KML 压缩包会被拒绝（`E0001 The file format is incorrect.`）。
2. 应按 DJI WPML 规范准备标准 KMZ（`template.kml` + `waylines.wpml` + `res/` 等）。
3. 文件名、目录名、元素命名需严格遵循 WPML 规范，否则可能上传失败。

## 7. 与本仓当前实现的关系

当前实现入口：

- 航线发布：`apps/route/views.py` 的 `publish`
- KMZ 打包：`apps/route/services.py` 的 `build_route_kmz_from_xml`
- 上游网关：`apps/dji_bff/gateway.py` 的 `upload_route`

当前发布链路按直接上传实现：

1. `manage/login`
2. `manage/workspaces/current`
3. `waylines/files/upload`
4. 从上传响应读取 `wayline_id` / `download_url`

`Route.publish` 运行路径直接使用 `upload_route` 的上传结果，不需要额外回调或列表回查。
