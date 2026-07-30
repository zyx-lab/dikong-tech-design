# 开发者 Onboarding 手册

本文面向第一次参与低空平台 API v2 开发的后端工程师。完成后，你应该能够在本地启动服务、理解一次请求经过的主要边界、完成小改动并通过项目验证门禁。

## 1. 先建立正确认知

- 项目基于 Python 3.12、Django 5.1、Django REST Framework、Channels。
- 当前只维护本系统的 `/api/v2/*` 业务接口。
- `apps.dji_cloud` 和 `apps.dji_mock` 中出现的 `/api/v1/*` 是 DJI 上游协议，不是本系统旧版 API。
- 普通 HTTP 请求由 `web` 处理；DJI MQTT 消息、资源状态和媒体同步由 `v2-dji-worker` 处理。
- PostgreSQL 是默认运行数据库；测试脚本和轻量本地开发使用 SQLite。

先阅读：

1. [项目 README](../README.md)：项目边界、入口和模块概览。
2. [工程评审图](../plantumls/engineering_review/README.md)：系统上下文、运行架构、核心领域和任务链路。
3. [前端接入指南](api-v2-frontend-guide.md)：接口语义和主要业务流程。

## 2. 本地启动

日常后端开发优先使用 SQLite，启动快，也不依赖外部服务。需要验证 PostgreSQL、Redis、MinIO、WebSocket 或 DJI worker 时再使用 Docker Compose。

### 2.1 SQLite 开发环境

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

export DB_ENGINE=sqlite
export DJANGO_LOG_DIR=/tmp/dikong-tech-design-logs

python manage.py migrate
python manage.py bootstrap_v2_system --reset \
  --username local_admin \
  --password '<local-password>' \
  --noinput
python manage.py runserver 0.0.0.0:8000
```

`bootstrap_v2_system --reset` 会清空并重建 v2 IAM 初始化数据，只能用于新建或确认可以重置的本地数据库。已有本地数据时运行不带 `--reset` 的命令，只同步权限、角色和菜单目录：

```bash
python manage.py bootstrap_v2_system
```

启动后检查：

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  http://127.0.0.1:8000/api/v2/docs/schema/
```

期望返回 `200`。常用入口：

| 入口 | 地址 |
| --- | --- |
| Swagger UI | `http://127.0.0.1:8000/api/v2/docs/` |
| OpenAPI Schema | `http://127.0.0.1:8000/api/v2/docs/schema/` |
| Django Admin | `http://127.0.0.1:8000/admin/` |
| API v2 | `http://127.0.0.1:8000/api/v2/*` |
| DJI MQTT WebSocket | `ws://127.0.0.1:8000/ws/v2/dji/mqtt` |

### 2.2 完整集成环境

完整环境的启动、初始化、日志和重置命令统一维护在 [本地部署说明](../DEPLOY.md)。最小启动命令是：

```bash
OBJECT_STORAGE_ENDPOINT_URL=http://127.0.0.1:9000 \
docker compose up -d --build db redis minio minio-init web v2-dji-worker
```

不要在需要 MQTT 或任务状态联调时只启动 `web`。不要随意执行 `docker compose down -v`，它会删除本地 PostgreSQL 和 MinIO 数据卷。

## 3. 代码地图

| 目录 | 职责 |
| --- | --- |
| `config/` | Django 设置、HTTP 根路由、ASGI/WSGI 入口、日志配置 |
| `apps/access/` | 用户、Bearer 会话、认证、异常、请求及外部调用日志、存储后端 |
| `apps/api_contracts/` | API 契约辅助代码 |
| `apps/api_v2/` | v2 聚合路由、OpenAPI 元数据和契约检查 |
| `apps/iam_v2/` | 部门、账号档案、角色、权限、菜单和请求上下文 |
| `apps/resource_v2/` | DJI 连接、设备资源、绑定、共享、MQTT 和 WebSocket |
| `apps/inspection_v2/` | 航线、任务、飞行会话、遥测、直播、相机、媒体和 worker |
| `apps/audit_v2/` | v2 操作审计模型和写入服务 |
| `apps/system_v2/` | 菜单、操作日志、登录日志和文件日志接口 |
| `apps/dji_cloud/` | DJI 上游 HTTP gateway |
| `apps/dji_mock/` | 本地 DJI 上游协议 mock |
| `scripts/` | 回归测试、Schema 对比和本地联调脚本 |

一次典型 API 请求的路径是：

```text
config/urls.py
  -> apps/api_v2/urls.py
  -> 业务模块 urls.py / view
  -> serializer 校验
  -> service / model
  -> apps.audit_v2（需要审计时）
  -> BusinessApiResponseMixin 标准响应
```

认证由 `apps.access.authentication.BearerAuthSessionAuthentication` 统一处理；权限目录的唯一代码定义在 `apps/iam_v2/permissions.py`；数据范围和权限判断集中在 `apps/iam_v2/services.py`。不要在业务 view 中另建一套角色或权限规则。

## 4. 完成一个改动

遵循仓库根目录 `AGENTS.md` 中的 KISS、YAGNI、TDD、DRY、SOLID 原则：

1. 从用户场景写出一个可观察的预期行为。
2. 找到拥有该行为的模块，不跨模块复制规则。
3. 先补最小失败测试或明确验证命令。
4. 修改 serializer、service、model 或 view 中真正负责该行为的一层。
5. 若数据结构变化，生成并检查迁移；不要手写与模型不一致的表结构说明。
6. 若接口字段、状态码或路径变化，同步 OpenAPI 元数据和相关接入说明。
7. 运行聚焦测试，再运行默认回归门禁。

常见改动位置：

| 改动 | 优先查看 |
| --- | --- |
| 新增或调整 API 路径 | 对应模块 `urls.py`、`views.py`、`serializers.py` |
| 业务状态转换 | 对应模块 `services.py` 和模型约束 |
| 权限或菜单 | `apps/iam_v2/permissions.py`、`bootstrap.py`、权限测试 |
| DJI HTTP 调用 | `apps/dji_cloud/gateway.py` 及调用方 service |
| MQTT、遥测、WS | `apps/resource_v2/mqtt.py`、`consumers.py`、inspection worker |
| 操作审计 | `apps/audit_v2/services.py` |
| 响应结构或 API 文档 | `apps/common/`、`apps/api_v2/` 和契约测试 |

## 5. 验证门禁

开发时先运行最相关的 Django 测试标签，例如：

```bash
DB_ENGINE=sqlite python manage.py test apps.resource_v2.tests
```

提交前运行默认门禁：

```bash
scripts/test_v2_regression.sh fast
```

涉及 DJI gateway、mock、共享响应边界或跨模块契约时运行：

```bash
scripts/test_v2_regression.sh boundary
```

默认门禁会执行 Django system check、迁移漂移检查和主要 v2 测试。测试范围及 Schema 线上对比方式以 [回归测试说明](v2-regression-testing.md) 为准。

提交前至少确认：

- 新行为有正向、失败或边界验证中的必要部分。
- `python manage.py makemigrations --check --dry-run` 没有遗漏迁移。
- API 仍使用标准响应和 Bearer 认证边界。
- 没有把 token、密码、数据库凭据或线上响应证据提交进仓库。
- 修改只覆盖当前需求，没有顺手加入推测性能力。

## 6. 调试顺序

1. 记录 HTTP 状态码、标准业务 `code`、响应体 `traceId` 或响应头 `X-Request-ID`。
2. 按对应的 `request_id` 查请求生命周期日志。
3. 涉及 DJI 时，再查外部调用日志，区分本地校验失败、上游认证失败、超时和上游业务错误。
4. 涉及 MQTT 时，同时检查 `v2-dji-worker`、Redis 和 WebSocket 链路。
5. 涉及文件时，检查 storage backend、bucket 和浏览器可访问的对象存储 endpoint。

具体查询命令和字段解释见 [Django 日志排查指南](django-logging-guide.md)。调试上游或代理 API 前，先按 `skills/debug-upstream/SKILL.md` 执行。

## 7. DJI 与对象存储边界

- DJI 上游连接保存在 `DjiConnection`，不是全局账号环境变量。
- `DJI_INTERNAL_API_TOKEN` 只保护 DJI 内部媒体回调；本地未配置时回调会被拒绝。
- 本地不做 DJI 联调时，可以不创建 DJI 连接。
- 默认 storage 是本地文件系统；完整 Compose 环境使用 MinIO。
- `OBJECT_STORAGE_ENDPOINT_URL` 必须是前端浏览器可访问的地址，跨机器访问时不能填写 `127.0.0.1`。
- 实验性联调不属于每次提交的强制门禁，只有改动触及对应边界时才执行。

## 8. 第一周建议

按下面顺序熟悉项目，不需要一次读完所有代码：

1. 本地启动并打开 Swagger，完成一次登录和一个只读 API 请求。
2. 从 `config/urls.py` 跟踪该请求到 view、serializer、service 和 model。
3. 阅读 `apps/iam_v2/services.py`，理解请求上下文、权限和部门数据范围。
4. 跑一个模块测试，再跑 `scripts/test_v2_regression.sh fast`。
5. 选择一个小改动，按“测试或验证 -> 最小实现 -> 回归门禁”的闭环完成。

完成标准：你能说明改动属于哪个模块、它影响哪个接口或后台链路、用什么证据证明行为正确，以及失败时先查哪类日志。
