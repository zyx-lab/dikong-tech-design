from dataclasses import dataclass


@dataclass(frozen=True)
class PermissionSpec:
    code: str
    name: str

    @property
    def parts(self) -> tuple[str, str, str]:
        domain, resource, action = self.code.split(":", 2)
        return domain, resource, action


@dataclass(frozen=True)
class RoleSpec:
    code: str
    name: str
    data_scope: str
    permissions: tuple[str, ...]
    is_system: bool = True
    is_super_admin: bool = False
    assignable_by_department_admin: bool = False
    sort: int = 100
    remark: str = ""


@dataclass(frozen=True)
class MenuSpec:
    code: str
    name: str
    menu_type: str
    parent_code: str = ""
    path: str = ""
    component: str = ""
    icon: str = ""
    sort: int = 100
    permissions: tuple[str, ...] = ()


PERMISSION_SPECS: tuple[PermissionSpec, ...] = (
    PermissionSpec("iam:department:read", "查看部门"),
    PermissionSpec("iam:department:create", "创建部门"),
    PermissionSpec("iam:department:update", "更新部门"),
    PermissionSpec("iam:department:enable", "启用部门"),
    PermissionSpec("iam:department:disable", "禁用部门"),
    PermissionSpec("iam:account:read", "查看账号"),
    PermissionSpec("iam:account:create", "创建账号"),
    PermissionSpec("iam:account:update", "更新账号"),
    PermissionSpec("iam:account:delete", "删除账号"),
    PermissionSpec("iam:account:enable", "启用账号"),
    PermissionSpec("iam:account:disable", "禁用账号"),
    PermissionSpec("iam:account:reset_password", "重置账号密码"),
    PermissionSpec("iam:account:assign_role", "分配账号角色"),
    PermissionSpec("iam:account:assign_post", "分配账号岗位"),
    PermissionSpec("iam:profile_type:read", "查看账号档案类型"),
    PermissionSpec("iam:profile_type:create", "创建账号档案类型"),
    PermissionSpec("iam:profile_type:update", "更新账号档案类型"),
    PermissionSpec("iam:profile_type:delete", "删除账号档案类型"),
    PermissionSpec("iam:account_profile:read", "查看账号角色档案"),
    PermissionSpec("iam:account_profile:create", "创建账号角色档案"),
    PermissionSpec("iam:account_profile:update", "更新账号角色档案"),
    PermissionSpec("iam:account_profile:delete", "删除账号角色档案"),
    PermissionSpec("iam:account_qualification:read", "查看账号资质"),
    PermissionSpec("iam:account_qualification:create", "创建账号资质"),
    PermissionSpec("iam:account_qualification:update", "更新账号资质"),
    PermissionSpec("iam:account_qualification:delete", "删除账号资质"),
    PermissionSpec("iam:role:read", "查看角色"),
    PermissionSpec("iam:role:create", "创建角色"),
    PermissionSpec("iam:role:update", "更新角色"),
    PermissionSpec("iam:role:delete", "删除角色"),
    PermissionSpec("iam:role:assign_permission", "角色授权权限"),
    PermissionSpec("iam:role:assign_menu", "角色授权菜单"),
    PermissionSpec("iam:role:update_data_scope", "更新角色数据范围"),
    PermissionSpec("iam:permission:read", "查看权限点"),
    PermissionSpec("iam:permission:sync", "同步权限点"),
    PermissionSpec("iam:post:read", "查看岗位"),
    PermissionSpec("iam:post:create", "创建岗位"),
    PermissionSpec("iam:post:update", "更新岗位"),
    PermissionSpec("iam:post:delete", "删除岗位"),
    PermissionSpec("system:menu:read", "查看菜单"),
    PermissionSpec("system:menu:create", "创建菜单"),
    PermissionSpec("system:menu:update", "更新菜单"),
    PermissionSpec("system:menu:delete", "删除菜单"),
    PermissionSpec("system:dict:read", "查看字典"),
    PermissionSpec("system:dict:create", "创建字典"),
    PermissionSpec("system:dict:update", "更新字典"),
    PermissionSpec("system:dict:delete", "删除字典"),
    PermissionSpec("system:config:read", "查看系统配置"),
    PermissionSpec("system:config:create", "创建系统配置"),
    PermissionSpec("system:config:update", "更新系统配置"),
    PermissionSpec("system:config:delete", "删除系统配置"),
    PermissionSpec("system:login_log:read", "查看登录日志"),
    PermissionSpec("system:operation_log:read", "查看操作日志"),
    PermissionSpec("system:file_log:read", "查看文件日志"),
    PermissionSpec("resource:dji_connection:read", "查看 DJI 连接"),
    PermissionSpec("resource:dji_connection:create", "创建 DJI 连接"),
    PermissionSpec("resource:dji_connection:update", "更新 DJI 连接"),
    PermissionSpec("resource:dji_connection:discover", "发现 DJI 资源"),
    PermissionSpec("resource:dji_connection:view_credentials", "查看 DJI 明文凭据"),
    PermissionSpec("resource:mqtt_health:read", "查看 MQTT 健康状态"),
    PermissionSpec("resource:mqtt_message:read", "查看 MQTT 消息"),
    PermissionSpec("resource:drone:read", "查看无人机资源"),
    PermissionSpec("resource:dock:read", "查看机场资源"),
    PermissionSpec("resource:gateway:read", "查看网关资源"),
    PermissionSpec("resource:payload:read", "查看负载资源"),
    PermissionSpec("resource:summary:read", "查看资源总览"),
    PermissionSpec("resource:binding:create", "绑定资源"),
    PermissionSpec("resource:binding:delete", "解绑资源"),
    PermissionSpec("resource:share_group:read", "查看资源共享组"),
    PermissionSpec("resource:share_group:create", "创建资源共享组"),
    PermissionSpec("resource:share_group:update", "更新资源共享组"),
    PermissionSpec("resource:share_group:delete", "删除资源共享组"),
    PermissionSpec("resource:share_group:manage_target", "维护共享目标部门"),
    PermissionSpec("resource:share_group:manage_resource", "维护共享资源"),
    PermissionSpec("inspection:route:read", "查看巡检航线"),
    PermissionSpec("inspection:route:create", "创建巡检航线"),
    PermissionSpec("inspection:route:update", "更新巡检航线"),
    PermissionSpec("inspection:mission:read", "查看巡检任务"),
    PermissionSpec("inspection:mission:create", "创建巡检任务"),
    PermissionSpec("inspection:mission:update", "更新巡检任务"),
    PermissionSpec("inspection:mission:start", "启动巡检任务"),
    PermissionSpec("inspection:mission:complete", "完成巡检任务"),
    PermissionSpec("inspection:mission:cancel", "取消巡检任务"),
    PermissionSpec("inspection:mission:fail", "标记巡检失败"),
    PermissionSpec("inspection:mission:abort", "安全中止巡检"),
    PermissionSpec("inspection:flight:read", "查看活动飞行"),
    PermissionSpec("inspection:telemetry:read", "查看遥测"),
    PermissionSpec("inspection:telemetry:create", "上报遥测"),
    PermissionSpec("inspection:live:read", "查看直播能力"),
    PermissionSpec("inspection:live:start", "启动直播"),
    PermissionSpec("inspection:live:stop", "停止直播"),
    PermissionSpec("inspection:live:update", "更新直播"),
    PermissionSpec("inspection:live:switch", "切换直播"),
    PermissionSpec("inspection:flight_record:read", "查看飞行记录"),
    PermissionSpec("inspection:flight_record:update", "更新飞行记录"),
    PermissionSpec("inspection:flight_record:refresh_media", "刷新飞行媒体"),
    PermissionSpec("inspection:media_file:read", "查看媒体文件"),
)

PERMISSION_CODES = frozenset(spec.code for spec in PERMISSION_SPECS)

ROLE_PLATFORM_SUPER_ADMIN = "platform_super_admin"
ROLE_DEPARTMENT_ADMIN = "department_admin"
ROLE_TASK_MONITOR_DISPATCHER = "task_monitor_dispatcher"
ROLE_PILOT = "pilot"
ROLE_WORK_ORDER_HANDLER = "work_order_handler"

DATA_SCOPE_ALL = "ALL"
DATA_SCOPE_DEPT_AND_CHILDREN = "DEPT_AND_CHILDREN"
DATA_SCOPE_SELF = "SELF"

_DEPARTMENT_ADMIN_PERMISSIONS = tuple(
    code
    for code in PERMISSION_CODES
    if code
    not in {
        "iam:department:create",
        "iam:department:update",
        "iam:department:enable",
        "iam:department:disable",
        "iam:profile_type:create",
        "iam:profile_type:update",
        "iam:profile_type:delete",
        "iam:role:create",
        "iam:role:update",
        "iam:role:delete",
        "iam:role:assign_permission",
        "iam:role:assign_menu",
        "iam:role:update_data_scope",
        "iam:permission:sync",
        "system:menu:create",
        "system:menu:update",
        "system:menu:delete",
        "system:dict:create",
        "system:dict:update",
        "system:dict:delete",
        "system:config:create",
        "system:config:update",
        "system:config:delete",
        "system:login_log:read",
        "system:file_log:read",
    }
)

ROLE_SPECS: tuple[RoleSpec, ...] = (
    RoleSpec(
        code=ROLE_PLATFORM_SUPER_ADMIN,
        name="平台超级管理员",
        data_scope=DATA_SCOPE_ALL,
        permissions=tuple(sorted(PERMISSION_CODES)),
        is_super_admin=True,
        sort=10,
        remark="内置超管角色，自动拥有全部权限和 ALL 数据范围。",
    ),
    RoleSpec(
        code=ROLE_DEPARTMENT_ADMIN,
        name="部门管理员",
        data_scope=DATA_SCOPE_DEPT_AND_CHILDREN,
        permissions=tuple(sorted(_DEPARTMENT_ADMIN_PERMISSIONS)),
        sort=20,
        remark="部门管理员默认管理本部门及下级的用户、岗位分配、资源共享和相关日志。",
    ),
    RoleSpec(
        code=ROLE_TASK_MONITOR_DISPATCHER,
        name="任务监控调度员",
        data_scope=DATA_SCOPE_DEPT_AND_CHILDREN,
        permissions=(
            "iam:department:read",
            "iam:account:read",
            "iam:account_profile:read",
            "iam:account_qualification:read",
            "resource:dji_connection:read",
            "resource:mqtt_message:read",
            "resource:drone:read",
            "resource:dock:read",
            "resource:gateway:read",
            "resource:payload:read",
            "resource:summary:read",
            "inspection:route:read",
            "inspection:route:create",
            "inspection:route:update",
            "inspection:mission:read",
            "inspection:mission:create",
            "inspection:mission:update",
            "inspection:mission:start",
            "inspection:mission:cancel",
            "inspection:mission:abort",
            "inspection:flight:read",
            "inspection:telemetry:read",
            "inspection:live:read",
            "inspection:live:start",
            "inspection:live:stop",
            "inspection:live:update",
            "inspection:live:switch",
            "inspection:flight_record:read",
            "inspection:media_file:read",
        ),
        assignable_by_department_admin=True,
        sort=30,
    ),
    RoleSpec(
        code=ROLE_PILOT,
        name="飞手",
        data_scope=DATA_SCOPE_SELF,
        permissions=(
            "iam:department:read",
            "iam:account_profile:read",
            "iam:account_qualification:read",
            "resource:drone:read",
            "resource:dock:read",
            "resource:gateway:read",
            "resource:payload:read",
            "inspection:route:read",
            "inspection:mission:read",
            "inspection:mission:start",
            "inspection:mission:complete",
            "inspection:mission:abort",
            "inspection:flight:read",
            "inspection:telemetry:read",
            "inspection:live:read",
            "inspection:flight_record:read",
            "inspection:media_file:read",
        ),
        assignable_by_department_admin=True,
        sort=40,
    ),
    RoleSpec(
        code=ROLE_WORK_ORDER_HANDLER,
        name="工单处理员",
        data_scope=DATA_SCOPE_DEPT_AND_CHILDREN,
        permissions=(
            "iam:department:read",
            "iam:account_profile:read",
            "iam:account_qualification:read",
            "resource:drone:read",
            "resource:dock:read",
            "resource:gateway:read",
            "resource:payload:read",
            "inspection:route:read",
            "inspection:mission:read",
            "inspection:flight_record:read",
            "inspection:media_file:read",
        ),
        assignable_by_department_admin=True,
        sort=50,
    ),
)

MENU_SPECS: tuple[MenuSpec, ...] = (
    MenuSpec("iam", "身份权限", "DIRECTORY", icon="shield", sort=10),
    MenuSpec("iam.departments", "部门管理", "MENU", parent_code="iam", path="/iam/departments", sort=10, permissions=("iam:department:read",)),
    MenuSpec("iam.accounts", "用户管理", "MENU", parent_code="iam", path="/iam/accounts", sort=20, permissions=("iam:account:read",)),
    MenuSpec("iam.accounts.create", "新增", "BUTTON", parent_code="iam.accounts", sort=10, permissions=("iam:account:create",)),
    MenuSpec("iam.accounts.assign_role", "分配角色", "BUTTON", parent_code="iam.accounts", sort=20, permissions=("iam:account:assign_role",)),
    MenuSpec("iam.accounts.reset_password", "重置密码", "BUTTON", parent_code="iam.accounts", sort=30, permissions=("iam:account:reset_password",)),
    MenuSpec("iam.roles", "角色管理", "MENU", parent_code="iam", path="/iam/roles", sort=30, permissions=("iam:role:read",)),
    MenuSpec("iam.permissions", "权限点", "MENU", parent_code="iam", path="/iam/permissions", sort=40, permissions=("iam:permission:read",)),
    MenuSpec("resource", "资源管理", "DIRECTORY", icon="boxes", sort=20),
    MenuSpec("resource.dji_connections", "DJI 连接", "MENU", parent_code="resource", path="/resource/dji-connections", sort=10, permissions=("resource:dji_connection:read",)),
    MenuSpec("resource.bindings", "资源绑定", "MENU", parent_code="resource", path="/resource/bindings", sort=20, permissions=("resource:drone:read", "resource:dock:read", "resource:gateway:read", "resource:payload:read")),
    MenuSpec("resource.share_groups", "资源共享组", "MENU", parent_code="resource", path="/resource/share-groups", sort=30, permissions=("resource:share_group:read",)),
    MenuSpec("resource.share_groups.create", "新增", "BUTTON", parent_code="resource.share_groups", sort=10, permissions=("resource:share_group:create",)),
    MenuSpec("inspection", "巡检作业", "DIRECTORY", icon="route", sort=40),
    MenuSpec("inspection.routes", "巡检航线", "MENU", parent_code="inspection", path="/inspection/routes", sort=10, permissions=("inspection:route:read",)),
    MenuSpec("inspection.missions", "巡检任务", "MENU", parent_code="inspection", path="/inspection/missions", sort=20, permissions=("inspection:mission:read",)),
    MenuSpec("inspection.flight_records", "飞行记录", "MENU", parent_code="inspection", path="/inspection/flight-records", sort=30, permissions=("inspection:flight_record:read",)),
    MenuSpec("system", "系统管理", "DIRECTORY", icon="settings", sort=50),
    MenuSpec("system.menus", "菜单管理", "MENU", parent_code="system", path="/system/menus", sort=10, permissions=("system:menu:read",)),
    MenuSpec("system.operation_logs", "操作日志", "MENU", parent_code="system", path="/system/operation-logs", sort=20, permissions=("system:operation_log:read",)),
)
