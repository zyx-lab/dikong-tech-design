# 航线 KMZ 直传与下载代理设计

- 日期: 2026-04-08
- 状态: 已在讨论中批准，编写用于实现规划
- 范围: 将业务航线接口从“上传 XML 草稿 + 显式 publish”重构为“上传 KMZ 即发布”，保留 `PUT /routes/{id}`，删除 `/publish`，并将下载接口切换为基于已保存 `download_url` 的 KMZ 代理下载
- supersedes:
  - `docs/superpowers/specs/2026-03-31-route-xml-source-design.md`
  - `docs/superpowers/specs/2026-04-07-route-upload-direct-publish-design.md`

## 1. 背景

当前航线链路仍然保留 XML 草稿语义：

1. `POST /api/v1/routes` 与 `PUT /api/v1/routes/{id}` 接收 `xml_file`
2. `Route` 本地保存 XML 草稿文件
3. `POST /api/v1/routes/{id}/publish` 读取本地 XML，转换为 KMZ，再上传 DJI
4. `GET /api/v1/routes/{id}/xml` 读取本地 XML 草稿文件

新的目标边界不是继续维护这套“双阶段草稿/发布”模型，而是：

1. 业务接口直接接收可发布的 `kmz_file`
2. 上传业务接口即完成 DJI 发布
3. 本地只保存 route 基本信息与 DJI 发布索引，不保存本地 XML/KMZ 草稿副本
4. 下载接口不再读本地文件，而是根据最近一次成功上传返回的 `download_url` 去 DJI 拉取资源并返回

这次改造的核心，是把 route 从“本地可编辑草稿”切换成“业务侧持有已发布航线的最小索引”。

## 2. 设计目标

1. `POST /api/v1/routes` 和 `PUT /api/v1/routes/{id}` 改为接收 `kmz_file`
2. 成功上传即完成 DJI 发布，不再要求额外调用 `/publish`
3. 保留 `PUT /api/v1/routes/{id}` 作为同一 route 资源的替换接口
4. 删除 `/publish` 与 `/xml`，新增或切换为 `/kmz` 下载接口
5. 下载接口仅依赖 `TenantRouteIndex.download_url` 获取上游文件
6. DJI 侧上传名称包含 `route.id` 与 `route.name`，但更新/删除仍只依赖 `wayline_id`
7. 不为旧 XML 草稿模型做兼容层，直接收口到新契约

## 3. 非目标

1. 不保留 `xml_file` 的兼容写法
2. 不保留 `/api/v1/routes/{id}/publish`
3. 不保留 `/api/v1/routes/{id}/xml`
4. 不在本地保存 KMZ 副本
5. 不引入 route 历史版本表
6. 不为旧 route 数据做 XML 到 KMZ 的迁移或回填
7. 不要求 DJI 侧航线名称与业务侧 `route.name` 严格相等

## 4. 方案对比

### 4.1 方案 A: KMZ 上传即发布，保留 PUT，删除 publish，不保存本地文件

特点：

1. `POST/PUT` 直接上传 KMZ 到 DJI
2. 本地只保存 `Route` 基本字段和 `TenantRouteIndex` 发布索引
3. `/routes/{id}/kmz` 基于 `download_url` 做下载代理
4. `/publish` 删除

优点：

1. 语义最简单
2. 调试路径最短
3. 不再维护 XML 草稿/转换链路
4. 完全对齐“下载接口按 `download_url` 拉资源返回”的要求

缺点：

1. 失去本地草稿态
2. route 创建/更新成功与否更强依赖 DJI 可用性

### 4.2 方案 B: KMZ 上传为草稿，继续保留 publish

特点：

1. `POST/PUT` 只保存本地 KMZ
2. `/publish` 负责上传 DJI
3. 下载仍可从本地或上游读取

问题：

1. `KMZ` 已经是成品包，再保留 `/publish` 会造成重复语义
2. 仍然维持两阶段状态，增加实现和排查复杂度
3. 与本次明确要求不一致

### 4.3 方案 C: KMZ 上传即发布，同时保留本地 KMZ 副本

特点：

1. `POST/PUT` 上传 DJI 的同时把 KMZ 也保存在本地
2. `/kmz` 可优先读本地副本

问题：

1. 本地副本与上游 `download_url` 会形成双事实源
2. 需要额外处理本地文件清理与一致性
3. 不符合当前“方案 1，不保留本地副本”的决定

### 4.4 选型结论

采用方案 A。

原因：

1. 满足“保留 `PUT`、删除 `/publish`”
2. 满足“使用 `download_url` 下载，不保存本地副本”
3. 删除 XML 草稿模型后，系统边界最清楚

## 5. 核心决策

### 5.1 上传即发布

`POST /api/v1/routes` 与 `PUT /api/v1/routes/{id}` 的成功语义都是：

1. 本地 route 已存在或已更新
2. 对应 DJI wayline 已创建成功
3. `TenantRouteIndex` 已写入新的 `dji_wayline_id` 与 `download_url`
4. `is_published=true`

不再存在“本地已保存但未发布”的 route 草稿态。

### 5.2 保留 PUT，删除 publish

`PUT /api/v1/routes/{id}` 继续存在，因为它表达的是“更新同一个 route 资源”。

`POST /api/v1/routes/{id}/publish` 删除，因为发布动作已经被吸收到 `POST/PUT` 中，再保留会造成重复语义。

### 5.3 删除 XML 语义

以下内容全部移除：

1. `Route.xml_file`
2. XML 解析校验
3. XML -> KMZ 转换服务
4. `/routes/{id}/xml`
5. 与本地 XML 草稿存在性相关的运行逻辑与测试

### 5.4 下载只依赖 `download_url`

`GET /api/v1/routes/{id}/kmz` 的唯一事实来源是 `TenantRouteIndex.download_url`。

服务端不从本地文件系统读取 KMZ，也不通过 `dji_wayline_id` 再次查询即时下载地址。

### 5.5 DJI 上传名称策略

DJI 上传接口中的 `name` 不要求与业务侧 `route.name` 完全相同，但必须包含业务可识别信息。

上传名称格式固定为：

- `{route.id}-{sanitized_route_name}-{uuid8}`

约束：

1. 包含 `route.id`，便于回查业务记录
2. 包含 `route.name` 的 sanitize 版本，便于人工识别
3. 包含 `uuid8`，避免 `PUT` 重传时撞同名
4. `sanitize` 只做轻量清洗：去首尾空格、折叠空白、替换明显危险字符、控制总长度

DJI 名称仅用于人工识别和排查，不作为更新或删除旧航线的定位依据。

## 6. 数据模型

### 6.1 Route

`Route` 收敛为最小业务主体：

- `id`
- `tenant`
- `name`
- `created_at`
- `updated_at`

删除字段：

- `xml_file`

本设计不要求增加新的本地文件字段来替代 `xml_file`。

### 6.2 TenantRouteIndex

`TenantRouteIndex` 继续承担 DJI 映射状态：

- `tenant`
- `route`
- `dji_wayline_id`
- `download_url`
- `is_published`
- `created_at`
- `updated_at`

字段语义：

1. `dji_wayline_id` 表示当前 route 对应的最新 DJI 航线 ID
2. `download_url` 表示该版本航线的下载路径
3. `is_published=true` 表示该 route 当前绑定的就是一条已成功上传的 DJI 航线
4. 在新模型下，只要 route 创建/更新成功，`is_published` 就应为 `true`

### 6.3 不新增其他表

本设计不新增：

1. route 上传历史表
2. route 版本表
3. 文件缓存表

当前需求只要求切换运行契约，不要求追踪多版本文件历史。

## 7. 公共 API

### 7.1 Route Create

`POST /api/v1/routes`

请求：

- `multipart/form-data`
- 字段：
  - `name`
  - `kmz_file`

行为：

1. 创建 route
2. 上传 KMZ 到 DJI
3. 写入 `TenantRouteIndex`
4. 返回 route 详情

成功响应中不返回本地文件字段。

### 7.2 Route Update

`PUT /api/v1/routes/{id}`

请求：

- `multipart/form-data`
- 字段：
  - `name`
  - `kmz_file`

行为：

1. 更新同一条 route 的业务名
2. 上传新的 KMZ 到 DJI
3. 切换 `TenantRouteIndex` 到新的 `dji_wayline_id/download_url`
4. 事务提交后清理旧 DJI 航线

### 7.3 Route Download

`GET /api/v1/routes/{id}/kmz`

行为：

1. 读取当前 route 的 `TenantRouteIndex.download_url`
2. 使用上游认证调用该 `download_url`
3. 将返回的 KMZ 二进制内容代理给客户端

错误语义：

1. 若 route 不存在，返回 `404`
2. 若 `download_url` 为空，返回 `404`
3. 若上游返回 `404`，返回 `404`
4. 其他上游异常按现有业务 API 错误包装处理

### 7.4 Route Delete

`DELETE /api/v1/routes/{id}`

保留现有规则：

1. 若仍被未删除且活跃的 mission 引用，则拒绝删除
2. 否则删除 route，并 best-effort 删除当前 `dji_wayline_id` 对应的 DJI 航线

但删除逻辑中不再涉及任何本地 XML 文件清理。

### 7.5 被移除接口

以下接口从公开契约中删除：

1. `POST /api/v1/routes/{id}/publish`
2. `GET /api/v1/routes/{id}/xml`

## 8. DJI 网关契约

### 8.1 上传

仍使用：

- `POST /api/v1/wayline/workspaces/{workspace_id}/waylines/files/upload`

必需成功字段：

1. `wayline_id`
2. `download_url`

网关规范化后至少返回：

1. `dji_wayline_id`
2. `download_url`

缺任一字段都视为上游响应不完整，按失败处理。

### 8.2 下载

新增或改造网关能力：

- 接收库存的 `download_url`
- 若为相对路径，则按 DJI base URL 解析
- 使用当前认证信息请求上游资源
- 允许跟随上游跳转，最终返回 KMZ 二进制内容

### 8.3 删除

继续使用：

- `DELETE /api/v1/wayline/workspaces/{workspace_id}/waylines/{wayline_id}`

删除旧航线只依赖 `wayline_id`，不依赖旧航线名称。

## 9. 创建流程

### 9.1 成功路径

由于 DJI 上传名称需要包含 `route.id`，创建顺序固定为：

1. 先创建本地 `Route`，获得 `route.id`
2. 生成 DJI 上传名称：`{route.id}-{sanitized_route_name}-{uuid8}`
3. 调用 `upload_route()` 上传 `kmz_file`
4. 获取 `new_wayline_id + new_download_url`
5. 创建或更新 `TenantRouteIndex`
   - `dji_wayline_id = new_wayline_id`
   - `download_url = new_download_url`
   - `is_published = true`
6. 返回成功响应

### 9.2 失败路径

1. 若上传前本地校验失败，直接返回 `400`
2. 若 DJI 上传失败，整个创建失败，本地 route 不保留
3. 若 DJI 上传成功但本地事务失败，必须补偿删除 `new_wayline_id`

### 9.3 本地校验边界

本地只做轻量校验，不引入复杂 KMZ 解析器。

最小校验边界：

1. `kmz_file` 必填且非空
2. 文件需满足基本 ZIP/KMZ 格式约束

更细的 WPML 语义正确性仍交给 DJI 上游判定。

## 10. 更新流程

`PUT /api/v1/routes/{id}` 的更新流程采用“先上传新航线，再切换本地索引，最后清理旧航线”的顺序，不允许先删除旧航线再上传新航线。服务端在更新开始时先读取当前 `TenantRouteIndex.dji_wayline_id`，记为 `old_wayline_id`。随后上传新的 KMZ 文件到 DJI，成功后得到 `new_wayline_id` 和 `download_url`，并在同一事务内把本地 `Route` 与 `TenantRouteIndex` 更新为新值。事务提交后，若 `old_wayline_id` 非空且与 `new_wayline_id` 不同，则以 best-effort 方式调用 `DELETE /api/v1/wayline/workspaces/{workspace_id}/waylines/{old_wayline_id}` 清理旧航线。旧航线清理仅依赖历史保存的 `old_wayline_id`，不依赖旧航线名称；DJI 侧上传名称仅用于人工识别和排查，不作为更新或删除的定位依据。若“新航线上传成功但本地事务失败”，服务端必须对 `new_wayline_id` 执行补偿删除，避免在 DJI 侧留下未被本地引用的孤儿航线。

补充约束：

1. 若 `old_wayline_id` 为空，更新成功后无需执行旧航线清理
2. 若 `old_wayline_id == new_wayline_id`，视为无需清理旧航线
3. best-effort 删除失败只记录日志，不回滚本次更新成功结果

## 11. 删除流程

`DELETE /api/v1/routes/{id}` 保持当前业务原则，但适配新模型：

1. 查询是否存在 `is_deleted=false` 且状态为 `PENDING/RUNNING/PAUSED` 的 mission 引用该 route
2. 若存在，返回 `400`
3. 若不存在，删除 route 本地记录
4. 若存在 `dji_wayline_id`，best-effort 删除该 DJI 航线

删除流程不清理本地 XML 或本地 KMZ，因为新设计中两者都不落本地。

## 12. 测试策略

### 12.1 Route API 测试

需要替换或重写现有 XML 相关测试，覆盖：

1. `POST /routes` 接收 `kmz_file`
2. `PUT /routes/{id}` 接收 `kmz_file`
3. 创建成功后 `TenantRouteIndex` 持久化 `dji_wayline_id + download_url`
4. `GET /routes/{id}/kmz` 通过 `download_url` 返回资源
5. `PUT` 时先上传新航线，再切换索引，再清理旧航线
6. 本地事务失败时补偿删除新航线
7. 删除 route 时 best-effort 删除当前 DJI 航线
8. `/publish` 返回 `404`
9. `/xml` 返回 `404`

### 12.2 Schema 测试

OpenAPI 需要同步锁定：

1. 写接口字段从 `xml_file` 改成 `kmz_file`
2. `/routes/{id}/publish` 不再暴露
3. `/routes/{id}/xml` 不再暴露
4. `/routes/{id}/kmz` 暴露为二进制下载接口

### 12.3 Live / Mock 测试

需要同步改动：

1. mock 下载路径中的断言改为 KMZ
2. live contract 从 XML 上传/回读切到 KMZ 上传/下载
3. 更新下载代理相关 mock，使其能响应保存的 `download_url`

## 13. 实现边界

实现阶段允许直接删除与新设计冲突的旧逻辑，不做兼容层，包括但不限于：

1. `xml_file` 字段与本地文件处理逻辑
2. XML 校验与 XML->KMZ 转换服务
3. `/publish` 运行路径
4. `/xml` 下载路径
5. 仅服务于旧 XML 模型的测试假设、注释和文档

本设计不要求保留旧 route 数据，也不要求保留旧接口返回结构的兼容性。
