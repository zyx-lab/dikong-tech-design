# API v2 资源权限重构下一步计划

## Summary

- 当前实现已完成第一垂直切片的大部分骨架：`apps/iam_v2`、`apps/resource_v2`、v2 docs/schema 路由、部门树/角色上下文、DJI 连接、资源发现、绑定/解绑、资源可见性、审计日志，以及对应测试文件。
- 下一步不要继续扩大到账户 CRUD、航线/任务/媒体 v2 业务接口；先把当前切片验收，再补齐“资源共享组管理 API”，让已有共享模型和可见性规则可以通过正式接口使用。
- 当前工作区里 `apps/iam_v2/`、`apps/resource_v2/`、`apps/api_v2/tests.py`、`docs/superpowers/plans/2026-05-31-api-v2-resource-permission-refactor-plan.md` 都是未跟踪文件；实现时必须把这些作为当前基线保留，不要从 HEAD 重新开始或恢复旧 v2 路由测试。
- 本地宿主 Python 是 3.7 且未安装 Django，不能用于验收；使用 Docker/Python 3.12 环境验证。已尝试 `docker compose run --rm --no-deps web python --version`，120 秒超时在镜像构建阶段，无运行中容器残留。

## Key Changes

- **先验收当前切片**
  - 保持 `/api/v2/` 只挂载 `iam/` 和 `resource/`，继续禁止旧 `/api/v2/drones|routes|missions|flight-records|media-files` 和 `/api/v2/__internal__/dji/*`。
  - 保持 `apps/api_v2` 作为聚合层，核心模型/规则继续放在 `apps/iam_v2` 与 `apps/resource_v2`。
  - 在 Python 3.12/Docker 中先跑现有 v2 测试；若失败，只修复当前设计偏差，不新增额外产品能力。

- **新增资源共享组管理 API**
  - 在 `apps/resource_v2` 暴露设计文档中的共享组路由：
    - `GET /api/v2/resource/share-groups`
    - `POST /api/v2/resource/share-groups`
    - `PUT /api/v2/resource/share-groups/{id}`
    - `POST /api/v2/resource/share-groups/{id}/departments`
    - `DELETE /api/v2/resource/share-groups/{id}/departments/{department_id}`
    - `POST /api/v2/resource/share-groups/{id}/resources`
    - `PUT /api/v2/resource/share-groups/{id}/resources/{resource_share_id}`
    - `DELETE /api/v2/resource/share-groups/{id}/resources/{resource_share_id}`
  - 复用现有 `ResourceShareGroup`、`ResourceShareGroupTargetDepartment`、`ResourceSharePermission` 模型；默认不新增迁移，除非 `makemigrations --check --dry-run` 暴露真实模型变更。
  - `GET /share-groups`：`department_admin` 只能看本部门拥有的共享组；`platform_super_admin` 可只读查看全部共享组；普通业务角色返回 403。
  - 共享组写操作只允许拥有部门的 `department_admin` 执行；`platform_super_admin` 默认不代替部门管理员创建或修改共享组，除非该账号同时拥有对应部门的 `department_admin` 角色。
  - `POST /share-groups` 请求体为 `{ "name": string }`，`owner_department` 固定为当前 v2 上下文部门。
  - `PUT /share-groups/{id}` 请求体为 `{ "name": string, "status": 0|1 }`，允许改名和启停；不支持变更拥有部门。
  - `POST /departments` 请求体为 `{ "departmentId": number }`；目标部门必须 active、同 tenant、精确目标部门，不自动包含子部门。
  - `POST /resources` 请求体为 `{ "resourceType": "drone"|"dock", "resourceId": number, "permissions": string[] }`；资源必须存在 active binding，且 binding 的 `owner_department` 必须等于共享组拥有部门。
  - `permissions` 统一按 `["view","monitor","dispatch_task","review_task","edit_config"]` 去重排序；空数组、未知权限、`unbind` 一律 400。
  - 重复添加目标部门或重复共享同一资源返回 409；`PUT /resources/{resource_share_id}` 只替换权限集合，不改变资源身份。
  - 删除目标部门或删除资源共享返回 200 + 被删除对象摘要，保持现有 `code/msg/data` envelope，不使用 204。

- **审计与可见性**
  - 新增共享组相关审计动作：
    - `create_share_group`
    - `update_share_group`
    - `add_share_group_target_department`
    - `remove_share_group_target_department`
    - `share_resource_to_group`
    - `update_resource_share_permissions`
    - `remove_resource_sharing`
  - 审计日志继续写入 `V2AuditLog`；`actor_department` 为操作者部门，涉及资源共享时 `resource_owner_department` 为共享组拥有部门。
  - 不改写 `visible_bindings_queryset` 的核心规则；共享组 API 创建的数据必须直接驱动现有 `/api/v2/resource/drones` 和 `/api/v2/resource/docks` 可见性。
  - 不允许共享权限授予解绑；解绑仍只允许 `platform_super_admin` 或资源拥有部门 `department_admin`。

## Test Plan

- **TDD 顺序**
  - 先扩展 `apps/api_v2/tests.py` 的 schema boundary 测试，要求 schema 包含新增 share-group 路由，且仍不包含旧 v2/内部 DJI 路由。
  - 再在 `apps/resource_v2/tests.py` 增加共享组 API 测试，确认失败后实现代码。

- **共享组 API 场景**
  - 部门管理员可创建、改名、启停本部门共享组，并产生审计日志。
  - 部门管理员不能管理父部门、子部门或兄弟部门拥有的共享组。
  - 普通业务角色不能创建或修改共享组。
  - 平台超级管理员可读取全部共享组，但默认不能代替部门创建/修改共享组。
  - 添加目标部门要求同 tenant 且 active；重复添加返回 409；删除后目标部门不再通过该组看到资源。
  - 只能共享本部门 active binding 的 drone/dock；不能共享未绑定资源、已解绑资源、其他部门资源、子部门资源。
  - `permissions` 拒绝空数组、未知值和 `unbind`；保存时按固定顺序去重。
  - 用 API 创建共享组、目标部门和资源共享后，目标部门用户通过 `/api/v2/resource/drones|docks` 能看到资源，`effectivePermissions` 等于固定角色权限与共享权限交集。
  - 删除资源共享后，目标部门用户不再通过该共享看到资源。
  - 所有共享组敏感变更写入 `V2AuditLog`，且部门管理员默认审计查询只返回本部门 actor 产生的日志。

- **当前切片回归**
  - 保留并跑通现有 IAM/resource v2 测试：部门树、v2 角色上下文、legacy `is_platform_admin` 不等于 v2 超管、DJI 连接凭证脱敏、发现、绑定冲突、解绑、资源可见性、审计查询。
  - 增加一条真实 `DjiConnectionGateway` 适配器测试：不 patch 整个 gateway，只 mock 上游请求，确认 session 字段写入 `DjiConnection`，发现 drone/dock 不写入 `DjiDeviceIndex`。
  - 跑 v1/internal DJI 回归，确认 `/api/v1/*` 和 `/api/v1/__internal__/dji/*` 未受影响。

- **验证命令**
  - 首选 Docker/Python 3.12：
    ```bash
    docker compose build web
    docker compose run --rm web sh -c "python manage.py check && python manage.py makemigrations --check --dry-run && python manage.py test apps.api_v2.tests apps.resource_v2.tests"
    docker compose run --rm web python manage.py test apps.api_v1.tests apps.dji_bff.tests apps.dji_bff.test_v2_platform_models apps.dji_mock.tests
    ```
  - 若改用本地环境，必须先创建 Python 3.12 虚拟环境并安装 `requirements.txt`；不要使用当前 Python 3.7 环境验收。

## Assumptions

- 下一步目标是“收口当前 v2 资源权限切片 + 补齐共享组 API”，不是继续实现全部设计文档。
- v2 账号 CRUD、账号部门变更、角色分配 API、v2 航线/任务/媒体/飞行记录接口继续延后；测试和后台可直接创建 `V2AccountProfile` 与 `V2AccountRoleAssignment`。
- 共享组的目标部门是精确部门，不包含目标部门子树；父部门看子部门资源仍由部门树可见性规则提供。
- 共享组写权限保持部门自治：只有拥有部门管理员管理自己的共享组；平台超级管理员本轮只读共享组和继续保留全局解绑/全局审计能力。
- 当前未跟踪的新 v2 文件和旧 v2 测试删除属于既有实施成果，实施时应纳入最终变更集，不应回滚。
