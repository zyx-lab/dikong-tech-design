# 低空平台 API v2

这是一个 Django + Django REST Framework 项目，当前只维护 `/api/v2/*` 正式业务 API。旧的本系统 `/api/v1/*`、v1 文档、legacy 业务 app 和 legacy 数据表已经移除。

## 当前入口

- API v2：`/api/v2/*`
- 启动 Onboarding：`/onboarding/`
- 运维页面：`/operations/`
- OpenAPI JSON：`/api/v2/docs/schema/`
- Swagger UI：`/api/v2/docs/`
- DJI internal callback：`/api/internal/dji/callbacks/media-upload`
- DJI internal group callback：`/api/internal/dji/callbacks/media-group-upload`
- DJI upstream mock：`/__mock-dji__/api/v1/*`

`/__mock-dji__/api/v1/*` 以及 `apps.dji_cloud.gateway` 中的 `/api/v1/manage/*`、`/api/v1/wayline/*`、`/api/v1/media/*` 是 DJI 云平台自身的上游协议路径，不是本系统对外业务 v1 API。

新成员从 [开发者 Onboarding 手册](docs/onboarding.md) 开始。

## 保留模块

- `apps.access`：账号、Bearer token 会话、认证类、标准异常、请求日志基础设施。
- `apps.iam_v2`：部门、账号档案、角色、权限、菜单、当前用户上下文。
- `apps.resource_v2`：DJI 连接、无人机、机场、网关、负载、资源绑定、共享组、资源审计。
- `apps.inspection_v2`：航线、巡检任务、飞行会话、遥测快照、直播、相机动作、飞行记录、云媒体文件。
- `apps.system_v2`：系统菜单、操作日志、登录日志、文件日志。
- `apps.api_v2`：v2 聚合路由、OpenAPI 文档和边界测试。
- `apps.dji_cloud`：DJI 上游 HTTP gateway；内部媒体 callback 由 `apps.inspection_v2` 承接。
- `apps.dji_mock`：本地 DJI 上游协议 mock。

## 已移除模块

- `apps.api_v1`
- `apps.access.api_v1`
- `apps.drone`
- `apps.drone_assignment`
- `apps.route`
- `apps.waypoint`
- `apps.mission`
- `apps.flight_record`
- `apps.media_file`
- `apps.dji_bff`

旧数据按删除策略处理，不迁移到 v2 表。保留的数据面是 `auth_users`、`auth_sessions` 和 `v2_*` 表。

## 本地运行

本地 Docker 调试也从启动向导开始：

```bash
docker compose up -d --build onboarding
open http://127.0.0.1:8080/onboarding/
```

按向导保存 `.env` 后启动系统：

```bash
docker compose stop onboarding
docker compose up -d db redis minio minio-init
docker compose up -d --build web v2-dji-worker
```

## v2 初始化

```bash
docker compose exec web python manage.py bootstrap_v2_system
```

## 对象存储

航线封面和 KMZ 文件通过 Django storage 保存。默认使用本地文件系统；配置 `OBJECT_STORAGE_BACKEND=s3` 或 `OBJECT_STORAGE_BACKEND=minio` 后可切换到对象存储。

常用变量：

```bash
export OBJECT_STORAGE_BACKEND=minio
export OBJECT_STORAGE_ENDPOINT_URL=http://127.0.0.1:9000
export OBJECT_STORAGE_BUCKET_NAME=dikong-route-covers
export OBJECT_STORAGE_ACCESS_KEY_ID=minioadmin
export OBJECT_STORAGE_SECRET_ACCESS_KEY=minioadmin
export OBJECT_STORAGE_REGION_NAME=us-east-1
```

相关维护命令：

```bash
python manage.py migrate_route_covers_to_object_storage
python manage.py refresh_route_kmz_download_urls
```

## DJI 集成

v2 资源发现通过 `/api/v2/resource/dji-connections/{id}/discover` 触发，并使用 `apps.resource_v2.gateway.DjiConnectionGateway` 复用 `apps.dji_cloud.gateway.DjiGateway`。

v2 正式业务链路没有全局 DJI 上游地址、账号或密码环境变量。上云地址、账号、密码和登录 flag 由 `POST /api/v2/resource/dji-connections` 写入 `v2_dji_connections` 表；后续资源发现、航线上传、任务、直播、相机控制和 `v2-dji-worker` 都通过对应 `DjiConnection.base_url/username/password/login_flag` 调用上游。

最小请求体示例：

```json
{
  "name": "本地 DJI",
  "baseUrl": "https://example-dji-cloud",
  "username": "admin",
  "password": "secret",
  "loginFlag": 1
}
```

DJI 媒体回调通过 `/api/internal/dji/callbacks/media-upload` 进入 v2 inspection 服务。带 `job_id` 的媒体会按 `MissionCloudExecution` 精确绑定任务；不带 `job_id` 时会按设备绑定写入未绑定云媒体记录。

## 验证

```bash
export DJANGO_LOG_DIR=/tmp/dikong-tech-design-logs
export DB_ENGINE=sqlite
export DJANGO_TEST_FAST_PASSWORD_HASHERS=1
python manage.py check
python manage.py makemigrations --check --dry-run
python -m compileall -q apps config scripts
scripts/test_v2_regression.sh fast
```
