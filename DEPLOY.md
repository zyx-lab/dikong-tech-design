# 部署说明

这份文档只讲正式启动路径：**`.env` 填基础设施，启动容器，创建第一个平台业务管理员，登录系统后再做业务配置。**

当前系统是单租户 v2。没有部署期配置向导页面，也不需要在 `.env` 里填写租户或 DJI 上云账号。

## 1. 最短路径

在项目根目录执行：

```bash
make env
make up
make verify

read -s ADMIN_PASSWORD
export ADMIN_PASSWORD
make first-admin ADMIN_USERNAME=admin
unset ADMIN_PASSWORD

make urls
make ps
```

打开 `make urls` 输出的 Swagger 地址。默认是：

```text
http://127.0.0.1:8000/api/v2/docs/
```

如果本机 `.env` 里改了 `WEB_PORT`，实际端口以 `make urls` 为准。

## 2. `.env` 填什么

先复制模板：

```bash
cp .env.example .env
chmod 600 .env
```

`.env.example` 是可提交的默认模板；`.env` 是本机真实配置，不要提交。

本地调试可以直接用模板默认值。正式环境至少改这些：

```dotenv
DB_PASSWORD=<strong-postgres-password>
DJANGO_SECRET_KEY=<long-random-django-secret>
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=api.example.com
MINIO_ROOT_PASSWORD=<strong-minio-password>
OBJECT_STORAGE_ENDPOINT_URL=http://minio:9000
OBJECT_STORAGE_PUBLIC_ENDPOINT_URL=https://files.example.com
```

随机值可以这样生成：

```bash
openssl rand -hex 32
```

完整变量说明：

| 变量 | 默认值 | 什么时候改 |
| --- | --- | --- |
| `DB_ENGINE` | `postgres` | 不改。正式和 Compose 都用 PostgreSQL |
| `DB_NAME` | `dikong` | 多实例或已有数据库名不同时改 |
| `DB_USER` | `dikong_app` | 多实例或已有数据库用户不同时改 |
| `DB_PASSWORD` | `dikong-local-postgres` | 正式环境必须改 |
| `DJANGO_SECRET_KEY` | 开发默认值 | 正式环境必须改 |
| `DJANGO_DEBUG` | `true` | 正式环境改成 `false` |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | 正式环境填 API 域名或 IP，不带 `http://` |
| `WEB_PORT` | `8000` | 宿主机 8000 被占用时改，比如 `8010` |
| `MINIO_ROOT_USER` | `minioadmin` | 正式环境建议改 |
| `MINIO_ROOT_PASSWORD` | `minioadmin123` | 正式环境必须改 |
| `MINIO_API_PORT` | `9000` | 宿主机 9000 被占用时改 |
| `MINIO_CONSOLE_PORT` | `9001` | 宿主机 9001 被占用时改 |
| `OBJECT_STORAGE_BUCKET_NAME` | `dikong-route-covers` | 需要换 bucket 名时改 |
| `OBJECT_STORAGE_ENDPOINT_URL` | `http://minio:9000` | web/worker 容器访问 MinIO 的内部地址；Compose 场景通常不改 |
| `OBJECT_STORAGE_PUBLIC_ENDPOINT_URL` | `http://127.0.0.1:9000` | 浏览器访问 MinIO 的公开地址；正式环境填文件域名 |
| `DJI_INTERNAL_API_TOKEN` | 空 | 只有启用 DJI 媒体内部回调鉴权时才填 |

不要在 `.env` 里填这些：

- `DB_HOST`、`DB_PORT`：Compose 内部已经固定为 `db:5432`
- Redis 地址：Compose 内部已经固定为 `redis://redis:6379/0`
- DJI 上云 `baseUrl / username / password / loginFlag`：登录系统后在“DJI 连接”里创建
- 租户配置：当前 v2 是单租户，权限隔离靠部门和数据范围

## 3. 启动入口

推荐用根目录 `Makefile`：

| 命令 | 用途 |
| --- | --- |
| `make env` | 如果没有 `.env`，从 `.env.example` 复制一份 |
| `make up` | 启动 PostgreSQL、Redis、MinIO、web、`v2-dji-worker` |
| `make first-admin ADMIN_USERNAME=admin` | 全新库创建第一个 v2 平台业务管理员；会执行 `--reset` |
| `make sync-catalog` | 已有库升级时同步权限、角色、菜单，不重置数据 |
| `make urls` | 打印本地 API、Swagger、MinIO 地址 |
| `make verify` | 等 web 完成启动后，验证 Swagger schema 和 worker 单次检查 |
| `make logs` | 查看 web 和 worker 日志 |
| `make stop` | 停止容器，不删除数据卷 |

不用 Makefile 时，对应原生命令是：

```bash
docker compose up -d --build
docker compose ps
```

## 4. 第一次创建管理员

全新数据库第一次启动后执行：

```bash
read -s ADMIN_PASSWORD
export ADMIN_PASSWORD
make first-admin ADMIN_USERNAME=admin
unset ADMIN_PASSWORD
```

这会执行：

```bash
docker compose exec -T web python manage.py bootstrap_v2_system --reset \
  --username admin \
  --password "$ADMIN_PASSWORD" \
  --noinput
```

这个账号是**平台业务管理员**，用于登录 `/api/v2/*` 和前端业务系统。

如果只是想快速创建一组不同角色的联调账号，可以额外执行：

```bash
docker compose exec -T web python manage.py bootstrap_v2_system \
  --frontend-test-accounts \
  --frontend-prefix jnu \
  --frontend-password '123456'
```

这会在根部门“总部”下同步以下账号，统一密码为 `123456`：

| 用户名 | 角色 | 用途 |
| --- | --- | --- |
| `jnu_super` | `platform_super_admin` | 平台超级管理员 |
| `jnu_admin` | `department_admin` | 部门管理员 |
| `jnu_dispatcher` | `task_monitor_dispatcher` | 任务监控调度员 |
| `jnu_pilot` | `pilot` | 飞手，含飞手档案和有效资质 |
| `jnu_handler` | `work_order_handler` | 工单处理员 |

这组账号用于本地联调或演示。正式生产用户建议登录系统后在“账号/部门”里按真实组织创建，或调用 `POST /api/v2/iam/accounts` 创建。

第一次初始化时也可以和 `--reset` 合并执行：

```bash
docker compose exec -T web python manage.py bootstrap_v2_system --reset \
  --username admin \
  --password "$ADMIN_PASSWORD" \
  --frontend-test-accounts \
  --frontend-prefix jnu \
  --frontend-password '123456' \
  --noinput
```

不要把它和 Django Admin 混在一起。只有确实需要进入 `/admin/` 后台维护时，才额外创建 Django 后台账号：

```bash
docker compose exec web python manage.py createsuperuser
```

建议不要也叫 `admin`，避免和平台业务管理员混淆。

## 5. 登录后配置什么

管理员登录系统后，再按业务顺序配置：

1. 创建部门。
2. 创建账号，给账号分配部门和角色。
3. 需要飞手能力时，给账号配置 `pilot` 角色、飞手档案和资质。
4. 创建 DJI 连接：

   ```json
   {
     "name": "正式 DJI 上云",
     "baseUrl": "https://dji-upstream.example.com",
     "username": "<upstream-user>",
     "password": "<upstream-password>",
     "loginFlag": 1
   }
   ```

5. 对 DJI 连接执行资源发现，再绑定无人机、机场、网关、负载到部门。
6. 创建航线、任务，开始巡检流程。

DJI 连接记录保存在 PostgreSQL。资源发现、航线上传、任务、直播、相机控制和 `v2-dji-worker` 都从这里读取 DJI 上游配置。

## 6. 常见问题

**为什么不是 8000？**

看 `.env` 的 `WEB_PORT`。如果是：

```dotenv
WEB_PORT=8010
```

Swagger 就是：

```text
http://127.0.0.1:8010/api/v2/docs/
```

**已有数据怎么升级权限和菜单？**

不要用 `--reset`，执行：

```bash
make sync-catalog
```

**本地想清空重来？**

只在本地执行：

```bash
docker compose down -v
make up
```

正式环境不要执行 `docker compose down -v`，它会删除 PostgreSQL 和 MinIO 数据卷。

**Redis 是做什么的？**

Redis 用于 Django Channels WebSocket 跨进程广播，以及 `v2-dji-worker` 写 DJI MQTT 心跳。业务主数据在 PostgreSQL。

**MinIO 地址怎么填？**

`OBJECT_STORAGE_ENDPOINT_URL` 填容器内部地址，Compose 默认 `http://minio:9000`。

`OBJECT_STORAGE_PUBLIC_ENDPOINT_URL` 填浏览器能访问的地址。本机调试默认 `http://127.0.0.1:9000`；如果 `.env` 改了 `MINIO_API_PORT=9100`，这里也改成 `http://127.0.0.1:9100`；正式环境填文件域名。

旧版 `.env` 如果只有 `OBJECT_STORAGE_ENDPOINT_URL=http://127.0.0.1:9000`，改成上面这两个变量；否则 web 容器会把 `127.0.0.1` 当成自己，连不到 MinIO。
