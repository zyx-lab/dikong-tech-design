# 低空平台 API v2

Django + Django REST Framework + Channels 后端。当前只维护 `/api/v2/*` 正式业务 API。

## 部署入口

正式启动看 [DEPLOY.md](DEPLOY.md)。最短命令：

```bash
make env
make up
make verify

read -s ADMIN_PASSWORD
export ADMIN_PASSWORD
make first-admin ADMIN_USERNAME=admin
unset ADMIN_PASSWORD

make urls
```

默认 Swagger：

```text
http://127.0.0.1:8000/api/v2/docs/
```

如果 `.env` 里改了 `WEB_PORT`，以 `make urls` 输出为准。

## 系统边界

- 当前 v2 是**单租户**：一个部署实例，一个根部门，部门树内做权限和数据范围隔离。
- `.env` 只放基础设施配置：PostgreSQL、Django、MinIO、端口和可选内部回调 token。
- DJI 上云账号不是环境变量；管理员登录后创建 DJI 连接。
- 平台业务管理员由 `bootstrap_v2_system --reset` 创建；Django `createsuperuser` 只用于 `/admin/`。

## 服务入口

| 入口 | 路径 |
| --- | --- |
| API v2 | `/api/v2/*` |
| Swagger UI | `/api/v2/docs/` |
| OpenAPI JSON | `/api/v2/docs/schema/` |
| Django Admin | `/admin/` |
| DJI MQTT WebSocket | `/ws/v2/dji/mqtt?token=<accessToken>` |
| DJI media callback | `/api/internal/dji/callbacks/media-upload` |
| DJI media group callback | `/api/internal/dji/callbacks/media-group-upload` |
| DJI upstream mock | `/__mock-dji__/api/v1/*` |

`/__mock-dji__/api/v1/*` 以及 `apps.dji_cloud.gateway` 中的 `/api/v1/manage/*`、`/api/v1/wayline/*`、`/api/v1/media/*` 是 DJI 上云自身协议路径，不是本系统对外业务 v1 API。

## 运行组件

| 组件 | 作用 |
| --- | --- |
| PostgreSQL | 主数据库 |
| Redis | WebSocket 跨进程广播、DJI MQTT worker 心跳 |
| MinIO | 航线、封面、媒体等对象存储 |
| `web` | Django ASGI API 服务 |
| `v2-dji-worker` | DJI 资源同步、MQTT、任务和媒体同步 |

## 模块地图

- `apps.access`：账号、Bearer token 会话、认证、异常、请求日志、存储后端。
- `apps.iam_v2`：部门、账号档案、角色、权限、菜单、当前用户上下文。
- `apps.resource_v2`：DJI 连接、无人机、机场、网关、负载、资源绑定、共享组、MQTT/WebSocket。
- `apps.inspection_v2`：航线、巡检任务、飞行会话、遥测、直播、相机动作、飞行记录、云媒体文件。
- `apps.system_v2`：系统菜单、操作日志、登录日志、文件日志。
- `apps.api_v2`：v2 聚合路由、OpenAPI 文档和边界测试。
- `apps.dji_cloud`：DJI 上游 HTTP gateway。
- `apps.dji_mock`：本地 DJI 上游协议 mock。

## 开发验证

开发者说明见 [docs/development.md](docs/development.md)。提交前常用验证：

```bash
export DJANGO_LOG_DIR=/tmp/dikong-tech-design-logs
export DB_ENGINE=sqlite
export DJANGO_TEST_FAST_PASSWORD_HASHERS=1

python manage.py check
python manage.py makemigrations --check --dry-run
python -m compileall -q apps config scripts
scripts/test_v2_regression.sh fast
```
