 # v2 算法实时分析接入设计

  ## Summary

  - 采用“Django 控制面 + 算法服务拉流”的架构：Django 负责部门体系、DJI 平台、无人机、mission、直播启动、算法会话、事件落库和 SSE 推送；视频流本身不进入 Django 进程。
  - 第一版使用 DJI 直播 RTMP URL 给算法系统拉流。DJI 官方 Cloud API 文档里 RTMP 是直播协议选项之一，Pilot/RC Plus 侧 url_type=1 为 RTMP；Dock 直播文档也说明 DJI 推流支持 Agora/RTMP/GB28181，WebRTC/WHIP
    更偏低延迟播放链路。参考：Pilot to Cloud Live Stream (https://developer.dji.com/doc/cloud-api-tutorial/en/api-reference/pilot-to-cloud/mqtt/rc-plus/live.html)、Dock Livestream
    (https://developer.dji.com/doc/cloud-api-tutorial/en/feature-set/dock-feature-set/dock-livestream.html)。
  - 只对“配置了算法 profile 的任务”启用算法。mission 从 PENDING -> RUNNING 时，必须先启动 DJI 直播并成功创建算法会话；失败则阻断任务启动，mission 保持 PENDING。
  - 算法结果第一版保存为“事件 + 证据”：事件类型、时间、置信度、目标框、原始 payload、截图/证据文件或证据 URL。暂不做算法标注视频流回传。
  - 前端实时查看算法事件使用 SSE，不引入 WebSocket/Channels；SSE 从数据库增量读取事件，支持 Last-Event-ID 断线续传。

  ## Public API / Data Model Changes

  - 新增 apps.algorithm 领域模块，避免把外部算法系统塞进 dji_bff：
      - AlgorithmProvider：部门体系级算法服务配置，字段包括 owner_department、name、base_url、auth_token、status、is_default、timeout_seconds。
      - AlgorithmProfile：算法能力配置，字段包括 owner_department、provider、name、code、params、stream_protocol=RTMP、is_default、enabled。
      - AlgorithmSession：一次 mission 实时分析会话，关联 mission、drone、dji_platform、profile，保存 video_id、stream_url、external_session_id、状态 STARTING/RUNNING/STOPPING/STOPPED/ERROR、错误信息
        和时间戳。
      - AlgorithmEvent：算法回调事件，关联 session、mission、drone、dji_platform，保存 external_event_id、event_type、event_time、confidence、bbox、payload、evidence_file/evidence_url；对同一 session 的
        external_event_id 做唯一约束保证幂等。
  - 新增 v2 配置接口：
      - GET/POST /api/v2/algorithm/providers
      - GET/PATCH /api/v2/algorithm/providers/{id}
      - GET/POST /api/v2/algorithm/profiles
      - GET/PATCH /api/v2/algorithm/profiles/{id}
  - 扩展 v2 mission 接口：
      - POST/PATCH /api/v2/missions 增加可选 algorithm_profile_id、live_video_id、live_video_quality。
      - 如果传了 algorithm_profile_id，任务启动时强制启动算法；如果没传，则不启用算法，不影响现有任务流程。
      - 如果没传 live_video_id，启动时从 DJI live/capacity 里按固定规则选择第一个 camera/video，生成 {device_sn}/{camera_index}/{video_index} 并持久化到 session。
  - 新增算法运行接口：
      - GET /api/v2/algorithm-sessions?mission_id=...
      - GET /api/v2/algorithm-sessions/{id}
      - GET /api/v2/algorithm-events?mission_id=...&session_id=...&event_type=...
      - GET /api/v2/algorithm-events/stream?mission_id=...，返回 text/event-stream，事件 id 使用 AlgorithmEvent.id。
      - POST /api/v2/algorithm-callback/events 给算法服务回调，使用 HMAC/Token 签名，不走普通用户 Bearer auth。
  - 修正 v2 live 语义：
      - v2 直播不能继承旧 v1 默认 workspace 逻辑。
      - 实现时抽出连接感知的直播服务，v2 所有直播和算法启动都必须先解析到 `DjiConnection`，再使用 `DjiConnectionGateway(connection)`。

  ## Implementation Flow

  - mission PENDING -> RUNNING：
      - 校验 mission、drone、route、DJI platform、算法 profile。
      - 如果 mission 没配置算法 profile，走现有 v2 advance 流程。
      - 如果配置了 profile，先创建 AlgorithmSession(STARTING)，再解析任务绑定的 `DjiConnection`，调用 `DjiConnectionGateway(connection).get_live_capacity()` 解析/校验 video_id。
      - 调用 `DjiConnectionGateway(connection).start_live(device_sn, video_id, url_type=1, video_quality=...)`，选择返回里的 rtmp_url 或 url 作为算法拉流地址。
      - 调用算法服务 POST {provider.base_url}/sessions，传 session_id、mission_id、department_id、drone/device_sn、dji_platform_id、stream_url、video_id、profile.code/params、callback_url。
      - 算法服务返回成功后，mission 才更新为 RUNNING，session 更新为 RUNNING。
      - 任一步失败：mission 保持 PENDING，session 标记 ERROR；如果 DJI 直播已启动，调用 stop_live 做补偿；接口返回 400/502。
  - mission RUNNING -> COMPLETED：
      - 保留当前逻辑：完成 mission、创建 FlightRecord、刷新该平台媒体、回算媒体数量。
      - 查找 RUNNING 算法 session，调用算法服务 stop，再调用 DJI stop_live。
      - 停止失败不阻断 mission 完成，只把 session 标记为 ERROR 或 STOPPED_WITH_ERROR 并记录错误，避免任务无法收尾。
  - 算法事件回调：
      - 校验 provider 签名、时间窗口和 session 状态。
      - 按 external_event_id 幂等写入 AlgorithmEvent。
      - 证据第一版支持图片证据：算法可传 evidence_url，Django 同步下载到默认对象存储并保存 evidence_file；下载失败时事件仍落库并记录 evidence_error。
  - SSE：
      - 使用 Django StreamingHttpResponse，不新增 Channels。
      - 连接鉴权复用 v2 部门体系和 scope 权限；只能订阅当前部门体系有权限的 mission/session。
      - 先发送 id > Last-Event-ID 的历史事件，再每 1 秒查询新增事件；每 15 秒发 heartbeat；单连接设置最大存活时间，前端自动重连。

  ## Test Plan

  - v2 mission 未配置算法 profile 时，advance 行为与当前一致，不启动直播、不调用算法服务。
  - 配置算法 profile 的 mission 启动成功：
      - 使用任务绑定的 `DjiConnectionGateway(connection)`；
      - start_live 使用 url_type=1；
      - 算法服务收到 RTMP URL、mission/drone/platform/profile 信息；
      - mission 变 RUNNING，session 变 RUNNING。
  - DJI live capacity 缺失、start_live 失败、算法服务失败：
      - mission 保持 PENDING；
      - session 变 ERROR；
      - 已启动直播时会调用 stop_live 补偿。
  - 同一部门体系多 DJI 平台、相同 device_sn 场景：
      - 算法启动只使用 mission drone 所属 dji_platform；
      - 不会误用默认平台或其他平台直播。
  - mission 完成：
      - 继续创建 FlightRecord、刷新平台媒体；
      - 调用算法 stop 和 DJI stop；
      - stop 失败不阻断 mission COMPLETED。
  - callback：
      - 签名错误返回 401/403；
      - 重复 external_event_id 不重复创建事件；
      - 事件正确关联 session、mission、drone、dji_platform；
      - 证据图片保存到对象存储或记录下载失败。
  - SSE：
      - 有权限用户能收到历史事件和新增事件；
      - Last-Event-ID 能断线续传；
      - 无权限或跨部门体系订阅返回 403/404；
      - heartbeat 不产生业务事件。

  ## Assumptions

  - 第一版只做 RTMP 拉流给算法，不做平台内转流、不做标注视频分发。
  - 算法系统愿意实现我们定义的 REST start/stop/callback 合同。
  - 算法只对显式配置了 algorithm_profile_id 的 mission 生效；未配置算法的 mission 不受影响。
  - 启动失败阻断任务启动；停止失败不阻断任务完成。
  - SSE 用数据库轮询实现，先满足实时事件查看，不引入 Redis、Celery、WebSocket 或新消息队列。
