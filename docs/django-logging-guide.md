# Django 日志排查指南

这份文档用于排查 Django 后端和上游 DJI 服务的问题。当前项目已经把请求日志、异常日志和上游调用日志写到文件里，通常只要拿到一次请求的 `request_id`，就能把这条链路从头到尾串起来。

## 后台管理入口

当前项目提供了一个 superuser 专用后台：

- `GET /admin/`：Django Admin 首页（只允许 `is_superuser=true` 且 `is_active=true` 登录）
- `GET /admin/system/logs/`：日志查看页面（可切换日志文件、查看尾部 N 行）

你可以在这个后台里直接查看数据库模型数据（各业务模型列表/详情页）和日志文件，不需要再单独搭建第二套管理系统。

## 先看哪里

默认日志目录是项目根目录下的 `logs/`，也可以通过环境变量覆盖：

- `DJANGO_LOG_DIR`：日志目录
- `DJANGO_LOG_LEVEL`：日志级别，默认 `INFO`
- `DJANGO_LOG_MAX_BYTES`：单个日志文件滚动阈值
- `DJANGO_LOG_BACKUP_COUNT`：保留多少个轮转备份
- `DJANGO_LOG_BODY_MAX_CHARS`：请求/响应体最大保留字符数
- `DJANGO_LOG_HEADER_MAX_CHARS`：请求/响应头最大保留字符数

当前默认会生成两个文件：

- `app.log`：正常业务日志、请求完成日志、上游请求/响应日志
- `error.log`：错误级别日志、异常堆栈、上游失败日志

日志是 **JSON 行** 格式，也就是一行一条记录。建议优先用 `jq`、`grep`、`tail -f` 来看，不要靠肉眼翻长文本。

## 一次请求怎么串起来

当前实现里，`request_id` 和 `trace_id` 会被设置成同一个值。你可以把它理解成同一条链路的两个名字：

- 客户端发请求时，如果带了 `X-Request-ID`，Django 会直接沿用
- 如果客户端没带，Django 会生成一个新的 UUID
- 响应里会回传 `X-Request-ID`
- 业务响应体里会带 `traceId`
- 文件日志里会同时记录 `request_id` 和 `trace_id`

所以排障时，优先顺序通常是：

1. 从响应头 `X-Request-ID` 拿链路号
2. 或从响应体里的 `traceId` 拿链路号
3. 用这个值去查 `app.log` 和 `error.log`

## 先看哪些事件

日志里常见的事件名如下：

- `request_finished`：一次请求正常结束，或者业务层正常返回
- `request_exception`：请求过程中抛异常，已经被异常处理器捕获
- `upstream_request`：向 DJI 上游发起请求
- `upstream_response`：收到 DJI 上游响应
- `upstream_login_request`：向 DJI 登录上游
- `upstream_login_response`：收到 DJI 登录响应
- `upstream_refresh_request`：刷新上游 token
- `upstream_refresh_response`：收到刷新结果
- `upstream_retry`：因为认证失败等原因触发重试
- `upstream_error`：上游超时、不可达、HTTP 错误、业务错误等

如果你在查 502、超时、上游接口异常，通常要同时看 `request_finished`、`request_exception` 和 `upstream_*` 事件。

## 常用查法

### 1. 先按 request_id 查整条链路

假设链路号是 `req-123`：

```bash
grep '"request_id":"req-123"' logs/app.log
grep '"request_id":"req-123"' logs/error.log
```

如果机器上有 `jq`，更推荐这样查：

```bash
jq -c 'select(.request_id=="req-123")' logs/app.log
jq -c 'select(.request_id=="req-123")' logs/error.log
```

### 2. 先看一次请求的完成日志

```bash
jq -c 'select(.event=="request_finished" and .request_id=="req-123")' logs/app.log
```

重点看这些字段：

- `status_code`
- `duration_ms`
- `request.method`
- `request.path`
- `request.body`
- `response.body`
- `context.user_id`
- `context.username`
- `context.tenant_code`

### 3. 查异常堆栈

```bash
jq -c 'select(.event=="request_exception" and .request_id=="req-123")' logs/error.log
```

重点看这些字段：

- `exception.type`
- `exception.message`
- `exception.stack`
- `status_code`
- `request.path`
- `response.status_code`

### 4. 查上游 DJI 调用

如果是 DJI 相关接口，优先看上游调用事件：

```bash
jq -c 'select(.event|startswith("upstream_"))' logs/app.log
jq -c 'select(.event=="upstream_error")' logs/error.log
```

如果你知道链路号，也可以直接按链路过滤：

```bash
jq -c 'select(.request_id=="req-123" and (.event | startswith("upstream_")))' logs/app.log
```

上游日志里通常会有：

- `request.method`
- `request.path`
- `request.url`
- `request.headers`
- `request.body`
- `response.status_code`
- `response.body`
- `error.type`
- `error.message`
- `duration_ms`
- `attempt`

## 怎么判断问题在哪一层

### 1. Django 本地问题

特征：

- `request_finished` 有记录，但没有对应的 `upstream_*`
- 或者 `request_exception` 里是本地 Python 异常、校验异常、权限异常

常见结论：

- 路由没挂上
- 请求参数不对
- 权限 / 租户上下文不对
- 本地业务代码抛错

### 2. 上游超时或不可达

特征：

- `upstream_error.type` 是 `timeout` 或 `unreachable`
- `error.message` 里有 `timed out` 或 `unreachable`
- `status_code` 通常是 `502`

常见结论：

- 上游服务不通
- 网络异常
- 上游响应太慢

### 3. 上游认证失败

特征：

- `upstream_error.type` 是 `auth_error`
- 先出现 `upstream_retry`
- 随后又发起第二次上游请求

常见结论：

- 上游 token 失效
- 登录态需要刷新
- 上游账号或密码配置有问题

### 4. 上游业务错误

特征：

- `upstream_error.type` 是 `business_error`
- 上游已经返回了响应，但业务状态不允许继续

常见结论：

- 设备不在线
- 资源已经不存在
- capacity / list 里被过滤掉
- 上游业务状态和本地预期不一致

## 查 DJI live 问题时的顺序

如果你在查 `live/start`、`live/capacity`、`live/stop` 这一类问题，可以按这个顺序看：

1. 先拿到这次请求的 `request_id`
2. 查 `app.log` 里的 `request_finished`
3. 如果返回 5xx，再查 `error.log` 里的 `request_exception`
4. 再查同一个 `request_id` 下的 `upstream_request` / `upstream_response` / `upstream_error`
5. 如果看到 `upstream_retry`，再看第一次失败和第二次成功/失败的差别
6. 如果是 `capacity` 里找不到设备，再确认上游是否真的返回了设备、设备状态是否在线、是否被租户或 workspace 过滤

## 看日志时要注意什么

- 请求体和响应体会被脱敏，常见敏感字段包括 `Authorization`、`Cookie`、`password`、`token`、`secret`、`mqtt_password`
- 大 body 会被截断，避免日志无限膨胀
- 二进制内容只会记录类型和大小，不会直接展开
- `error.log` 里的错误通常也会在 `app.log` 里出现一份，因为两者都挂在同一个 root logger 上
- 如果日志文件滚动了，注意同时看 `app.log.1`、`error.log.1` 之类的历史文件

## 在服务器上怎么查

如果 Django 是直接跑在宿主机上：

```bash
tail -f logs/app.log
tail -f logs/error.log
```

如果你不确定日志目录在哪，可以先看环境变量或 Django 设置：

```bash
python manage.py shell -c 'from django.conf import settings; print(settings.DJANGO_LOG_DIR)'
```

如果 Django 跑在 Docker 里，先进入容器再看文件：

```bash
docker-compose exec web sh
tail -f logs/app.log
tail -f logs/error.log
```

如果容器里的工作目录不是项目根目录，就先用上面的 `settings.DJANGO_LOG_DIR` 打印出真实路径。

## 发现问题后看什么

- 如果是业务参数问题，重点看 `request_finished` 里的 `request.body` 和 `response.body`
- 如果是权限问题，重点看 `context.user_id`、`context.username`、`context.tenant_code`
- 如果是上游问题，重点看 `upstream_error`、`upstream_response`、`duration_ms`、`attempt`
- 如果是 404 / 502 / timeout，一般先看 `error.log`，再回 `app.log` 复盘整条链路

## 快速命令模板

```bash
# 1. 看最近的应用日志
tail -n 50 logs/app.log

# 2. 看最近的错误日志
tail -n 50 logs/error.log

# 3. 按 request_id 查整条链路
jq -c 'select(.request_id=="req-123")' logs/app.log logs/error.log

# 4. 查某次请求的异常
jq -c 'select(.event=="request_exception" and .request_id=="req-123")' logs/error.log

# 5. 查某次请求的上游调用
jq -c 'select(.request_id=="req-123" and (.event=="upstream_request" or .event=="upstream_response" or .event=="upstream_error"))' logs/app.log logs/error.log
```

这份文档的目标很简单：先把 `request_id` 找出来，再把 `app.log` 和 `error.log` 串起来看。只要链路号一致，通常就能定位问题落在哪一层。
