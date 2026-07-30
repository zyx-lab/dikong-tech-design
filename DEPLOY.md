# 启动配置填写说明

这份文档只讲一件事：**从零把低空平台跑起来时，`.env` 应该怎么填，填完按什么顺序启动。**

当前 Docker Compose 会启动 PostgreSQL、Redis、MinIO、Django ASGI 应用和 `v2-dji-worker`。DJI 上云账号不写在 `.env`，由平台用户登录系统后创建 DJI 连接。

## 1. 先复制 `.env.example`

在项目根目录执行：

```bash
cp .env.example .env
chmod 600 .env
```

`.env.example` 是默认模板，可以提交；`.env` 是本机真实配置，不要提交。Docker Compose 会自动读取 `.env`。

## 2. 本地调试怎么填

`.env.example` 已经填了本地调试默认值。刚复制出来时内容等价于：

```dotenv
DB_ENGINE=postgres
DB_NAME=dikong
DB_USER=dikong_app
DB_PASSWORD=dikong-local-postgres

DJANGO_SECRET_KEY=dev-dikong-secret-key-change-me-32chars
DJANGO_DEBUG=true
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
WEB_PORT=8000

MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=minioadmin123
MINIO_API_PORT=9000
MINIO_CONSOLE_PORT=9001
OBJECT_STORAGE_BUCKET_NAME=dikong-route-covers
OBJECT_STORAGE_ENDPOINT_URL=http://<your-machine-ip>:9000

DJI_INTERNAL_API_TOKEN=
```

本地如果只是先看 Swagger，可以直接用默认的 `http://127.0.0.1:9000`。如果要上传文件或让另一台机器上的前端打开文件链接，`OBJECT_STORAGE_ENDPOINT_URL` 必须改成浏览器能访问到的 MinIO 地址，例如：

```dotenv
OBJECT_STORAGE_ENDPOINT_URL=http://192.168.1.20:9000
```

如果本机端口被其他进程占用，可以只改宿主机端口：

```dotenv
WEB_PORT=8010
MINIO_API_PORT=9100
MINIO_CONSOLE_PORT=9101
```

这时访问地址也要跟着变成 `http://127.0.0.1:8010/api/v2/docs/`。

## 3. 正式部署怎么填

正式环境用强随机值，不要沿用本地默认密码：

```dotenv
DB_ENGINE=postgres
DB_NAME=dikong
DB_USER=dikong_app
DB_PASSWORD=<strong-postgres-password>

DJANGO_SECRET_KEY=<long-random-django-secret>
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=api.example.com
WEB_PORT=8000

MINIO_ROOT_USER=dikong-storage
MINIO_ROOT_PASSWORD=<strong-minio-password>
MINIO_API_PORT=9000
MINIO_CONSOLE_PORT=9001
OBJECT_STORAGE_BUCKET_NAME=dikong-route-covers
OBJECT_STORAGE_ENDPOINT_URL=https://files.example.com

DJI_INTERNAL_API_TOKEN=
```

生成随机值可以用：

```bash
openssl rand -hex 32
```

正式环境填写规则：

- `DJANGO_DEBUG` 固定填 `false`
- `DJANGO_ALLOWED_HOSTS` 只填域名或 IP，不要写 `http://`，不要写路径
- `OBJECT_STORAGE_ENDPOINT_URL` 填用户浏览器能访问的对象存储根地址
- `DB_PASSWORD`、`DJANGO_SECRET_KEY`、`MINIO_ROOT_PASSWORD` 必须换成强随机值
- `DJI_INTERNAL_API_TOKEN` 默认留空，只有 DJI 上云侧要主动推送媒体上传结果时才填

## 4. 每个变量是什么意思

| 变量 | 怎么填 | 说明 |
| --- | --- | --- |
| `DB_ENGINE` | 固定 `postgres` | 本项目启动环境统一使用 PostgreSQL |
| `DB_NAME` | 常用 `dikong` | PostgreSQL 数据库名 |
| `DB_USER` | 常用 `dikong_app` | PostgreSQL 用户名 |
| `DB_PASSWORD` | 本地可用示例值，正式用强密码 | PostgreSQL 密码 |
| `DJANGO_SECRET_KEY` | 随机长字符串 | Django 签名密钥，正式环境必须保密 |
| `DJANGO_DEBUG` | 本地 `true`，正式 `false` | 是否开启调试模式 |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` 或 `api.example.com` | 允许访问 API 的主机名，多个用英文逗号分隔 |
| `WEB_PORT` | 默认 `8000` | 宿主机访问 API 的端口；如果改成 `8010`，Swagger 就在 `http://127.0.0.1:8010/api/v2/docs/` |
| `MINIO_ROOT_USER` | 本地 `minioadmin`，正式自定义 | MinIO 管理员 Access Key |
| `MINIO_ROOT_PASSWORD` | 本地 `minioadmin123`，正式用强密码 | MinIO 管理员 Secret Key |
| `MINIO_API_PORT` | 默认 `9000` | 宿主机访问 MinIO S3 API 的端口 |
| `MINIO_CONSOLE_PORT` | 默认 `9001` | 宿主机访问 MinIO Console 的端口 |
| `OBJECT_STORAGE_BUCKET_NAME` | 常用 `dikong-route-covers` | MinIO bucket 名 |
| `OBJECT_STORAGE_ENDPOINT_URL` | 本地 MinIO 地址或正式文件域名 | API 返回文件 URL 时使用 |
| `DJI_INTERNAL_API_TOKEN` | 可留空 | 可选媒体 Webhook 鉴权，不是 DJI 上游账号 |

不用在 `.env` 里填这些：

- `DB_HOST`：Compose 里已经给 `web` 固定为 `db`
- `DB_PORT`：Compose 里已经固定为 `5432`
- Redis 地址：Compose 里已经固定为 `redis://redis:6379/0`
- DJI 上云 `baseUrl / username / password / loginFlag`：登录系统后创建 DJI 连接

## 5. 启动系统

填好 `.env` 后，在项目根目录执行：

```bash
docker compose up -d db redis minio minio-init
docker compose up -d --build web v2-dji-worker
docker compose ps
```

正常状态：

- `db`、`redis`、`minio` 是 healthy
- `web` 是 Up
- `v2-dji-worker` 是 Up
- `minio-init` 成功退出

如果你已经用旧 `.env` 启动过本地数据库，再改 `DB_NAME`、`DB_USER`、`DB_PASSWORD` 不会自动重建旧数据卷。本地想重来可以执行：

```bash
docker compose down -v
docker compose up -d db redis minio minio-init
docker compose up -d --build web v2-dji-worker
```

正式环境不要执行 `docker compose down -v`，它会删除 PostgreSQL 和 MinIO 数据卷。

## 6. 创建首个账号

全新数据库第一次启动后创建平台管理员：

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

平台业务账号和 Django Admin 是两套入口。普通业务配置优先用平台账号。

## 7. 最小验证

本地调试：

```bash
curl -fsS http://127.0.0.1:8000/api/v2/docs/schema/ > /dev/null
curl -fsS http://127.0.0.1:9000/minio/health/live > /dev/null
docker compose exec -T web python manage.py run_v2_dji_worker --once
```

访问地址：

- API：`http://127.0.0.1:8000`
- Swagger：`http://127.0.0.1:8000/api/v2/docs/`
- MinIO Console：`http://127.0.0.1:9001`

正式部署时，把 `127.0.0.1` 换成你在 `.env` 中配置的实际域名或 IP。

## 8. DJI 连接怎么配

`.env` 里不填 DJI 上云账号。

系统启动后，用平台管理员登录，再创建 DJI 连接，填写：

```json
{
  "name": "正式 DJI 上云",
  "baseUrl": "https://dji-upstream.example.com",
  "username": "<upstream-user>",
  "password": "<upstream-password>",
  "loginFlag": 1
}
```

这条记录保存到 PostgreSQL。资源发现、航线上传、任务、直播、相机控制和 `v2-dji-worker` 都从这条记录读取 DJI 上游配置。

`DJI_INTERNAL_API_TOKEN` 只用于可选媒体 Webhook。如果上云侧不主动推送媒体上传结果，就留空。
