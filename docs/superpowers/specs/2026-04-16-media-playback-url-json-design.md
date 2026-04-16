# 2026-04-16 Media Playback URL JSON Design

## 1. 背景

当前媒体资源已经具备两层播放能力：

1. `DjiGateway.get_media_playback_url(dji_file_id)` 已经可以调用 DJI 上游 `GET /api/v1/media/workspaces/{workspace_id}/files/{file_id}/playback-url`
2. `GET /api/v1/media-files/{id}/playback` 已经通过平台鉴权后返回 `302`，把客户端重定向到 DJI 播放地址
3. `flight_record` 详情里的 `media_files[].playback_url` 也已经指向平台自身的 `/api/v1/media-files/{id}/playback`

这次新增需求不是替换现有重定向接口，而是在 `media` 系列上补一个 JSON 风格的 playback-url 接口，让客户端在需要时可以直接读取 DJI 视频地址，同时继续复用平台的权限、租户隔离和对象可见性控制。

目标上游接口固定为：

- `GET /api/v1/media/workspaces/{workspace_id}/files/{file_id}/playback-url`

## 2. 目标

1. 新增 `GET /api/v1/media-files/{id}/playback-url`
2. 成功时返回规范业务 JSON envelope：`code` / `msg` / `data`
3. `data` 固定为 `{ "playback_url": "<DJI 视频地址>" }`
4. 仅视频媒体允许调用；非视频仍返回 `400/B0001`
5. 复用现有媒体对象查询、租户过滤、权限控制和 DJI gateway 能力

## 3. 非目标

1. 不修改现有 `GET /api/v1/media-files/{id}/playback` 的 `302` 行为
2. 不修改 `flight_record` 详情里 `media_files[].playback_url` 的语义
3. 不新增数据库字段、索引或迁移
4. 不改动媒体同步链路
5. 不新增本地流式转发或代理播放能力

## 4. 方案对比

### 4.1 方案 A：新增独立 JSON 接口，保留现有 `302` 播放接口

做法：

1. 新增 `/api/v1/media-files/{id}/playback-url`
2. 内部复用现有 `get_object()`、视频类型校验和 `DjiGateway.get_media_playback_url()`
3. 成功时返回 `{ "playback_url": "<DJI 地址>" }`
4. 现有 `/api/v1/media-files/{id}/playback` 保持重定向语义

优点：

1. 不破坏现有客户端契约
2. JSON 和重定向两种语义清晰分离
3. 改动最小，复用现有实现最多
4. 对前端更灵活，既能直接跳转，也能先拿地址再自行处理

缺点：

1. 媒体资源下会多一个只读动作接口

### 4.2 方案 B：把现有 `/playback` 改为 JSON 返回

优点：

1. 看起来少一条路由

缺点：

1. 直接破坏现有 `302` 契约
2. 会影响 `flight_record` 详情已暴露出来的平台播放入口语义
3. 需要同步调整现有测试和客户端行为，风险不必要

### 4.3 方案 C：同一路径根据查询参数或请求头切换 JSON/302

优点：

1. 路径数量最少

缺点：

1. 同一路径承载两种语义，文档和测试都更难维护
2. 客户端行为依赖隐式协商，不利于长期稳定
3. 不符合当前项目“接口语义单一”的风格

### 4.4 选型结论

采用方案 A。

原因：

1. 它是最小正确改动
2. 它完全兼容当前 `/playback` 已上线的重定向能力
3. 它把“获取地址”和“直接跳转”两种用途清楚拆开

## 5. 核心设计

### 5.1 对外 API 契约

新增：

- `GET /api/v1/media-files/{id}/playback-url`

成功响应：

```json
{
  "code": "00000",
  "msg": "success",
  "data": {
    "playback_url": "https://example.com/path/to/video.m3u8"
  }
}
```

失败响应规则：

1. 目标媒体不存在，或在当前租户/授权范围下不可见：返回 `404/C0404`
2. 目标媒体不是视频：返回 `400/B0001`
3. 上游未返回可用播放地址：沿用现有 gateway 异常处理，最终返回标准错误 envelope

非视频媒体的错误数据固定为：

```json
{
  "code": "B0001",
  "msg": "参数校验失败",
  "data": {
    "media_type": ["该媒体不支持 playback"]
  }
}
```

### 5.2 视图层落点

落点：

- `apps/media_file/views.py`

新增 `MediaFileViewSet.playback_url()` action，顺序固定为：

1. `media_file = self.get_object()`
2. 若 `media_file.media_type != MediaType.VIDEO`，返回业务 `400`
3. 调用 `DjiGateway().get_media_playback_url(media_file.dji_index.dji_file_id)`
4. 返回 `{"playback_url": playback_url}`

设计约束：

1. 复用 `BusinessApiResponseMixin` 自动包裹标准成功 envelope
2. 不接受 request body
3. 权限映射增加 `"playback_url": "media_file.view_media_file"`
4. 行为与现有 `playback()` 保持同一套对象访问控制，只改变返回形态

### 5.3 路由设计

落点：

- `apps/media_file/urls.py`

新增：

- `path("media-files/<int:pk>/playback-url", media_file_playback_url, name="media-file-playback-url")`

并保留：

- `path("media-files/<int:pk>/playback", media_file_playback, name="media-file-playback")`

这样两个接口职责清晰：

1. `/playback-url` 返回 JSON 地址
2. `/playback` 直接 `302`

### 5.4 Gateway 复用

落点：

- `apps/dji_bff/gateway.py`

本次不新增 gateway 方法，也不改现有方法签名。直接复用：

- `get_media_playback_url(dji_file_id: str)`

理由：

1. 该方法已经封装了 DJI 上游 `.../playback-url` 调用
2. 已兼容 `Location` 响应头和 JSON/string payload 两类返回
3. 已有对应单元测试，不需要为本需求重复造一层 gateway 抽象

### 5.5 与现有接口的关系

保持不变：

1. `GET /api/v1/media-files/{id}/playback` 仍返回 `302`
2. `GET /api/v1/flight-records/{id}` 中 `media_files[].playback_url` 仍返回平台相对路径 `/api/v1/media-files/{id}/playback`
3. `MediaFileReadSerializer` 不新增 `playback_url`

原因：

1. 这次需求只是在 media 资源面新增一个 JSON 地址接口
2. `flight_record` 详情已经有稳定的“平台播放入口”语义，不应在本次需求中顺手漂移

## 6. 测试设计

### 6.1 `apps/media_file/tests.py`

先写失败测试，再实现：

1. `test_playback_url_should_return_standard_json_for_video`
   - 视频媒体调用 `/api/v1/media-files/{id}/playback-url`
   - 断言返回 `200`
   - 断言 `response.data["data"]["playback_url"]` 为 DJI 地址
   - 断言调用 `get_media_playback_url(dji_file_id)`
2. `test_playback_url_should_reject_photo_media_file`
   - 图片媒体调用该接口
   - 断言返回 `400/B0001`
   - 断言错误数据为 `{"media_type": ["该媒体不支持 playback"]}`

### 6.2 `apps/media_file/test_live_api.py`

补一个 live contract：

1. 视频媒体请求 `/api/v1/media-files/{id}/playback-url`
2. 走真实 mock DJI HTTP 链路
3. 断言响应为 `200`
4. 断言 `data.playback_url` 落到 mock DJI 返回的地址

### 6.3 `apps/api_v1/tests.py`

更新 schema 回归：

1. `/api/v1/media-files/{id}/playback-url` 出现在 schema paths 中
2. 该 path 只暴露 `GET`
3. 该操作不带 `requestBody`
4. 响应码至少覆盖 `200/400/401/403/404/500`

## 7. 实施边界

本次实现只应修改以下文件：

1. `apps/media_file/views.py`
2. `apps/media_file/urls.py`
3. `apps/media_file/tests.py`
4. `apps/media_file/test_live_api.py`
5. `apps/api_v1/tests.py`

如实现过程中发现必须修改其他文件，说明现有边界判断有偏差，需要在动手前重新说明原因。

## 8. 验证计划

最小验证集：

```bash
python manage.py test \
  apps.media_file.tests.MediaFileApiTests \
  apps.media_file.test_live_api.LiveMediaFileApiTests \
  apps.api_v1.tests.OpenApiDocsTests
```

若 schema 测试类名或 live 测试选择器与当前代码不一致，以仓库内实际测试类名为准，但验证范围不变：media 单元测试、live contract、schema 回归三组必须覆盖。
