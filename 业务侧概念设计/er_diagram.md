# 低空智能巡检平台 - 概念设计（ER图 / Future）

> 本文档是未来目标域的概念草图，不代表当前代码已落地实现。  
> 当前已实现的业务数据模型请看：`逻辑设计/business_logical_model.md`。

## 涉及模块

- 任务管理
- 航线管理
- 飞行记录
- 飞行控制台

---

## 1. 实体识别

| 实体           | 英文名       | 说明                       | 来源页面             |
| -------------- | ------------ | -------------------------- | -------------------- |
| **无人机类型** | DroneType    | 无人机型号，如大疆Mavic 3E | 航线管理             |
| **无人机**     | Drone        | 具体的无人机设备           | 任务管理、飞行控制台 |
| **飞手**       | Pilot        | 执行飞行任务的操作员       | 任务管理、飞行记录   |
| **航线**       | Route        | 飞行路径规划               | 航线管理             |
| **航点**       | Waypoint     | 航线上的具体坐标点         | 新增航线页面         |
| **任务**       | Mission      | 巡检任务                   | 任务管理             |
| **飞行记录**   | FlightRecord | 一次飞行的执行记录（架次） | 飞行记录             |
| **媒体文件**   | MediaFile    | 拍摄的照片和视频           | 飞行记录             |

---

## 2. ER图（Mermaid）

```mermaid
erDiagram
    %% ========== 实体定义 ==========

    DroneType_无人机类型 {
        bigint id PK "类型ID"
        string name "型号名称"
        string manufacturer "制造商"
    }

    Drone_无人机 {
        bigint id PK "无人机ID"
        string name "名称"
        string sn "序列号"
        int status "状态"
    }

    Pilot_飞手 {
        bigint id PK "飞手ID"
        string name "姓名"
        string license_no "执照编号"
        int status "状态"
    }

    Route_航线 {
        bigint id PK "航线ID"
        string name "航线名称"
        int route_type "航线类型"
        decimal total_distance "总长度"
        int estimated_duration "预计时长"
    }

    Waypoint_航点 {
        bigint id PK "航点ID"
        int sequence "序号"
        decimal latitude "纬度"
        decimal longitude "经度"
        decimal altitude "高度"
    }

    Mission_任务 {
        bigint id PK "任务ID"
        string name "任务名称"
        int execution_type "执行策略"
        int status "状态"
    }

    FlightRecord_飞行记录 {
        bigint id PK "记录ID"
        string flight_no "架次编号"
        int flight_duration "飞行时长"
        int photo_count "照片数量"
        int video_count "视频数量"
    }

    MediaFile_媒体文件 {
        bigint id PK "媒体ID"
        int media_type "类型"
        string file_url "文件地址"
        decimal latitude "位置-纬度"
        decimal longitude "位置-经度"
    }

    %% ========== 关系定义 ==========

    %% 无人机类型与无人机
    DroneType_无人机类型 ||--o{ Drone_无人机 : "包含 (1:N)"

    %% 无人机类型与航线
    DroneType_无人机类型 ||--o{ Route_航线 : "适用于 (1:N)"

    %% 航线与航点
    Route_航线 ||--o{ Waypoint_航点 : "包含 (1:N)"

    %% 航线与任务
    Route_航线 ||--o{ Mission_任务 : "执行于 (1:N)"

    %% 无人机与任务
    Drone_无人机 ||--o{ Mission_任务 : "执行 (1:N)"

    %% 飞手与任务
    Pilot_飞手 ||--o{ Mission_任务 : "负责 (1:N)"

    %% 任务与飞行记录
    Mission_任务 ||--o{ FlightRecord_飞行记录 : "产生 (1:N)"

    %% 无人机与飞行记录
    Drone_无人机 ||--o{ FlightRecord_飞行记录 : "执行 (1:N)"

    %% 飞手与飞行记录
    Pilot_飞手 ||--o{ FlightRecord_飞行记录 : "操作 (1:N)"

    %% 飞行记录与媒体文件
    FlightRecord_飞行记录 ||--o{ MediaFile_媒体文件 : "包含 (1:N)"
```

---

## 3. 实体关系说明

| 关系                     | 基数 | 说明                         |
| ------------------------ | ---- | ---------------------------- |
| DroneType — Drone        | 1:N  | 一种机型有多架无人机         |
| DroneType — Route        | 1:N  | 一种机型适用于多条航线       |
| Route — Waypoint         | 1:N  | 一条航线包含多个航点         |
| Route — Mission          | 1:N  | 一条航线可被多个任务使用     |
| Drone — Mission          | 1:N  | 一架无人机可执行多个任务     |
| Pilot — Mission          | 1:N  | 一个飞手可负责多个任务       |
| Mission — FlightRecord   | 1:N  | 一个任务可产生多条飞行记录   |
| Drone — FlightRecord     | 1:N  | 一架无人机有多条飞行记录     |
| Pilot — FlightRecord     | 1:N  | 一个飞手有多条飞行记录       |
| FlightRecord — MediaFile | 1:N  | 一条飞行记录包含多个媒体文件 |

---

## 4. 核心业务流程

```
航线管理 → 任务管理 → 飞行执行 → 飞行记录
    │           │           │           │
    ▼           ▼           ▼           ▼
  航线        任务      实时控制      媒体文件
  航点      (分配无人机、飞手)
```
