# 航线发布直传收口设计

- 日期: 2026-04-07
- 状态: 已在讨论中批准，编写用于实现规划
- 范围: 将 DJI 航线发布链路收口为 `POST /api/v1/wayline/workspaces/{workspace_id}/waylines/files/upload` 直传，保存上传响应中的 `wayline_id` 与 `download_url`，并移除 STS 发布运行路径及其旧测试假设

## 1. 背景

当前仓内航线发布运行路径仍基于 STS 流程：

1. `Route.publish` 调用 `DjiGateway.publish_route_via_sts()`
2. 申请 STS 凭证
3. 直传对象存储
4. 调用 `upload-callback`
5. 通过航线名称回查航线列表，解析 `wayline_id`
6. 回写 `TenantRouteIndex.dji_wayline_id`

但最新线上真实行为已经变化：

1. `POST /api/v1/wayline/workspaces/{workspace_id}/waylines/files/upload` 可直接创建航线
2. 上传响应直接返回：
   - `wayline_id`
   - `workspace_id`
   - `download_url`
   - `name`

继续保留 STS 作为正式发布路径会带来两类问题：

1. 运行链路比实际上游要求更复杂，排查成本更高
2. 代码、mock、测试和文档会长期保留“必须回查 `wayline_id`”这一过时假设

本设计的目标就是把航线发布链路彻底收口到当前真实可用、最短的一条路径。

## 2. 设计目标

1. 将 `files/upload` 设为唯一正式航线发布路径
2. 在本地持久化最新发布成功后的 `dji_wayline_id` 与 `download_url`
3. 删除 STS 发布链路、按名称回查 `wayline_id` 的运行逻辑、测试假设与面向当前实现的说明文档
4. 保持现有 `Route.publish` 的对外业务行为基本稳定，只改变上游交互实现
5. 让 mock 与真实上游的上传响应结构一致，避免测试建立在错误契约上

## 3. 非目标

1. 不保留 STS 作为 fallback
2. 不兼容旧的“上传后再查列表解析 `wayline_id`”逻辑
3. 不为历史 `TenantRouteIndex` 数据做 `download_url` 回填
4. 不将上传返回的 `workspace_id` 额外落库
5. 不将保存的 `download_url` 转换为绝对 URL 后入库
6. 不新增或扩展对外 Route API 字段，除非实现规划阶段另行确认确有消费需求

## 4. 核心决策

### 4.1 唯一发布链路

正式发布链路固定为：

1. `Route.publish`
2. `build_route_kmz_from_xml(route)`
3. `DjiGateway.upload_route(route_name=..., file_obj=kmz_file)`
4. 直接从上传响应中提取并规范化：
   - `dji_wayline_id <- wayline_id`
   - `download_url`
5. 回写 `TenantRouteIndex`
6. 如有旧 `dji_wayline_id`，在新发布成功并持久化后 best-effort 删除旧上游航线

STS 申请、对象存储直传、上传回调、按名称回查航线列表，不再属于正式发布路径。

### 4.2 `download_url` 的存储位置

`download_url` 与 `dji_wayline_id` 一样，都属于 DJI 侧发布状态，而不是航线业务主体字段。

因此新增字段应放在 `TenantRouteIndex`，而不是 `Route`：

1. `TenantRouteIndex.dji_wayline_id`
2. `TenantRouteIndex.download_url`
3. `TenantRouteIndex.is_published`

这样可以继续保持边界清晰：

1. `Route` 表示本地航线草稿及其基本信息
2. `TenantRouteIndex` 表示该航线在 DJI 侧的映射状态

### 4.3 `download_url` 的存储格式

保存 DJI 上传响应中返回的原始 `download_url` 字符串，不做域名拼接或二次加工。

原因：

1. 线上真实返回当前是相对路径
2. 域名与部署环境相关，不应硬编码进数据库
3. 原样保存最有利于排查真实上游响应

### 4.4 `download_url` 的使用边界

本次设计要求保存 `download_url`，但不要求把它变成当前系统下载链路的唯一事实来源。

当前下载能力仍可继续通过 `dji_wayline_id` 调用 DJI `GET /waylines/{wayline_id}/url` 获取即时可用地址。

因此保存下来的 `download_url` 在本阶段主要承担两项职责：

1. 记录最近一次成功上传时 DJI 返回的原始下载路径
2. 为后续排查和扩展提供可追溯数据

本阶段不要求把现有下载动作强制切换为“直接使用库存 `download_url`”。

## 5. 数据模型

### 5.1 TenantRouteIndex

`TenantRouteIndex` 从：

- `tenant`
- `route`
- `dji_wayline_id`
- `is_published`
- `created_at`
- `updated_at`

调整为：

- `tenant`
- `route`
- `dji_wayline_id`
- `download_url`
- `is_published`
- `created_at`
- `updated_at`

字段建议：

- `download_url = models.CharField(max_length=500, blank=True, default="")`

语义：

1. `dji_wayline_id=""` 表示当前没有有效的已发布 DJI 航线 ID
2. `download_url=""` 表示当前没有记录到有效的上传返回下载路径
3. 成功发布后，`dji_wayline_id` 与 `download_url` 都必须为非空
4. `is_published=true` 表示当前本地 XML 草稿与最近一次成功发布到 DJI 的版本一致

### 5.2 不新增其他表

本设计不增加新的发布历史表、上传日志表或额外索引表。

原因：

1. 当前需求仅要求收口发布路径并保存上游返回的关键字段
2. 额外历史建模会扩大实现面，不符合 KISS 和 YAGNI

## 6. 网关契约

### 6.1 `DjiGateway.upload_route()`

请求保持为 multipart 上传：

1. `POST /api/v1/wayline/workspaces/{workspace_id}/waylines/files/upload`
2. 表单字段包含：
   - `name`
   - `file`

成功响应按真实线上契约处理：

```json
{
  "code": "00000",
  "msg": "success",
  "data": {
    "name": "test_test",
    "wayline_id": "647eb40f-2ad2-48e1-bb24-5cf175e41f88",
    "workspace_id": "e3dea0f5-37f2-4d79-ae58-490af3228069",
    "download_url": "/api/v1/wayline/workspaces/e3dea0f5-37f2-4d79-ae58-490af3228069/waylines/647eb40f-2ad2-48e1-bb24-5cf175e41f88/url"
  }
}
```

`upload_route()` 的规范化返回至少应包含：

1. `dji_wayline_id`
2. `download_url`
3. `name`
4. 如上游有 `workspace_id`，可一并透传

### 6.2 必填成功字段

对于本系统的“发布成功”语义，以下字段都视为必需：

1. `wayline_id`
2. `download_url`

规范化后即：

1. `dji_wayline_id`
2. `download_url`

只要任一缺失，网关就应抛出 `DjiGatewayUpstreamError(status_code=502)`，视为上游响应不完整，而不是本地降级成功。

### 6.3 保留与删除

保留：

1. `upload_route()`
2. `get_route_download_url()`
3. `delete_route()`

删除：

1. `get_storage_sts()`
2. `report_wayline_upload()`
3. `resolve_wayline_id_by_name()`
4. `publish_route_via_sts()`

前提是这些方法在代码库中已无其他合法运行调用方。如果清理时确认没有调用方，则直接删除，不保留备用死代码。

## 7. Route 发布流程

### 7.1 成功路径

`Route.publish` 的成功路径调整为：

1. 读取当前 route
2. 生成 KMZ
3. 调用 `gateway.upload_route()`
4. 从返回值读取：
   - `dji_wayline_id`
   - `download_url`
5. 更新 `TenantRouteIndex`
   - `dji_wayline_id = new_wayline_id`
   - `download_url = new_download_url`
   - `is_published = true`
6. 提交事务后，如存在旧 `dji_wayline_id` 且与新值不同，则 best-effort 删除旧上游航线

### 7.2 失败路径

以下失败语义保持现有边界：

1. KMZ 构建失败：
   - 返回业务 400
   - 用户可见消息仍为“当前 XML 草稿无法转换为可发布 KMZ”或其已接受变体
2. 上游返回 `E0001 file format is incorrect`：
   - 映射为业务 400
   - 用户可见消息仍为“当前 XML 草稿不符合 DJI WPML 航线格式”
3. 其他上传异常：
   - 继续走 `DjiGatewayUpstreamError` 错误路径
   - 不写入新的 `dji_wayline_id` 或 `download_url`

### 7.3 持久化失败补偿

若 DJI 上传成功，但本地持久化失败：

1. 尝试删除刚创建的新上游航线
2. 不改动旧的 `TenantRouteIndex`
3. 不保留半成功的本地状态

这一规则与当前发布流程保持一致，只是补偿对象从 STS 生成后的航线 ID，变成直传返回的航线 ID。

### 7.4 日志收口

发布日志中不再记录 STS 特有概念，例如：

1. `object_key`
2. `upload_callback`
3. `resolve_wayline_id_by_name`

日志聚焦为：

1. KMZ 构建开始/成功/失败
2. 直传开始/成功/失败
3. `dji_wayline_id`
4. `download_url`
5. 旧航线删除调度结果

## 8. Mock 与测试

### 8.1 Mock 契约

`apps/dji_mock` 中的航线上传 mock 返回结构应贴近线上真实响应，而不是内部自定义响应。

上传 mock 的 `data` 至少应包含：

1. `name`
2. `wayline_id`
3. `workspace_id`
4. `download_url`

不再将 `dji_wayline_id` 作为 mock 上传响应字段。

### 8.2 单元测试

保留并调整以下网关测试：

1. `upload_route()` 能从真实风格响应中解析 `wayline_id`
2. `upload_route()` 能保存并返回 `download_url`
3. 当 `wayline_id` 缺失时抛出上游异常
4. 当 `download_url` 缺失时抛出上游异常

删除以下旧测试假设：

1. STS 上传后再执行 `upload-callback`
2. 通过 `resolve_wayline_id_by_name()` 回查列表获得 ID
3. `publish_route_via_sts()` 的 orchestration 测试

### 8.3 集成测试

`Route.publish` 相关测试至少覆盖：

1. 发布成功后 `TenantRouteIndex.dji_wayline_id` 被更新
2. 发布成功后 `TenantRouteIndex.download_url` 被更新
3. 发布成功后 `TenantRouteIndex.is_published=true`
4. 重复发布时旧上游 `wayline_id` 按现有策略被 best-effort 删除
5. 上传失败时不污染本地索引状态

## 9. 文档收口

以下面向当前实现的文档必须与新设计保持一致：

1. `项目总体概览/DJI_backend_api.md`
2. `docs/dji-server-wayline-reference.md`
3. 任何仍声称“`files/upload` 不返回 `wayline_id`”“必须回查”“STS 是正式推荐发布路径”的当前实现文档

研究文档可以保留历史调研痕迹，但必须明确其结论已过时，不能继续被当作当前实现依据。

## 10. 实施约束

1. 不做双路径并存
2. 不做旧逻辑兼容层
3. 不为历史数据补救 `download_url`
4. 优先删除死代码，而不是保留名义上的“备用方案”
5. 变更应以最小正确修改完成，不扩展到与当前发布路径无关的模块

## 11. 验收标准

满足以下条件即可视为本设计被正确实现：

1. `Route.publish` 的正式运行路径只调用 `upload_route()`
2. 代码库中不再存在 `publish_route_via_sts()` 作为发布运行路径
3. 发布成功后数据库能同时看到 `dji_wayline_id` 与 `download_url`
4. mock 与测试使用的上传响应结构与线上真实返回一致
5. 面向当前实现的文档不再保留“必须通过 STS + 回查获取航线 ID”的描述
