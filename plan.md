 # Pilot2 手动执行与 Dock 自动任务双模式改造计划

  Summary

  - 结论：当前 start_mission() 把所有任务都推进成 DJI /flight-tasks 是错的；该路径只应保留给机场自动执行模式。Pilot2 遥控器测试环境应走“本地创建任务 -> 确保航线已同步到现有上云服务/Pilot2 可见
    -> 飞手在遥控器执行 -> 平台手动开始/完成闭环”。

  - 官方依据：DJI 产品支持页区分遥控器 domain=2 与机场 domain=3；Pilot2 航线管理是云端给 Pilot2 提供航线列表/下载/上传结果接口，不是创建 flight task。参考 DJI 官方文档：产品支持
    (https://developer.dji.com/doc/cloud-api-tutorial/cn/overview/product-support.html)、Pilot2 获取航线列表
    (https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/pilot-to-cloud/https/waypoint-management/obtain-waypointfile-list.html)、Pilot2 获取航线下载地址
    (https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/pilot-to-cloud/https/waypoint-management/get-waypointfile-download-location.html)、Pilot2 上传结果回调
    (https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/pilot-to-cloud/https/waypoint-management/waypointfile-upload-result-report.html)。

  - 仓库锚点：现有错误调用在 apps/inspection_v2/services.py:688；/flight-tasks 被硬编码在 apps/dji_cloud/gateway.py:329；前端文档也需要改掉通用任务启动说明 docs/api-v2-frontend-guide.md:335。
  - 置信度：高。代码证据和 DJI 官方能力边界一致；唯一外部假设是你已确认 Pilot2 当前连接“已有上云服务”，不是直接连本 Django 后端。

  Key Changes

  - 任务模式由资源字段决定，不新增写入字段：
      - dockId 存机场自动执行资源，模式为 DOCK_AUTO。
      - executorId 存遥控器/Pilot2 执行端，模式为 PILOT2_MANUAL。
      - 创建/更新任务时 dockId 与 executorId 必须二选一；都不传或都传返回 400。

  - 新增只读字段：
      - MissionReadSerializer.executionMode: DOCK_AUTO | PILOT2_MANUAL。
      - MissionCloudExecution.execution_mode 模型字段，历史已有 dji_job_id 的执行记录默认迁移为 DOCK_AUTO。
      - MissionPreflightCheckResponse.execution.executionMode。
      - routeSnapshot.djiFile：任务创建时快照航线的 djiConnectionId/workspaceId/djiFileId/waylineType/downloadUrl，让“任务已同步到 Pilot2 可见航线库”可被前端直接展示和排查。

  - 航线同步策略：
      - 保留现有航线创建/更新时上传 KMZ 到已有上云服务的流程。
      - 任务创建/更新时不重传 KMZ，因为当前没有持久化原始 KMZ 文件；只做强校验：任务航线必须已有 WaypointRouteCloudFile，且其 dji_connection_id 必须等于无人机绑定和所选 dockId/executorId 的 DJI
        连接。

      - 校验失败返回 409，提示“任务航线尚未同步到当前 DJI 连接，请重新上传/更新航线”。

  - 网关命名收敛：
      - 新增 DjiGateway.create_dock_flight_task(...)，内部仍调用 POST /api/v1/wayline/workspaces/{workspace_id}/flight-tasks。
      - 保留 create_mission(...) 作为兼容薄包装，但业务代码全部改用 create_dock_flight_task，避免后续把机场任务接口误用于 Pilot2。

  Behavior

  - DOCK_AUTO：
      - preflight-check 校验任务状态、航线同步、无人机绑定、机场绑定、同一 DJI 连接、无人机/机场在线、资源占用、直播能力。
      - start 启动直播后调用 create_dock_flight_task(file_id=route.dji_file_id, dock_sn=mission.dock.device_sn, ...)。
      - 成功后创建 MissionCloudExecution(execution_mode=DOCK_AUTO, dji_job_id=...)，任务进入 RUNNING。
      - cloud-execution/refresh 仅支持该模式：继续按 djiJobId 调 DJI jobs 列表刷新进度。
      - cancel 仅在该模式且有 dji_job_id 时调用 DJI DELETE /jobs。

  - PILOT2_MANUAL：
      - preflight-check 不再出现“wayline flight task 支持未验证”的阻断/警告；改为检查航线已同步、无人机与遥控器在同一 DJI 连接、资源未占用、设备在线。
      - 直播能力在该模式下是辅助能力：查询失败或没有可用视频源只给 warning，不阻断飞手在遥控器执行。
      - start 只把本地任务置为 RUNNING 并创建 FlightSession / MissionCloudExecution(execution_mode=PILOT2_MANUAL, dji_job_id="")；绝不调用 /flight-tasks。
      - start 可 best-effort 启动直播：成功则记录 liveStatus=RUNNING，失败则记录 liveStatus=FAILED/liveErrorMessage，但任务仍进入 RUNNING。
      - cloud-execution/refresh 对该模式返回 409：“Pilot2 手动执行任务没有 DJI job，不能通过 jobs 刷新”。
      - complete/fail/cancel/abort 只更新本地状态并停止直播；不调用 DJI job cancel。
      - Dock 模式继续优先用 dji_job_id 精确绑定媒体。
      - Pilot2 模式无 dji_job_id 时，完成任务时按 workspace_id + drone_sn + session 时间窗口 + captured_at 关联媒体；没有可解析拍摄时间的媒体不自动绑定，避免错绑。
      - 已通过回调落库但未绑定任务的媒体，也按同一规则归档到飞行记录。


  - 更新 mock/gateway 测试：
      - 保留 /flight-tasks mock 作为 Dock 自动模式能力。
      - 新增 gateway 测试覆盖 create_dock_flight_task，并保留 create_mission 兼容包装。

  - 更新 schema/docs 测试：
      - OpenAPI 包含 executionMode 和 routeSnapshot.djiFile。
      - 文档不再把 /flight-tasks 描述为通用任务启动路径；只在 Dock 自动模式章节出现。

  - 建议验证命令：
      - DB_ENGINE=sqlite DJANGO_TEST_FAST_PASSWORD_HASHERS=1 .venv/bin/python manage.py test apps.inspection_v2.tests apps.dji_mock.tests apps.dji_cloud.test_gateway_logging
        apps.api_v2.test_schema_docs_sync

      - DB_ENGINE=sqlite DJANGO_TEST_FAST_PASSWORD_HASHERS=1 .venv/bin/python manage.py check

  Assumptions

  - Pilot2 当前连接的是已有上云服务；本仓库本轮不新增 DJI 官方 /wayline/api/v1/... 或 /storage/api/v1/... 对外接口。
  - “同步航线到 Pilot2”在本仓库中的实现含义是：航线 KMZ 已通过现有 DjiConnectionGateway.upload_route 上传到同一 DJI workspace，并在任务创建时强校验和快照，不重新上传。
  - 第一版不做 MQTT 自动判断起飞/降落；Pilot2 手动执行模式由平台用户调用 start/complete/fail/cancel 推进本地状态。
  - 第一版不引入新任务状态枚举，例如 SYNCED/DISPATCHED；用 executionMode 区分流程，用现有 PENDING/RUNNING/COMPLETED/CANCELED/FAILED 保持状态机简单。
