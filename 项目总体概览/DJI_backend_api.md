# DJI Cloud API Demo 实时接口文档

- 生成时间：`2026-03-26 10:21:00`
- 服务地址：`http://127.0.0.1:6789`
- 数据来源：`API接口文档-规范化.md` + 运行中服务 OpenAPI + 当前环境真实接口返回体
- 生成脚本：`python3 scripts/generate_api_markdown.py`

## 文档入口

- Swagger UI：`http://127.0.0.1:6789/docs`
- ReDoc：`http://127.0.0.1:6789/redoc`
- OpenAPI JSON：`http://127.0.0.1:6789/openapi.json`
- OpenAPI 原始地址：`http://127.0.0.1:6789/v3/api-docs`

## 统一响应结构

```json
{
  "code": "00000",
  "msg": "success",
  "data": {}
}
```

## 业务码

| 业务码 | 含义             |
| ------ | ---------------- |
| 00000  | 成功             |
| A0401  | 未登录或登录失效 |
| A0403  | 无权限           |
| B0001  | 参数校验失败     |
| B0002  | 缺少必要参数     |
| B0003  | 参数格式错误     |
| C0404  | 资源不存在       |
| D0001  | 依赖服务错误     |
| E0001  | 系统内部错误     |

## 模块索引

- 管理模块：认证与用户, 设备, HMS, 固件, 日志, 直播, 拓扑
- 地图模块：飞行区域, 设备状态, 地图元素
- 媒体模块：媒体文件, Pilot 媒体回调
- 存储模块：存储模块
- 航线模块：航线文件, 航线任务
- 控制模块：DRC, 设备控制

## 管理模块

### 认证与用户

#### 登录

- 方法：`POST`
- 路径：`/api/v1/manage/login`
- 认证：`无需 x-auth-token`
- 说明：用户登录
- operationId：`login`

请求体：

- Content-Type：`application/json`
- Schema：`UserLoginDTO`

请求字段：

| 字段     | 类型    | 必填 | 说明 |
| -------- | ------- | ---- | ---- |
| username | string  | 否   |      |
| password | string  | 否   |      |
| flag     | integer | 否   |      |

请求示例：

```json
{
  "username": "adminPC",
  "password": "adminPC",
  "flag": 1
}
```

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

`data` 字段结构（基于当前环境返回示例）：

| 字段          | 类型    | 说明 |
| ------------- | ------- | ---- |
| username      | string  |      |
| user_id       | string  |      |
| workspace_id  | string  |      |
| user_type     | integer |      |
| mqtt_username | string  |      |
| mqtt_password | string  |      |
| access_token  | string  |      |
| mqtt_addr     | string  |      |

当前环境返回示例：

```json
{
  "code": "00000",
  "msg": "success",
  "data": {
    "username": "adminPC",
    "user_id": "a1559e7c-8dd8-4780-b952-100cc4797da2",
    "workspace_id": "e3dea0f5-37f2-4d79-ae58-490af3228069",
    "user_type": 1,
    "mqtt_username": "admin",
    "mqtt_password": "admin",
    "access_token": "***live-token-redacted***",
    "mqtt_addr": "tcp://192.168.3.33:1883"
  }
}
```

#### 刷新 Token

- 方法：`POST`
- 路径：`/api/v1/manage/token/refresh`
- 认证：`无需 x-auth-token`
- 说明：刷新登录态
- operationId：`refreshToken`

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

#### 当前用户

- 方法：`GET`
- 路径：`/api/v1/manage/users/current`
- 认证：`需要 x-auth-token`
- 说明：获取当前登录用户信息
- operationId：`getCurrentUserInfo`

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

`data` 字段结构（基于当前环境返回示例）：

| 字段          | 类型    | 说明 |
| ------------- | ------- | ---- |
| username      | string  |      |
| user_id       | string  |      |
| workspace_id  | string  |      |
| user_type     | integer |      |
| mqtt_username | string  |      |
| mqtt_password | string  |      |
| mqtt_addr     | string  |      |

当前环境返回示例：

```json
{
  "code": "00000",
  "msg": "success",
  "data": {
    "username": "adminPC",
    "user_id": "a1559e7c-8dd8-4780-b952-100cc4797da2",
    "workspace_id": "e3dea0f5-37f2-4d79-ae58-490af3228069",
    "user_type": 1,
    "mqtt_username": "admin",
    "mqtt_password": "admin",
    "mqtt_addr": "tcp://192.168.3.33:1883"
  }
}
```

#### 工作空间用户列表

- 方法：`GET`
- 路径：`/api/v1/manage/workspaces/{workspace_id}/users`
- 认证：`需要 x-auth-token`
- 说明：分页查询用户
- operationId：`getUsers`

参数：

| 名称         | 位置  | 必填 | 类型    | 说明 |
| ------------ | ----- | ---- | ------- | ---- |
| page         | query | 否   | integer |      |
| page_size    | query | 否   | integer |      |
| workspace_id | path  | 是   | string  |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponsePaginationDataUserListDTO`

响应字段：

| 字段 | 类型                      | 必填 | 说明                                  |
| ---- | ------------------------- | ---- | ------------------------------------- |
| code | string                    | 否   | Business code, use 00000 for success. |
| msg  | string                    | 否   | The response message.                 |
| data | PaginationDataUserListDTO | 否   | Format of paged data                  |

`data` 字段结构（`PaginationDataUserListDTO`）：

| 字段       | 类型               | 必填 | 说明                                             |
| ---------- | ------------------ | ---- | ------------------------------------------------ |
| list       | array<UserListDTO> | 否   | The collection in which the data list is stored. |
| pagination | Pagination         | 否   | Used for paging display                          |

当前环境返回示例：

```json
{
  "code": "00000",
  "msg": "success",
  "data": {
    "list": [
      {
        "user_id": "a1559e7c-8dd8-4780-b952-100cc4797da2",
        "username": "adminPC",
        "workspace_name": "Test Group One",
        "user_type": "Web",
        "mqtt_username": "admin",
        "mqtt_password": "admin",
        "create_time": "2021-10-22 18:26:50"
      },
      {
        "user_id": "be7c6c3d-afe9-4be4-b9eb-c55066c0914e",
        "username": "pilot",
        "workspace_name": "Test Group One",
        "user_type": "Pilot",
        "mqtt_username": "pilot",
        "mqtt_password": "pilot123",
        "create_time": "2021-10-22 18:26:50"
      }
    ],
    "pagination": {
      "page": 1,
      "total": 2,
      "page_size": 50
    }
  }
}
```

#### 更新用户

- 方法：`PUT`
- 路径：`/api/v1/manage/workspaces/{workspace_id}/users/{user_id}`
- 认证：`需要 x-auth-token`
- 说明：更新用户 mqtt 相关信息
- operationId：`updateUser`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明 |
| ------------ | ---- | ---- | ------ | ---- |
| workspace_id | path | 是   | string |      |
| user_id      | path | 是   | string |      |

请求体：

- Content-Type：`application/json`
- Schema：`UserListDTO`

请求字段：

| 字段          | 类型   | 必填 | 说明 |
| ------------- | ------ | ---- | ---- |
| userId        | string | 否   |      |
| username      | string | 否   |      |
| workspaceName | string | 否   |      |
| userType      | string | 否   |      |
| mqttUsername  | string | 否   |      |
| mqttPassword  | string | 否   |      |
| createTime    | string | 否   |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

#### 当前工作空间

- 方法：`GET`
- 路径：`/api/v1/manage/workspaces/current`
- 认证：`需要 x-auth-token`
- 说明：获取当前用户所在工作空间
- operationId：`getCurrentWorkspace`

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

`data` 字段结构（基于当前环境返回示例）：

| 字段           | 类型    | 说明 |
| -------------- | ------- | ---- |
| id             | integer |      |
| workspace_id   | string  |      |
| workspace_name | string  |      |
| workspace_desc | string  |      |
| platform_name  | string  |      |
| bind_code      | string  |      |

当前环境返回示例：

```json
{
  "code": "00000",
  "msg": "success",
  "data": {
    "id": 1,
    "workspace_id": "e3dea0f5-37f2-4d79-ae58-490af3228069",
    "workspace_name": "Test Group One",
    "workspace_desc": "Cloud Sample Test Platform",
    "platform_name": "Cloud Api Platform",
    "bind_code": "qwe"
  }
}
```

### 设备

#### 工作空间设备列表

- 方法：`GET`
- 路径：`/api/v1/manage/workspaces/{workspace_id}/devices`
- 认证：`需要 x-auth-token`
- 说明：获取拓扑设备列表
- operationId：`getDevices`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明 |
| ------------ | ---- | ---- | ------ | ---- |
| workspace_id | path | 是   | string |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponseListDeviceDTO`

响应字段：

| 字段 | 类型             | 必填 | 说明                                  |
| ---- | ---------------- | ---- | ------------------------------------- |
| code | string           | 否   | Business code, use 00000 for success. |
| msg  | string           | 否   | The response message.                 |
| data | array<DeviceDTO> | 否   | The response data.                    |

`data` 字段结构（基于当前环境返回示例）：

| 字段             | 类型               | 说明 |
| ---------------- | ------------------ | ---- |
| device_sn        | array item string  |      |
| device_name      | array item string  |      |
| workspace_id     | array item string  |      |
| control_source   | array item string  |      |
| device_desc      | array item string  |      |
| child_device_sn  | array item string  |      |
| domain           | array item integer |      |
| type             | array item integer |      |
| sub_type         | array item integer |      |
| icon_url         | array item object  |      |
| status           | array item boolean |      |
| bound_status     | array item boolean |      |
| login_time       | array item string  |      |
| bound_time       | array item string  |      |
| nickname         | array item string  |      |
| firmware_version | array item string  |      |
| workspace_name   | array item string  |      |
| children         | array item object  |      |
| firmware_status  | array item integer |      |
| thing_version    | array item string  |      |

当前环境返回示例：

```json
{
  "code": "00000",
  "msg": "success",
  "data": [
    {
      "device_sn": "9N9CMCJ001131H",
      "device_name": "9N9CMCJ001131H",
      "workspace_id": "e3dea0f5-37f2-4d79-ae58-490af3228069",
      "control_source": "A",
      "device_desc": "",
      "child_device_sn": "1581F7FVC252A00CJ5TT",
      "domain": 2,
      "type": 174,
      "sub_type": 0,
      "icon_url": {
        "normal_icon_url": "",
        "selected_icon_url": ""
      },
      "status": true,
      "bound_status": true,
      "login_time": "2026-03-26 09:28:52",
      "bound_time": "2026-03-26 09:28:55",
      "nickname": "9N9CMCJ001131H",
      "firmware_version": "01.64.0711",
      "workspace_name": "Test Group One",
      "children": {
        "device_sn": "1581F7FVC252A00CJ5TT",
        "device_name": "1581F7FVC252A00CJ5TT",
        "workspace_id": "e3dea0f5-37f2-4d79-ae58-490af3228069",
        "control_source": "",
        "device_desc": "",
        "child_device_sn": "",
        "domain": 0,
        "type": 99,
        "sub_type": 0,
        "payloads_list": [
          {
            "payload_sn": "1581F7FVC252A00CJ5TT-0",
            "payload_name": "undefined",
            "index": 0,
            "control_source": "A",
            "payload_index": "88-0-0"
          }
        ],
        "icon_url": {
          "normal_icon_url": "",
          "selected_icon_url": ""
        },
        "status": true,
        "bound_status": true,
        "login_time": "2026-03-26 09:28:52",
        "bound_time": "2026-03-23 17:33:07",
        "nickname": "1581F7FVC252A00CJ5TT",
        "firmware_version": "16.01.0006",
        "workspace_name": "Test Group One",
        "firmware_status": 1,
        "thing_version": "1.2.0"
      },
      "firmware_status": 1,
      "thing_version": "1.2.0"
    }
  ]
}
```

#### 设备绑定

- 方法：`POST`
- 路径：`/api/v1/manage/devices/{device_sn}/binding`
- 认证：`需要 x-auth-token`
- 说明：绑定设备
- operationId：`bindDevice`

参数：

| 名称      | 位置 | 必填 | 类型   | 说明 |
| --------- | ---- | ---- | ------ | ---- |
| device_sn | path | 是   | string |      |

请求体：

- Content-Type：`application/json`
- Schema：`DeviceDTO`

请求字段：

| 字段             | 类型                    | 必填 | 说明                                                                                                                                                                                                                                             |
| ---------------- | ----------------------- | ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| deviceSn         | string                  | 否   |                                                                                                                                                                                                                                                  |
| deviceName       | string                  | 否   |                                                                                                                                                                                                                                                  |
| workspaceId      | string                  | 否   |                                                                                                                                                                                                                                                  |
| controlSource    | string                  | 否   |                                                                                                                                                                                                                                                  |
| deviceDesc       | string                  | 否   |                                                                                                                                                                                                                                                  |
| childDeviceSn    | string                  | 否   |                                                                                                                                                                                                                                                  |
| domain           | DeviceDomainEnum        | 否   | device domain                                                                                                                                                                                                                                    |
| type             | DeviceTypeEnum          | 否   | device type                                                                                                                                                                                                                                      |
| subType          | DeviceSubTypeEnum       | 否   | device subType                                                                                                                                                                                                                                   |
| payloadsList     | array<DevicePayloadDTO> | 否   |                                                                                                                                                                                                                                                  |
| iconUrl          | DeviceIconUrl           | 否   | device icon url. <br/>You can use icons from the web, and the App internally downloads and caches these icons and loads them at a fixed size (28dp) to display on the map. Example: http://r56978dr7.hn-bkt.clouddn.com/tsa_equipment_normal.png |
| status           | boolean                 | 否   |                                                                                                                                                                                                                                                  |
| boundStatus      | boolean                 | 否   |                                                                                                                                                                                                                                                  |
| loginTime        | string                  | 否   |                                                                                                                                                                                                                                                  |
| boundTime        | string                  | 否   |                                                                                                                                                                                                                                                  |
| nickname         | string                  | 否   |                                                                                                                                                                                                                                                  |
| userId           | string                  | 否   |                                                                                                                                                                                                                                                  |
| firmwareVersion  | string                  | 否   |                                                                                                                                                                                                                                                  |
| workspaceName    | string                  | 否   |                                                                                                                                                                                                                                                  |
| children         | DeviceDTO               | 否   |                                                                                                                                                                                                                                                  |
| firmwareStatus   | string                  | 否   |                                                                                                                                                                                                                                                  |
| firmwareProgress | integer                 | 否   |                                                                                                                                                                                                                                                  |
| parentSn         | string                  | 否   |                                                                                                                                                                                                                                                  |
| thingVersion     | string                  | 否   |                                                                                                                                                                                                                                                  |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

#### 设备详情

- 方法：`GET`
- 路径：`/api/v1/manage/workspaces/{workspace_id}/devices/{device_sn}`
- 认证：`需要 x-auth-token`
- 说明：按 SN 查询设备
- operationId：`getDevice`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明 |
| ------------ | ---- | ---- | ------ | ---- |
| workspace_id | path | 是   | string |      |
| device_sn    | path | 是   | string |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

`data` 字段结构（基于当前环境返回示例）：

| 字段             | 类型    | 说明 |
| ---------------- | ------- | ---- |
| device_sn        | string  |      |
| device_name      | string  |      |
| workspace_id     | string  |      |
| control_source   | string  |      |
| device_desc      | string  |      |
| child_device_sn  | string  |      |
| domain           | integer |      |
| type             | integer |      |
| sub_type         | integer |      |
| icon_url         | object  |      |
| status           | boolean |      |
| bound_status     | boolean |      |
| login_time       | string  |      |
| bound_time       | string  |      |
| nickname         | string  |      |
| firmware_version | string  |      |
| workspace_name   | string  |      |
| firmware_status  | integer |      |
| thing_version    | string  |      |

当前环境返回示例：

```json
{
  "code": "00000",
  "msg": "success",
  "data": {
    "device_sn": "9N9CMCJ001131H",
    "device_name": "9N9CMCJ001131H",
    "workspace_id": "e3dea0f5-37f2-4d79-ae58-490af3228069",
    "control_source": "A",
    "device_desc": "",
    "child_device_sn": "1581F7FVC252A00CJ5TT",
    "domain": 2,
    "type": 174,
    "sub_type": 0,
    "icon_url": {
      "normal_icon_url": "",
      "selected_icon_url": ""
    },
    "status": true,
    "bound_status": true,
    "login_time": "2026-03-26 09:28:52",
    "bound_time": "2026-03-26 09:28:55",
    "nickname": "9N9CMCJ001131H",
    "firmware_version": "01.64.0711",
    "workspace_name": "Test Group One",
    "firmware_status": 1,
    "thing_version": "1.2.0"
  }
}
```

#### 已绑定设备分页

- 方法：`GET`
- 路径：`/api/v1/manage/workspaces/{workspace_id}/devices/bound`
- 认证：`需要 x-auth-token`
- 说明：支持 page/page_size/domain
- operationId：`getBoundDevicesWithDomain`

参数：

| 名称         | 位置  | 必填 | 类型    | 说明 |
| ------------ | ----- | ---- | ------- | ---- |
| workspace_id | path  | 是   | string  |      |
| domain       | query | 是   | integer |      |
| page         | query | 否   | integer |      |
| page_size    | query | 否   | integer |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponsePaginationDataDeviceDTO`

响应字段：

| 字段 | 类型                    | 必填 | 说明                                  |
| ---- | ----------------------- | ---- | ------------------------------------- |
| code | string                  | 否   | Business code, use 00000 for success. |
| msg  | string                  | 否   | The response message.                 |
| data | PaginationDataDeviceDTO | 否   | Format of paged data                  |

`data` 字段结构（`PaginationDataDeviceDTO`）：

| 字段       | 类型             | 必填 | 说明                                             |
| ---------- | ---------------- | ---- | ------------------------------------------------ |
| list       | array<DeviceDTO> | 否   | The collection in which the data list is stored. |
| pagination | Pagination       | 否   | Used for paging display                          |

当前环境返回示例：

```json
{
  "code": "00000",
  "msg": "success",
  "data": {
    "list": [],
    "pagination": {
      "page": 1,
      "total": 0,
      "page_size": 50
    }
  }
}
```

#### 设备解绑

- 方法：`DELETE`
- 路径：`/api/v1/manage/devices/{device_sn}/unbinding`
- 认证：`需要 x-auth-token`
- 说明：解绑设备
- operationId：`unbindingDevice`

参数：

| 名称      | 位置 | 必填 | 类型   | 说明 |
| --------- | ---- | ---- | ------ | ---- |
| device_sn | path | 是   | string |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

#### 更新设备

- 方法：`PUT`
- 路径：`/api/v1/manage/workspaces/{workspace_id}/devices/{device_sn}`
- 认证：`需要 x-auth-token`
- 说明：更新设备信息
- operationId：`updateDevice`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明 |
| ------------ | ---- | ---- | ------ | ---- |
| workspace_id | path | 是   | string |      |
| device_sn    | path | 是   | string |      |

请求体：

- Content-Type：`application/json`
- Schema：`DeviceDTO`

请求字段：

| 字段             | 类型                    | 必填 | 说明                                                                                                                                                                                                                                             |
| ---------------- | ----------------------- | ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| deviceSn         | string                  | 否   |                                                                                                                                                                                                                                                  |
| deviceName       | string                  | 否   |                                                                                                                                                                                                                                                  |
| workspaceId      | string                  | 否   |                                                                                                                                                                                                                                                  |
| controlSource    | string                  | 否   |                                                                                                                                                                                                                                                  |
| deviceDesc       | string                  | 否   |                                                                                                                                                                                                                                                  |
| childDeviceSn    | string                  | 否   |                                                                                                                                                                                                                                                  |
| domain           | DeviceDomainEnum        | 否   | device domain                                                                                                                                                                                                                                    |
| type             | DeviceTypeEnum          | 否   | device type                                                                                                                                                                                                                                      |
| subType          | DeviceSubTypeEnum       | 否   | device subType                                                                                                                                                                                                                                   |
| payloadsList     | array<DevicePayloadDTO> | 否   |                                                                                                                                                                                                                                                  |
| iconUrl          | DeviceIconUrl           | 否   | device icon url. <br/>You can use icons from the web, and the App internally downloads and caches these icons and loads them at a fixed size (28dp) to display on the map. Example: http://r56978dr7.hn-bkt.clouddn.com/tsa_equipment_normal.png |
| status           | boolean                 | 否   |                                                                                                                                                                                                                                                  |
| boundStatus      | boolean                 | 否   |                                                                                                                                                                                                                                                  |
| loginTime        | string                  | 否   |                                                                                                                                                                                                                                                  |
| boundTime        | string                  | 否   |                                                                                                                                                                                                                                                  |
| nickname         | string                  | 否   |                                                                                                                                                                                                                                                  |
| userId           | string                  | 否   |                                                                                                                                                                                                                                                  |
| firmwareVersion  | string                  | 否   |                                                                                                                                                                                                                                                  |
| workspaceName    | string                  | 否   |                                                                                                                                                                                                                                                  |
| children         | DeviceDTO               | 否   |                                                                                                                                                                                                                                                  |
| firmwareStatus   | string                  | 否   |                                                                                                                                                                                                                                                  |
| firmwareProgress | integer                 | 否   |                                                                                                                                                                                                                                                  |
| parentSn         | string                  | 否   |                                                                                                                                                                                                                                                  |
| thingVersion     | string                  | 否   |                                                                                                                                                                                                                                                  |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

#### 创建设备 OTA 任务

- 方法：`POST`
- 路径：`/api/v1/manage/workspaces/{workspace_id}/devices/ota`
- 认证：`需要 x-auth-token`
- 说明：批量下发升级任务
- operationId：`createOtaJob`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明 |
| ------------ | ---- | ---- | ------ | ---- |
| workspace_id | path | 是   | string |      |

请求体：

- Content-Type：`application/json`
- Schema：`array<DeviceFirmwareUpgradeDTO>`

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

#### 设置设备属性

- 方法：`PUT`
- 路径：`/api/v1/manage/workspaces/{workspace_id}/devices/{device_sn}/property`
- 认证：`需要 x-auth-token`
- 说明：设置属性参数
- operationId：`devicePropertySet`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明 |
| ------------ | ---- | ---- | ------ | ---- |
| workspace_id | path | 是   | string |      |
| device_sn    | path | 是   | string |      |

请求体：

- Content-Type：`application/json`
- Schema：`JsonNode`

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

### HMS

#### HMS 列表

- 方法：`GET`
- 路径：`/api/v1/manage/workspaces/{workspace_id}/devices/hms`
- 认证：`需要 x-auth-token`
- 说明：分页查询 HMS
- operationId：`getHmsInformation`

参数：

| 名称         | 位置  | 必填 | 类型                | 说明 |
| ------------ | ----- | ---- | ------------------- | ---- |
| param        | query | 是   | DeviceHmsQueryParam |      |
| workspace_id | path  | 是   | string              |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponsePaginationDataDeviceHmsDTO`

响应字段：

| 字段 | 类型                       | 必填 | 说明                                  |
| ---- | -------------------------- | ---- | ------------------------------------- |
| code | string                     | 否   | Business code, use 00000 for success. |
| msg  | string                     | 否   | The response message.                 |
| data | PaginationDataDeviceHmsDTO | 否   | Format of paged data                  |

`data` 字段结构（`PaginationDataDeviceHmsDTO`）：

| 字段       | 类型                | 必填 | 说明                                             |
| ---------- | ------------------- | ---- | ------------------------------------------------ |
| list       | array<DeviceHmsDTO> | 否   | The collection in which the data list is stored. |
| pagination | Pagination          | 否   | Used for paging display                          |

#### HMS 已读更新

- 方法：`PUT`
- 路径：`/api/v1/manage/workspaces/{workspace_id}/devices/hms/{device_sn}`
- 认证：`需要 x-auth-token`
- 说明：将未读改为已读
- operationId：`updateReadHmsByDeviceSn`

参数：

| 名称      | 位置 | 必填 | 类型   | 说明 |
| --------- | ---- | ---- | ------ | ---- |
| device_sn | path | 是   | string |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

#### 单设备未读 HMS

- 方法：`GET`
- 路径：`/api/v1/manage/workspaces/{workspace_id}/devices/hms/{device_sn}`
- 认证：`需要 x-auth-token`
- 说明：查询指定设备 HMS
- operationId：`getUnreadHmsByDeviceSn`

参数：

| 名称      | 位置 | 必填 | 类型   | 说明 |
| --------- | ---- | ---- | ------ | ---- |
| device_sn | path | 是   | string |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponseListDeviceHmsDTO`

响应字段：

| 字段 | 类型                | 必填 | 说明                                  |
| ---- | ------------------- | ---- | ------------------------------------- |
| code | string              | 否   | Business code, use 00000 for success. |
| msg  | string              | 否   | The response message.                 |
| data | array<DeviceHmsDTO> | 否   | The response data.                    |

### 固件

#### 最新固件说明

- 方法：`GET`
- 路径：`/api/v1/manage/workspaces/firmware-release-notes/latest`
- 认证：`需要 x-auth-token`
- 说明：参数：device_name（可重复）
- operationId：`getLatestFirmwareNote`

参数：

| 名称        | 位置  | 必填 | 类型          | 说明 |
| ----------- | ----- | ---- | ------------- | ---- |
| device_name | query | 是   | array<string> |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponseListDeviceFirmwareNoteDTO`

响应字段：

| 字段 | 类型                         | 必填 | 说明                                  |
| ---- | ---------------------------- | ---- | ------------------------------------- |
| code | string                       | 否   | Business code, use 00000 for success. |
| msg  | string                       | 否   | The response message.                 |
| data | array<DeviceFirmwareNoteDTO> | 否   | The response data.                    |

#### 固件分页

- 方法：`GET`
- 路径：`/api/v1/manage/workspaces/{workspace_id}/firmwares`
- 认证：`需要 x-auth-token`
- 说明：查询固件列表
- operationId：`getAllFirmwarePagination`

参数：

| 名称         | 位置  | 必填 | 类型                     | 说明 |
| ------------ | ----- | ---- | ------------------------ | ---- |
| workspace_id | path  | 是   | string                   |      |
| param        | query | 是   | DeviceFirmwareQueryParam |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponsePaginationDataDeviceFirmwareDTO`

响应字段：

| 字段 | 类型                            | 必填 | 说明                                  |
| ---- | ------------------------------- | ---- | ------------------------------------- |
| code | string                          | 否   | Business code, use 00000 for success. |
| msg  | string                          | 否   | The response message.                 |
| data | PaginationDataDeviceFirmwareDTO | 否   | Format of paged data                  |

`data` 字段结构（`PaginationDataDeviceFirmwareDTO`）：

| 字段       | 类型                     | 必填 | 说明                                             |
| ---------- | ------------------------ | ---- | ------------------------------------------------ |
| list       | array<DeviceFirmwareDTO> | 否   | The collection in which the data list is stored. |
| pagination | Pagination               | 否   | Used for paging display                          |

#### 上传固件文件

- 方法：`POST`
- 路径：`/api/v1/manage/workspaces/{workspace_id}/firmwares/files/upload`
- 认证：`需要 x-auth-token`
- 说明：表单上传
- operationId：`importFirmwareFile`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明 |
| ------------ | ---- | ---- | ------ | ---- |
| workspace_id | path | 是   | string |      |

请求体：

- Content-Type：`application/json`
- Schema：`object`

请求字段：

| 字段  | 类型                      | 必填 | 说明 |
| ----- | ------------------------- | ---- | ---- |
| file  | string                    | 否   |      |
| param | DeviceFirmwareUploadParam | 否   |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

#### 修改固件状态

- 方法：`PUT`
- 路径：`/api/v1/manage/workspaces/{workspace_id}/firmwares/{firmware_id}`
- 认证：`需要 x-auth-token`
- 说明：更新固件状态
- operationId：`changeFirmwareStatus`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明 |
| ------------ | ---- | ---- | ------ | ---- |
| workspace_id | path | 是   | string |      |
| firmware_id  | path | 是   | string |      |

请求体：

- Content-Type：`application/json`
- Schema：`DeviceFirmwareUpdateParam`

请求字段：

| 字段   | 类型    | 必填 | 说明 |
| ------ | ------- | ---- | ---- |
| status | boolean | 是   |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

### 日志

#### 已上传日志分页

- 方法：`GET`
- 路径：`/api/v1/manage/workspaces/{workspace_id}/devices/{device_sn}/logs-uploaded`
- 认证：`需要 x-auth-token`
- 说明：分页查询
- operationId：`getUploadedLogs`

参数：

| 名称         | 位置  | 必填 | 类型                 | 说明 |
| ------------ | ----- | ---- | -------------------- | ---- |
| param        | query | 是   | DeviceLogsQueryParam |      |
| workspace_id | path  | 是   | string               |      |
| device_sn    | path  | 是   | string               |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

#### 设备可上传日志

- 方法：`GET`
- 路径：`/api/v1/manage/workspaces/{workspace_id}/devices/{device_sn}/logs`
- 认证：`需要 x-auth-token`
- 说明：获取可上传日志列表
- operationId：`getLogsBySn`

参数：

| 名称         | 位置  | 必填 | 类型               | 说明 |
| ------------ | ----- | ---- | ------------------ | ---- |
| workspace_id | path  | 是   | string             |      |
| device_sn    | path  | 是   | string             |      |
| param        | query | 是   | DeviceLogsGetParam |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

#### 发起日志上传

- 方法：`POST`
- 路径：`/api/v1/manage/workspaces/{workspace_id}/devices/{device_sn}/logs`
- 认证：`需要 x-auth-token`
- 说明：上传任务创建
- operationId：`uploadLogs`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明 |
| ------------ | ---- | ---- | ------ | ---- |
| workspace_id | path | 是   | string |      |
| device_sn    | path | 是   | string |      |

请求体：

- Content-Type：`application/json`
- Schema：`DeviceLogsCreateParam`

请求字段：

| 字段            | 类型                       | 必填 | 说明 |
| --------------- | -------------------------- | ---- | ---- |
| logsInformation | string                     | 否   |      |
| happenTime      | integer                    | 否   |      |
| files           | array<FileUploadStartFile> | 否   |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

#### 取消日志上传

- 方法：`DELETE`
- 路径：`/api/v1/manage/workspaces/{workspace_id}/devices/{device_sn}/logs`
- 认证：`需要 x-auth-token`
- 说明：取消上传
- operationId：`cancelUploadedLogs`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明 |
| ------------ | ---- | ---- | ------ | ---- |
| workspace_id | path | 是   | string |      |
| device_sn    | path | 是   | string |      |

请求体：

- Content-Type：`application/json`
- Schema：`FileUploadUpdateRequest`

请求字段：

| 字段       | 类型          | 必填 | 说明 |
| ---------- | ------------- | ---- | ---- |
| moduleList | array<string> | 是   |      |
| status     | string        | 是   |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

#### 删除上传记录

- 方法：`DELETE`
- 路径：`/api/v1/manage/workspaces/{workspace_id}/devices/{device_sn}/logs/{logs_id}`
- 认证：`需要 x-auth-token`
- 说明：删除历史
- operationId：`deleteUploadedLogs`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明 |
| ------------ | ---- | ---- | ------ | ---- |
| workspace_id | path | 是   | string |      |
| device_sn    | path | 是   | string |      |
| logs_id      | path | 是   | string |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

#### 日志文件下载地址

- 方法：`GET`
- 路径：`/api/v1/manage/workspaces/{workspace_id}/logs/{logs_id}/url/{file_id}`
- 认证：`需要 x-auth-token`
- 说明：返回下载 URL
- operationId：`getFileUrl_1`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明 |
| ------------ | ---- | ---- | ------ | ---- |
| workspace_id | path | 是   | string |      |
| file_id      | path | 是   | string |      |
| logs_id      | path | 是   | string |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

### 直播

#### 实测环境

- 实测时间：`2026-03-26 17:14:08` 到 `2026-03-26 17:14:41`（Asia/Shanghai）
- 后端服务：`http://127.0.0.1:6789`
- 前端开发服务：`http://127.0.0.1:8080/`
- 遥控器 SN：`9N9CMCJ001131H`
- 无人机 SN：`1581F7FVC252A00CJ5TT`
- Payload 索引：`88-0-0`
- 视频源：`normal-0`
- 推流类型：`RTMP`（`url_type=1`）
- RTMP 推流基地址：`rtmp://192.168.3.33:1935/live/`
- MediaMTX 管理接口：`http://127.0.0.1:9997/v3/paths/list`

#### 实测结论

- `GET /api/v1/manage/live/capacity` 已返回当前在线无人机的真实直播能力，不再是空数组。
- `POST /api/v1/manage/live/streams/start` 本轮实测直接返回 `code=00000`，并给出 `rtmp_url`、`webrtc_url`、`whep_url`、`hls_url`。
- MediaMTX 在路径 `live/1581F7FVC252A00CJ5TT-88-0-0` 下确认收到 RTMP 真实流，状态为 `ready=true`、`available=true`、`online=true`，视频轨为 `H264`，分辨率 `960x544`。
- `bytesReceived` 在约 15 秒内从 `793243` 增长到 `5067327`，说明无人机持续在推送真实码流；`bytesSent` 增长到 `2053203`，同时出现 `webRTCSession` reader，说明播放侧也已开始消费。
- `POST /api/v1/manage/live/streams/stop` 同样返回 `code=00000`，且 MediaMTX 路径列表在 2 秒内回到空数组。

#### 直播能力列表

- 方法：`GET`
- 路径：`/api/v1/manage/live/capacity`
- 认证：`需要 x-auth-token`
- 说明：查询直播能力
- operationId：`getLiveCapacity`

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponseListCapacityDeviceDTO`

响应字段：

| 字段 | 类型                     | 必填 | 说明                                  |
| ---- | ------------------------ | ---- | ------------------------------------- |
| code | string                   | 否   | Business code, use 00000 for success. |
| msg  | string                   | 否   | The response message.                 |
| data | array<CapacityDeviceDTO> | 否   | The response data.                    |

实测响应：

```json
{
  "code": "00000",
  "msg": "success",
  "data": [
    {
      "sn": "1581F7FVC252A00CJ5TT",
      "name": "1581F7FVC252A00CJ5TT",
      "cameras_list": [
        {
          "id": "a245ac1f-31b2-4045-ad71-86fbfde5a28c",
          "index": "88-0-0",
          "videos_list": [
            {
              "id": "4410f5c1-a08a-4767-b194-960f70de6b5b",
              "index": "normal-0",
              "type": "wide"
            },
            {
              "id": "a282d91a-f260-4f9d-8a65-a3707bee361f",
              "index": "normal-0",
              "type": "normal"
            }
          ]
        }
      ]
    }
  ]
}
```

说明：

- `sn` 是无人机 SN。
- `cameras_list[].index` 是载荷索引，本次实测为 `88-0-0`。
- `videos_list[].type` 表示视频源类型，本次实测中同一镜头下同时返回了 `wide` 和 `normal`。
- 开始推流使用的 `video_id` 不是上述 `id` 字段，而是按 `无人机SN/载荷索引/视频索引` 组装；本次实测实际使用值为 `1581F7FVC252A00CJ5TT/88-0-0/normal-0`。

#### 开始直播

- 方法：`POST`
- 路径：`/api/v1/manage/live/streams/start`
- 认证：`需要 x-auth-token`
- 说明：启动推流
- operationId：`liveStart`

请求体：

- Content-Type：`application/json`
- Schema：`LiveTypeDTO`

请求字段：

| 字段          | 类型    | 必填 | 说明                                                                                                                    |
| ------------- | ------- | ---- | ----------------------------------------------------------------------------------------------------------------------- |
| videoType     | string  | 否   | 镜头切换接口使用；开始直播时可省略。                                                                                    |
| url_type      | integer | 是   | 推流类型。`0=AGORA`，`1=RTMP`，`2=RTSP`，`3=GB28181`，`4=WHIP`。本次实测使用 `1`。                                      |
| video_id      | string  | 是   | 直播视频源标识，格式：`{drone_sn}/{payload_index}/{video_type}-0`。本次实测值：`1581F7FVC252A00CJ5TT/88-0-0/normal-0`。 |
| video_quality | integer | 否   | 画质。`0=AUTO`，`1=SMOOTH`，`2=STANDARD_DEFINITION`，`3=HIGH_DEFINITION`，`4=ULTRA_HD`。本次实测使用 `0`。              |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

`data` 字段结构（`LiveDTO`）：

| 字段       | 类型   | 说明                                     |
| ---------- | ------ | ---------------------------------------- |
| url        | string | 推流地址；RTMP 场景下等同于 `rtmp_url`。 |
| rtmp_url   | string | RTMP 推流地址。                          |
| webrtc_url | string | MediaMTX WebRTC 播放地址。               |
| play_url   | string | 当前实现中与 `webrtc_url` 相同。         |
| whep_url   | string | MediaMTX WHEP 低延迟播放地址。           |
| hls_url    | string | MediaMTX HLS 播放地址。                  |
| username   | string | 仅特定协议场景可能返回。                 |
| password   | string | 仅特定协议场景可能返回。                 |

实测请求：

```json
{
  "url_type": 1,
  "video_id": "1581F7FVC252A00CJ5TT/88-0-0/normal-0",
  "video_quality": 0
}
```

实测 HTTP 响应：

```json
{
  "code": "00000",
  "msg": "success",
  "data": {
    "url": "rtmp://192.168.3.33:1935/live/1581F7FVC252A00CJ5TT-88-0-0",
    "rtmp_url": "rtmp://192.168.3.33:1935/live/1581F7FVC252A00CJ5TT-88-0-0",
    "webrtc_url": "http://192.168.3.33:8889/live/1581F7FVC252A00CJ5TT-88-0-0",
    "play_url": "http://192.168.3.33:8889/live/1581F7FVC252A00CJ5TT-88-0-0",
    "whep_url": "http://192.168.3.33:8889/live/1581F7FVC252A00CJ5TT-88-0-0/whep",
    "hls_url": "http://192.168.3.33:8888/live/1581F7FVC252A00CJ5TT-88-0-0/index.m3u8"
  }
}
```

实测结果：

- 后端于 `2026-03-26 17:14:08` 下发 MQTT 服务 `live_start_push`。
- 遥控器于同一轮实测中先回报 `status=false`，随后在 `2026-03-26 17:14:08` 回报 `status=true`。
- 后端在 `services_reply` 中收到 `{"method":"live_start_push","data":{"result":0}}`，本轮接口返回与设备实际动作一致。
- MediaMTX 路径：`live/1581F7FVC252A00CJ5TT-88-0-0`
- MediaMTX 源类型：`rtmpConn`
- MediaMTX 轨道：`H264`
- MediaMTX 编码参数：`width=960`、`height=544`、`profile=High`、`level=3.1`

MediaMTX 轮询观测：

| 轮询序号 | bytesReceived | bytesSent | readers              |
| -------- | ------------- | --------- | -------------------- |
| 1        | 793243        | 0         | 0                    |
| 2        | 1860713       | 0         | 1（`webRTCSession`） |
| 3        | 2919494       | 0         | 1（`webRTCSession`） |
| 4        | 3974672       | 960548    | 1（`webRTCSession`） |
| 5        | 5067327       | 2053203   | 1（`webRTCSession`） |

结论：

- 以本次真实环境实测为准，`/api/v1/manage/live/streams/start` 已能稳定触发无人机开始 RTMP 推流。
- 当前环境中，开始直播接口已不再出现之前的 `E0001` 超时现象，而是返回标准成功响应。
- 前端可以直接使用响应中的 `whep_url` 做低延迟播放，或使用 `hls_url` 做 HLS 播放。

#### 停止直播

- 方法：`POST`
- 路径：`/api/v1/manage/live/streams/stop`
- 认证：`需要 x-auth-token`
- 说明：停止推流
- operationId：`liveStop`

请求体：

- Content-Type：`application/json`
- Schema：`LiveTypeDTO`

请求字段：

| 字段          | 类型    | 必填 | 说明                     |
| ------------- | ------- | ---- | ------------------------ |
| videoType     | string  | 否   | 该接口不使用。           |
| url_type      | integer | 否   | 该接口不使用。           |
| video_id      | string  | 是   | 要停止的直播视频源标识。 |
| video_quality | integer | 否   | 该接口不使用。           |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

实测请求：

```json
{
  "video_id": "1581F7FVC252A00CJ5TT/88-0-0/normal-0"
}
```

实测 HTTP 响应：

```json
{
  "code": "00000",
  "msg": "success"
}
```

实测结果：

- 后端于 `2026-03-26 17:14:41` 下发 MQTT 服务 `live_stop_push`。
- 后端在 `services_reply` 中收到 `{"method":"live_stop_push","data":{"result":0}}`。
- 遥控器随后回报 `status=false`，与停止推流动作一致。
- MediaMTX 中对应流路径已在 2 秒内被移除。
- `http://127.0.0.1:9997/v3/paths/list` 连续 5 次轮询均返回：

```json
{
  "itemCount": 0,
  "pageCount": 0,
  "items": []
}
```

结论：

- 以本次真实环境实测为准，`/api/v1/manage/live/streams/stop` 已能稳定停止正在推送的 RTMP 流。
- 当前环境中，停止直播接口同样返回标准成功响应。

#### 设置清晰度

- 方法：`POST`
- 路径：`/api/v1/manage/live/streams/update`
- 认证：`需要 x-auth-token`
- 说明：更新推流质量
- operationId：`liveSetQuality`

请求体：

- Content-Type：`application/json`
- Schema：`LiveTypeDTO`

请求字段：

| 字段          | 类型    | 必填 | 说明                                                                                         |
| ------------- | ------- | ---- | -------------------------------------------------------------------------------------------- |
| videoType     | string  | 否   | 该接口不使用。                                                                               |
| url_type      | integer | 否   | 该接口不使用。                                                                               |
| video_id      | string  | 是   | 要调整清晰度的直播视频源标识。                                                               |
| video_quality | integer | 是   | 画质枚举。`0=AUTO`，`1=SMOOTH`，`2=STANDARD_DEFINITION`，`3=HIGH_DEFINITION`，`4=ULTRA_HD`。 |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

说明：

- 本轮未单独实测该接口。
- 从实现看，该接口只消费 `video_id` 和 `video_quality` 两个字段。

#### 切换镜头

- 方法：`POST`
- 路径：`/api/v1/manage/live/streams/switch`
- 认证：`需要 x-auth-token`
- 说明：直播镜头切换
- operationId：`liveLensChange`

请求体：

- Content-Type：`application/json`
- Schema：`LiveTypeDTO`

请求字段：

| 字段          | 类型    | 必填 | 说明                                         |
| ------------- | ------- | ---- | -------------------------------------------- |
| videoType     | string  | 是   | 目标视频类型。枚举值：`zoom`、`wide`、`ir`。 |
| url_type      | integer | 否   | 该接口不使用。                               |
| video_id      | string  | 是   | 当前直播视频源标识。                         |
| video_quality | integer | 否   | 该接口不使用。                               |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

说明：

- 本轮未单独实测该接口。
- `videoType` 推荐结合 `capacity` 返回的 `switch_video_types` 选择；当前实测响应中未返回该字段时，可按设备支持能力使用 `zoom`、`wide`、`ir`。

### 拓扑

#### 设备拓扑

- 方法：`GET`
- 路径：`/api/v1/manage/workspaces/{workspace_id}/devices/topologies`
- 认证：`需要 x-auth-token`
- 说明：Pilot/管理端拓扑查询
- operationId：`obtainDeviceTopologyList`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明         |
| ------------ | ---- | ---- | ------ | ------------ |
| workspace_id | path | 是   | string | workspace id |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponseTopologyResponse`

响应字段：

| 字段 | 类型             | 必填 | 说明                                  |
| ---- | ---------------- | ---- | ------------------------------------- |
| code | string           | 否   | Business code, use 00000 for success. |
| msg  | string           | 否   | The response message.                 |
| data | TopologyResponse | 否   | topology response data                |

`data` 字段结构（`TopologyResponse`）：

| 字段 | 类型                | 必填 | 说明 |
| ---- | ------------------- | ---- | ---- |
| list | array<TopologyList> | 是   |      |

当前环境返回示例：

```json
{
  "code": "00000",
  "msg": "success",
  "data": {
    "list": [
      {
        "hosts": [
          {
            "sn": "1581F7FVC252A00CJ5TT",
            "model": "1581F7FVC252A00CJ5TT",
            "bound_status": true,
            "domain": 0,
            "device_callsign": "1581F7FVC252A00CJ5TT",
            "device_model": {
              "domain": 0,
              "type": 99,
              "device_model_key": "0-99-0",
              "sub_type": 0
            },
            "online_status": true,
            "user_callsign": "1581F7FVC252A00CJ5TT",
            "icon_urls": {
              "normal_icon_url": "",
              "selected_icon_url": ""
            }
          }
        ],
        "parents": [
          {
            "sn": "9N9CMCJ001131H",
            "model": "9N9CMCJ001131H",
            "bound_status": true,
            "domain": 2,
            "device_callsign": "9N9CMCJ001131H",
            "device_model": {
              "domain": 2,
              "type": 174,
              "device_model_key": "2-174-0",
              "sub_type": 0
            },
            "online_status": true,
            "user_callsign": "9N9CMCJ001131H",
            "icon_urls": {
              "normal_icon_url": "",
              "selected_icon_url": ""
            }
          }
        ]
      }
    ]
  }
}
```

## 地图模块

### 飞行区域

#### 飞行区域列表

- 方法：`GET`
- 路径：`/api/v1/map/workspaces/{workspace_id}/flight-areas`
- 认证：`需要 x-auth-token`
- 说明：查询飞行区
- operationId：`getFlightAreas`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明 |
| ------------ | ---- | ---- | ------ | ---- |
| workspace_id | path | 是   | string |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponseListFlightAreaDTO`

响应字段：

| 字段 | 类型                 | 必填 | 说明                                  |
| ---- | -------------------- | ---- | ------------------------------------- |
| code | string               | 否   | Business code, use 00000 for success. |
| msg  | string               | 否   | The response message.                 |
| data | array<FlightAreaDTO> | 否   | The response data.                    |

#### 新增飞行区域

- 方法：`POST`
- 路径：`/api/v1/map/workspaces/{workspace_id}/flight-areas`
- 认证：`需要 x-auth-token`
- 说明：新增
- operationId：`createFlightArea`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明 |
| ------------ | ---- | ---- | ------ | ---- |
| workspace_id | path | 是   | string |      |

请求体：

- Content-Type：`application/json`
- Schema：`PostFlightAreaParam`

请求字段：

| 字段    | 类型              | 必填 | 说明 |
| ------- | ----------------- | ---- | ---- |
| id      | string            | 是   |      |
| name    | string            | 是   |      |
| type    | string            | 是   |      |
| content | FlightAreaContent | 是   |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

#### 删除飞行区域

- 方法：`DELETE`
- 路径：`/api/v1/map/workspaces/{workspace_id}/flight-areas/{area_id}`
- 认证：`需要 x-auth-token`
- 说明：删除
- operationId：`deleteFlightArea`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明 |
| ------------ | ---- | ---- | ------ | ---- |
| workspace_id | path | 是   | string |      |
| area_id      | path | 是   | string |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

#### 更新飞行区域

- 方法：`PUT`
- 路径：`/api/v1/map/workspaces/{workspace_id}/flight-areas/{area_id}`
- 认证：`需要 x-auth-token`
- 说明：更新
- operationId：`updateFlightArea`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明 |
| ------------ | ---- | ---- | ------ | ---- |
| workspace_id | path | 是   | string |      |
| area_id      | path | 是   | string |      |

请求体：

- Content-Type：`application/json`
- Schema：`PutFlightAreaParam`

请求字段：

| 字段    | 类型              | 必填 | 说明 |
| ------- | ----------------- | ---- | ---- |
| name    | string            | 否   |      |
| content | FlightAreaContent | 否   |      |
| status  | boolean           | 否   |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

#### 同步飞行区域

- 方法：`POST`
- 路径：`/api/v1/map/workspaces/{workspace_id}/flight-areas/sync`
- 认证：`需要 x-auth-token`
- 说明：同步到设备
- operationId：`syncFlightArea`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明 |
| ------------ | ---- | ---- | ------ | ---- |
| workspace_id | path | 是   | string |      |

请求体：

- Content-Type：`application/json`
- Schema：`SyncFlightAreaParam`

请求字段：

| 字段      | 类型          | 必填 | 说明 |
| --------- | ------------- | ---- | ---- |
| device_sn | array<string> | 是   |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

### 设备状态

#### 设备状态列表

- 方法：`GET`
- 路径：`/api/v1/map/workspaces/{workspace_id}/device-statuses`
- 认证：`需要 x-auth-token`
- 说明：设备状态聚合
- operationId：`getDeviceFlightAreaStatus`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明 |
| ------------ | ---- | ---- | ------ | ---- |
| workspace_id | path | 是   | string |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponseListDeviceDataStatusDTO`

响应字段：

| 字段 | 类型                       | 必填 | 说明                                  |
| ---- | -------------------------- | ---- | ------------------------------------- |
| code | string                     | 否   | Business code, use 00000 for success. |
| msg  | string                     | 否   | The response message.                 |
| data | array<DeviceDataStatusDTO> | 否   | The response data.                    |

当前环境返回示例：

```json
{
  "code": "E0001",
  "msg": "illegal argument"
}
```

### 地图元素

#### 元素组+元素列表

- 方法：`GET`
- 路径：`/api/v1/map/workspaces/{workspace_id}/element-groups`
- 认证：`需要 x-auth-token`
- 说明：参数：group_id、is_distributed
- operationId：`getMapElements`

参数：

| 名称           | 位置  | 必填 | 类型    | 说明                                                                                                                                                                                                                                                                                                                                                    |
| -------------- | ----- | ---- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| workspace_id   | path  | 是   | string  | workspace id                                                                                                                                                                                                                                                                                                                                            |
| group_id       | query | 否   | string  | element group id. The same element group can contain multiple map elements, which is equivalent to grouping map elements. When initiating the request, if the group id parameter is not included, the server needs to return all map elements. If the group id is specified, it only needs to return the set of elements in the specified element group |
| is_distributed | query | 否   | boolean | Whether the element group is distributed.                                                                                                                                                                                                                                                                                                               |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponseListGetMapElementsResponse`

响应字段：

| 字段 | 类型                          | 必填 | 说明                                  |
| ---- | ----------------------------- | ---- | ------------------------------------- |
| code | string                        | 否   | Business code, use 00000 for success. |
| msg  | string                        | 否   | The response message.                 |
| data | array<GetMapElementsResponse> | 否   | The response data.                    |

#### 新增元素

- 方法：`POST`
- 路径：`/api/v1/map/workspaces/{workspace_id}/element-groups/{group_id}/elements`
- 认证：`需要 x-auth-token`
- 说明：新增点/线/面
- operationId：`createMapElement`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明                                                                                                                                                                                                                                                                                                                                                    |
| ------------ | ---- | ---- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| workspace_id | path | 是   | string | workspace id                                                                                                                                                                                                                                                                                                                                            |
| group_id     | path | 是   | string | element group id. The same element group can contain multiple map elements, which is equivalent to grouping map elements. When initiating the request, if the group id parameter is not included, the server needs to return all map elements. If the group id is specified, it only needs to return the set of elements in the specified element group |

请求体：

- Content-Type：`application/json`
- Schema：`CreateMapElementRequest`

请求字段：

| 字段     | 类型            | 必填 | 说明             |
| -------- | --------------- | ---- | ---------------- |
| id       | string          | 是   | element id       |
| name     | string          | 是   | element name     |
| resource | ElementResource | 是   | element resource |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponseCreateMapElementResponse`

响应字段：

| 字段 | 类型                     | 必填 | 说明                                  |
| ---- | ------------------------ | ---- | ------------------------------------- |
| code | string                   | 否   | Business code, use 00000 for success. |
| msg  | string                   | 否   | The response message.                 |
| data | CreateMapElementResponse | 否   | Create element response data          |

`data` 字段结构（`CreateMapElementResponse`）：

| 字段 | 类型   | 必填 | 说明       |
| ---- | ------ | ---- | ---------- |
| id   | string | 是   | element id |

#### 更新元素

- 方法：`PUT`
- 路径：`/api/v1/map/workspaces/{workspace_id}/elements/{element_id}`
- 认证：`需要 x-auth-token`
- 说明：更新点/线/面
- operationId：`updateMapElement`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明         |
| ------------ | ---- | ---- | ------ | ------------ |
| workspace_id | path | 是   | string | workspace id |
| element_id   | path | 是   | string | element id   |

请求体：

- Content-Type：`application/json`
- Schema：`UpdateMapElementRequest`

请求字段：

| 字段    | 类型           | 必填 | 说明            |
| ------- | -------------- | ---- | --------------- |
| name    | string         | 是   | element name    |
| content | ElementContent | 是   | element content |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

#### 删除元素

- 方法：`DELETE`
- 路径：`/api/v1/map/workspaces/{workspace_id}/elements/{element_id}`
- 认证：`需要 x-auth-token`
- 说明：删除单个元素
- operationId：`deleteMapElement`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明         |
| ------------ | ---- | ---- | ------ | ------------ |
| workspace_id | path | 是   | string | workspace id |
| element_id   | path | 是   | string | element id   |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

#### 删除分组下所有元素

- 方法：`DELETE`
- 路径：`/api/v1/map/workspaces/{workspace_id}/element-groups/{group_id}/elements`
- 认证：`需要 x-auth-token`
- 说明：批量删除分组元素
- operationId：`deleteAllElementByGroupId`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明 |
| ------------ | ---- | ---- | ------ | ---- |
| workspace_id | path | 是   | string |      |
| group_id     | path | 是   | string |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

## 媒体模块

### 媒体文件

#### 工作空间媒体文件分页

- 方法：`GET`
- 路径：`/api/v1/media/workspaces/{workspace_id}/files`
- 认证：`需要 x-auth-token`
- 说明：支持 page/page_size
- operationId：`getFilesList`

参数：

| 名称         | 位置  | 必填 | 类型    | 说明 |
| ------------ | ----- | ---- | ------- | ---- |
| page         | query | 否   | integer |      |
| page_size    | query | 否   | integer |      |
| workspace_id | path  | 是   | string  |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponsePaginationDataMediaFileDTO`

响应字段：

| 字段 | 类型                       | 必填 | 说明                                  |
| ---- | -------------------------- | ---- | ------------------------------------- |
| code | string                     | 否   | Business code, use 00000 for success. |
| msg  | string                     | 否   | The response message.                 |
| data | PaginationDataMediaFileDTO | 否   | Format of paged data                  |

`data` 字段结构（`PaginationDataMediaFileDTO`）：

| 字段       | 类型                | 必填 | 说明                                             |
| ---------- | ------------------- | ---- | ------------------------------------------------ |
| list       | array<MediaFileDTO> | 否   | The collection in which the data list is stored. |
| pagination | Pagination          | 否   | Used for paging display                          |

#### 文件下载地址跳转

- 方法：`GET`
- 路径：`/api/v1/media/workspaces/{workspace_id}/files/{file_id}/url`
- 认证：`需要 x-auth-token`
- 说明：302 重定向到对象存储地址
- operationId：`getFileUrl`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明 |
| ------------ | ---- | ---- | ------ | ---- |
| workspace_id | path | 是   | string |      |
| file_id      | path | 是   | string |      |

成功响应：

- Content-Type：`-`
- Schema：`-`

### Pilot 媒体回调

#### 指纹秒传校验

- 方法：`POST`
- 路径：`/api/v1/media/workspaces/{workspace_id}/fast-upload`
- 认证：`需要 x-auth-token`
- 说明：按 fingerprint 校验
- operationId：`mediaFastUpload`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明         |
| ------------ | ---- | ---- | ------ | ------------ |
| workspace_id | path | 是   | string | workspace id |

请求体：

- Content-Type：`application/json`
- Schema：`MediaFastUploadRequest`

请求字段：

| 字段        | 类型                | 必填 | 说明                                                                            |
| ----------- | ------------------- | ---- | ------------------------------------------------------------------------------- |
| ext         | FastUploadExtension | 是   | media file fast upload extension data                                           |
| fingerprint | string              | 是   | media file fingerprint                                                          |
| name        | string              | 是   | media file name                                                                 |
| path        | string              | 否   | media file path. This value is empty if the photo was not taken in the wayline. |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

#### 上传回调

- 方法：`POST`
- 路径：`/api/v1/media/workspaces/{workspace_id}/upload-callback`
- 认证：`需要 x-auth-token`
- 说明：文件上传完成回调
- operationId：`mediaUploadCallback`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明         |
| ------------ | ---- | ---- | ------ | ------------ |
| workspace_id | path | 是   | string | workspace id |

请求体：

- Content-Type：`application/json`
- Schema：`MediaUploadCallbackRequest`

请求字段：

| 字段          | 类型               | 必填 | 说明                                                                            |
| ------------- | ------------------ | ---- | ------------------------------------------------------------------------------- |
| ext           | MediaFileExtension | 是   | media file upload callback extension data                                       |
| fingerprint   | string             | 是   | media file fingerprint                                                          |
| name          | string             | 是   | media file name                                                                 |
| path          | string             | 否   | media file path. This value is empty if the photo was not taken in the wayline. |
| metadata      | MediaFileMetadata  | 是   | media file metadata                                                             |
| object_key    | string             | 是   | The key of the object in the bucket                                             |
| sub_file_type | integer            | 是   | The type of image file. <br /><p>0: normal picture; <p/><p>1: panorama.</p>     |

成功响应：

- Content-Type：`application/json`
- Schema：`-`

当前环境返回示例：

```json
{
  "code": "00000",
  "msg": "success",
  "data": "media/DJI_20220831151616_0004_W_Waypoint4.JPG"
}
```

#### tiny-fingerprint 批量校验

- 方法：`POST`
- 路径：`/api/v1/media/workspaces/{workspace_id}/files/tiny-fingerprints`
- 认证：`需要 x-auth-token`
- 说明：返回已存在指纹
- operationId：`getExistFileTinyFingerprint`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明         |
| ------------ | ---- | ---- | ------ | ------------ |
| workspace_id | path | 是   | string | workspace id |

请求体：

- Content-Type：`application/json`
- Schema：`GetFileFingerprintRequest`

请求字段：

| 字段              | 类型          | 必填 | 说明                         |
| ----------------- | ------------- | ---- | ---------------------------- |
| tiny_fingerprints | array<string> | 是   | tiny fingerprints collection |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponseGetFileFingerprintResponse`

响应字段：

| 字段 | 类型                       | 必填 | 说明                                                  |
| ---- | -------------------------- | ---- | ----------------------------------------------------- |
| code | string                     | 否   | Business code, use 00000 for success.                 |
| msg  | string                     | 否   | The response message.                                 |
| data | GetFileFingerprintResponse | 否   | response data for tiny fingerprints of existing files |

`data` 字段结构（`GetFileFingerprintResponse`）：

| 字段              | 类型          | 必填 | 说明                         |
| ----------------- | ------------- | ---- | ---------------------------- |
| tiny_fingerprints | array<string> | 是   | tiny fingerprints collection |

#### 文件组上传回调

- 方法：`POST`
- 路径：`/api/v1/media/workspaces/{workspace_id}/group-upload-callback`
- 认证：`需要 x-auth-token`
- 说明：分组上传状态上报
- operationId：`folderUploadCallback`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明         |
| ------------ | ---- | ---- | ------ | ------------ |
| workspace_id | path | 是   | string | workspace id |

请求体：

- Content-Type：`application/json`
- Schema：`FolderUploadCallbackRequest`

请求字段：

| 字段                | 类型    | 必填 | 说明                                           |
| ------------------- | ------- | ---- | ---------------------------------------------- |
| file_group_id       | string  | 是   | file group id                                  |
| file_count          | integer | 是   | total amount of media in the file group        |
| file_uploaded_count | integer | 是   | the number of uploaded media in the file group |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

## 存储模块

### 存储模块

#### STS 临时凭证

- 方法：`POST`
- 路径：`/api/v1/storage/workspaces/{workspace_id}/sts`
- 认证：`需要 x-auth-token`
- 说明：获取对象存储临时凭证
- operationId：`getTemporaryCredential`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明         |
| ------------ | ---- | ---- | ------ | ------------ |
| workspace_id | path | 是   | string | workspace id |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponseStsCredentialsResponse`

响应字段：

| 字段 | 类型                   | 必填 | 说明                                  |
| ---- | ---------------------- | ---- | ------------------------------------- |
| code | string                 | 否   | Business code, use 00000 for success. |
| msg  | string                 | 否   | The response message.                 |
| data | StsCredentialsResponse | 否   | Temporary credential data             |

`data` 字段结构（`StsCredentialsResponse`）：

| 字段              | 类型             | 必填 | 说明                                                 |
| ----------------- | ---------------- | ---- | ---------------------------------------------------- |
| bucket            | string           | 是   | bucket name                                          |
| credentials       | CredentialsToken | 是   | The token data of the temporary credential           |
| endpoint          | string           | 是   | access domain name for external services             |
| provider          | OssTypeEnum      | 是   | oss type                                             |
| region            | string           | 是   | The region where the bucket is located.              |
| object_key_prefix | string           | 是   | The folder path where the object needs to be stored. |

## 航线模块

### 航线文件

#### 航线分页查询

- 方法：`GET`
- 路径：`/api/v1/wayline/workspaces/{workspace_id}/waylines`
- 认证：`需要 x-auth-token`
- 说明：条件查询
- operationId：`getWaylineList`

参数：

| 名称              | 位置  | 必填 | 类型                   | 说明                             |
| ----------------- | ----- | ---- | ---------------------- | -------------------------------- |
| workspace_id      | path  | 是   | string                 | workspace id                     |
| favorited         | query | 否   | boolean                | Is the wayline file favorited?   |
| orderBy.column    | query | 是   | string                 |                                  |
| orderBy.desc      | query | 否   | boolean                |                                  |
| page              | query | 否   | int                    | current page                     |
| page_size         | query | 否   | int                    | page size                        |
| template_type     | query | 否   | array<WaylineTypeEnum> | wayline template type collection |
| action_type       | query | 否   | string                 | wayline template type collection |
| drone_model_keys  | query | 否   | string                 | drone device product enum        |
| payload_model_key | query | 否   | string                 | payload device product enum      |
| key               | query | 否   | string                 | wayline file name                |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponsePaginationDataGetWaylineListResponse`

响应字段：

| 字段 | 类型                                 | 必填 | 说明                                  |
| ---- | ------------------------------------ | ---- | ------------------------------------- |
| code | string                               | 否   | Business code, use 00000 for success. |
| msg  | string                               | 否   | The response message.                 |
| data | PaginationDataGetWaylineListResponse | 否   | Format of paged data                  |

`data` 字段结构（`PaginationDataGetWaylineListResponse`）：

| 字段       | 类型                          | 必填 | 说明                                             |
| ---------- | ----------------------------- | ---- | ------------------------------------------------ |
| list       | array<GetWaylineListResponse> | 否   | The collection in which the data list is stored. |
| pagination | Pagination                    | 否   | Used for paging display                          |

#### 航线下载地址

- 方法：`GET`
- 路径：`/api/v1/wayline/workspaces/{workspace_id}/waylines/{wayline_id}/url`
- 认证：`需要 x-auth-token`
- 说明：下载地址跳转
- operationId：`getWaylineFileDownloadAddress`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明         |
| ------------ | ---- | ---- | ------ | ------------ |
| workspace_id | path | 是   | string | workspace id |
| wayline_id   | path | 是   | string | wayline id   |

成功响应：

- Content-Type：`-`
- Schema：`-`

#### 重名校验

- 方法：`GET`
- 路径：`/api/v1/wayline/workspaces/{workspace_id}/waylines/duplicate-names`
- 认证：`需要 x-auth-token`
- 说明：参数：name（可重复）
- operationId：`getDuplicatedWaylineName`

参数：

| 名称         | 位置  | 必填 | 类型          | 说明              |
| ------------ | ----- | ---- | ------------- | ----------------- |
| workspace_id | path  | 是   | string        | workspace id      |
| name         | query | 是   | array<string> | wayline file name |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponseListString`

响应字段：

| 字段 | 类型          | 必填 | 说明                                  |
| ---- | ------------- | ---- | ------------------------------------- |
| code | string        | 否   | Business code, use 00000 for success. |
| msg  | string        | 否   | The response message.                 |
| data | array<string> | 否   | The response data.                    |

#### 上传结果回调

- 方法：`POST`
- 路径：`/api/v1/wayline/workspaces/{workspace_id}/upload-callback`
- 认证：`需要 x-auth-token`
- 说明：Pilot 上传回调
- operationId：`fileUploadResultReport`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明         |
| ------------ | ---- | ---- | ------ | ------------ |
| workspace_id | path | 是   | string | workspace id |

请求体：

- Content-Type：`application/json`
- Schema：`WaylineUploadCallbackRequest`

请求字段：

| 字段       | 类型                          | 必填 | 说明                                |
| ---------- | ----------------------------- | ---- | ----------------------------------- |
| name       | string                        | 是   | wayline file name                   |
| metadata   | WaylineUploadCallbackMetadata | 是   | Wayline file metadata               |
| object_key | string                        | 是   | The key of the object in the bucket |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

#### 删除航线

- 方法：`DELETE`
- 路径：`/api/v1/wayline/workspaces/{workspace_id}/waylines/{wayline_id}`
- 认证：`需要 x-auth-token`
- 说明：删除航线
- operationId：`deleteWayline`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明 |
| ------------ | ---- | ---- | ------ | ---- |
| workspace_id | path | 是   | string |      |
| wayline_id   | path | 是   | string |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

#### 导入 KMZ

- 方法：`POST`
- 路径：`/api/v1/wayline/workspaces/{workspace_id}/waylines/files/upload`
- 认证：`需要 x-auth-token`
- 说明：表单上传
- operationId：`importKmzFile`

参数：

| 名称 | 位置  | 必填 | 类型   | 说明 |
| ---- | ----- | ---- | ------ | ---- |
| file | query | 是   | string |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

#### 收藏航线（批量）

- 方法：`POST`
- 路径：`/api/v1/wayline/workspaces/{workspace_id}/favorites`
- 认证：`需要 x-auth-token`
- 说明：参数：id（可重复）
- operationId：`batchFavoritesWayline`

参数：

| 名称         | 位置  | 必填 | 类型          | 说明         |
| ------------ | ----- | ---- | ------------- | ------------ |
| workspace_id | path  | 是   | string        | workspace id |
| id           | query | 是   | array<string> | wayline id   |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

#### 取消收藏（批量）

- 方法：`DELETE`
- 路径：`/api/v1/wayline/workspaces/{workspace_id}/favorites`
- 认证：`需要 x-auth-token`
- 说明：参数：id（可重复）
- operationId：`batchUnfavoritesWayline`

参数：

| 名称         | 位置  | 必填 | 类型          | 说明         |
| ------------ | ----- | ---- | ------------- | ------------ |
| workspace_id | path  | 是   | string        | workspace id |
| id           | query | 是   | array<string> | wayline id   |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

### 航线任务

#### 创建任务

- 方法：`POST`
- 路径：`/api/v1/wayline/workspaces/{workspace_id}/flight-tasks`
- 认证：`需要 x-auth-token`
- 说明：创建飞行任务
- operationId：`createJob`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明 |
| ------------ | ---- | ---- | ------ | ---- |
| workspace_id | path | 是   | string |      |

请求体：

- Content-Type：`application/json`
- Schema：`CreateJobParam`

请求字段：

| 字段               | 类型                  | 必填 | 说明                                                                           |
| ------------------ | --------------------- | ---- | ------------------------------------------------------------------------------ |
| name               | string                | 是   |                                                                                |
| fileId             | string                | 是   |                                                                                |
| dockSn             | string                | 是   |                                                                                |
| waylineType        | WaylineTypeEnum       | 是   | <p>0: waypoint<p/><p>1: mapping2d<p/><p>2: mapping3d<p/><p>3: mappingStrip</p> |
| taskType           | string                | 是   |                                                                                |
| rthAltitude        | integer               | 是   |                                                                                |
| outOfControlAction | string                | 是   |                                                                                |
| minBatteryCapacity | integer               | 否   |                                                                                |
| minStorageCapacity | integer               | 否   |                                                                                |
| taskDays           | array<integer>        | 否   |                                                                                |
| taskPeriods        | array<array<integer>> | 否   |                                                                                |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

#### 任务分页

- 方法：`GET`
- 路径：`/api/v1/wayline/workspaces/{workspace_id}/jobs`
- 认证：`需要 x-auth-token`
- 说明：支持 page/page_size
- operationId：`getJobs`

参数：

| 名称         | 位置  | 必填 | 类型    | 说明 |
| ------------ | ----- | ---- | ------- | ---- |
| page         | query | 否   | integer |      |
| page_size    | query | 否   | integer |      |
| workspace_id | path  | 是   | string  |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponsePaginationDataWaylineJobDTO`

响应字段：

| 字段 | 类型                        | 必填 | 说明                                  |
| ---- | --------------------------- | ---- | ------------------------------------- |
| code | string                      | 否   | Business code, use 00000 for success. |
| msg  | string                      | 否   | The response message.                 |
| data | PaginationDataWaylineJobDTO | 否   | Format of paged data                  |

`data` 字段结构（`PaginationDataWaylineJobDTO`）：

| 字段       | 类型                 | 必填 | 说明                                             |
| ---------- | -------------------- | ---- | ------------------------------------------------ |
| list       | array<WaylineJobDTO> | 否   | The collection in which the data list is stored. |
| pagination | Pagination           | 否   | Used for paging display                          |

#### 取消任务

- 方法：`DELETE`
- 路径：`/api/v1/wayline/workspaces/{workspace_id}/jobs`
- 认证：`需要 x-auth-token`
- 说明：参数：job_id（可重复）
- operationId：`publishCancelJob`

参数：

| 名称         | 位置  | 必填 | 类型          | 说明 |
| ------------ | ----- | ---- | ------------- | ---- |
| job_id       | query | 是   | array<string> |      |
| workspace_id | path  | 是   | string        |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

#### 任务媒体优先上传

- 方法：`POST`
- 路径：`/api/v1/wayline/workspaces/{workspace_id}/jobs/{job_id}/media-highest`
- 认证：`需要 x-auth-token`
- 说明：任务媒体优先级调整
- operationId：`uploadMediaHighestPriority`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明 |
| ------------ | ---- | ---- | ------ | ---- |
| workspace_id | path | 是   | string |      |
| job_id       | path | 是   | string |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

#### 更新任务状态

- 方法：`PUT`
- 路径：`/api/v1/wayline/workspaces/{workspace_id}/jobs/{job_id}`
- 认证：`需要 x-auth-token`
- 说明：任务状态流转
- operationId：`updateJobStatus`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明 |
| ------------ | ---- | ---- | ------ | ---- |
| workspace_id | path | 是   | string |      |
| job_id       | path | 是   | string |      |

请求体：

- Content-Type：`application/json`
- Schema：`UpdateJobParam`

请求字段：

| 字段   | 类型   | 必填 | 说明 |
| ------ | ------ | ---- | ---- |
| status | string | 否   |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

## 控制模块

### DRC

#### DRC 连接鉴权

- 方法：`POST`
- 路径：`/api/v1/control/workspaces/{workspace_id}/drc/connect`
- 认证：`需要 x-auth-token`
- 说明：用户接入 DRC
- operationId：`drcConnect`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明 |
| ------------ | ---- | ---- | ------ | ---- |
| workspace_id | path | 是   | string |      |

请求体：

- Content-Type：`application/json`
- Schema：`DrcConnectParam`

请求字段：

| 字段      | 类型    | 必填 | 说明 |
| --------- | ------- | ---- | ---- |
| clientId  | string  | 否   |      |
| expireSec | integer | 否   |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

#### DRC 进入

- 方法：`POST`
- 路径：`/api/v1/control/workspaces/{workspace_id}/drc/enter`
- 认证：`需要 x-auth-token`
- 说明：设备进入 DRC
- operationId：`drcEnter`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明 |
| ------------ | ---- | ---- | ------ | ---- |
| workspace_id | path | 是   | string |      |

请求体：

- Content-Type：`application/json`
- Schema：`DrcModeParam`

请求字段：

| 字段       | 类型               | 必填 | 说明 |
| ---------- | ------------------ | ---- | ---- |
| clientId   | string             | 是   |      |
| dockSn     | string             | 是   |      |
| expireSec  | integer            | 否   |      |
| deviceInfo | DeviceDrcInfoParam | 否   |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

#### DRC 退出

- 方法：`POST`
- 路径：`/api/v1/control/workspaces/{workspace_id}/drc/exit`
- 认证：`需要 x-auth-token`
- 说明：设备退出 DRC
- operationId：`drcExit`

参数：

| 名称         | 位置 | 必填 | 类型   | 说明 |
| ------------ | ---- | ---- | ------ | ---- |
| workspace_id | path | 是   | string |      |

请求体：

- Content-Type：`application/json`
- Schema：`DrcModeParam`

请求字段：

| 字段       | 类型               | 必填 | 说明 |
| ---------- | ------------------ | ---- | ---- |
| clientId   | string             | 是   |      |
| dockSn     | string             | 是   |      |
| expireSec  | integer            | 否   |      |
| deviceInfo | DeviceDrcInfoParam | 否   |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

### 设备控制

#### 通用调试控制

- 方法：`POST`
- 路径：`/api/v1/control/devices/{sn}/jobs/{service_identifier}`
- 认证：`需要 x-auth-token`
- 说明：远程调试动作
- operationId：`createControlJob`

参数：

| 名称               | 位置 | 必填 | 类型   | 说明 |
| ------------------ | ---- | ---- | ------ | ---- |
| sn                 | path | 是   | string |      |
| service_identifier | path | 是   | string |      |

请求体：

- Content-Type：`application/json`
- Schema：`RemoteDebugParam`

请求字段：

| 字段   | 类型    | 必填 | 说明 |
| ------ | ------- | ---- | ---- |
| action | integer | 是   |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

#### 飞向目标点

- 方法：`POST`
- 路径：`/api/v1/control/devices/{sn}/jobs/fly-to-point`
- 认证：`需要 x-auth-token`
- 说明：飞向目标点
- operationId：`flyToPoint`

参数：

| 名称 | 位置 | 必填 | 类型   | 说明 |
| ---- | ---- | ---- | ------ | ---- |
| sn   | path | 是   | string |      |

请求体：

- Content-Type：`application/json`
- Schema：`FlyToPointParam`

请求字段：

| 字段     | 类型         | 必填 | 说明 |
| -------- | ------------ | ---- | ---- |
| flyToId  | string       | 否   |      |
| maxSpeed | integer      | 是   |      |
| points   | array<Point> | 是   |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

#### 停止飞向目标点

- 方法：`DELETE`
- 路径：`/api/v1/control/devices/{sn}/jobs/fly-to-point`
- 认证：`需要 x-auth-token`
- 说明：停止飞行
- operationId：`flyToPointStop`

参数：

| 名称 | 位置 | 必填 | 类型   | 说明 |
| ---- | ---- | ---- | ------ | ---- |
| sn   | path | 是   | string |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

#### 起飞到目标点

- 方法：`POST`
- 路径：`/api/v1/control/devices/{sn}/jobs/takeoff-to-point`
- 认证：`需要 x-auth-token`
- 说明：起飞动作
- operationId：`takeoffToPoint`

参数：

| 名称 | 位置 | 必填 | 类型   | 说明 |
| ---- | ---- | ---- | ------ | ---- |
| sn   | path | 是   | string |      |

请求体：

- Content-Type：`application/json`
- Schema：`TakeoffToPointParam`

请求字段：

| 字段                    | 类型   | 必填 | 说明 |
| ----------------------- | ------ | ---- | ---- |
| flightId                | string | 否   |      |
| targetLongitude         | number | 是   |      |
| targetLatitude          | number | 是   |      |
| targetHeight            | number | 是   |      |
| securityTakeoffHeight   | number | 是   |      |
| rthAltitude             | number | 是   |      |
| rcLostAction            | string | 是   |      |
| exitWaylineWhenRcLost   | string | 是   |      |
| maxSpeed                | number | 是   |      |
| rthMode                 | string | 否   |      |
| commanderModeLostAction | string | 否   |      |
| commanderFlightMode     | string | 否   |      |
| commanderFlightHeight   | number | 否   |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

#### 抢占飞行控制权

- 方法：`POST`
- 路径：`/api/v1/control/devices/{sn}/authority/flight`
- 认证：`需要 x-auth-token`
- 说明：飞行控制权
- operationId：`seizeFlightAuthority`

参数：

| 名称 | 位置 | 必填 | 类型   | 说明 |
| ---- | ---- | ---- | ------ | ---- |
| sn   | path | 是   | string |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

#### 抢占载荷控制权

- 方法：`POST`
- 路径：`/api/v1/control/devices/{sn}/authority/payload`
- 认证：`需要 x-auth-token`
- 说明：载荷控制权
- operationId：`seizePayloadAuthority`

参数：

| 名称 | 位置 | 必填 | 类型   | 说明 |
| ---- | ---- | ---- | ------ | ---- |
| sn   | path | 是   | string |      |

请求体：

- Content-Type：`application/json`
- Schema：`DronePayloadParam`

请求字段：

| 字段         | 类型    | 必填 | 说明 |
| ------------ | ------- | ---- | ---- |
| payloadIndex | string  | 是   |      |
| cameraType   | string  | 否   |      |
| zoomFactor   | number  | 否   |      |
| cameraMode   | string  | 否   |      |
| locked       | boolean | 否   |      |
| pitchSpeed   | number  | 否   |      |
| yawSpeed     | number  | 否   |      |
| x            | number  | 否   |      |
| y            | number  | 否   |      |
| resetMode    | string  | 否   |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |

#### 载荷指令下发

- 方法：`POST`
- 路径：`/api/v1/control/devices/{sn}/payload/commands`
- 认证：`需要 x-auth-token`
- 说明：载荷命令
- operationId：`payloadCommands`

参数：

| 名称 | 位置 | 必填 | 类型   | 说明 |
| ---- | ---- | ---- | ------ | ---- |
| sn   | path | 是   | string |      |

请求体：

- Content-Type：`application/json`
- Schema：`PayloadCommandsParam`

请求字段：

| 字段 | 类型              | 必填 | 说明 |
| ---- | ----------------- | ---- | ---- |
| sn   | string            | 否   |      |
| cmd  | string            | 是   |      |
| data | DronePayloadParam | 是   |      |

成功响应：

- Content-Type：`application/json`
- Schema：`HttpResultResponse`

响应字段：

| 字段 | 类型   | 必填 | 说明                                  |
| ---- | ------ | ---- | ------------------------------------- |
| code | string | 否   | Business code, use 00000 for success. |
| msg  | string | 否   | The response message.                 |
| data | object | 否   | The response data.                    |
