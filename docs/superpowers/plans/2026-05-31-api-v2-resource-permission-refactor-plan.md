# API v2 Resource Permission Refactor Plan

## Summary

Refactor the project to match `docs/superpowers/specs/2026-05-29-api-v2-resource-permission-design.md` with the confirmed scope:

- Implement the first vertical slice, not the entire design in one change.
- Hard-cut `/api/v2/` to the new IAM/resource boundary.
- Do not reuse old `/api/v2/drones|routes|missions|flight-records|media-files` behavior.
- Do not mount `apps.dji_bff.urls` under `/api/v2/`.
- Keep v1 and internal DJI bridge behavior intact.
- Use a new v2 account/role model; `platform_super_admin` is not derived from `User.is_platform_admin`.

The current environment could not run Django checks during planning because the active Python was 3.7 without Django installed. Implementation verification should run in the project intended Python 3.12/Docker environment.

## Key Changes

### v2 Routing And Docs

- Replace `apps/api_v2` with a routing/API aggregation layer only.
- Add v2 docs routes in `config.urls`:
  - `GET /api/v2/docs/`
  - `GET /api/v2/docs/schema/`
- Add a v2-only schema urlconf, equivalent in shape to the existing v1 schema urlconf, including only `path("api/v2/", include("apps.api_v2.urls"))`.
- Update the OpenAPI envelope hook so `code/msg/data` wrapping applies to `/api/v2/*` as well as `/api/v1/*`.
- Remove old v2 route registrations from the public v2 router:
  - `/api/v2/drones`
  - `/api/v2/routes`
  - `/api/v2/missions`
  - `/api/v2/flight-records`
  - `/api/v2/media-files`
  - old IAM-scoped DJI platform routes
- Keep `apps.dji_bff` installed and keep `/api/v1/__internal__/dji/*` unchanged for v1/internal use.

### New `apps/iam_v2` Domain

Create `apps/iam_v2` and add it to `INSTALLED_APPS`.

Core models:

- `Department`
  - Fields: internal compatibility boundary, `parent`, `name`, `status`, `path`, `depth`, `created_by_user`, timestamps.
  - `path` uses stable department IDs, for example `/1/2/8/`.
  - Create sets `path/depth` transactionally after the ID exists.
  - Parent changes are rejected; department movement is out of scope.
  - The root department is initialized by the system; public v2 APIs manage child departments.
- `V2AccountProfile`
  - One-to-one with `access.User`.
  - Fields: `department`, `status`, timestamps.
  - No v1 boundary header and no public boundary identifier for v2; the internal compatibility boundary is derived from the account department.
- `V2AccountRoleAssignment`
  - Fields: `account_profile`, `role_code`, `assigned_by_user`, timestamps.
  - `role_code` is one of:
    - `platform_super_admin`
    - `department_admin`
    - `task_monitor_dispatcher`
    - `pilot`
    - `work_order_handler`
  - Roles are fixed enum values, not a mutable role catalog table.
  - `platform_super_admin` is a platform identity; `GET /api/v2/iam/roles` exposes only the four department role codes.
- `ResourceShareGroup`
  - Owned by one department.
  - Fields: `owner_department`, `name`, `status`, timestamps.
- `ResourceShareGroupTargetDepartment`
  - Links a share group to target departments.
  - Targeting is exact-department membership for this slice; descendant inheritance for share targets is not added.

IAM API routes in scope:

- `GET /api/v2/iam/me/context`
- `GET /api/v2/iam/departments`
- `POST /api/v2/iam/departments`
- `PUT /api/v2/iam/departments/{id}`
- `POST /api/v2/iam/departments/{id}/enable`
- `POST /api/v2/iam/departments/{id}/disable`
- `GET /api/v2/iam/roles`

IAM authorization rules:

- Authentication still uses the existing Bearer Token/session mechanism.
- v2 authorization loads `V2AccountProfile` and `V2AccountRoleAssignment`.
- A user with only `User.is_platform_admin=True` does not get v2 super-admin rights.
- `platform_super_admin` can create/edit/enable/disable departments and query global audit logs.
- `department_admin` can manage only resources and connections owned by their own department.
- `department_admin` cannot manage child departments, grant `department_admin`, or grant `platform_super_admin`.

### New `apps/resource_v2` Domain

Create `apps/resource_v2` and add it to `INSTALLED_APPS`.

Core models:

- `DjiConnection`
  - Owned by one department.
  - Fields: `owner_department`, `name`, `base_url`, `username`, `password`, `login_flag`, `extra_params`, status, creator, timestamps.
  - Include DJI session/cache fields needed by the gateway: `workspace_id`, `dji_user_id`, `dji_username`, `dji_user_type`, `access_token`, `mqtt_username`, `mqtt_password`, `mqtt_addr`, `expires_at`, `last_checked_at`.
  - List responses redact credentials and tokens.
  - Detail responses redact credentials by default; `includeCredentials=true` returns plaintext credentials and writes an audit log.
- `DroneResource`
  - Global v2 drone identity.
  - Fields: `device_sn`, `name`, `model`, `online_status`, `firmware_version`, `firmware_status`, `last_payload`, `last_seen_at`, timestamps.
  - Unique by `device_sn`.
- `DockResource`
  - Global v2 dock identity.
  - Fields mirror drone where applicable: `device_sn` or dock SN, `name`, `model`, `online_status`, firmware/status payload fields, timestamps.
  - Unique by stable dock/device SN.
- `ResourceBinding`
  - Shared ownership policy table for drones and docks.
  - Fields: `resource_type`, `resource_object_id`, `owner_department`, `dji_connection`, `status`, `bound_by_user`, `bound_at`, `unbound_by_user`, `unbound_at`.
  - At most one active binding exists per `(resource_type, resource_object_id)`.
  - Rows are marked unbound, not deleted.
- `ResourceBindingHistory`
  - Append-only binding audit history.
  - Fields include `action_type`, `resource_type`, `resource_object_id`, stable resource identifier/SN, previous department, new department, DJI connection snapshot, actor, actor department, timestamp.
- `ResourceSharePermission`
  - Links share groups to individual resources.
  - Fields: `share_group`, `resource_type`, `resource_object_id`, `permissions`.
  - `permissions` is a JSON list constrained to:
    - `view`
    - `monitor`
    - `dispatch_task`
    - `review_task`
    - `edit_config`
  - `unbind` is never allowed in share permissions.
- `V2AuditLog`
  - Generic v2 audit log owned by `resource_v2`.
  - Fields: `action`, `actor_user`, `actor_department`, `resource_owner_department`, `resource_type`, `resource_object_id`, `target_type`, `target_id`, `before_data`, `after_data`, `ip`, `request_id`, `created_at`.

Resource API routes in scope:

- `GET /api/v2/resource/dji-connections`
- `POST /api/v2/resource/dji-connections`
- `GET /api/v2/resource/dji-connections/{id}`
- `PUT /api/v2/resource/dji-connections/{id}`
- `POST /api/v2/resource/dji-connections/{id}/discover`
- `GET /api/v2/resource/drones`
- `GET /api/v2/resource/docks`
- `POST /api/v2/resource/bindings`
- `DELETE /api/v2/resource/bindings/{id}`
- `GET /api/v2/resource/audit-logs`

Resource behavior:

- `POST /dji-connections`
  - `department_admin`: creates only for their own department.
  - `platform_super_admin`: may create/update connections for any active department.
  - Sensitive create/update actions write audit logs.
- `POST /dji-connections/{id}/discover`
  - Allowed for owning department admin or platform super admin.
  - Uses a v2 gateway adapter backed by `DjiConnection`, not `dji_bff.DjiCloudPlatform`.
  - Fetches drone and dock device lists by resource type/domain.
  - Upserts `DroneResource` and `DockResource` snapshots.
  - Returns discovered `drones` and `docks`.
- `POST /resource/bindings`
  - Request uses `resourceType`, `resourceId`, and `djiConnectionId`.
  - Only `department_admin` of the connection owning department can bind.
  - Platform super admin is not granted bind by default because the design only explicitly grants global unbind.
  - If another active binding exists, return `409` with occupying department information.
  - Creates active binding, binding history, and audit log.
- `DELETE /resource/bindings/{id}`
  - Allowed for `platform_super_admin`, or for `department_admin` when the binding owner is their own department.
  - Marks binding unbound and preserves history.
  - Shared permissions never grant unbind.
- `GET /resource/drones` and `GET /resource/docks`
  - Apply visibility at queryset/SQL level.
  - Visible sources:
    - Resources owned by the actor own department.
    - Resources owned by descendant departments, using `owner_department.path LIKE current.path + "%"`.
    - Resources shared to the actor exact department through share groups.
    - Platform super admin sees all active bindings.
  - Response includes resource fields, owner department, binding ID, and effective operation permissions.

Fixed role operation defaults for this slice:

- `platform_super_admin`: all v2 management permissions, including global unbind.
- `department_admin`: `view`, `monitor`, `dispatch_task`, `review_task`, `edit_config`, plus own-department bind/unbind.
- `task_monitor_dispatcher`: `view`, `monitor`, `dispatch_task`.
- `pilot`: `view`, `monitor`, `review_task`.
- `work_order_handler`: `view`, `review_task`.
- For cross-department shared resources, effective permissions are `fixed role permissions INTERSECT ResourceSharePermission.permissions`.

Gateway approach:

- Do not move old `dji_bff` models into v2.
- Keep `apps.dji_bff.gateway.DjiGateway` as the shared HTTP client foundation.
- Add a `resource_v2` gateway adapter/subclass that stores session fields on `DjiConnection` and exposes resource discovery by type.
- Extend mock/test support so dock discovery can be represented without changing v1 internal behavior.

## Test Plan

Write tests first, then implement.

Core API/schema tests:

- `/api/v2/docs/` returns 200.
- `/api/v2/docs/schema/` returns 200.
- v2 schema contains only `/api/v2/*` paths.
- v2 schema does not contain:
  - `/api/v2/__internal__/dji/*`
  - `/api/v2/drones`
  - `/api/v2/routes`
  - `/api/v2/missions`
  - `/api/v2/flight-records`
  - `/api/v2/media-files`
  - old IAM-scoped DJI platform routes

IAM tests:

- Department creation sets stable `path` and `depth`.
- Department parent movement is rejected.
- Platform super admin can create/rename/enable/disable departments.
- Department admin cannot create or manage child departments.
- `GET /api/v2/iam/me/context` returns user, department, and v2 fixed roles.
- A user with `User.is_platform_admin=True` but no v2 role is not a v2 super admin.
- `GET /api/v2/iam/roles` returns exactly the four department role codes and does not return `platform_super_admin`.

Resource model/API tests:

- Department admin can create and update own department DJI connections.
- Department admin cannot create/update another department DJI connection.
- Platform super admin can maintain all department connections.
- List/detail responses redact password and tokens by default.
- `includeCredentials=true` returns plaintext credentials only for authorized users and writes `view_plaintext_dji_credentials`.
- Discover uses the selected v2 `DjiConnection`, upserts drone/dock resources, and does not write to `DjiDeviceIndex`.
- Binding succeeds for an owning department admin.
- Binding the same active resource into another department returns 409 with occupying department data.
- Unbind marks the binding inactive, writes binding history, and allows later rebind.
- Department admin cannot unbind another department resource.
- Platform super admin can unbind any resource.

Visibility tests:

- Own department resources are visible.
- Parent department users see child department resources.
- Child department users do not see parent department resources.
- Share group target department users see shared resources.
- Effective shared permissions equal fixed role permissions intersected with share permissions.
- Visibility helpers return querysets and apply filters in SQL/queryset form, not by post-loading all resources into Python.

Audit tests:

- Required sensitive actions write `V2AuditLog` with both `actor_department` and, where applicable, `resource_owner_department`.
- Department admin default audit query filters `actor_department=current department`.
- Resource-specific audit query can filter by `resource_type/resource_object_id`.
- Platform super admin can query global audit logs.

Regression tests:

- Existing `/api/v1/*` tests continue to pass.
- Existing `/api/v1/__internal__/dji/*` behavior remains unchanged.
- Old v2 tests that assert legacy `/api/v2/drones|routes|...` behavior are removed or rewritten against the new v2 API.
- Run verification in Python 3.12 or Docker:
  - `python manage.py check`
  - `python manage.py makemigrations --check --dry-run` after committed migrations are generated
  - focused v2 test modules
  - existing v1/internal DJI regression tests

## Assumptions And Defaults

- This slice intentionally does not implement v2 account CRUD, role assignment APIs, share group CRUD APIs, or v2 business route/mission/media endpoints.
- New v2 account profiles and role assignments can be created in tests/admin/fixtures until account management APIs are implemented in a later slice.
- Existing v1 business tables are not migrated or replaced.
- Existing v1 boundary model remains as an internal compatibility detail; v2 department ownership is the real authorization boundary.
- Department movement is unsupported and actively rejected.
- Share group models are added now because visibility depends on them, but public share management APIs are deferred.
- Docks are represented as first-class v2 resources even though current local mock data mostly covers drones.
- Credentials remain plaintext because the design explicitly requires plaintext credential access; access is restricted and audited.
- The implementation should use the project existing `BusinessApiResponseMixin`, standard pagination, exception envelope, and Bearer authentication style.
