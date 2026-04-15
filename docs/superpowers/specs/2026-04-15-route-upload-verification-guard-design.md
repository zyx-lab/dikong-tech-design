# 2026-04-15 Route Upload Verification Guard Design

## 1. 背景

当前 `POST /api/v1/routes` 和 `PUT /api/v1/routes/{id}` 在收到 upstream `files/upload` 成功响应后，会直接把 `dji_wayline_id`、`download_url` 和 `is_published=true` 写入本地。

线上实测表明，这个假设并不稳定：upstream 可能已经创建 wayline 元数据并返回 `download_url`，但对象存储里的 KMZ 实际并不存在。此时业务侧会出现：

1. 航线创建或更新返回成功
2. `GET /api/v1/routes/{id}` 可见
3. `GET /api/v1/routes/{id}/kmz` 立即返回 `404`

这会把 upstream 的“假成功”扩散成业务脏数据。

## 2. 目标

1. 在 route create / update 成功写库前，立即验证 upstream KMZ 对象确实可下载
2. 若对象不存在，则本次请求整体失败，不把无效 `download_url` 落入本地库
3. create 失败时回滚新建 route
4. update 失败时保留旧 route 和旧 `TenantRouteIndex`
5. 对本次新上传的 upstream wayline 做 best-effort 删除，避免残留无效元数据

## 3. 非目标

1. 不修改 `GET /api/v1/routes/{id}/kmz` 下载逻辑
2. 不引入后台异步补偿任务
3. 不新增 route 状态字段
4. 不把 KMZ 对象内容落本地
5. 不处理历史已存在的脏 route 数据

## 4. 方案对比

### 4.1 方案 A：上传后同步验对象存在

做法：

1. 调用 upstream `files/upload`
2. 用返回的 `download_url` 直接下载一次
3. 若下载 `404`，再尝试通过 `dji_wayline_id` 刷新一次下载地址并重试
4. 两次都失败则判定本次上传无效

优点：

1. 改动最小
2. 可以复用现有下载网关逻辑
3. 能在请求内阻断脏数据写库
4. create / update 行为边界清晰

缺点：

1. create / update 多一次或两次上游下载请求
2. 请求耗时会略有增加

### 4.2 方案 B：先写库，再靠后台修复

问题：

1. 业务侧仍会持续暴露坏数据
2. 前端体验和排障成本都更差
3. 与“上传成功即应可下载”的契约相冲突

### 4.3 选型结论

采用方案 A。

## 5. 核心设计

### 5.1 新增上传后验证步骤

在 route create / update 的 upstream upload 成功后，增加一个同步校验步骤：

1. 优先用 upload 响应里的 `download_url` 调用 `gateway.download_route_file`
2. 如果返回 `404`，则用 `dji_wayline_id` 调 `gateway.get_route_download_url`
3. 用刷新后的地址再调用一次 `gateway.download_route_file`
4. 只要任一次下载成功，就认为本次上传有效
5. 两次都 `404`，则认为 upstream 只创建了元数据、未写入对象

这里不要求消费下载到的字节内容，只要求对象可访问。

### 5.2 create 路径行为

`POST /api/v1/routes` 的顺序调整为：

1. 先本地创建 `Route`
2. 调 upstream upload
3. 同步验证对象可下载
4. 只有验证通过后，才写入 `TenantRouteIndex`
5. 若验证失败：
   - 删除本次新上传的 upstream wayline
   - 删除本次新建的本地 `Route`
   - 返回 `502`

这样可保证 create 成功返回时，下载链路已具备基本可用性。

### 5.3 update 路径行为

`PUT /api/v1/routes/{id}` 的顺序调整为：

1. 保留现有 route 和旧 `TenantRouteIndex`
2. 先上传新的 upstream wayline
3. 同步验证新对象可下载
4. 只有验证通过后，才更新 route 名称和新的 `TenantRouteIndex`
5. 若验证失败：
   - 删除本次新上传的 upstream wayline
   - 不更新 route
   - 不覆盖旧 `TenantRouteIndex`
   - 返回 `502`

这样 update 失败不会破坏已有可用航线。

### 5.4 错误语义

当 upload 已成功，但对象验证失败时，Django 应返回一个明确的 upstream 错误，而不是继续写库。

建议错误语义：

- HTTP 状态：`502`
- 含义：upstream 上传后的 KMZ 对象不可下载，业务侧拒绝接收这次上传结果

这次不额外定义新的业务 code，沿用现有 upstream error 转换语义即可，但错误消息要明确指出“上传后对象校验失败”。

### 5.5 清理策略

对本次新上传 wayline 的删除，仍然采用 best-effort：

1. create 失败时清理新 wayline
2. update 失败时清理新 wayline
3. 清理失败只记录日志，不覆盖主错误

本次不尝试补删历史脏数据。

## 6. 测试设计

至少补两组回归测试：

1. create：upload 返回成功，但对象下载两次都 `404`
   - 断言接口返回 `502`
   - 断言本地 `Route` 不存在
   - 断言本地 `TenantRouteIndex` 不存在
   - 断言新 upstream wayline 被触发清理

2. update：upload 返回新 wayline，但对象下载两次都 `404`
   - 断言接口返回 `502`
   - 断言 route 名称保持旧值
   - 断言旧 `TenantRouteIndex` 保持不变
   - 断言新 upstream wayline 被触发清理

并保留现有成功路径测试，确保：

1. create 成功时仍会落库
2. update 成功时仍会替换 `TenantRouteIndex`
3. `/routes/{id}/kmz` 原有下载逻辑不受影响

## 7. 风险与边界

1. route create / update 会增加一次同步网络校验，接口耗时会上升
2. 若 upstream 存在短暂延迟写入，本方案会偏向严格失败
3. 但在当前线上故障背景下，严格失败优于写入坏数据

本次优先保证业务一致性，而不是容忍上游抖动。
