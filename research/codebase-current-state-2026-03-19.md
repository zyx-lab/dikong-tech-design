# Codebase Research: Current State (2026-03-19)

## High-level summary
This repository is a Django 5.1.6 + Django REST Framework 3.15.2 API project with drf-spectacular for schema generation and Swagger UI exposure. The active server-side surface is organized around a formal IAM plane under `/api/v1/iam/*` and business APIs under `/api/v1/*`, all routed from `config/urls.py` through `apps/api_v1/urls.py`. Request processing combines request ID injection, tenant header resolution, Bearer-token session authentication, tenant-context enforcement, and standardized `code/msg/data` response envelopes. The business domain is split across seven apps: drone, drone_assignment, route, waypoint, mission, flight_record, and media_file. The repository also contains a standalone Vue-based `api-tester.html` page that implements a tabbed API testing UI and issues requests to both `/internal/auth/*` and `/api/v1/*` endpoints.

## Runtime entry points and repository structure
- The Django CLI entry point is `manage.py`, which sets `DJANGO_SETTINGS_MODULE=config.settings` and delegates to `execute_from_command_line`. `manage.py:1-22`
- Python dependencies declared in the repository are Django, Django REST Framework, drf-spectacular, and psycopg. `requirements.txt:1-4`
- Django settings register the IAM app, shared API infrastructure, and seven business apps in `INSTALLED_APPS`, and define the middleware stack, custom user model, REST framework defaults, and OpenAPI post-processing hook. `config/settings.py:10-40`, `config/settings.py:102-137`
- The default database path is `db.sqlite3`, with an alternate PostgreSQL configuration selected by `DB_ENGINE=postgres`. `config/settings.py:62-80`
- Global URL configuration exposes `/admin/`, `/docs/`, `/docs/schema/`, `/internal/docs/`, `/internal/docs/schema/`, `/api/v1/docs/`, `/api/v1/docs/schema/`, and the `/api/v1/` API namespace. `config/urls.py:9-30`
- `config/business_api_urlconf.py` and `config/openapi_urlconf.py` both include `apps.api_v1.urls`, while `config/internal_api_urlconf.py` currently contains an empty `urlpatterns` list. `config/business_api_urlconf.py:1-5`, `config/openapi_urlconf.py:1-5`, `config/internal_api_urlconf.py:1-1`

## Shared API infrastructure
### Root routes and API namespace
- `ApiV1RootView` returns the service name, version, and links to documentation, health, IAM, and business endpoints. `apps/api_v1/views.py:10-34`
- `ApiV1HealthView` returns `{"status": "ok", "service": "business-api-v1"}`. `apps/api_v1/views.py:37-44`
- `apps/api_v1/urls.py` mounts the API root, health endpoint, IAM routes, and all seven business-app routers under the `/api/v1/` namespace. `apps/api_v1/urls.py:5-16`

### Standard response envelope
- `BusinessApiResponseMixin.finalize_response()` rewrites DRF responses into the standard `code/msg/data` structure and attaches `traceId` when available from the request. `apps/api_v1/business_response.py:239-253`
- Success responses are wrapped by `standard_success_payload`, and error responses are derived by `_infer_standard_code`, `_preferred_error_message`, and `_build_error_data`. `apps/api_v1/business_response.py:85-104`, `apps/api_v1/business_response.py:138-232`
- The defined business codes include success, authentication, authorization, validation, duplicate, state conflict, resource-in-use, not-found, and internal-error codes. `apps/api_v1/business_response.py:10-22`

### Pagination and tenant scope helpers
- `StandardPageNumberPagination` uses `pageNum` and `pageSize`, caps page size at `100`, defaults to `20`, and returns paginated data as `{"list": ..., "total": ...}`. `apps/api_v1/pagination.py:5-36`
- `TenantScopedBusinessMixin` requires `request.tenant_context`, blocks active platform admins from tenant business APIs, and filters querysets by the configured tenant lookup field. `apps/api_v1/tenant_scope.py:5-40`
- `require_request_tenant()` and `get_request_tenant()` are used by serializers and view code to resolve the current tenant from request context. `apps/api_v1/tenant_scope.py:10-22`

### OpenAPI response shaping
- `standardize_response_schema_hook()` walks `/api/v1/*` OpenAPI paths and wraps JSON response schemas and examples into the same `code/msg/data` shape used at runtime. `apps/api_v1/openapi_hooks.py:128-152`
- `_wrap_schema()`, `_strip_legacy_envelope()`, and `_flatten_nested_list_items()` normalize list and nested response schemas before wrapping. `apps/api_v1/openapi_hooks.py:30-114`

## IAM and authentication subsystem
### Core models
- `apps/access/models.py` defines the IAM data model, including enums for directory, user, tenant, member, role-binding, scope, and audit states. `apps/access/models.py:23-72`
- `User` is the custom auth model and includes status flags, platform-admin state, and revocation behavior for disabled accounts. `apps/access/models.py:113-171`
- `AuthSession` stores hashed access and refresh tokens, expiration timestamps, revocation timestamps, client metadata, and last-used fields. `apps/access/models.py:174-204`
- `StaffProfile` stores user profile data used by formal business accounts. `apps/access/models.py:205-236`
- `AuditLog` stores tenant, actor, action, target, before/after JSON snapshots, client IP, request ID, and timestamps. `apps/access/models.py:239-278`
- `Permission`, `Role`, and `RolePermissionGrant` define the permission catalog and role-to-permission grants. `apps/access/models.py:280-404`
- `Tenant`, `TenantMember`, and `TenantMemberRole` model tenant membership and role assignment status within a tenant. `apps/access/models.py:406-640`

### Request middleware and tenant context
- `RequestContextMiddleware` creates or forwards `X-Request-ID` and stores it on the request and response. `apps/access/middleware.py:8-22`
- `TenantContextMiddleware` reads `X-TENANT-CODE`, stores `tenant_context_code`, resolves `request.tenant_context`, and enforces active tenants for non-IAM `/api/v1/*` requests. `apps/access/middleware.py:25-52`

### Bearer-token authentication and session lifecycle
- `BearerAuthSessionAuthentication` reads `Authorization: Bearer <token>`, hashes the token, looks up a non-revoked, non-expired `AuthSession`, updates last-used metadata, and returns `(user, session)`. `apps/access/api_v1/authentication.py:14-60`
- `create_auth_session()` issues random access and refresh tokens, stores only their hashes, records IP and user-agent metadata, updates `last_login`, and returns token payload plus basic user data. `apps/access/api_v1/services/auth.py:50-75`
- `refresh_auth_session()` rotates both token hashes and expiry timestamps inside a transaction and updates refresh and last-used metadata. `apps/access/api_v1/services/auth.py:78-120`
- `revoke_current_session()` and `revoke_all_user_sessions()` mark sessions as revoked by setting `revoked_at`. `apps/access/api_v1/services/auth.py:123-129`
- `register_by_username()` creates a `User` and `StaffProfile` after username and phone conflict checks, while `register_by_phone()` validates the mock SMS code `123456` before creating the same pair. `apps/access/api_v1/services/auth.py:132-153`

### Formal identity and tenant request context
- `require_authenticated_formal_identity()` classifies a request user as `unassigned`, `tenant_member`, or `platform_operator`, excluding inactive users and superusers from the formal IAM runtime. `apps/access/api_v1/context.py:50-67`
- `require_business_identity()` excludes platform operators from business-identity flows, and `require_platform_operator()` requires platform-operator runtime. `apps/access/api_v1/context.py:70-81`
- `parse_tenant_code_header()` validates the tenant header format against `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`. `apps/access/api_v1/context.py:84-93`
- `resolve_tenant_request_context()` resolves the active tenant member, requires active tenant/member state, and collects granted role codes excluding `platform_admin`. `apps/access/api_v1/context.py:96-129`
- `tenant_context_has_permission()` checks whether any active granted role in the current tenant request context has the requested permission. `apps/access/api_v1/context.py:136-143`

### Authorization, audit, and shared IAM services
- `apps/access/services.py` defines reusable authorization logic, including request permission resolution, scope checks, queryset scoping, and audit logging. `apps/access/services.py:91-568`
- `log_action()` creates `AuditLog` rows and derives tenant, actor, IP, and request ID context from the request when available. `apps/access/services.py:168-196`
- `get_request_permission_codes()` and `has_request_permission()` resolve effective permissions for superusers, platform admins, and tenant members. `apps/access/services.py:336-414`
- `AuthzService.authorize()` returns an `AuthorizationDecision` after combining permission presence, scope resolution, queryset/object checks, and optional business-rule callbacks. `apps/access/services.py:477-528`
- `apply_scope_to_queryset()` and related helpers support `ALL`, `OWN`, and `ASSIGNED` data filtering. `apps/access/services.py:416-475`

### IAM API routes
- Session endpoints are defined as `/api/v1/iam/session/login`, `/refresh`, `/logout`, `/register`, and `/register-by-phone`. `apps/access/api_v1/urls.py:33-38`
- Me endpoints are `/api/v1/iam/me/profile` and `/api/v1/iam/me/tenants`. `apps/access/api_v1/urls.py:39-40`
- Tenant endpoints are `/api/v1/iam/tenant/me`, `/members`, `/members/{memberId}`, `/members/{memberId}/roles`, `/members/{memberId}/enable`, `/members/{memberId}/disable`, `/roles`, and `/audit-logs`. `apps/access/api_v1/urls.py:41-48`
- Platform endpoints are `/api/v1/iam/platform/permissions`, `/roles`, `/roles/{roleId}`, `/audit-logs`, `/tenants`, `/tenants/{tenantId}`, `/tenants/{tenantId}/enable`, `/tenants/{tenantId}/disable`, and `/tenants/{tenantId}/initialize-admin`. `apps/access/api_v1/urls.py:49-61`

### IAM API views and service operations
- Session views implement login, refresh, logout, and registration flows on top of the auth service functions. `apps/access/api_v1/views/session.py:46-142`
- `MeProfileView` and `MeTenantsView` expose the current formal business profile and the user’s tenant memberships. `apps/access/api_v1/views/me.py:34-84`
- Tenant views implement current-member retrieval, member listing/creation, member detail and display-name update, role replacement, member enable/disable, role listing, and tenant audit-log listing. `apps/access/api_v1/views/tenant.py:103-403`
- Platform views implement permission and role directories, platform audit-log listing, tenant catalog/list/create/detail, tenant enable/disable, and tenant-admin initialization. `apps/access/api_v1/views/platform.py:35-410`
- Tenant creation and tenant status changes are implemented in `apps/access/api_v1/services/tenants.py`, and tenant-member lifecycle, role replacement, and tenant-admin initialization are implemented in `apps/access/api_v1/services/members.py`. `apps/access/api_v1/services/tenants.py:8-74`, `apps/access/api_v1/services/members.py:24-290`

## Business domain applications
### Drone
- `Drone` is tenant-scoped and stores code, name, model, serial number, org identifier, creator info, and status values `ENABLED`, `DISABLED`, `MAINTENANCE`, and `RETIRED`. `apps/drone/models.py:5-74`
- Tenant-local uniqueness is enforced for `(tenant, code)` and `(tenant, serial_no)`, and `clean()` prevents leaving the `RETIRED` status. `apps/drone/models.py:47-74`
- `DroneViewSet` implements CRUD, status actions (`enable`, `disable`, `maintenance`, `retire`), and related assignment-history endpoints. `apps/drone/views.py:337-856`
- Drone routes are exposed through `SimpleRouter` under `/api/v1/drones`. `apps/drone/urls.py:1-8`

### Drone assignment
- `DroneAssignment` links a tenant-scoped drone to a tenant member, with status values `ACTIVE` and `INACTIVE` and a uniqueness constraint on active drone/member pairs. `apps/drone_assignment/models.py:9-93`
- Model and serializer validation require same-tenant resources, non-retired drones, active tenant members, and pilot-role eligibility. `apps/drone_assignment/models.py:60-90`, `apps/drone_assignment/serializers.py:43-87`
- `DroneAssignmentViewSet` implements list, retrieve, create, `cancel`, and `reactivate`. `apps/drone_assignment/views.py:185-406`
- Routes are exposed under `/api/v1/drone-assignments`. `apps/drone_assignment/urls.py:1-8`

### Route and waypoint
- `Route` stores tenant-scoped route metadata, route type, applicable drone type, distance, duration, waypoint count, creator, and status. `apps/route/models.py:4-58`
- `RouteViewSet` implements CRUD plus `enable` and `disable`; delete logic checks whether missions reference the route and either disables or deletes accordingly. `apps/route/views.py:239-585`
- `Waypoint` belongs to a route, stores sequence and coordinates, and enforces route-local sequence uniqueness. `apps/waypoint/models.py:7-43`
- `WaypointViewSet` implements CRUD and keeps `route.waypoint_count` synchronized after create, update, and delete operations. `apps/waypoint/views.py:205-393`
- Routes are exposed under `/api/v1/routes` and `/api/v1/waypoints`. `apps/route/urls.py:1-8`, `apps/waypoint/urls.py:1-8`

### Mission
- `Mission` binds a tenant to a route, drone, and pilot tenant member, stores denormalized route/drone/pilot names, scheduled time, remarks, and status values including `PENDING`, `RUNNING`, `PAUSED`, `COMPLETED`, `CANCELED`, and `FAILED`. `apps/mission/models.py:18-98`
- `clean()` enforces tenant alignment and allowed state transitions. `apps/mission/models.py:58-94`
- `MissionViewSet` implements CRUD plus `start`, `pause`, `resume`, `complete`, `fail`, and `cancel`, and uses assigned-scope filtering keyed by `pilot_id`. `apps/mission/views.py:280-813`
- Mission routes are exposed under `/api/v1/missions`. `apps/mission/urls.py:1-8`

### Flight record
- `FlightRecord` optionally links to a mission, drone, and pilot, stores denormalized names, flight timing and media counts, and uses status values `IN_PROGRESS`, `COMPLETED`, and `ABORTED`. `apps/flight_record/models.py:16-120`
- Validation checks tenant alignment, mission/drone/pilot consistency, status transitions, and time ordering. `apps/flight_record/models.py:81-117`
- `FlightRecordViewSet` implements CRUD plus `complete` and `abort`, with assigned-scope filtering bound to the current pilot. `apps/flight_record/views.py:273-573`
- Flight-record routes are exposed under `/api/v1/flight-records`. `apps/flight_record/urls.py:1-8`

### Media file
- `MediaFile` links tenant-scoped media metadata to an optional flight record, stores file metadata, capture metadata, logical-deletion fields, and derives its pilot from the linked flight record. `apps/media_file/models.py:10-67`
- `MediaFileViewSet` implements CRUD over non-deleted records and performs logical deletion by setting `is_deleted` and `deleted_at` in `destroy()`. `apps/media_file/views.py:249-454`
- Assigned-scope filtering is based on `flight_record__pilot_id`. `apps/media_file/views.py:276-319`
- Media-file routes are exposed under `/api/v1/media-files`. `apps/media_file/urls.py:1-8`

## Frontend and testing surface
- `api-tester.html` is a standalone HTML page that loads Vue 3 from `https://unpkg.com/vue@3/dist/vue.global.prod.js`. `api-tester.html:1-7`
- The page contains a login overlay, top header, tenant selector, tab strip, twelve case panels, and a request-log panel. `api-tester.html:81-121`, `api-tester.html:123-928`
- The Vue app initializes reactive state for login, tenant selection, request logs, per-case results, and all business-form inputs inside `createApp(...).setup()`. `api-tester.html:932-1029`
- `getHeaders()` adds `Content-Type: application/json` and conditionally adds `X-TENANT-CODE`, while `apiRequest()` performs `fetch()` with `credentials: 'include'`, parses JSON or text responses, and records all traffic in `requestLogs`. `api-tester.html:1031-1078`
- Authentication handlers in the page call `/internal/auth/login`, `/internal/auth/logout`, and `/internal/auth/me/tenants`. `api-tester.html:1090-1120`
- The page also defines case handlers that issue requests to `/internal/auth/*` and `/api/v1/*` endpoints for platform checks, tenant-context checks, invite/member flows, role/permission tests, drones, routes, assignments, missions, flight records, and media files. `api-tester.html:1130-1384`
- The request-log UI renders method, URL, headers, body, status, and response for each stored request. `api-tester.html:912-928`, `api-tester.html:1039-1048`

## Cross-component connections and data flows
### Request processing flow
1. Global routing directs incoming traffic through `config/urls.py` into `/api/v1/` or one of the schema/documentation endpoints. `config/urls.py:9-30`
2. Middleware attaches a request ID and resolves the tenant header into `request.tenant_context`. `apps/access/middleware.py:8-52`
3. DRF authentication uses `BearerAuthSessionAuthentication` to bind an authenticated request to a `User` and `AuthSession`. `apps/access/api_v1/authentication.py:14-60`
4. IAM code resolves a formal identity and, for tenant-scoped operations, a `TenantRequestContext` with active membership and granted role codes. `apps/access/api_v1/context.py:50-143`
5. Business views use `TenantScopedBusinessMixin` and app-specific permission maps to scope querysets and action permissions to the active tenant. `apps/api_v1/tenant_scope.py:25-40`, `apps/drone/views.py:337-421`, `apps/mission/views.py:280-357`
6. Responses are standardized into the `code/msg/data` envelope by `BusinessApiResponseMixin`. `apps/api_v1/business_response.py:239-253`

### Session and audit flow
1. Login and refresh actions create or rotate `AuthSession` rows with hashed tokens and expiry timestamps. `apps/access/api_v1/services/auth.py:50-120`
2. Logout and account-status revocation paths mark sessions as revoked by updating `revoked_at`. `apps/access/api_v1/services/auth.py:123-129`, `apps/access/api_v1/authentication.py:47-55`
3. IAM and platform service operations emit audit rows through `log_action()`, including before/after payloads and request metadata. `apps/access/services.py:168-196`, `apps/access/api_v1/services/tenants.py:19-74`, `apps/access/api_v1/services/members.py:97-290`
4. Audit-log APIs expose tenant-scoped and platform-scoped audit listings. `apps/access/api_v1/views/tenant.py:352-403`, `apps/access/api_v1/views/platform.py:128-184`

### Domain relationship flow
- A route contains many waypoints through `Waypoint.route`, and waypoint mutations update `Route.waypoint_count`. `apps/waypoint/models.py:7-43`, `apps/waypoint/views.py:339-384`
- A mission references a route, drone, and pilot tenant member and stores denormalized names for each. `apps/mission/models.py:18-98`
- A flight record may reference a mission and independently references a drone and pilot, while enforcing consistency across those links. `apps/flight_record/models.py:16-120`
- A media file may reference a flight record and derives pilot ownership from that record for assigned-scope enforcement. `apps/media_file/models.py:19-57`, `apps/media_file/views.py:276-319`
- A drone assignment links a drone to a tenant member and is used by drone history and active/latest assignment endpoints. `apps/drone_assignment/models.py:9-93`, `apps/drone/views.py:683-856`

## README-declared current API surface
- The README states that the repository currently centers on a formal IAM plane under `/api/v1/iam/*`, business APIs under `/api/v1/*`, and unified OpenAPI docs under `/docs/` and `/api/v1/docs/`. `README.md:3-37`
- It lists the current route families and endpoint inventory for IAM, drone, drone_assignment, route, waypoint, mission, flight_record, and media_file. `README.md:51-155`
- It also documents the standardized `/api/v1/*` response contract and the IAM runtime/scope terminology used across the codebase. `README.md:156-236`
