# DJI 真实服务航线对接速查（Wayline）

更新时间：2026-04-03  
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

## 3. 真实服务标准流程（推荐，方案 B）

1. 登录获取 `x-auth-token`  
   `POST /api/v1/manage/login`
2. 获取 `workspace_id`  
   `GET /api/v1/manage/workspaces/current`
3. 上传前查重  
   `GET /api/v1/wayline/workspaces/{workspace_id}/waylines/duplicate-names?name=...`
4. 申请 STS 临时凭证  
   `POST /api/v1/storage/workspaces/{workspace_id}/sts`
5. 客户端直传 KMZ 到对象存储（MinIO/S3）
6. 上报上传结果  
   `POST /api/v1/wayline/workspaces/{workspace_id}/upload-callback`
7. 查询航线列表确认入库  
   `GET /api/v1/wayline/workspaces/{workspace_id}/waylines?...`
8. 取下载地址  
   `GET /api/v1/wayline/workspaces/{workspace_id}/waylines/{wayline_id}/url`

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

### 4.2 查重

```bash
curl -sS -H "x-auth-token: $TOKEN" \
  "$DJI_BASE_URL/api/v1/wayline/workspaces/$WORKSPACE_ID/waylines/duplicate-names?name=test_route" | jq .
```

### 4.3 获取 STS

```bash
curl -sS -X POST -H "x-auth-token: $TOKEN" \
  "$DJI_BASE_URL/api/v1/storage/workspaces/$WORKSPACE_ID/sts" | jq .
```

### 4.4 上传回调（仅示例）

说明：`object_key` 对应的文件必须已经存在于 bucket；否则会返回  
`E0001 The file ... does not exist in the bucket[...]`

```bash
curl -sS -X POST \
  "$DJI_BASE_URL/api/v1/wayline/workspaces/$WORKSPACE_ID/upload-callback" \
  -H "x-auth-token: $TOKEN" \
  -H 'Content-Type: application/json' \
  --data '{
    "name":"demo-wayline",
    "object_key":"wayline/demo-wayline.kmz",
    "metadata":{
      "drone_model_key":"0-67-0",
      "payload_model_keys":["1-53-0"],
      "template_types":[0]
    }
  }' | jq .
```

### 4.5 航线列表

```bash
curl -sS -H "x-auth-token: $TOKEN" \
  "$DJI_BASE_URL/api/v1/wayline/workspaces/$WORKSPACE_ID/waylines?page=1&page_size=20&orderBy=create_time" | jq .
```

## 5. 关键契约与已验证行为

1. 航线主键应以 `waylines.list[].id` 为准（后续任务创建用 `fileId`）。
2. `files/upload` 文档是通用 `HttpResultResponse`，不保证 `dji_wayline_id` 一定存在。
3. `upload-callback` 成功仅返回通用成功体，不会返回航线 ID。
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
- 上游网关：`apps/dji_bff/gateway.py` 的 `publish_route_via_sts`

当前发布链路已按 STS 标准流程实现：

1. `get_storage_sts`
2. `_upload_object_via_sts`
3. `report_wayline_upload`
4. `resolve_wayline_id_by_name`

`upload_route` 仍保留在网关中作为兼容辅助方法，但 `Route.publish` 运行路径不再使用该旧直传接口。
