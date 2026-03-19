# Codebase Research: Current State (2026-03-19)

## High-level summary
This repository is a Django 5.1.6 + Django REST Framework 3.15.2 API service with drf-spectacular used for OpenAPI generation and Swagger UI exposure. The active HTTP surface is mounted under `/api/v1/`, with formal IAM endpoints under `/api/v1/iam/*`, business APIs under `/api/v1/*`, and OpenAPI docs exposed at `/api/v1/docs/` and `/api/v1/docs/schema/`. Request processing combines request ID middleware, tenant-header resolution, Bearer-token authentication backed by `AuthSession`, tenant-scoped authorization, and a standardized `code/msg/data` response envelope. The business implementation is split across seven apps: `drone`, `drone_assignment`, `route`, `waypoint`, `mission`, `flight_record`, and `media_file`. Automated tests exist for the shared API layer, IAM layer, and each business domain app.

## 1. Runtime entry points and repository structure
- The command-line entry point is `manage.py`, which sets `DJANGO_SETTINGS_MODULE=config.settings` and delegates to `execute_from_command_line`. `manage.py:1-22`
- Declared Python dependencies are Django 5.1.6, Django REST Framework 3.15.2, drf-spectacular 0.27.2, and psycopg 3.2.3. `requirements.txt:1-4`
- Django settings register the IAM app, shared API infrastructure app, and seven business apps in `INSTALLED_APPS`, and wire middleware, REST framework defaults, and Spectacular settings. `config/settings.py:10-40`, `config/settings.py:106-137`
- The default database is SQLite at `db.sqlite3`, with PostgreSQL selected when `DB_ENGINE=postgres`. `config/settings.py:62-80`
- The repository-level README documents the current API planes, endpoint inventory, response contract, and linked design documents. `README.md:3-37`, `README.md:51-155`, `README.md:156-260`

## 2. Global configuration and shared API infrastructure

### 2.1 URL surface and documentation endpoints
- Project URL configuration exposes `/admin/`, `/api/v1/docs/schema/`, `/api/v1/docs/`, and the `/api/v1/` API namespace. `config/urls.py:1-18`
- The schema route uses `SpectacularJSONAPIView.as_view(urlconf="config.business_api_urlconf")`, so schema generation follows the business URLconf. `config/urls.py:11-16`
- `config/business_api_urlconf.py` includes `apps.api_v1.urls` under `api/v1/`, mirroring the live route tree used by the application. `config/business_api_urlconf.py:1-5`
- `apps/api_v1/urls.py` mounts the API root, health endpoint, IAM routes, and all domain routers under `/api/v1/`. `apps/api_v1/urls.py:1-16`

### 2.2 API root and health endpoints
- `ApiV1RootView` is open to unauthenticated callers and returns the service name, version, and links for docs, schema, health, IAM, drones, drone assignments, routes, missions, and media files. `apps/api_v1/views.py:10-33`
- `ApiV1HealthView` is also open to unauthenticated callers and returns `{ "status": "ok", "service": "business-api-v1" }`. `apps/api_v1/views.py:36-43`

### 2.3 Standard response envelope
- `StandardCode` enumerates the standard business codes used across `/api/v1/*`, including success, authentication, authorization, validation, duplicate, state-conflict, resource-in-use, not-found, and internal-error codes. `apps/api_v1/business_response.py:10-22`
- `build_standard_response` converts raw success or error payloads into the unified `code/msg/data` envelope. `apps/api_v1/business_response.py:222-232`
- `BusinessApiResponseMixin.finalize_response` applies that envelope to DRF `Response` objects and adds `traceId` when available from the request. `apps/api_v1/business_response.py:239-253`

### 2.4 Pagination, tenant scoping, and schema post-processing
- `StandardPageNumberPagination` standardizes `pageNum` and `pageSize`, uses default size 20, caps at 100, and returns paginated payloads as `{ "list": ..., "total": ... }`. `apps/api_v1/pagination.py:5-36`
- `TenantScopedBusinessMixin` requires a tenant context on the request, blocks active platform admins from tenant business APIs, and scopes querysets by the configured tenant lookup field. `apps/api_v1/tenant_scope.py:5-40`
- `standardize_response_schema_hook` walks `/api/v1/*` OpenAPI operations and wraps JSON response schemas and examples into the same `code/msg/data` envelope used at runtime. `apps/api_v1/openapi_hooks.py:128-152`
- The hook uses `_strip_legacy_envelope`, `_wrap_schema`, and `_flatten_nested_list_items` to normalize component schemas before wrapping. `apps/api_v1/openapi_hooks.py:17-114`
- `apps/api_v1/tests.py` verifies the root/health envelope, docs availability, schema availability, schema path inventory, and documented examples/parameters for business endpoints. `apps/api_v1/tests.py:29-220`

## 3. Access / IAM subsystem

### 3.1 IAM data model
- `apps/access/models.py` defines the shared timestamp base class plus enums for directory status, user status, tenant status, tenant member status, scope type, and audit/action-related state. `apps/access/models.py:13-112`
- `User` is the custom auth model and stores status, platform-admin state, last login, and the one-to-one relationship to `StaffProfile`. `apps/access/models.py:113-171`
- `AuthSession` stores access/refresh token hashes, expiry timestamps, revocation timestamps, client metadata, and last-used fields. `apps/access/models.py:174-204`
- `StaffProfile` stores name, phone, email, staff number, employment status, and organization information for a user. `apps/access/models.py:205-236`
- `AuditLog` stores tenant, actor, action, target identifiers, request metadata, and before/after snapshots. `apps/access/models.py:239-278`
- `Permission`, `Role`, and `RolePermissionGrant` define the permission catalog and the role-to-permission grant table. `apps/access/models.py:280-404`
- `Tenant`, `TenantMember`, and `TenantMemberRole` define tenant entities, membership records, and granted tenant-role bindings. `apps/access/models.py:406-640`

### 3.2 Middleware, exception handling, and permission/scoping helpers
- `RequestContextMiddleware` creates or forwards the request ID and stores it on both the request and response. `apps/access/middleware.py:8-22`
- `TenantContextMiddleware` reads `X-TENANT-CODE`, stores `tenant_context_code`, resolves `request.tenant_context`, and enforces active tenant status for non-IAM `/api/v1/*` requests. `apps/access/middleware.py:25-52`
- `custom_exception_handler` wraps API errors into the business envelope and logs unhandled API exceptions under the `apps.access.exceptions` logger. `apps/access/exceptions.py:1-209`
- `PermissionMapMixin`, `RequireInternalPermission`, `ScopedActionPermission`, and `ScopedQuerysetMixin` provide action-to-permission mapping, request authorization checks, and scope-based queryset filtering. `apps/access/drf_permissions.py:1-125`
- `log_action`, `get_request_permission_codes`, `has_request_permission`, `apply_scope_to_queryset`, and `AuthzService.authorize` provide the shared audit and authorization logic used by IAM and business views. `apps/access/services.py:168-196`, `apps/access/services.py:336-528`

### 3.3 IAM API v1 infrastructure
- `BearerAuthSessionAuthentication` reads `Authorization: Bearer <token>`, hashes the incoming token, resolves a non-revoked/non-expired `AuthSession`, updates usage metadata, and returns `(user, session)`. `apps/access/api_v1/authentication.py:14-60`
- `IamAPIView` and `IamGenericAPIView` centralize Bearer authentication and pagination for IAM endpoints; `StrictSerializer` rejects unexpected request fields. `apps/access/api_v1/base.py:14-50`
- `apps/access/api_v1/openapi.py` defines shared IAM query/header parameter objects, standardized IAM error-response objects, the path parameter helper, and the `BearerAuthSessionScheme` OpenAPI authentication extension. `apps/access/api_v1/openapi.py:8-130`
- `apps/access/api_v1/urls.py` declares the formal IAM route tree under `session/*`, `me/*`, `tenant/*`, and `platform/*`. `apps/access/api_v1/urls.py:1-62`

### 3.4 Formal identity and tenant-request context
- `require_authenticated_formal_identity` classifies the current authenticated user into `unassigned`, `tenant_member`, or `platform_operator`. `apps/access/api_v1/context.py:50-67`
- `require_business_identity` rejects platform operators from business-account identity flows, and `require_platform_operator` requires platform-operator runtime. `apps/access/api_v1/context.py:70-81`
- `parse_tenant_code_header` validates `X-TENANT-CODE` against the code format regex, and `resolve_tenant_request_context` resolves the active tenant member plus granted role codes for the request. `apps/access/api_v1/context.py:84-129`
- `tenant_context_has_permission` checks whether the current tenant request context carries a given permission. `apps/access/api_v1/context.py:136-143`

### 3.5 Session endpoints and auth services
- Session routes are `POST /api/v1/iam/session/login`, `/refresh`, `/logout`, `/register`, and `/register-by-phone`. `apps/access/api_v1/urls.py:33-38`
- `SessionLoginView`, `SessionRefreshView`, `SessionLogoutView`, `SessionRegisterView`, and `SessionRegisterByPhoneView` implement those flows and document request/response examples through `extend_schema`. `apps/access/api_v1/views/session.py:260-483`
- `authenticate_formal_user`, `create_auth_session`, `refresh_auth_session`, `revoke_current_session`, `register_by_username`, and `register_by_phone` implement the session and registration lifecycle. `apps/access/api_v1/services/auth.py:19-161`
- `register_by_phone` validates the mock SMS code `123456` before creating the user and staff profile. `apps/access/api_v1/services/auth.py:143-153`

### 3.6 Me endpoints
- `GET /api/v1/iam/me/profile` and `GET /api/v1/iam/me/tenants` are declared in the IAM URLconf. `apps/access/api_v1/urls.py:39-40`
- `MeProfileView` returns the current business account’s global profile, while `MeTenantsView` returns the paginated list of ACTIVE tenant memberships for the current business account. `apps/access/api_v1/views/me.py:83-176`
- `MeProfileResponseSerializer` and `MeTenantsItemSerializer` define the documented response shapes for those endpoints. `apps/access/api_v1/serializers/me.py:1-11`

### 3.7 Tenant endpoints
- Tenant routes include `GET /api/v1/iam/tenant/me`, `GET/POST /api/v1/iam/tenant/members`, `GET/PATCH /api/v1/iam/tenant/members/{memberId}`, `PUT /api/v1/iam/tenant/members/{memberId}/roles`, `POST /api/v1/iam/tenant/members/{memberId}/enable`, `POST /api/v1/iam/tenant/members/{memberId}/disable`, `GET /api/v1/iam/tenant/roles`, and `GET /api/v1/iam/tenant/audit-logs`. `apps/access/api_v1/urls.py:41-48`
- `TenantMeView`, `TenantMembersView`, `TenantMemberDetailView`, `TenantRolesView`, `TenantMemberRolesReplaceView`, `TenantMemberEnableView`, `TenantMemberDisableView`, and `TenantAuditLogsView` implement those tenant-scope APIs. `apps/access/api_v1/views/tenant.py:240-743`
- `TenantMemberCreateSerializer`, `TenantMemberUpdateSerializer`, and `TenantMemberRolesReplaceSerializer` define strict tenant member request bodies. `apps/access/api_v1/serializers/tenant.py:1-36`
- `create_tenant_member`, `update_member_display_name`, `replace_member_roles`, `enable_member`, `disable_member`, and `initialize_tenant_admin` implement member creation, mutation, role replacement, and state transitions. `apps/access/api_v1/services/members.py:97-291`

### 3.8 Platform endpoints
- Platform routes include `GET /api/v1/iam/platform/permissions`, `GET /api/v1/iam/platform/roles`, `GET /api/v1/iam/platform/roles/{roleId}`, `GET /api/v1/iam/platform/audit-logs`, `GET/POST /api/v1/iam/platform/tenants`, `GET /api/v1/iam/platform/tenants/{tenantId}`, `POST /api/v1/iam/platform/tenants/{tenantId}/enable`, `POST /api/v1/iam/platform/tenants/{tenantId}/disable`, and `POST /api/v1/iam/platform/tenants/{tenantId}/initialize-admin`. `apps/access/api_v1/urls.py:49-61`
- `PlatformPermissionsView`, `PlatformRolesView`, `PlatformRoleDetailView`, `PlatformAuditLogsView`, `PlatformTenantsView`, `PlatformTenantDetailView`, `PlatformTenantEnableView`, `PlatformTenantDisableView`, and `PlatformTenantInitializeAdminView` implement the platform-scope APIs. `apps/access/api_v1/views/platform.py:211-757`
- `PlatformTenantCreateSerializer` and `PlatformInitializeAdminSerializer` define platform-side request bodies. `apps/access/api_v1/serializers/platform.py:1-18`
- `create_tenant`, `enable_tenant`, and `disable_tenant` implement the tenant entity lifecycle for platform scope. `apps/access/api_v1/services/tenants.py:8-74`

### 3.9 IAM tests
- `apps/access/tests.py` contains session, me-scope, tenant-scope, platform-scope, and IAM schema tests. `apps/access/tests.py:1-515`
- The tests exercise login/refresh/logout/register flows, me endpoints, tenant member flows, tenant audit logs, platform directories and tenant operations, and OpenAPI path presence. `apps/access/tests.py:1-515`

## 4. Business domain applications

### 4.1 Drone
- `Drone` is tenant-scoped and stores code, name, model, serial number, org identifier, creator member ID, and status values `ENABLED`, `DISABLED`, `MAINTENANCE`, and `RETIRED`. `apps/drone/models.py:12-74`
- The model enforces tenant-local uniqueness for `(tenant, code)` and `(tenant, serial_no)` and blocks leaving `RETIRED` once entered. `apps/drone/models.py:47-74`
- `DroneReadSerializer` and `DroneWriteSerializer` define read/write payloads and validate tenant-local uniqueness during writes. `apps/drone/serializers.py:7-63`
- `DroneViewSet` implements list, retrieve, create, update, partial update, destroy, status transitions (`enable`, `disable`, `maintenance`, `retire`), and assignment-related endpoints (`history`, `active_assignments`, `latest_assignment`). `apps/drone/views.py:337-856`
- Drone routes are registered under `/api/v1/drones`. `apps/drone/urls.py:1-8`
- `apps/drone/tests.py` includes authorization tests and write-flow tests for list/create/update/audit behavior. `apps/drone/tests.py:20-260`

### 4.2 Drone assignment
- `DroneAssignment` links a drone to a tenant member, carries status values `ACTIVE` and `INACTIVE`, and enforces tenant alignment plus uniqueness of active drone/member pairs. `apps/drone_assignment/models.py:14-94`
- The serializers validate same-tenant resources, active tenant members, non-retired drones, and pilot-role eligibility. `apps/drone_assignment/serializers.py:10-100`
- `DroneAssignmentViewSet` implements list, retrieve, create, `cancel`, and `reactivate`. `apps/drone_assignment/views.py:185-407`
- Drone-assignment routes are registered under `/api/v1/drone-assignments`. `apps/drone_assignment/urls.py:1-8`
- `apps/drone_assignment/tests.py` contains API tests covering assignment creation, state transitions, tenant constraints, and permission behavior. `apps/drone_assignment/tests.py:26-655`

### 4.3 Route
- `Route` stores tenant-scoped route metadata including route type, drone type, distance, duration, waypoint count, creator name, and status. `apps/route/models.py:13-58`
- `RouteReadSerializer` and `RouteWriteSerializer` define route read/write payloads. `apps/route/serializers.py:6-48`
- `RouteViewSet` implements list, retrieve, create, update, partial update, destroy, and status transitions (`enable`, `disable`). Delete behavior checks whether missions reference the route. `apps/route/views.py:239-585`
- Route routes are registered under `/api/v1/routes`. `apps/route/urls.py:1-8`
- `apps/route/tests.py` contains create/list/retrieve/filter/authz behavior and route delete/state cases. `apps/route/tests.py:24-260`

### 4.4 Waypoint
- `Waypoint` belongs to a route, stores sequence and coordinate fields, and enforces route-local uniqueness on `sequence`. `apps/waypoint/models.py:7-43`
- `WaypointCreateSerializer` and `WaypointUpdateSerializer` define waypoint request payloads. `apps/waypoint/serializers.py:23-95`
- `WaypointViewSet` implements list, retrieve, create, update, partial update, and destroy, and keeps `route.waypoint_count` synchronized after mutations. `apps/waypoint/views.py:205-393`
- Waypoint routes are registered under `/api/v1/waypoints`. `apps/waypoint/urls.py:1-8`
- `apps/waypoint/tests.py` contains API tests in `WaypointApiTests` and `WaypointBoundaryAndExtendedTests`. `apps/waypoint/tests.py:14-400`, `apps/waypoint/tests.py:515-700`

### 4.5 Mission
- `Mission` binds a tenant to a route, drone, and pilot tenant member and stores denormalized route/drone/pilot names, scheduling fields, remarks, and status values `PENDING`, `RUNNING`, `PAUSED`, `COMPLETED`, `CANCELED`, and `FAILED`. `apps/mission/models.py:18-98`
- `Mission.clean()` enforces tenant alignment, resource state checks, and allowed state transitions. `apps/mission/models.py:58-94`
- `MissionReadSerializer` and `MissionWriteSerializer` define response/request payloads and populate denormalized names. `apps/mission/serializers.py:19-115`
- `MissionViewSet` implements list, retrieve, create, update, partial update, and actions `start`, `pause`, `resume`, `complete`, `fail`, and `cancel`, with assigned-scope filtering bound to `pilot_id`. `apps/mission/views.py:280-813`
- Mission routes are registered under `/api/v1/missions`. `apps/mission/urls.py:1-8`
- `apps/mission/tests.py` contains API tests and pilot-scope tests. `apps/mission/tests.py:20-400`, `apps/mission/tests.py:1195-1400`

### 4.6 Flight record
- `FlightRecord` stores tenant-scoped flight metadata, optional mission linkage, drone and pilot references, denormalized names, timing/media counters, and status values `IN_PROGRESS`, `COMPLETED`, and `ABORTED`. `apps/flight_record/models.py:13-120`
- The model validates tenant alignment, mission/drone/pilot consistency, status transitions, and time ordering. `apps/flight_record/models.py:81-117`
- `FlightRecordReadSerializer` and `FlightRecordWriteSerializer` define payloads and enforce tenant and assigned-scope validations. `apps/flight_record/serializers.py:17-197`
- `FlightRecordViewSet` implements list, retrieve, create, update, partial update, and actions `complete` and `abort`, with assigned-scope filtering keyed by the pilot. `apps/flight_record/views.py:273-573`
- Flight-record routes are registered under `/api/v1/flight-records`. `apps/flight_record/urls.py:1-8`
- `apps/flight_record/tests.py` contains API tests and pilot-scope tests. `apps/flight_record/tests.py:24-400`, `apps/flight_record/tests.py:951-1200`

### 4.7 Media file
- `MediaFile` stores tenant-scoped media metadata, optional flight-record linkage, file metadata, capture metadata, and logical-delete fields `is_deleted` and `deleted_at`. `apps/media_file/models.py:10-67`
- `MediaFileReadSerializer` and `MediaFileWriteSerializer` define payloads and validate tenant and assigned-scope constraints. `apps/media_file/serializers.py:8-75`
- `MediaFileViewSet` implements list, retrieve, create, update, partial update, and logical delete in `destroy()`, and applies assigned-scope filtering through `flight_record__pilot_id`. `apps/media_file/views.py:249-454`
- Media-file routes are registered under `/api/v1/media-files`. `apps/media_file/urls.py:1-8`
- `apps/media_file/tests.py` contains API tests and pilot-scope tests. `apps/media_file/tests.py:25-400`, `apps/media_file/tests.py:607-800`

## 5. Cross-component connections and data flows

### 5.1 Request processing flow
1. Project routing sends traffic from `config/urls.py` into the `/api/v1/` tree defined in `apps/api_v1/urls.py`. `config/urls.py:9-17`, `apps/api_v1/urls.py:5-16`
2. `RequestContextMiddleware` attaches or forwards the request ID, and `TenantContextMiddleware` resolves `X-TENANT-CODE` into `request.tenant_context`. `apps/access/middleware.py:8-52`
3. DRF authentication uses `BearerAuthSessionAuthentication` to resolve the request user and session from an `AuthSession` token hash. `apps/access/api_v1/authentication.py:14-60`
4. IAM endpoints classify the caller with `require_authenticated_formal_identity`, `require_business_identity`, `require_platform_operator`, or `resolve_tenant_request_context`. `apps/access/api_v1/context.py:50-143`
5. Business viewsets use `TenantScopedBusinessMixin`, `PermissionMapMixin`, `ScopedActionPermission`, and `ScopedQuerysetMixin` to enforce tenant scoping and permission checks. `apps/api_v1/tenant_scope.py:25-40`, `apps/access/drf_permissions.py:1-125`
6. The final response payload is wrapped into the standard `code/msg/data` envelope by `BusinessApiResponseMixin.finalize_response`. `apps/api_v1/business_response.py:239-253`

### 5.2 Session and audit flow
1. Login and refresh flows create or rotate `AuthSession` rows with token hashes, expiries, and client metadata. `apps/access/api_v1/services/auth.py:50-120`
2. Logout and user-disable paths revoke sessions by setting `revoked_at`. `apps/access/api_v1/services/auth.py:123-129`, `apps/access/models.py:153-171`
3. IAM and business mutations record audit entries through `log_action`, which derives tenant, actor, request ID, and client metadata from the request when available. `apps/access/services.py:168-196`
4. Tenant and platform audit-log APIs expose those audit records through `/api/v1/iam/tenant/audit-logs` and `/api/v1/iam/platform/audit-logs`. `apps/access/api_v1/views/tenant.py:687-743`, `apps/access/api_v1/views/platform.py:348-419`

### 5.3 Domain relationship flow
- A route contains many waypoints through `Waypoint.route`, and waypoint mutations update `Route.waypoint_count`. `apps/waypoint/models.py:7-43`, `apps/waypoint/views.py:339-384`
- A mission references a route, drone, and pilot tenant member and stores denormalized route/drone/pilot names. `apps/mission/models.py:18-98`
- A flight record may reference a mission and separately references a drone and pilot, with model-level consistency checks among those links. `apps/flight_record/models.py:13-120`
- A media file may reference a flight record, and assigned-scope filtering derives pilot ownership from `flight_record__pilot_id`. `apps/media_file/models.py:19-57`, `apps/media_file/views.py:276-319`
- A drone assignment links a drone to a tenant member and is used by drone assignment history, active assignment, and latest assignment endpoints. `apps/drone_assignment/models.py:14-94`, `apps/drone/views.py:683-856`

## 6. Documentation and test surfaces
- The README documents the current formal IAM plane, business API plane, unified docs location, endpoint inventory, response contract, runtime terminology, and linked design documents. `README.md:3-37`, `README.md:71-236`
- Shared API-layer tests live in `apps/api_v1/tests.py`. `apps/api_v1/tests.py:29-220`
- IAM-layer tests live in `apps/access/tests.py`. `apps/access/tests.py:1-515`
- Domain tests live in `apps/drone/tests.py`, `apps/drone_assignment/tests.py`, `apps/route/tests.py`, `apps/waypoint/tests.py`, `apps/mission/tests.py`, `apps/flight_record/tests.py`, and `apps/media_file/tests.py`. `apps/drone/tests.py:20-260`, `apps/drone_assignment/tests.py:26-655`, `apps/route/tests.py:24-260`, `apps/waypoint/tests.py:14-700`, `apps/mission/tests.py:20-1400`, `apps/flight_record/tests.py:24-1200`, `apps/media_file/tests.py:25-800`
