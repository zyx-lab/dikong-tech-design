# 本地部署说明

本文档说明如何在本机用 Docker Compose 启动低空平台后端服务。当前本地部署会启动 PostgreSQL、Redis、MinIO、Django ASGI 应用和 v2 DJI MQTT worker。

## 需要准备

- Docker Desktop 或 Docker Engine
- Docker Compose v2，也就是 `docker compose` 命令
- 可用端口：
  - `8000`：Django API / Swagger / WebSocket
  - `9000`：MinIO S3 API
  - `9001`：MinIO Console

先确认 Docker 可用：

```bash
docker --version
docker compose version
```

## 启动方式

在项目根目录执行：

```bash
cd dikong-tech-design

OBJECT_STORAGE_ENDPOINT_URL=http://127.0.0.1:9000 \
docker compose up -d --build db redis minio minio-init web v2-dji-worker
```

说明：

- `OBJECT_STORAGE_ENDPOINT_URL` 必须是浏览器能访问的 MinIO 地址。本机部署通常用 `http://127.0.0.1:9000`。
- 第一次启动建议带 `--build`，确保镜像按当前代码构建。
- 后续没有改 Python 依赖时，可以使用 `--no-build` 加快启动。

启动后查看状态：

```bash
docker compose ps
```

正常情况下应该看到：

- `db`、`redis`、`minio` 为 healthy
- `web` 为 Up
- `v2-dji-worker` 为 Up
- `minio-init` 已退出且状态为成功完成

## 会启动几个容器

`docker-compose.yml` 定义了 6 个 service：

| service | 是否常驻 | 作用 |
| --- | --- | --- |
| `db` | 是 | PostgreSQL 16 数据库 |
| `redis` | 是 | Django Channels 和 MQTT 广播使用的 Redis |
| `minio` | 是 | 航线封面等对象存储 |
| `minio-init` | 否 | 初始化 MinIO bucket，执行完成后退出 |
| `web` | 是 | Django ASGI 应用，提供 HTTP 和 WebSocket |
| `v2-dji-worker` | 是 | DJI MQTT 后台 worker，处理设备消息、任务事件和遥测 |

因此本地部署时会创建 6 个 service，其中 5 个长期运行，1 个一次性初始化。

## 访问地址

服务启动后访问：

- API 根服务：`http://127.0.0.1:8000`
- API v2 Swagger：`http://127.0.0.1:8000/api/v2/docs/`
- API v2 OpenAPI Schema：`http://127.0.0.1:8000/api/v2/docs/schema/`
- MinIO Console：`http://127.0.0.1:9001`
- MinIO S3 API：`http://127.0.0.1:9000`

MinIO 默认账号来自 `docker-compose.yml`：

| 用户名 | 密码 |
| --- | --- |
| `minioadmin` | `minioadmin123` |

## v2 初始化

当前 compose 启动时，`web` 容器会自动执行迁移、v2 权限/角色/菜单同步和航线封面对象存储迁移。

不会内置固定业务账号。如果本地库需要创建或重置一个 v2 平台超管账号，手动执行：

```bash
docker compose exec web python manage.py bootstrap_v2_system --reset --username <super_username> --password '<strong_password>' --noinput
```

如果需要 Django Admin 超级管理员，进入容器后手动创建：

```bash
docker compose exec web python manage.py createsuperuser
```

## 最小验证

启动后执行以下检查：

```bash
docker compose ps

curl -sS -o /dev/null -w '%{http_code}\n' \
  http://127.0.0.1:8000/api/v2/docs/schema/

curl -sS -o /dev/null -w '%{http_code}\n' \
  http://127.0.0.1:9000/minio/health/live

docker compose exec -T web python manage.py run_v2_dji_worker --once
```

期望结果：

- API schema 返回 `200`
- MinIO health 返回 `200`
- `run_v2_dji_worker --once` 输出当前检查到的 DJI 连接数量

如果 API schema 不是 `200`，先看 `web` 日志：

```bash
docker compose logs -f web
```

## 常用操作

查看日志：

```bash
docker compose logs -f web
docker compose logs -f v2-dji-worker
docker compose logs -f db
docker compose logs -f redis
docker compose logs -f minio
```

进入 Django 容器：

```bash
docker compose exec web sh
```

执行 Django 命令：

```bash
docker compose exec web python manage.py migrate
docker compose exec web python manage.py showmigrations
docker compose exec web python manage.py shell
```

重启应用容器：

```bash
docker compose restart web v2-dji-worker
```

代码或依赖更新后重新构建：

```bash
OBJECT_STORAGE_ENDPOINT_URL=http://127.0.0.1:9000 \
docker compose up -d --build web v2-dji-worker
```

停止服务：

```bash
docker compose down
```

停止并删除数据库、对象存储数据卷：

```bash
docker compose down -v
```

`down -v` 会删除 PostgreSQL 和 MinIO 数据，只在你确认要重置本地环境时使用。

## 本地数据重置

如果想重建一个干净环境：

```bash
docker compose down -v

OBJECT_STORAGE_ENDPOINT_URL=http://127.0.0.1:9000 \
docker compose up -d --build db redis minio minio-init web v2-dji-worker
```

启动后重新创建需要的 Django Admin 账号：

```bash
docker compose exec web python manage.py createsuperuser
```

## DJI 联调配置

`web` 负责 HTTP 和 WebSocket；`v2-dji-worker` 负责 MQTT 后台消息。正式联调 DJI 设备时，不要只启动 `web`。

当前 v2 没有全局 DJI 上游地址、账号或密码环境变量。真实 DJI 上游连接来自数据库中的 `DjiConnection` 记录，而不是部署环境变量。

启动服务：

```bash
export DJI_INTERNAL_API_TOKEN='<shared-callback-token>'

OBJECT_STORAGE_ENDPOINT_URL=http://127.0.0.1:9000 \
docker compose up -d --build db redis minio minio-init web v2-dji-worker
```

服务启动后，通过 v2 API 创建 DJI 连接。请求体里的 `baseUrl/username/password/loginFlag` 会保存到 `v2_dji_connections`；资源发现、航线上传、任务、直播、相机控制和 `v2-dji-worker` 都读取这条连接配置：

```http
POST /api/v2/resource/dji-connections
Content-Type: application/json

{
  "name": "本地 DJI",
  "baseUrl": "https://example-dji-upstream.test",
  "username": "your-username",
  "password": "your-password",
  "loginFlag": 1
}
```

如果要让 DJI 媒体上传结果主动回调本系统，还需要在 DJI 上云侧配置回调地址，并让上云侧请求头 `X-DJI-Internal-Token` 使用同一个 `DJI_INTERNAL_API_TOKEN`：

```http
POST http://<django-host>:8000/api/internal/dji/callbacks/media-upload
X-DJI-Internal-Token: <shared-callback-token>
```

没有设置 `DJI_INTERNAL_API_TOKEN` 时，Django 会拒绝内部回调。回调不是唯一媒体同步机制：任务完成时会自动同步媒体，前端仍可通过 `POST /api/v2/inspection/flight-records/{id}/refresh-media` 主动刷新飞行记录媒体，历史飞行记录可通过 `python manage.py refresh_v2_flight_record_media --all-completed` 一次性回填。

如果只做普通 API 本地验证，可以不创建 DJI 连接。此时涉及真实 DJI 上游的接口会因为没有可用 `DjiConnection`、未发现/未绑定资源或上游调用失败而返回业务错误。

## 对象存储说明

本地 compose 已包含 MinIO，默认 bucket 为 `dikong-route-covers`。API v2 航线封面通过 Django default storage 写入 MinIO。

关键环境变量：

| 变量名 | 本地默认值 | 说明 |
| --- | --- | --- |
| `OBJECT_STORAGE_BACKEND` | `minio` | 使用 MinIO/S3 兼容存储 |
| `OBJECT_STORAGE_ACCESS_KEY_ID` | `minioadmin` | MinIO access key |
| `OBJECT_STORAGE_SECRET_ACCESS_KEY` | `minioadmin123` | MinIO secret key |
| `OBJECT_STORAGE_BUCKET_NAME` | `dikong-route-covers` | bucket 名 |
| `OBJECT_STORAGE_ENDPOINT_URL` | `http://127.0.0.1:9000` | 浏览器可访问的 MinIO S3 endpoint |
| `AWS_S3_ADDRESSING_STYLE` | `path` | MinIO 推荐 path-style |
| `AWS_QUERYSTRING_AUTH` | `true` | 返回私有对象预签名 URL |

如果部署机器不是本机访问，`OBJECT_STORAGE_ENDPOINT_URL` 要改成前端浏览器能访问到的地址，例如 `http://192.168.x.x:9000`。

## 常见问题

### 端口被占用

检查占用：

```bash
lsof -i :8000
lsof -i :9000
lsof -i :9001
```

解决方式是停止占用进程，或者修改 `docker-compose.yml` 里的端口映射。

### `web` 启动失败

查看日志：

```bash
docker compose logs --tail=200 web
```

常见原因：

- 数据库未 healthy
- 迁移失败
- 环境变量错误
- MinIO endpoint 不可访问

### `v2-dji-worker` 没有收到新消息

先确认 worker 在运行：

```bash
docker compose ps v2-dji-worker
docker compose logs -f v2-dji-worker
```

再确认 Redis、DJI 上游和数据库连接配置。`web` 只负责 HTTP/WebSocket，新的 DJI MQTT 消息依赖 `v2-dji-worker`。

### MinIO 图片链接打不开

确认 `OBJECT_STORAGE_ENDPOINT_URL` 是浏览器可访问地址：

```bash
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9000/minio/health/live
```

如果你从另一台机器访问前端，不能用 `127.0.0.1`，要改成部署机器 IP 或域名。

## 生产部署提醒

当前 `docker-compose.yml` 更适合本地部署和正式联调。生产环境至少需要做这些调整：

- 修改 PostgreSQL、MinIO、Django secret 等敏感密码
- 使用 `.env`、Docker Secret 或部署平台密钥管理，不要把生产密码写进仓库
- 使用外部 PostgreSQL、Redis、S3/MinIO 时，删除或替换 compose 内置基础设施 service
- 使用 HTTPS 和反向代理，例如 Nginx、Traefik 或云负载均衡
- 为 PostgreSQL 和对象存储配置备份
- 根据访问量横向扩展 `web`，但 `v2-dji-worker` 是否能多副本运行需要先确认 MQTT 订阅和重复消费策略
