# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common commands

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_role_permissions --mode replace
python manage.py runserver 0.0.0.0:8001
```

### Run tests

```bash
.venv/bin/python manage.py test
.venv/bin/python manage.py test apps.access apps.api_v1
.venv/bin/python manage.py test apps.drone.test_live_api
.venv/bin/python manage.py test apps.mission.test_live_api apps.route.test_live_api
```

### Generate OpenAPI schema

```bash
python manage.py spectacular --file /tmp/openapi.yaml --urlconf config.business_api_urlconf
```

## Big-picture architecture

### API layout
- All public API entrypoints are mounted from `config/urls.py`.
- `/api/v1/` is the single API root and is assembled in `apps/api_v1/urls.py`.
- There are three HTTP surfaces under the same runtime:
  - IAM plane: `/api/v1/iam/*`
  - Business plane: `/api/v1/{resource}` for drones, drone assignments, routes, missions, flight records, and media files.
  - Internal DJI bridge: `/api/v1/__internal__/dji/*`
- A mock DJI upstream for tests and local integration is exposed separately at `/__mock-dji__/api/v1/*`.

### Core cross-cutting patterns
- Business APIs use a uniform `code/msg/data` envelope via `apps/api_v1/business_response.py`.
- OpenAPI generation is centralized through `drf-spectacular` with shared helpers in `apps/api_v1/schema.py` and post-processing hooks in `apps/api_v1/openapi_hooks.py`.
- Tenant context is resolved from `X-TENANT-CODE` in `apps/access/middleware.py`; business APIs should assume tenant-scoped execution unless they are IAM platform endpoints.
- Authentication for formal APIs uses bearer tokens from `apps/access/api_v1/authentication.py`.
- Authorization is role/permission/scope based, implemented in `apps/access/services.py` and enforced through `apps/access/drf_permissions.py`.

### App responsibilities
- `apps/access`: custom user model, tenant/platform IAM, auth sessions, RBAC, audit logs, tenant middleware.
- `apps/api_v1`: API root view, pagination, shared response/schema helpers, OpenAPI hooks.
- `apps/drone`, `apps/drone_assignment`, `apps/route`, `apps/mission`, `apps/flight_record`, `apps/media_file`: public business resource modules.
- `apps/waypoint`: internal route-owned waypoint storage; no public `/api/v1/waypoints*` API remains.
- `apps/dji_bff`: DJI integration boundary; owns the upstream gateway, sync tasks, internal callback entrypoints, and local DJI index tables.
- `apps/dji_mock`: test-only mock DJI upstream used by live HTTP tests and local integration.

### Permission and scope model
- Tenant-side authorization flows through:
  `TenantMember -> TenantMemberRole -> RolePermissionGrant -> Permission`
- Platform-side authorization uses `User.is_platform_admin` and platform role grants.
- Scope semantics are centralized in `apps/access/services.py`:
  - `ALL`: all visible data in tenant scope
  - `OWN`: records created by current `TenantMember`
  - `ASSIGNED`: records assigned to current `TenantMember`
- Many business viewsets combine these mixins in the same pattern: `BusinessApiResponseMixin`, `TenantScopedBusinessMixin`, `PermissionMapMixin`, `ScopedQuerysetMixin`.

### Current DJI integration direction
- The design baseline is documented in `项目总体概览/DJI适配接入边界设计.md` and `权限管理侧实现/DJI权限与租户隔离设计.md`.
- The intended model is: tenant isolation remains local, while DJI is treated as a single upstream resource pool behind `apps/dji_bff`.
- `Drone` is being shifted from pure local asset CRUD to “claim a shared upstream device by device_sn”.
- `apps/dji_bff/gateway.py` is a concrete HTTP client with mock-friendly behavior. Keep DJI-specific behavior behind that boundary instead of leaking it into business apps.
- `apps/dji_bff/tasks.py` plus `run_dji_sync_scheduler` form the minimum runnable sync loop for devices, missions, and media.
- `apps/dji_bff/models.py` holds the local index/mapping tables used to relate tenant resources to DJI resources.

### Testing and contract validation
- The repository relies heavily on Django test cases and live-style HTTP contract tests.
- Schema and contract coverage live in:
  - `apps/api_v1/tests.py`
  - `apps/access/test_live_schema_api.py`
  - `apps/*/test_live_api.py`
- DJI bridge and mock-upstream behavior are covered in `apps/dji_bff/tests.py` and `apps/dji_mock/tests.py`.
- When changing API shape, update schema assertions and live API tests together.

## Important repository conventions
- Prefer changing existing modules over adding new abstraction layers.
- Keep API contract definitions centralized in serializers/schema helpers instead of duplicating response structure in multiple places.
- For business endpoints, preserve the tenant boundary and standard envelope unless the existing code explicitly does otherwise.
- If a feature touches DJI behavior, check whether it belongs in `apps/dji_bff` before changing a business app directly.

## Detected conventions snapshot (2026-05-29)
- Language/runtime: Python 3 + Django 5.1 + DRF + drf-spectacular.
- File naming is predominantly `snake_case.py`; Django app modules follow the standard `models.py`, `views.py`, `serializers.py`, `urls.py`.
- Tests are mainly colocated by app (`apps/*/tests.py`, `apps/*/test_live_api.py`, `apps/access/test_live_*.py`), plus script-level test helpers in `scripts/`.
- API docs are generated from code annotations and shared schema helpers, not hand-maintained static files.
- Git branch style is `feat/<topic>` or `task/<topic>`; commit style is mixed but trends toward Conventional Commit prefixes (`feat:`, `fix:`, `chore:`).

## Request lifecycle quick trace
1. Request enters `config/urls.py` and then `apps/api_v1/urls.py`.
2. `apps/access/middleware.py` sets request id, structured lifecycle logging, and tenant context (`X-TENANT-CODE`).
3. DRF authentication/permissions run via `apps/access/api_v1/authentication.py` and scoped permission mixins.
4. Business viewsets execute domain logic and return unified `code/msg/data` envelopes.
5. For DJI-dependent operations, business modules call into `apps/dji_bff/gateway.py` and related index models.

## Useful references
- `README.md`: current project overview, bootstrap steps, and the current document entry point.
- `DEPLOY.md`: containerized deployment notes.
- `docs/django-logging-guide.md`: logging and request-chain troubleshooting.
- `docs/host-django-postgres-migration.md`: host-to-Docker PostgreSQL migration notes.
- `项目总体概览/DJI适配接入边界设计.md`: DJI integration boundary baseline.
- `权限管理侧实现/DJI权限与租户隔离设计.md`: permission and tenant isolation baseline.
