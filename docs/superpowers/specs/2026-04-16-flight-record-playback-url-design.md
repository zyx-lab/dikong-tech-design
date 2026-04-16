# 2026-04-16 Flight Record Playback URL Design

## 1. 背景

当前 `flight_record` 详情接口已经会在 `media_files[]` 中返回：

1. `download_url`
2. 平台自身媒体下载入口 `/api/v1/media-files/{id}/download`

前端现在需要新增“在线播放”能力，但还缺少与 `download_url` 对齐的平台播放入口。

目前系统现状是：

1. `GET /api/v1/flight-records/{id}` 详情中的媒体项只返回 `download_url`
2. `MediaFileViewSet.download()` 通过平台接口代理到 DJI 媒体下载地址
3. `DjiGateway.get_media_url()` 当前请求 DJI `GET /api/v1/media/workspaces/{workspace_id}/files/{file_id}/url`
4. 代码中还没有使用 DJI `GET /api/v1/media/workspaces/{workspace_id}/files/{file_id}/playback-url`

这次需求不是让前端直接拼 DJI 地址，也不是让前端知道 DJI 账号密码，而是延续现有模式：

1. 前端拿到平台相对路径
2. 平台做鉴权、租户隔离和上游转发
3. 最终由平台跳转到上游可播放地址

## 2. 目标

1. 在 `GET /api/v1/flight-records/{id}` 的 `media_files[]` 中新增 `playback_url`
2. 为视频媒体提供平台播放入口 `/api/v1/media-files/{id}/playback`
3. 平台通过 DJI `.../playback-url` 获取真实播放地址并返回 `302`
4. 保持现有 `download_url` 语义、权限模型和列表接口不变
5. 用最小正确改动补齐“播放链接”能力

## 3. 非目标

1. 不修改 `GET /api/v1/flight-records` 列表接口结构
2. 不把 DJI 原始 playback 地址直接暴露到 `flight_record` 详情里
3. 不扩展 `MediaFileReadSerializer`，不把 `playback_url` 扩散到媒体列表或媒体详情接口
4. 不修改数据库结构
5. 不新增本地播放代理流式转发；本次仍采用 `302` 跳转模式
6. 不改变图片媒体的下载能力

## 4. 方案对比

### 4.1 方案 A：新增平台 playback 入口并在 flight_record 详情暴露相对路径

做法：

1. 新增 `/api/v1/media-files/{id}/playback`
2. `flight_record.detail.media_files[]` 新增 `playback_url`
3. 视频媒体返回平台相对路径，图片媒体返回空字符串
4. 后端再去调用 DJI `.../playback-url`

优点：

1. 与现有 `download_url` 模式一致
2. 权限、租户和对象存在性仍由平台统一控制
3. 前端不需要理解 DJI 上游细节
4. 后续如果播放链路需要额外鉴权或审计，平台仍有演进空间

缺点：

1. 需要补一条 media-file 业务路由
2. 需要新增一条 gateway 方法和对应测试

### 4.2 方案 B：在 flight_record 详情里直接返回 DJI playback 原始地址

优点：

1. 表面上改动更少

缺点：

1. 暴露上游地址策略给前端
2. 与现有 `download_url` 平台入口语义不一致
3. 不利于权限收口和后续演进

### 4.3 方案 C：复用现有 download 入口，通过参数切换播放语义

优点：

1. 少一条 URL

缺点：

1. 下载和播放语义混在一个入口里
2. 前端接口含义变模糊
3. 后端实现和测试都更难维护

### 4.4 选型结论

采用方案 A。

原因：

1. 它和现有 `download_url` 实现最一致
2. 它能保持平台作为业务边界，而不是把上游地址直接暴露出去
3. 它是当前最小正确改动，不需要重构现有媒体返回模型

## 5. 核心设计

### 5.1 对外 API 契约

只修改 `GET /api/v1/flight-records/{id}` 详情接口，不修改 `GET /api/v1/flight-records` 列表接口。

`media_files[]` 在现有字段基础上新增：

- `playback_url`

字段语义：

1. 视频媒体：
   - `download_url = /api/v1/media-files/{id}/download`
   - `playback_url = /api/v1/media-files/{id}/playback`
2. 图片媒体：
   - `download_url` 保持原样
   - `playback_url = ""`

这里的 `playback_url` 与 `download_url` 一样，都是平台相对路径，不是 DJI 原始地址。

### 5.2 新增平台 playback 业务入口

新增：

- `GET /api/v1/media-files/{id}/playback`

行为：

1. 鉴权规则与 `GET /api/v1/media-files/{id}/download` 保持一致
2. 租户隔离、assigned scope、软删除和 `dji_index__isnull=False` 过滤规则与现有 `MediaFileViewSet.get_object()` 保持一致
3. 若目标媒体是视频，调用 DJI playback 接口并返回 `302`
4. 若目标媒体不是视频，返回业务 `400/B0001`

非视频请求的错误语义固定为：

- `{"media_type": ["该媒体不支持 playback"]}`

原因：

1. 图片记录是存在的，因此不应返回 `404`
2. 这属于业务参数/资源类型不匹配，应返回 `400`
3. 前端可以稳定据此区分“无此记录”和“此记录不可播放”

### 5.3 DJI gateway 适配

在 `DjiGateway` 中新增一个与 `get_media_url()` 对应的方法：

- `get_media_playback_url(dji_file_id: str)`

实现规则：

1. 请求 `GET /api/v1/media/workspaces/{workspace_id}/files/{file_id}/playback-url`
2. `follow_redirects=False`
3. 若响应头有 `Location`，直接返回该值
4. 若响应体是 JSON，继续复用现有 URL 提取逻辑
5. 若最终拿不到可用 URL，则抛 `DjiGatewayUpstreamError("未获取到媒体播放地址", status_code=502, data=payload)`

这样可以兼容上游两种可能返回：

1. 直接 `302`
2. JSON 中包含 URL

### 5.4 MediaFileViewSet 落点

在 `MediaFileViewSet` 里新增 `playback()` action，落点紧邻现有 `download()`。

行为顺序：

1. `media_file = self.get_object()`
2. 若 `media_file.media_type != MediaType.VIDEO`，返回业务 `400`
3. 否则调用 `DjiGateway().get_media_playback_url(media_file.dji_index.dji_file_id)`
4. `return HttpResponseRedirect(playback_url)`

`permission_map` 中需要把：

- `"playback": "media_file.view_media_file"`

加入现有映射。

### 5.5 flight_record 详情 serializer 落点

仅修改 `FlightRecordMediaFileSerializer`。

新增一个 `SerializerMethodField`：

- `playback_url`

规则：

1. 若 `obj.media_type == MediaType.VIDEO`，返回 `reverse("media-file-playback", kwargs={"pk": obj.id})`
2. 否则返回 `""`

保持不变：

1. `FlightRecordDetailSerializer.get_media_files()` 仍只读取 `obj.media_files`
2. 现有过滤条件仍是 `is_deleted=False` 且 `dji_index__isnull=False`
3. `MediaFileReadSerializer` 不新增 `playback_url`

### 5.6 路由落点

在 `apps/media_file/urls.py` 中新增：

- `path("media-files/<int:pk>/playback", media_file_playback, name="media-file-playback")`

保持现有 `download` 路由不变。

## 6. 测试设计

### 6.1 flight_record 详情测试

扩展现有 `apps/flight_record/tests.py` 中的详情测试，锁定：

1. 视频媒体返回 `download_url`
2. 视频媒体同时返回 `playback_url`
3. 图片媒体返回 `playback_url == ""`
4. 列表接口仍不返回 `media_files`

### 6.2 media_file playback 入口测试

在 `apps/media_file/tests.py` 中新增：

1. `test_playback_should_redirect_to_dji_playback_url_for_video`
2. `test_playback_should_reject_photo_media_file`

第一条验证：

1. 视频媒体访问 `/api/v1/media-files/{id}/playback`
2. 返回 `302`
3. 跳转到 mock DJI playback 地址

第二条验证：

1. 图片媒体访问 `/api/v1/media-files/{id}/playback`
2. 返回 `400`
3. 错误码为 `B0001`
4. 错误载荷为 `{"media_type": ["该媒体不支持 playback"]}`

### 6.3 OpenAPI schema 测试

更新 `apps/api_v1/tests.py` 中 `flight_record detail media_files` 的 schema 断言。

要求：

1. `media_files` item 新增 `playback_url`
2. 仍只出现在 `GET /api/v1/flight-records/{id}` 详情接口中
3. 不扩散到 flight-record 列表返回

## 7. 错误处理

### 7.1 平台业务错误

1. 无权限：`401/403`
2. 记录不存在、已删除或不在当前租户范围：`404`
3. 图片请求 playback：`400/B0001`

### 7.2 上游错误

若 DJI playback 接口未返回可用地址，平台按现有 upstream error 路径返回 `502`。

本次不在平台层增加 playback URL 缓存，也不新增额外兜底。

## 8. 兼容性

### 8.1 对前端的影响

新增字段是向后兼容的：

1. 老前端忽略 `playback_url` 不受影响
2. 新前端可以在视频媒体上读取并使用该字段

### 8.2 对现有下载链路的影响

本次不改变：

1. `download_url` 字段名
2. `GET /api/v1/media-files/{id}/download` 行为
3. `flight_record` 详情里 `download_url` 的生成方式

## 9. 验收标准

满足以下条件即视为完成：

1. `GET /api/v1/flight-records/{id}` 的视频媒体项返回非空 `playback_url`
2. `GET /api/v1/flight-records/{id}` 的图片媒体项返回 `playback_url == ""`
3. `GET /api/v1/media-files/{id}/playback` 对视频返回 `302`
4. `GET /api/v1/media-files/{id}/playback` 对图片返回 `400/B0001`
5. `GET /api/v1/flight-records` 列表结构不变
6. OpenAPI schema 正确描述 `playback_url`
