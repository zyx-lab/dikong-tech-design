# Dock 3 DRC 本项目托管 Proposal

## 1. 目标

让前端只连接本 Django 项目，由本项目完成鉴权、资源校验、DRC MQTT 连接和消息转发。DJI broker 地址、账号、密码、clientId 和 topic 不返回前端。

## 2. 链路

```text
Frontend --REST/WebSocket--> Django --REST--> Java Cloud API
                                  |
                                  +--short-lived MQTT--> DJI broker --> Dock 3
```

职责：

- 前端：展示 DRC 页面，通过 REST 创建/退出会话，通过本项目 WebSocket 发送摇杆帧并接收 DRC 上行消息。
- Django：登录态、角色和资源权限；保管短期 MQTT 凭据；连接固定 DRC pub/sub topic；heartbeat、序列号、停帧中立和断线退出。
- Java：复用现有 `connect/enter/exit`；生成最小 topic ACL；通过现有 services 接口处理相机和云台命令。
- Dock：执行 `stick_control`，返回心跳、OSD、HSI、延迟及控制结果。

## 3. 最小接口

| 接口 | 作用 |
|---|---|
| `GET /api/v2/inspection/drc/capabilities?dockId=` | 返回 Dock、子无人机、摇杆范围和当前已开放的载荷能力。 |
| `POST /api/v2/inspection/drc/connect` | Django 调用 Java `connect -> enter`，内部保存 MQTT 凭据，只返回会话 ID 和本项目 WebSocket 地址。 |
| `WS /ws/v2/drc/sessions/{sessionId}?token=` | Django 代理 DRC MQTT；前端不能指定 broker 或 topic。 |
| `POST /api/v2/inspection/drc/exit` | 按服务端会话调用 Java `exit`，撤销 ACL 并删除本地短期配置。 |
| `POST /api/v2/inspection/camera/actions` | 通过 Django/Java 的标准 services MQTT 执行相机和云台命令。 |

DRC WebSocket 只承载 `stick_control` 和 DRC 上行数据。官方页面中的相机、云台、红外命令使用 `thing/product/{gatewaySn}/services`，不能错误发布到 `/drc/down`。

## 4. 会话与安全

- Django cache 只保存短期会话配置，不新增数据库 session/operation 表。
- 浏览器响应和日志中不得出现 MQTT secrets、真实 topic 或上游 clientId。
- WebSocket 只接受 `control.arm`、`control.disarm`、`control.frame`，四轴值限制为 `364..1684`。
- Django 每 5 秒发送 `heart_beat`，控制帧停发 500 ms 后发送四轴 `1024` 并解除 armed。
- WebSocket 断开时 best-effort 发送中立帧、断开 MQTT、调用 Java `exit` 并删除会话。
- Java ACL 只能发布 `{dockSn}/drc/down`、订阅 `{dockSn}/drc/up`，禁止空 topic `ALL`。

## 5. Java 范围

本次只保留必要改动：

- `connect` 必填 `dockSn`，并校验 Dock 属于路径 workspace。
- `connect/enter` 的 ACL 限制为当前 Dock 的一对 DRC topic。
- `enter` 使用请求中的有效期、OSD 和 HSI 频率。
- `exit` 幂等清理 DRC owner 和 MQTT ACL。

不恢复 Java DRC session manager、Java WebSocket、operation registry、Redis 租约平台或联合 OpenAPI 类型。

## 6. 页面范围与缺口

- 飞控链路：使用本项目 DRC WebSocket。
- 已有相机能力：拍照、开始/停止录像、模式切换、变焦、点选瞄准、云台复位，继续复用 `/camera/actions`。
- 官方页面其余相机、云台和红外方法：后续扩展现有 `CameraActionSerializer` 与 Java `PayloadCommandsEnum`，仍走 services MQTT。
- 扬声器与探照灯：只有协议和真机型号确认后再开放，不混入通用 DRC down。

## 7. 交付顺序

1. 修正 Django 会话响应和 DRC WebSocket 代理，确保前端只连本项目。
2. 保留 Java 最小 ACL、workspace 校验、频率和幂等退出修改。
3. 前端接入 REST/WebSocket 和现有相机动作接口。
4. 按官方 services 方法逐项补齐剩余页面操作并做真机验收。
