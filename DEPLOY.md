# 启动说明

本文档只说明如何把低空平台启动起来。本地调试和正式部署都先使用 Onboarding 生成 `.env`，实际服务统一使用 PostgreSQL、Redis、MinIO、Django ASGI 应用和 `v2-dji-worker`。

备份、恢复、更新、回滚、日志排障和停服命令放在启动后的运维页面：`http://127.0.0.1:8000/operations/`。

## 需要准备

- Docker Desktop 或 Docker Engine
- Docker Compose v2，也就是 `docker compose` 命令
- 可用端口：
  - `8080`：Onboarding 启动向导，只绑定 `127.0.0.1`
  - `8000`：Django API / Swagger / WebSocket
  - `9000`：MinIO S3 API
  - `9001`：MinIO Console

先确认 Docker 可用：

```bash
docker --version
docker compose version
```

## 1. 启动 Onboarding

在项目根目录执行：

```bash
docker compose up -d --build onboarding
```

本机打开：

```text
http://127.0.0.1:8080/onboarding/
```

如果在远程服务器上部署，通过 SSH 隧道访问，不要把 8080 暴露到公网：

```bash
ssh -L 8080:127.0.0.1:8080 <deploy-user>@<server>
```

## 2. 保存启动配置

在 Onboarding 里按页面逐项填写：

1. 选择场景：`本地调试` 或 `正式部署`
2. PostgreSQL：数据库名、用户、密码
3. Django：`SECRET_KEY`、允许访问的主机名
4. MinIO：Access Key、Secret Key、bucket、浏览器访问地址
5. DJI：不配置上游账号；平台用户登录后在系统内创建 DJI 连接

常用默认写法：

```dotenv
DB_ENGINE=postgres

# 本地调试
DJANGO_DEBUG=true
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
OBJECT_STORAGE_ENDPOINT_URL=http://127.0.0.1:9000

# 正式部署
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=api.example.com
OBJECT_STORAGE_ENDPOINT_URL=https://files.example.com
```

保存后向导会在项目根目录写入权限为 `600` 的 `.env`。普通 `web` 服务不能修改 `.env`。

## 3. 启动系统

保存配置后停止向导：

```bash
docker compose stop onboarding
```

按顺序启动基础设施和应用：

```bash
docker compose up -d db redis minio minio-init
docker compose up -d --build web v2-dji-worker
docker compose ps
```

正常情况下应该看到：

- `db`、`redis`、`minio` 为 healthy
- `web` 为 Up
- `v2-dji-worker` 为 Up
- `minio-init` 已退出且成功完成

## 4. 创建首个账号

全新数据库第一次启动后，创建平台管理员：

```bash
read -s ADMIN_PASSWORD
docker compose exec web python manage.py bootstrap_v2_system --reset \
  --username <platform-admin> --password "$ADMIN_PASSWORD" --noinput
unset ADMIN_PASSWORD
```

如果需要 Django Admin 超级用户，再执行：

```bash
docker compose exec web python manage.py createsuperuser
```

## 5. 最小验证

本地调试默认验证：

```bash
curl -fsS http://127.0.0.1:8000/api/v2/docs/schema/ > /dev/null
curl -fsS http://127.0.0.1:9000/minio/health/live > /dev/null
docker compose exec -T web python manage.py run_v2_dji_worker --once
```

访问地址：

- API 根服务：`http://127.0.0.1:8000`
- API v2 Swagger：`http://127.0.0.1:8000/api/v2/docs/`
- API v2 OpenAPI Schema：`http://127.0.0.1:8000/api/v2/docs/schema/`
- MinIO Console：`http://127.0.0.1:9001`
- MinIO S3 API：`http://127.0.0.1:9000`
- 运维页面：`http://127.0.0.1:8000/operations/`

正式部署时，把上面的 `127.0.0.1` 换成 Onboarding 中填写的实际访问域名。

## DJI 连接

DJI 上云地址、账号、密码和 `loginFlag` 由平台用户登录系统后创建，保存到 PostgreSQL 的 `DjiConnection` 记录中，不写入 `.env`。

创建后，资源发现、航线上传、任务、直播、相机控制和 `v2-dji-worker` 都读取这条连接配置。

如果 DJI 上云侧需要主动推送媒体上传结果，可以在 Onboarding 中填写可选媒体 Webhook Token，然后让上云侧请求：

```http
POST http://<django-host>:8000/api/internal/dji/callbacks/media-upload
X-DJI-Internal-Token: <shared-callback-token>
```

不配置该 token 时，媒体 Webhook 会被拒绝，这是正常的可选状态。任务完成同步、前端手动刷新和历史回填命令仍可用于媒体同步。
