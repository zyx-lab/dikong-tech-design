# 2026-04-17 Django File Logging Design

## 1. 背景

当前项目已经有：

1. `request_id` 中间件，能给请求附加 `X-Request-ID`
2. 统一业务响应封装，能把 `traceId` 带回客户端
3. 上游网关 `apps/dji_bff/gateway.py`，会调用真实 DJI 或 mock 上游
4. 统一异常处理 `apps/access/exceptions.py`

但现状仍然不足以高效溯源问题，主要缺口有：

1. 没有稳定的文件日志落盘
2. 没有统一的请求级上下文日志
3. 没有把本地请求、业务响应、异常堆栈、上游请求/响应串成一条完整链路
4. 对复杂上游问题，缺少可直接回放的请求体、响应体、状态码和耗时记录

这次需求的目标是把 Django 后端升级成“可从文件直接复盘一条请求”的日志系统。

## 2. 目标

1. 所有核心日志写入文件，不只输出到控制台
2. 记录尽可能完整的请求链路上下文，方便后续溯源
3. 覆盖本地请求、响应、异常、上游调用、重试、超时、业务错误
4. 保持业务可用，不因为日志增强引入明显性能退化或大体积失控
5. 统一请求标识，让一次请求能串起应用日志、异常日志和上游日志

## 3. 非目标

1. 不接入第三方日志平台或链路追踪系统
2. 不改数据库表来存运行时日志
3. 不把所有二进制内容原样写入文件
4. 不引入新的外部依赖，仅靠 Django / Python 标准库完成
5. 不改变现有业务接口语义

## 4. 方案对比

### 4.1 方案 A：统一文件日志 + 请求上下文 + 上游网关日志

做法：

1. 在 `config/settings.py` 里配置文件型 `LOGGING`
2. 增加 `logs/` 目录下的轮转文件
3. 给请求生命周期补充结构化上下文
4. 在 `BusinessApiResponseMixin`、异常处理器和上游网关处分别打日志
5. 通过 `request_id` / `traceId` 串起一条链路

优点：

1. 溯源能力最强
2. 不依赖外部系统
3. 与现有代码结构兼容
4. 改动可控，便于逐步落地

缺点：

1. 文件量会明显增加
2. 需要清晰的脱敏和截断策略

### 4.2 方案 B：只补 Django `LOGGING` 到文件

优点：

1. 改动最小
2. 不需要调整业务层代码太多

缺点：

1. 只能看到普通应用日志，缺少请求/响应体和上游调用细节
2. 出问题时很难直接复盘完整链路

### 4.3 方案 C：接入外部日志/链路平台

优点：

1. 查询和检索能力更强
2. 更适合长期运营

缺点：

1. 明显超出这次需求范围
2. 需要额外基础设施和运维成本

### 4.4 选型结论

采用方案 A。

原因：

1. 它满足“尽量全量记录”的需求
2. 它最贴近当前项目结构
3. 它能在不引入新依赖的前提下实现

## 5. 核心设计

### 5.1 文件日志布局

日志目录：

- `BASE_DIR/logs/`，可由 `DJANGO_LOG_DIR` 覆盖

建议文件：

1. `BASE_DIR/logs/app.log`
   - 记录正常业务日志、请求日志、上游调用日志
2. `BASE_DIR/logs/error.log`
   - 记录异常堆栈、5xx、上游失败、未处理异常

轮转策略：

1. 使用 `RotatingFileHandler`
2. 单文件大小到阈值后轮转
3. 保留最近若干份历史文件
4. 启动时自动创建日志目录

文件日志必须包含：

1. 时间
2. 级别
3. logger 名称
4. 进程号
5. 线程号
6. 请求 ID
7. 租户上下文
8. 用户上下文
9. 请求方法
10. 请求路径
11. 状态码
12. 耗时

日志格式：

1. 采用结构化 JSON 行日志
2. 每条日志一行，便于 grep、jq 和按 `request_id` 检索
3. 同一条请求的核心字段使用固定 key，避免不同 logger 的字段名漂移

### 5.2 请求上下文

当前项目已有 `RequestContextMiddleware` 负责 `request_id`。

本次需要增强为：

1. 接收外部 `X-Request-ID`
2. 若没有则生成新的请求 ID
3. 同时保留 `request_id` 和 `trace_id` 的兼容别名
4. 记录请求开始时间
5. 附加基础上下文到 `request`

建议上下文字段：

1. `request_id`
2. `trace_id`
3. `method`
4. `path`
5. `query_string`
6. `tenant_code`
7. `tenant_id`
8. `user_id`
9. `username`
10. `remote_addr`
11. `user_agent`
12. `start_ts`

说明：

1. `trace_id` 继续向业务响应透出，避免和现有标准 envelope 脱节
2. `request_id` 继续作为请求入口和审计关联字段
3. 这两个值在本项目里应保持一致
4. 日志文件中建议统一使用 `request_id` 作为主键字段名，同时兼容读取 `trace_id`

### 5.3 请求日志

请求日志应该在每次请求完成时写一条完整记录，覆盖成功和失败两类情况。

建议落点：

1. `RequestContextMiddleware` 记录请求开始
2. `BusinessApiResponseMixin.finalize_response()` 记录请求完成
3. `custom_exception_handler()` 记录异常失败

每条请求完成日志至少包含：

1. 请求 ID
2. 方法
3. 路径
4. 查询参数
5. 请求头摘要
6. 请求体摘要或全文
7. 响应状态码
8. 响应体摘要或全文
9. 总耗时
10. 用户和租户信息
11. 是否业务 API
12. 是否命中异常

### 5.4 请求体与响应体

因为你要的是“尽量全量记录”，策略应尽量宽，但仍要避免日志被二进制和超大 payload 撑爆。

规则：

1. JSON 请求和 JSON 响应尽量原样记录
2. 表单数据尽量原样记录
3. `multipart/form-data` 记录字段名、文件名、content-type、大小，文件内容只记录文本摘要或截断摘要
4. 二进制响应只记录大小、content-type 和摘要，不原样落盘
5. 超长文本正文截断，但保留前后若干关键片段和总长度

建议的统一结构：

```json
{
  "request": {
    "method": "POST",
    "path": "/api/v1/...",
    "query": "...",
    "headers": {...},
    "body": {...}
  },
  "response": {
    "status_code": 200,
    "headers": {...},
    "body": {...}
  },
  "context": {
    "request_id": "...",
    "trace_id": "...",
    "tenant_code": "...",
    "user_id": 1,
    "username": "admin"
  },
  "duration_ms": 123
}
```

### 5.5 上游调用日志

上游调用发生在 `apps/dji_bff/gateway.py`，因此这里要补成一等日志点。

每次上游请求建议记录：

1. 本地调用方方法名
2. 上游 method
3. 上游 path
4. 上游 query
5. 上游 headers
6. 上游 payload
7. 重试次数
8. 上游状态码
9. 上游响应体
10. 上游耗时
11. 超时或网络错误详情
12. 业务错误 code/msg/data

要求：

1. `401` 后的自动重试要可见
2. `HTTPError`、`URLError`、`TimeoutError`、`SocketTimeout` 都要写明
3. 业务错误和传输错误要分开记录

### 5.6 异常日志

`apps/access/exceptions.py` 的异常处理器仍然负责统一 envelope，但还要补充日志写入。

对于未处理异常，日志中至少要有：

1. 请求 ID
2. 请求上下文
3. 异常类型
4. 异常消息
5. 完整堆栈
6. 当前响应状态码
7. 业务 API / 非业务 API 标识

对于标准化业务异常，日志中要保留：

1. 标准 code
2. 标准 msg
3. 标准 data
4. 对应请求上下文

### 5.7 脱敏与安全边界

“尽量全量记录”不等于“无边界记录”。

必须脱敏的字段：

1. `Authorization`
2. `Cookie`
3. `Set-Cookie`
4. `password`
5. `access_token`
6. `refresh_token`
7. `token`
8. `mqtt_password`
9. `DJI_UPSTREAM_PASSWORD`
10. 任何明显的 secret / key / bearer token

处理原则：

1. 保留字段名
2. 保留结构
3. 用固定标记替换敏感值

建议固定替换为：

- `***REDACTED***`

### 5.8 响应与业务 envelope 的一致性

现有业务响应里已经会返回 `traceId`，但异常处理和日志上下文需要统一成同一个 ID。

本次需要保证：

1. `request_id` 和 `trace_id` 指向同一值
2. 文件日志里使用同一个字段名
3. 响应 envelope 继续透出该 ID
4. 这样客户端、后端日志、上游排障能对齐同一条链路

## 6. 实现落点

### 6.1 `config/settings.py`

增加 `LOGGING` 配置，包含：

1. 文件 handler
2. 错误文件 handler
3. 业务 API 日志 logger
4. Django 请求日志 logger
5. 上游网关 logger

建议增加可配置环境变量：

1. `DJANGO_LOG_DIR`
2. `DJANGO_LOG_LEVEL`
3. `DJANGO_LOG_MAX_BYTES`
4. `DJANGO_LOG_BACKUP_COUNT`
5. `DJANGO_LOG_BODY_MAX_CHARS`
6. `DJANGO_LOG_HEADER_MAX_CHARS`

默认值应适合本地开发和容器运行。

同时需要把 `logs/` 加入 `.gitignore`，避免运行时日志污染仓库状态。

### 6.2 `apps/access/middleware.py`

增强 `RequestContextMiddleware`：

1. 生成或接收 `request_id`
2. 补齐 `trace_id`
3. 记录开始时间
4. 记录请求基础上下文

### 6.3 `apps/api_v1/business_response.py`

增强 `BusinessApiResponseMixin.finalize_response()`：

1. 记录响应日志
2. 记录耗时
3. 记录响应 envelope
4. 记录最终状态码

### 6.4 `apps/access/exceptions.py`

增强异常处理器：

1. 记录未处理异常堆栈
2. 记录业务异常上下文
3. 保留当前标准 envelope 行为

### 6.5 `apps/dji_bff/gateway.py`

增强 gateway 内部请求方法：

1. `_request`
2. `_request_raw`
3. `_request_json`
4. `_request_binary`
5. `_request_multipart`

这些位置负责写出上游请求、响应、重试、错误和耗时。

### 6.6 `apps/access/services.py`

如有必要，补一个轻量请求上下文提取帮助函数，避免 middleware、response、gateway 各自重复组装上下文。

## 7. 测试设计

### 7.1 `apps/access/tests.py`

覆盖：

1. `RequestContextMiddleware` 会补齐 `request_id` / `trace_id`
2. 请求上下文在响应里可见
3. 异常时仍能保留同一个请求 ID

### 7.2 `apps/api_v1/tests.py`

覆盖：

1. 成功响应会写文件日志
2. 失败响应会写文件日志
3. 响应体和状态码能在日志里被复盘

### 7.3 `apps/dji_bff/tests.py`

覆盖：

1. 上游请求会写出 method、path、payload、status、duration
2. 超时和网络错误会被单独记录
3. 自动重试路径可见

### 7.4 文件日志验证

测试应至少验证：

1. 日志文件被创建
2. 日志内容包含 request_id / trace_id
3. 日志内容包含路径、状态码、耗时
4. 敏感字段被脱敏

## 8. 验收标准

1. 业务 API 的请求、响应、异常都能写入文件
2. 上游调用日志可以把一次请求的完整链路串起来
3. 请求体、响应体、上游 payload 在文本场景下能被直接复盘
4. 敏感信息不会原样落盘
5. 日志轮转生效，不会无限增长
6. `request_id` / `trace_id` 在响应和文件日志里一致

## 9. 风险与取舍

1. 日志更详细会增加磁盘占用
2. 过度记录可能暴露敏感信息，因此脱敏必须默认开启
3. 大 body / multipart / binary 场景需要截断策略，否则日志文件会快速膨胀
4. 如果后续需要更强检索能力，可以再补外部日志系统，但不在本次范围内
