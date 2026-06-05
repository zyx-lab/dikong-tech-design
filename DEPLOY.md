# 容器化部署指南

本项目使用 Docker Compose 进行容器化部署，当前 v2 正式联调服务包含 PostgreSQL、Redis、MinIO、Django ASGI 应用和 v2 DJI MQTT worker。

如果你的 Django 运行在宿主机而不是容器里，请先看
[宿主机 Django 切换到 Docker PostgreSQL 操作手册](docs/host-django-postgres-migration.md)；
下面的 compose 启动步骤只适用于 web 和 db 都放进容器的场景。

## 快速启动

```bash
# 1. 克隆项目后，进入项目目录
cd dikong-tech-design

# 2. 启动 8000 正式联调服务
# OBJECT_STORAGE_ENDPOINT_URL 必须是前端浏览器可访问的 MinIO 地址；本机自测可用 http://127.0.0.1:9000
OBJECT_STORAGE_ENDPOINT_URL=http://192.168.3.99:9000 docker compose up -d --no-build db redis minio minio-init web v2-dji-worker

# 3. 查看服务状态
docker compose ps

# 4. 查看日志
docker compose logs -f web v2-dji-worker
```

服务启动后访问 http://localhost:8000

`web` 只负责 HTTP 和 WebSocket 接入；DJI MQTT 设备消息、任务进度和遥测回流依赖独立的 `v2-dji-worker`。正式重启时不要只重启 `web`，否则前端 WebSocket 只能读到数据库中已有的 latest 消息，收不到新的 MQTT 广播。

## 默认账号

| 角色 | 用户名 | 密码 | 说明 |
|------|--------|------|------|
| 超级管理员 | `admin` | `admin123` | Django Admin 后台，有全部权限 |
| 运维管理员 | `ops_admin` | `ops_admin123` | Django Admin 后台，有全部权限 |
| 业务管理员 | `biz_root` | `admin123` | 业务 API 账号，不可登录 Admin |

## 架构说明

```
┌─────────────────────────────────────────────┐
│                 docker-compose.yml           │
├─────────────────────────────────────────────┤
│  PostgreSQL(db) + Redis + MinIO              │
│       │              │                       │
│       ├──────────────┤                       │
│       ▼              ▼                       │
│  Django ASGI(web:8000) + v2-dji-worker       │
└─────────────────────────────────────────────┘
```

- **db**: PostgreSQL 16 数据库容器，数据持久化到 `pgdata` 卷
- **redis**: Django Channels 和 MQTT 广播使用的 Redis
- **minio**: API v2 航线封面等对象存储
- **web**: Django ASGI 应用容器，端口映射到主机 8000
- **v2-dji-worker**: DJI MQTT 后台 worker，负责写入 MQTT latest 消息、任务事件和遥测快照

## 环境变量

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `DB_ENGINE` | `postgres` | 数据库引擎 |
| `DB_HOST` | `db` | 数据库主机 |
| `DB_NAME` | `dikong` | 数据库名称 |
| `DB_USER` | `postgres` | 数据库用户 |
| `DB_PASSWORD` | `postgres` | 数据库密码 |
| `DB_PORT` | `5432` | 数据库端口 |
| `DJANGO_SETTINGS_MODULE` | `config.settings` | Django 设置模块 |

## 常用命令

### 启动/停止服务

```bash
# 启动 8000 正式联调服务（后台运行）
# OBJECT_STORAGE_ENDPOINT_URL 必须是前端浏览器可访问的 MinIO 地址；本机自测可用 http://127.0.0.1:9000
OBJECT_STORAGE_ENDPOINT_URL=http://192.168.3.99:9000 docker compose up -d --no-build db redis minio minio-init web v2-dji-worker

# 停止
docker compose down

# 停止并删除数据卷（慎用）
docker compose down -v
```

### 数据库操作

```bash
# 进入 Django 容器
docker compose exec web sh

# 执行 Django 命令
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
docker compose exec web python manage.py shell
```

### 日志查看

```bash
# 查看 Web 服务日志
docker compose logs -f web

# 查看数据库日志
docker compose logs -f db

# 查看 v2 DJI MQTT worker 日志
docker compose logs -f v2-dji-worker

# 查看所有日志
docker compose logs -f
```

如果你要查 Django 的文件日志、`request_id` / `trace_id`，或者上游调用细节，请看
[Django 日志排查指南](docs/django-logging-guide.md)。

### 重新构建镜像

```bash
# 重新构建（代码变更后使用）
docker compose build web v2-dji-worker

# 启动并重建
OBJECT_STORAGE_ENDPOINT_URL=http://192.168.3.99:9000 docker compose up -d --build db redis minio minio-init web v2-dji-worker
```

### 重启后的最小验证

```bash
docker compose ps
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/v2/docs/schema/
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9000/minio/health/live
docker compose exec -T web python manage.py run_v2_dji_worker --once
```

期望结果：

- `web` 处于 `Up`，并映射 `0.0.0.0:8000->8000`
- `db`、`redis`、`minio` 处于 healthy
- `v2-dji-worker` 处于 `Up`
- schema 和 MinIO health 都返回 `200`
- `run_v2_dji_worker --once` 能看到当前检查到的 DJI 连接数量

## 生产环境部署

当前 `docker-compose.yml` 仅适用于开发环境。生产部署需做以下调整：

### 1. 修改数据库密码

```yaml
# docker-compose.yml
environment:
  POSTGRES_PASSWORD: <strong-password>
  DB_PASSWORD: <strong-password>
```

### 2. 使用外部数据库

```yaml
# docker-compose.yml
db:
  image: postgres:16-alpine
  # 注释掉 volumes，使用外部数据库
  # volumes:
  #   - pgdata:/var/lib/postgresql/data
  # 使用外部 PostgreSQL
  # 移除 db 服务，改用外部数据库
```

### 3. 使用 Gunicorn 替代开发服务器

```dockerfile
# Dockerfile 添加
RUN pip install gunicorn

# docker-compose.yml command 修改
command: gunicorn config.asgi:application -b 0.0.0.0:8000 --workers 4
```

### 4. 配置静态文件

```python
# config/settings/production.py
STATIC_ROOT = '/staticfiles'
MEDIA_ROOT = '/mediafiles'

# Nginx 配置
location /static/ {
    alias /staticfiles/;
}
location /media/ {
    alias /mediafiles/;
}
```

### 5. 安全建议

- 使用环境变量或 Docker Secret 存储敏感信息
- 启用 HTTPS
- 限制容器网络访问
- 定期备份数据库

## 故障排查

### 服务启动失败

```bash
# 检查端口是否被占用
netstat -tlnp | grep 8000

# 查看详细错误日志
docker-compose logs web
```

### 数据库连接失败

```bash
# 检查数据库健康状态
docker-compose ps

# 测试数据库连接
docker-compose exec db psql -U postgres -c "SELECT 1"
```

### 数据迁移问题

```bash
# 查看迁移状态
docker-compose exec web python manage.py showmigrations

# 重新执行迁移
docker-compose exec web python manage.py migrate --fake-initial
```
