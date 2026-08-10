# Dock 3 DRC 前端直连 MQTT Proposal

## 1. 目标

让前端通过本 Django 桥接服务完成权限校验并取得短期 DRC MQTT 凭据，然后按 DJI DRC 协议直接控制 Dock 3。Java 上云 API 只复用现有 `connect/enter/exit` 能力，不新增服务端 DRC 会话平台。

## 2. 链路

```text
Frontend --REST--> Django --REST--> Java Cloud API
Frontend --short-lived MQTT--> DJI broker --DRC MQTT--> Dock 3
```

职责：

- Django：登录态、角色、机场/无人机资源权限、能力判断、审计、上云 API 代理。
- Java：生成短期 MQTT 凭据、最小 topic ACL、进入/退出 DRC 模式。
- 前端：MQTT 生命周期、`seq`、heartbeat、`stick_control`、载荷指令、回执和断线收敛。

## 3. 最小后端接口

| 接口 | 作用 |
|---|---|
| `GET /api/v2/inspection/drc/capabilities?dockId=` | 返回本地 Dock 3、子无人机、控制范围和负载方法。 |
| `POST /api/v2/inspection/drc/connect` | 调用 Java `connect` 后调用 `enter`，返回短期 MQTT 凭据和唯一 pub/sub topic。 |
| `POST /api/v2/inspection/drc/exit` | 调用 Java `exit` 并撤销 MQTT ACL。 |

不新增 Django DRC session/operation 表，不新增 Django/Java DRC WebSocket，不代理每条摇杆或载荷指令。

## 4. Java 范围

- 保留现有 `/drc/connect`、`/drc/enter`、`/drc/exit`。
- `connect` 必须接收 `dockSn`，ACL 只能发布该 Dock 的 `/drc/down`、订阅 `/drc/up`。
- `enter` 使用请求传入的有效期、OSD 和 HSI 频率，不使用硬编码值。
- `exit` 幂等清理 DRC owner 和 MQTT ACL。

不增加 session manager、Redis 分布式租约、Java WebSocket、operation registry 或 DRC OpenAPI 联合类型。

## 5. 风险

- 短期凭据会进入浏览器内存，必须禁止持久化到 localStorage、日志和错误上报。
- 权限撤销不能立即终止已签发 MQTT 凭据，最长残余窗口等于凭据有效期；默认 3600 秒，最低 1800 秒受上游约束。
- 前端崩溃后无法保证主动发送中立帧；真机必须验证 Dock 在 `stick_control` 停止后的 fail-stop 行为。
- 浏览器是唯一 DRC down publisher；不得同时调用 Java `/drc/payload/commands`。

## 6. 交付顺序

1. 后端三接口和 Java topic ACL。
2. 前端 MQTT 连接、heartbeat、摇杆和断线退出。
3. 相机/扬声器方法及回执。
4. Dock 3 真机安全验收。
