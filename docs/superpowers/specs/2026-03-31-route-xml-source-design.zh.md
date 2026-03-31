# 航线 XML 源设计

- 日期: 2026-03-31
- 状态: 已在讨论中批准，编写用于实现规划
- 范围: 重构 `Route`，使其以上传的 XML 作为唯一可编辑源，通过 Django 默认存储保存 XML，并在发布前将 XML 转换为 KMZ 后上传到 DJI
- 替代: `docs/superpowers/specs/2026-03-30-route-waypoints-aggregation-design.md` 中关于航线编辑边界的设计

## 1. 背景

当前的航线实现仍然将航线编辑视为结构化的本地数据：

1. `Route` 暴露头部字段，例如 `route_type`、`drone_type_id`、`total_distance`、`estimated_duration` 和 `waypoint_count`。
2. 对外航线 API 暴露可编辑的 `waypoints[]`。
3. 航线发布从本地航点行构建 KMZ。

新的产品方向不同：

1. 前端将上传完整的 XML 航线文件。
2. Django 必须将该 XML 文件持久化为航线草稿源。
3. Django 必须仅在调用 `publish` 时将已保存的 XML 文件转换为 KMZ。
4. 对外 API 不应再暴露 `waypoints[]`。

这再次改变了航线聚合边界：

- 航线草稿事实来源从结构化航点行转移为已存储的 XML 文件
- 发布仍然是显式操作
- DJI 仍然只接收 KMZ 输出，绝不接收原始可编辑航线状态

## 2. 设计目标

1. 使 `xml_file` 成为唯一可编辑的航线源。
2. 保持航线 API 简洁且明确。
3. 使用 Django `FileField` 和 `default_storage` 存储 XML。
4. 通过使用本地 `MEDIA_ROOT` 保持开发简单。
5. 仅在 `publish` 期间将 XML 转换为 KMZ。
6. 移除现已冗余的、以航点为导向的对外 API 语义。

## 3. 非目标

1. 不为旧的 `waypoints[]` 客户端提供向后兼容。
2. 不进行航线数据迁移工作。
3. 除 Django storage 外，不引入特定于对象存储的抽象。
4. 不为 XML 或 KMZ 产物提供版本历史。
5. 在创建/更新期间，除“文件是可解析的 XML”之外，不进行 XML 语义校验。
6. 本阶段不支持前端直传到 S3 / MinIO。

## 4. 核心决策

`Route` 变为一个由 XML 支撑的聚合。

对外：

1. 唯一可编辑的输入为：
   - `name`
   - `xml_file`
2. `POST /api/v1/routes` 通过完整 XML 文件创建航线。
3. `PUT /api/v1/routes/{id}` 使用新的完整 XML 文件替换航线。
4. 移除 `PATCH /api/v1/routes/{id}`。
5. 从所有对外航线响应和请求中移除 `waypoints[]`。

对内：

1. 当前航线草稿就是已保存的 XML 文件。
2. 发布时读取当前 XML 文件，将其转换为 KMZ，并将 KMZ 上传到 DJI。
3. `TenantRouteIndex.is_published` 仍然只回答一个问题：
   - 当前保存的 XML 草稿是否与最近一次成功发布到 DJI 的版本一致？

## 5. 选定架构

### 5.1 存储模型

使用 Django `FileField` 和 `default_storage`。

这意味着：

1. 开发环境使用本地 `MEDIA_ROOT`。
2. 部署时后续可以切换存储后端，而无需改变航线业务语义。
3. 当前不引入额外的对象存储集成层。

后端负责管理存储文件名，不应依赖客户端上传的文件名来保证唯一性。

### 5.2 草稿 / 发布模型

航线生命周期为：

1. 创建本地 XML 草稿
2. 替换本地 XML 草稿
3. 显式将当前 XML 草稿发布到 DJI

航线更新不会自动发布。

任何成功的 `PUT` 都必须将 `is_published=false`。

这保留了已被接受的、适用于航线草稿的同一条高层规则：

- 本地编辑会造成与上一次已发布 DJI 版本的偏离

### 5.3 发布边界

`publish` 是为向 DJI 交付而解释航线 XML 的唯一位置。

发布流水线为：

1. 读取已保存的 XML 文件
2. 将 XML 转换为 KMZ
3. 将 KMZ 上传到 DJI
4. 在本地标记发布成功
5. 如果存在先前的 `dji_wayline_id`，则仅在新上传成功后删除旧的 DJI 航线

这使本地草稿编辑与上游发布保持清晰分离。

## 6. 数据模型

### 6.1 Route

`Route` 简化为：

- `id`
- `tenant`
- `name`
- `xml_file`
- `created_at`
- `updated_at`

从 `Route` 中移除：

- `route_type`
- `drone_type_id`
- `total_distance`
- `estimated_duration`
- `waypoint_count`
- 任何对外的、由 route 拥有的 `waypoints[]` 表示

### 6.2 TenantRouteIndex

`TenantRouteIndex` 保持为：

- `tenant`
- `route`
- `dji_wayline_id`
- `is_published`
- `created_at`
- `updated_at`

语义：

1. `dji_wayline_id=""` 表示该航线从未成功发布过。
2. `is_published=false` 表示当前本地 XML 草稿不是当前已发布到 DJI 的产物。
3. `is_published=true` 表示当前本地 XML 草稿就是当前已发布到 DJI 的产物。

### 6.3 航点存储

`waypoints` 不再属于航线运行时链路的一部分。

该设计假定：

1. 航线创建/更新/读取不再依赖航点行
2. 航线发布不再读取航点行
3. 对外 API 不再暴露派生自航点的数据

对于实现规划，目标终态是：

- 航线模块在业务行为上不再依赖 `apps.waypoint`

遗留航点表/模型是立即删除，还是在实现过程中作为死代码退役，属于执行细节，但它已不再是被接受的航线设计的一部分。

## 7. 对外 API

## 7.1 航线列表

### `GET /api/v1/routes`

目的：

- 列出当前租户可见的航线

支持的过滤条件：

- `name`

响应字段：

- `id`
- `name`
- `is_published`
- `created_at`
- `updated_at`

不返回：

- `waypoints[]`
- `xml_file`
- `dji_wayline_id`

示例项：

```json
{
  "id": 1,
  "name": "珠海巡检线 A",
  "is_published": false,
  "created_at": "2026-03-31T10:00:00+08:00",
  "updated_at": "2026-03-31T10:30:00+08:00"
}
```

## 7.2 航线创建

### `POST /api/v1/routes`

目的：

- 通过完整 XML 文件创建本地航线草稿

请求：

- `multipart/form-data`

必填字段：

- `name`
- `xml_file`

校验：

1. `name` 必填且非空
2. `xml_file` 必填
3. `xml_file` 内容必须是可解析的 XML

行为：

1. 通过 `Route.xml_file` 保存 XML
2. 创建 `Route`
3. 创建 `TenantRouteIndex(dji_wayline_id="", is_published=false)`
4. 不调用 DJI

响应字段：

- `id`
- `name`
- `is_published`
- `created_at`
- `updated_at`

## 7.3 航线详情

### `GET /api/v1/routes/{id}`

目的：

- 读取单个航线的元数据

响应字段：

- `id`
- `name`
- `is_published`
- `created_at`
- `updated_at`

不返回：

- `waypoints[]`
- XML 文件内容
- XML 存储路径

## 7.4 航线 XML 回读

### `GET /api/v1/routes/{id}/xml`

目的：

- 返回当前存储的原始 XML 航线草稿

行为：

1. 读取 `Route.xml_file`
2. 返回文件流响应
3. 使用 XML 内容类型

推荐的响应头：

- `Content-Type: application/xml`
- `Content-Disposition: attachment; filename="<generated-or-stored-name>.xml"`

该端点是对外回读可编辑航线源文件的唯一方式。

## 7.5 航线替换

### `PUT /api/v1/routes/{id}`

目的：

- 使用新的 XML 文件和航线名称替换完整航线草稿

请求：

- `multipart/form-data`

必填字段：

- `name`
- `xml_file`

校验：

1. `name` 必填且非空
2. `xml_file` 必填
3. `xml_file` 内容必须是可解析的 XML

行为：

1. 存储新的 XML 文件
2. 替换之前的 XML 文件引用
3. 成功替换后删除旧的本地 XML 文件
4. 更新 `Route.name`
5. 将 `TenantRouteIndex.is_published=false`
6. 保持现有 `dji_wayline_id` 不变，直到后续成功发布时再替换
7. 不调用 DJI

移除 `PATCH /api/v1/routes/{id}`。

原因：

- XML 是唯一可编辑的航线源
- 部分更新语义会重新引入歧义

## 7.6 航线发布

### `POST /api/v1/routes/{id}/publish`

目的：

- 将当前 XML 草稿发布到 DJI

请求：

- 请求体必须为空

行为：

1. 加载当前 XML 文件
2. 将 XML 转换为 KMZ
3. 将 KMZ 上传到 DJI
4. 写入新的 `dji_wayline_id`
5. 设置 `is_published=true`
6. 如果存在旧的 `dji_wayline_id` 且其与新的不同，则在新发布成功后删除旧的 DJI 航线

失败行为：

1. 如果请求体非空，返回 `400`
2. 如果 XML 无法转换为可发布的 KMZ，返回 `400`
3. 如果 DJI 上传失败，返回映射后的上游错误
4. 失败时，保持本地 XML 草稿不变
5. 失败时，不删除之前已发布的 DJI 航线

## 7.7 航线删除

### `DELETE /api/v1/routes/{id}`

目的：

- 删除航线及其存储的 XML 草稿

请求：

- 请求体必须为空

行为：

1. 如果存在引用该航线的 `PENDING` 或 `RUNNING` 任务，则拒绝删除
2. 否则删除本地航线
3. 删除本地 XML 文件
4. 如果存在 `dji_wayline_id`，尝试删除对应的 DJI 航线

删除语义有意保持与当前航线生命周期一致：

- 本地草稿删除
- 适用时进行上游清理

## 8. 校验规则

### 8.1 创建 / 替换时的 XML 校验

创建和替换仅校验：

1. 上传文件存在
2. 文件字节可被解析为 XML

创建和替换不校验：

1. DJI 任务语义
2. DJI 航线 schema 正确性
3. 航点完整性规则
4. 可发布性

这些检查被延后到发布时转换阶段。

### 8.2 发布时校验

发布时校验：

1. 航线存在 XML 文件
2. XML 可转换为 KMZ
3. 生成的 KMZ 上传到 DJI 成功

精确的 XML 到 KMZ 转换规则属于实现范畴，但对外契约是：

- 航线可以作为 XML 被存储，但仍可能发布失败

## 9. 错误模型

最小预期错误情况：

1. 创建/替换字段无效或缺失
   - `400 / B0001`
2. 上传文件不是可解析的 XML
   - `400 / B0001`
3. 发布请求包含请求体
   - `400 / B0001`
4. 已存储的 XML 无法转换为 KMZ
   - `400 / B0001`
5. 活跃任务使用中导致航线删除被阻止
   - 保持当前业务错误行为
6. 航线未找到
   - `404 / C0404`
7. 发布/删除期间 DJI 上游失败
   - 保持当前网关错误映射

## 10. 权限

航线权限保持不变：

1. `route.view_route`
   - `GET /api/v1/routes`
   - `GET /api/v1/routes/{id}`
   - `GET /api/v1/routes/{id}/xml`
2. `route.manage_route`
   - `POST /api/v1/routes`
   - `PUT /api/v1/routes/{id}`
   - `POST /api/v1/routes/{id}/publish`
   - `DELETE /api/v1/routes/{id}`

不再保留独立的航点权限。

## 11. 必需的代码简化 / 移除

该设计要求移除剩余的、对外以航点为导向的航线语义：

1. 从航线请求序列化器中移除 `waypoints[]`
2. 从航线读取序列化器中移除 `waypoints[]`
3. 移除 `RouteWaypointSerializer`
4. 移除航线运行时对 `replace_route_waypoints(...)` 的依赖
5. 用基于 XML 文件的 KMZ 生成替换当前基于航点行的 KMZ 生成
6. 移除 `PATCH /api/v1/routes/{id}`
7. 移除 `GET /api/v1/routes/{id}/download`
8. 移除那些仅为基于航点支撑的航线编辑而存在的 route queryset prefetch 和响应字段

## 12. 测试要求

最低必需覆盖范围：

1. 使用有效 XML 文件创建航线成功
2. 使用无效 XML 创建航线失败并返回 `400`
3. 航线详情不再返回 `waypoints`
4. 航线列表不再返回派生自航点的字段
5. `PUT /api/v1/routes/{id}` 要求完整替换载荷
6. 替换航线时存储新的 XML 并重置 `is_published=false`
7. `PATCH /api/v1/routes/{id}` 不可用
8. `GET /api/v1/routes/{id}/xml` 返回已存储的 XML
9. 发布时读取 XML、将其转换为 KMZ，并通过 `DjiGateway` 上传
10. 带非空请求体的发布返回 `400`
11. 发布转换失败返回 `400`
12. 删除会移除本地 XML 文件
13. 航线 OpenAPI 不再文档化 `waypoints[]`
14. 航线 OpenAPI 不再文档化 `download`

## 13. 验收标准

当以下所有条件都成立时，此次重构即告完成：

1. 航线创建/更新的对外写模型仅为 `name + xml_file`
2. 航线对外读模型不包含 `waypoints[]`
3. XML 通过 Django `FileField` 和 `default_storage` 存储
4. 航线发布在上传到 DJI 之前将已存储的 XML 转换为 KMZ
5. 任何航线替换都会重置 `is_published=false`
6. 可通过 `GET /api/v1/routes/{id}/xml` 回读航线 XML
7. `PATCH /api/v1/routes/{id}` 不存在
8. `GET /api/v1/routes/{id}/download` 不存在
