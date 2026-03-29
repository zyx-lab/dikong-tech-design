# DJI 权限与租户隔离设计（认领模式）

> **设计原则**：最小化变更，不新增 dji_integration 模块，沿用现有 `module.permission` 权限体系。

## 1. 结论
1. `tenant` 仍是唯一正式隔离边界。
2. `workspace` 只作为上游作用域参数，不作为本系统租户边界。
3. 上游共享资源先进入本地共享池，再通过认领进入 tenant 视角。
4. 前端不持有 DJI `x-auth-token`，也不直接调用 DJI 控制接口。
5. 所有 DJI 相关操作归属到现有业务模块权限，不新增 `dji_integration.*` 权限体系。

---

## 2. 权限归属

> **核心原则**：不新增 `dji_integration` 模块，沿用现有业务权限。

### 2.1 用户操作权限归属

| 操作 | 归属模块 | 权限 | 说明 |
|------|----------|------|------|
| `POST /api/v1/drones` 认领设备 | drone | `drone.manage_drone` | 租户内操作 |
| `GET /api/v1/drones/available` 获取可认领设备 | drone | `drone.view_drone` | 读共享池 |
| 直播操作（start/stop/video-quality/video-source） | drone | `drone.manage_drone` | 同上 |
| 设备解绑/转移归属 | drone | `drone.manage_drone` | 同上 |
| `POST /api/v1/missions` 创建任务 | mission | `mission.manage_mission` | 租户内操作 |
| `DELETE /api/v1/routes/{id}` 删除航线 | route | `route.manage_route` | 租户内操作 |
| 查看审计日志 | access | `access.view_auth_audit_logs` | 复用现有 |

### 2.2 系统身份操作（无需用户权限）

| 操作 | 身份 | 说明 |
|------|------|------|
| 共享池同步 | Celery 系统身份 | 全局任务，不区分 tenant |
| 任务状态同步 | Celery 系统身份 | 全局任务，按需关联 tenant |
| 媒体列表同步 | Celery 系统身份 | 全局任务，按需关联 tenant |
| DJI 回调处理 | 系统身份 | 不校验用户权限 |

### 2.3 权限归属总结表

| 操作 | 归属模块 | 权限 | 身份 |
|------|----------|------|------|
| `POST /drones` 认领 | drone | `drone.manage_drone` | tenant_member |
| `GET /drones/available` | drone | `drone.view_drone` | tenant_member |
| 直播操作 | drone | `drone.manage_drone` | tenant_member |
| `POST /missions` 创建 | mission | `mission.manage_mission` | tenant_member |
| 共享池同步 | system | 无（Celery） | system |
| 回调处理 | system | 无 | system |
| 审计日志 | access | `access.view_auth_audit_logs` | tenant_member |

---

## 3. ScopeType（保持不变）

> **核心原则**：不需要新增 PLATFORM 作用域，保持现有三值。

```python
# 当前 ScopeType（不变）
class ScopeType(models.TextChoices):
    ALL = "ALL", "ALL"        # 全量可见
    OWN = "OWN", "OWN"         # 自己创建的
    ASSIGNED = "ASSIGNED"      # 被分配给我的
```

**理由**：
- `dji_integration.*` 不存在了，不需要 PLATFORM 作用域
- 共享池同步是系统身份执行，不需要 ScopeType
- 设备认领带 `tenant_id`，自然就是租户内操作

---

## 4. 平台管理员（platform_admin）限制

> **核心原则**：现有约束不变，DJI 集成不暴露为独立模块，platform_admin 无感知。

| 身份 | 业务 API | 说明 |
|------|----------|------|
| `platform_admin=True` | 拒绝 | 现有约束，不可访问租户业务接口 |
| `is_superuser=True` | 拒绝 | 平台管理员不能是 superuser |
| `TenantMember` | 按权限 | 按 `drone.manage_drone` 等业务权限 |

**理由**：
- 现有约束 `platform_admin` 不能绑定 `TenantMember`，自然无法访问租户内业务 API
- DJI 集成不暴露为独立模块，`platform_admin` 无感知

---

## 5. 认领校验
1. tenant 不能直接占用共享池设备，必须走认领。
2. tenant 管理员通过 `POST /api/v1/drones` 认领设备，传入 `device_sn`。
3. 认领时必须校验 `device_sn` 是否存在于共享池（`DjiDeviceIndex`）、是否已被其他 tenant 认领。
4. 校验通过后写入 `Drone` 表（`tenant_id + device_sn` 组合唯一），并记录审计日志。
5. 同一 `device_sn` 在任一时刻只能归属于一个 tenant；解绑或转移归属必须走显式管理流程。
6. `bind_code` 只可作为平台内部辅助信息，不能直接作为设备认领凭据。
7. `device_sn` 只用于标识认领目标，不替代正式权限与 tenant 校验。

---

## 6. 准入公式

```
允许 = 账号状态通过 AND tenant上下文通过 AND 本地权限命中 AND 认领/绑定命中 AND 上游调用通过
```

### 6.1 上游调用失败处理

> **核心原则**：统一返回 `D0001`（依赖服务错误），不暴露 DJI 内部细节。

| 上游状态 | 我方返回 | 说明 |
|----------|----------|------|
| DJI 接口成功 | 透传结果 | 正常流程 |
| DJI 接口失败 | `D0001` | 不区分具体错误类型 |
| DJI Token 失效 | `D0001` | 服务端自动重试，不暴露给前端 |

**理由**：
- 上游 DJI 失败不应暴露给前端（避免泄露 DJI 内部）
- `A0401` 是"未登录"语义，不适合后端代理场景
- `D0001` 表示"依赖服务错误"，最通用

---

## 7. 关键表

> **核心原则**：简化命名，与 BFF 文档保持一致。

| 表名 | 说明 | tenant_id | 唯一约束 |
|------|------|-----------|----------|
| `DjiDeviceIndex(device_sn, last_payload, last_seen_at)` | 共享池设备快照 | 无（全局） | device_sn PK |
| `TenantRouteIndex(tenant_id, route_id, dji_wayline_id, sync_status, last_sync_at)` | 航线索引映射 | 有 | tenant + route_id |
| `TenantMissionIndex(tenant_id, mission_id, dji_job_id, execution_status, last_sync_at)` | 任务索引映射 | 有 | tenant + mission_id |
| `TenantMediaIndex(tenant_id, media_id, dji_file_id, device_sn, mission_id, sync_status)` | 媒体索引映射 | 有 | tenant + media_id |
| `Drone(tenant_id, device_sn, code, name, status, ...)` | 已认领设备 | 有 | tenant + device_sn |

### 7.1 Drone 表字段变更

> **核心原则**：`serial_no` 重命名为 `device_sn`，与 DJI 设备 SN 统一。

```sql
-- Migration
RenameField('serial_no', 'device_sn')
-- 唯一约束变更
ALTER TABLE drones DROP CONSTRAINT uniq_drone_tenant_serial_no;
ALTER TABLE drones ADD CONSTRAINT uniq_drone_tenant_device_sn UNIQUE (tenant_id, device_sn);
```

| 变更前 | 变更后 | 说明 |
|--------|--------|------|
| `serial_no` 必填 | `device_sn` 必填 | 重命名 |
| `model` 必填 | 删除 | 从 DjiDeviceIndex 同步 |
| `name` 必填 | 可选 | 默认用 device_sn |

---

## 8. 认领流程与 Drone 表关系

### 8.1 两张表的关系

```
┌─────────────────────┐     device_sn      ┌─────────────────────┐
│   DjiDeviceIndex    │ ←───────────────── │       Drone         │
│   (共享池，全局)     │                    │   (已认领，租户内)   │
├─────────────────────┤                    ├─────────────────────┤
│ device_sn (PK)      │                    │ tenant_id (FK)      │
│ is_online           │                    │ device_sn           │
│ last_seen_at        │                    │ code                │
│ last_payload (JSON) │                    │ name                │
│ firmware_version    │                    │ status              │
│ firmware_status     │                    └─────────────────────┘
└─────────────────────┘

特点：
- DjiDeviceIndex：无 tenant_id（全局共享池）
- Drone：tenant_id + device_sn 唯一
- 认领时只写 Drone 表，不操作 DjiDeviceIndex
```

### 8.2 POST /api/v1/drones 请求体

> **核心原则**：合并认领流程与创建流程，统一语义。

```json
{
    "device_sn": "1581F7FVC252A00CJ5TT",  // 必填，来自共享池
    "code": "DJ-001",                      // 可选，本地展示用
    "name": "巡检机A"                       // 可选，默认用 device_sn
}
```

### 8.3 认领校验逻辑

```python
# POST /api/v1/drones 视图
class ClaimDroneView(APIView):
    def post(self, request):
        # 1. 从 header 获取 tenant 上下文
        tenant = request.tenant_context.tenant  # 从 middleware 获取

        # 2. 校验 device_sn 存在
        device_sn = request.data["device_sn"]
        if not DjiDeviceIndex.objects.filter(device_sn=device_sn).exists():
            raise ValidationError({"device_sn": "设备不存在于共享池"})

        # 3. 校验 device_sn 尚未被认领
        if Drone.objects.filter(device_sn=device_sn).exists():
            raise ValidationError({"device_sn": "设备已被其他租户认领"})

        # 4. 创建 Drone 记录
        drone = Drone.objects.create(
            tenant=tenant,
            device_sn=device_sn,
            code=request.data.get("code", device_sn),
            name=request.data.get("name", device_sn),
        )

        # 5. 记录审计日志
        log_action(
            request=request,
            action="DRONE_CLAIM",
            target_type="Drone",
            target_id=drone.id,
        )

        return Response(DroneReadSerializer(drone).data)
```

---

## 9. Mission.dji_job_id 存储

> **核心原则**：同时存储在 Mission 表和 TenantMissionIndex。

### 9.1 Mission 模型新增字段

```python
class Mission(models.Model):
    # ... 现有字段 ...
    dji_job_id = models.CharField(
        "DJI Job ID",
        max_length=64,
        null=True,
        blank=True,
        help_text="DJI 云端任务 ID，用于 cancel 操作"
    )
```

### 9.2 TenantMissionIndex 结构

```python
class TenantMissionIndex(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    mission = models.ForeignKey(Mission, on_delete=models.CASCADE)
    dji_job_id = models.CharField("DJI Job ID", max_length=64)
    execution_status = models.CharField(...)  # DJI 任务状态
    sync_status = models.CharField(...)        # PENDING/SYNCED/ERROR
    last_sync_at = models.DateTimeField(auto_now=True)
```

### 9.3 cancel 流程

```python
def cancel_mission(mission_id, tenant):
    mission = Mission.objects.get(id=mission_id, tenant=tenant)

    if not mission.dji_job_id:
        raise ValidationError("任务尚未同步到 DJI，无法取消")

    # 1. 调用 DJI DELETE /jobs?job_id=xxx
    gateway.delete_job(mission.dji_job_id)

    # 2. 任务状态由后台同步更新，不在这里改
    # 3. 记录审计日志
    log_action(
        action="MISSION_CANCEL",
        target_type="Mission",
        target_id=mission.id,
    )
```

---

## 10. 回调处理

> **核心原则**：不做签名校验，只记录，不回写业务表。

### 10.1 安全措施

| 安全措施 | 说明 |
|----------|------|
| 不做签名校验 | 设计文档未提，无此能力 |
| 回调只读不回写 | 不暴露写入接口 |
| 未认领设备忽略 | `Drone.objects.filter(device_sn=...).first()` |
| 审计日志 | 记录 device_sn + event_type + timestamp |

### 10.2 回调处理流程

```python
# apps/dji_bff/views.py
class DjiCallbackView(APIView):
    permission_classes = []  # 无需认证
    authentication_classes = []  # 无需认证

    def post(self, request):
        device_sn = request.data.get("device_sn")
        event_type = request.data.get("event_type")

        # 1. 反查认领记录
        drone = Drone.objects.filter(device_sn=device_sn).first()
        if not drone:
            return Response(status=204)  # 未认领，忽略

        # 2. 只记录，不做业务处理
        AuditLog.objects.create(
            tenant=drone.tenant,
            action=f"dji.callback.{event_type}",
            target_type="Drone",
            target_id=str(drone.id),
            before_data=None,
            after_data=request.data,
        )

        return Response(status=200)
```

**理由**：
- DJI 回调不带 tenant 信息，通过 `device_sn → Drone(tenant_id)` 反查是最直接方案
- 不做签名校验（无此能力），只做记录

---

## 11. Celery 后台同步任务

> **核心原则**：全局任务，不按 tenant；系统身份执行，不校验用户权限。

### 11.1 共享池同步任务

```python
# apps/dji_bff/tasks.py

@shared_task
def sync_device_pool():
    """全局同步共享池：每 60s 执行一次"""
    gateway = DjiGateway()
    devices = gateway.get_devices()
    DjiDeviceIndex.objects.sync_batch(devices)
```

**特点**：
- 一个同步任务，不区分 tenant
- `DjiDeviceIndex` 无 `tenant_id`（全局共享池）
- 不经过 API 层，直接调用 `DjiGateway`

### 11.2 任务/媒体同步任务

```python
@shared_task
def sync_missions():
    """同步所有 tenant 的任务状态：每 30s 执行一次"""
    gateway = DjiGateway()
    jobs = gateway.get_jobs()

    # 按 tenant 分组处理
    for tenant_id, job_list in group_by_tenant(jobs).items():
        TenantMissionIndex.objects.sync_batch(tenant_id, job_list)


@shared_task
def sync_media():
    """同步所有 tenant 的媒体列表：每 5min 执行一次"""
    gateway = DjiGateway()
    files = gateway.get_media_files()

    # 按 tenant 分组处理
    for tenant_id, file_list in group_by_tenant(files).items():
        TenantMediaIndex.objects.sync_batch(tenant_id, file_list)
```

**特点**：
- 任务和媒体同步时需要关联 tenant（通过 `DjiDeviceIndex.device_sn → Drone.tenant_id`）
- `group_by_tenant()` 按 device_sn 反查 tenant

### 11.3 任务分组逻辑

```python
def group_by_tenant(items: list[dict]) -> dict[int, list[dict]]:
    """按 device_sn 反查 tenant_id，分组"""
    device_sns = [item["device_sn"] for item in items]
    drones = Drone.objects.filter(device_sn__in=device_sns).select_related("tenant")

    # 构建 device_sn → tenant_id 映射
    sn_to_tenant = {drone.device_sn: drone.tenant_id for drone in drones}

    # 按 tenant 分组
    result = defaultdict(list)
    for item in items:
        tenant_id = sn_to_tenant.get(item["device_sn"])
        if tenant_id:
            result[tenant_id].append(item)

    return dict(result)
```

---

## 12. 审计日志

> **核心原则**：复用现有 `view_auth_audit_logs`，不新增 DJI 专属权限。

| 操作 | 审计字段 | 权限 |
|------|----------|------|
| 设备认领 | `action="DRONE_CLAIM"`, `tenant_id=X`, `target_id=drone_id` | `access.view_auth_audit_logs` |
| 直播操作 | `action="DRONE_LIVE_START"`, `target_id=device_sn` | `access.view_auth_audit_logs` |
| 任务创建 | `action="MISSION_CREATE"`, `tenant_id=X`, `target_id=mission_id` | `access.view_auth_audit_logs` |
| DJI 回调 | `action="dji.callback.{event_type}"`, `tenant_id=X` | `access.view_auth_audit_logs` |
| 同步任务 | 系统执行 | 不记用户审计日志 |

**理由**：
- `view_auth_audit_logs` 已存在，足够查看所有审计日志
- DJI 操作记入 `AuditLog.actor_user=system`，不记具体用户
- 不需要按模块拆分的审计权限

---

## 13. 强约束
1. 不对前端暴露真实 `workspace_id`。
2. 不允许浏览器持有 DJI `x-auth-token`。
3. 未认领设备不得进入 tenant 业务接口。
4. 不允许直接返回上游共享列表，必须先按 tenant 认领结果过滤。
5. 回调不得直接写业务表，必须先反查 binding 再处理。
6. 直播、DRC、遥控、控制权抢占必须写审计日志。
7. `device_sn` 不能单独作为授权凭据；通过 `device_sn` 找到设备后，仍必须继续做 tenant 绑定校验与权限校验。
8. 设备解绑、转移归属、回收认领都必须走显式管理流程，不能靠前端自行覆盖绑定关系。

---

## 14. 不需要的设计（YAGNI）

> 以下是原本规划但实际不需要的设计，按 YAGNI 原则排除。

| 原本规划 | 实际不需要 | 理由 |
|----------|------------|------|
| `dji_integration.*` 权限模块 | 不新增 | 当前只有 DJI 一个上游，过度设计 |
| `ScopeType.PLATFORM` | 不需要 | 共享池同步是系统身份，不需要 Scope |
| `platform_admin` 的 DJI 权限 | 不暴露 | DJI 集成不作为独立模块暴露 |
| 独立的事件日志权限 | 复用现有 | `access.view_auth_audit_logs` 已足够 |
| 回调签名校验 | 不需要 | 无此能力，只记录 |
| 平台管理员身份 | 不需要 | Celery 任务直接调用 Gateway，不经过 API 层 |
| 多 tenant 同步任务 | 不需要 | 一个全局任务，按 device_sn 反查 tenant |

---

## 15. 与 BFF 文档的关联

| 本文档（权限层） | BFF 文档（接入层） | 说明 |
|-----------------|-------------------|------|
| 权限归属 | API 路由 | 保持一致 |
| ScopeType | 作用域规则 | 保持一致 |
| 认领校验 | 设备认领流程 | 保持一致 |
| 准入公式 | 错误处理 | 保持一致 |
| 系统身份 | Celery 任务 | 保持一致 |
| Drone.device_sn | 共享池关联 | serial_no 重命名 |

---

## 16. 一句话结论

所有 DJI 相关操作归属到现有业务模块权限（`drone.manage_drone` 等），不新增 `dji_integration` 权限体系；共享池同步和回调处理使用系统身份，不经过 API 层，不校验用户权限；`Drone.serial_no` 重命名为 `device_sn`，与 DJI 设备 SN 统一。
