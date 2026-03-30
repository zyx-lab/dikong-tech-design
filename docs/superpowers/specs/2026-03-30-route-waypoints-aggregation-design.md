# Route / Waypoints Aggregation Design

- date: 2026-03-30
- status: approved in conversation, written for implementation planning
- scope: refactor route/waypoint API boundary to remove `Waypoint` as an independent business resource while preserving route editing capability

## 1. Context

The current codebase mixes two different route models:

1. Local business modeling
   - `Route` stores route header fields.
   - `Waypoint` stores route child points.
   - `/api/v1/waypoints*` exposes independent CRUD for single points.

2. DJI boundary modeling
   - DJI backend route capability is modeled as a whole `wayline` / KMZ file.
   - The upstream API exposes whole-wayline operations only:
     - list waylines
     - download wayline
     - duplicate-name check
     - upload KMZ
     - delete wayline
   - The upstream API does not expose single-waypoint CRUD or batch waypoint update APIs.

This creates a double-truth problem:

- local `waypoint` edits change the local database
- DJI route truth remains unchanged until a separate whole-route upload happens
- the system appears to let users edit a route, but only edits local draft data

The goal of this refactor is to remove that boundary ambiguity.

## 2. Design Goals

1. Remove `Waypoint` as a top-level business resource.
2. Keep route editing capability in the product.
3. Align the public API with DJI’s whole-route / whole-wayline boundary.
4. Keep the smallest correct implementation.
5. Avoid speculative sync state infrastructure.
6. Preserve a clean path for frontend route editing.

## 3. Non-Goals

1. No data migration compatibility work.
2. No backward-compatible support for old `/api/v1/waypoints*` callers.
3. No attempt to model DJI single-waypoint operations that do not exist upstream.
4. No introduction of asynchronous route publishing workflow in this phase.
5. No redesign of mission or flight_record in this spec.

## 4. Core Decision

`Route` becomes the only public business resource for route editing.

`waypoints[]` remains part of the route editing structure, but no longer exists as an independent business concept.

This means:

1. Publicly:
   - only `routes` exists
   - clients edit route metadata and route waypoints through route APIs

2. Internally:
   - the existing `waypoints` table may remain in phase 1 as an implementation detail
   - it is no longer treated as a standalone module or standalone permission surface

3. Upstream:
   - DJI is touched only by route publish
   - editing route data never directly implies DJI has been updated

## 5. Chosen Architecture

### 5.1 Aggregate Boundary

`Route` is the aggregate root.

`waypoints[]` is route-owned data.

Consequences:

1. `Waypoint` is removed as an independent API resource.
2. Route detail becomes the canonical read model for route waypoints.
3. Route write APIs become the canonical write model for route waypoints.
4. Route publish becomes the only action that pushes route data to DJI.

### 5.2 Draft vs Published Semantics

The system still needs to distinguish between:

1. a local route draft
2. a route draft that has been successfully published to DJI

The minimal state chosen for this is:

- `TenantRouteIndex.is_published: bool`

Semantics:

- `false`
  - route has never been published, or
  - route was published before but local route data changed afterward

- `true`
  - the current local route draft matches the latest successful DJI publish result

This spec explicitly does **not** keep:

- `Route.status`
- `sync_status`
- `last_sync_at`

because the accepted design is to keep only the minimum state needed to answer whether the current local route draft has been successfully pushed.

## 6. Data Model

### 6.1 Route

`Route` remains the business entity.

`Route.status` is removed.

`Route` keeps:

- `id`
- `tenant`
- `name`
- `route_type`
- `drone_type_id`
- `total_distance`
- `estimated_duration`
- `waypoint_count`
- `creator_name`
- `created_at`
- `updated_at`

### 6.2 Waypoint Storage

Phase 1 keeps the current `waypoints` table as internal storage only.

This is an implementation decision, not a public domain decision.

Reason:

- smallest correct change
- reuses existing sequence uniqueness and ordering behavior
- avoids an unnecessary JSON rewrite in the same refactor

The `waypoints` table is no longer a public resource and no longer has an independent API surface.

### 6.3 TenantRouteIndex

`TenantRouteIndex` is simplified to the minimum route-to-DJI mapping:

- `tenant`
- `route`
- `dji_wayline_id`
- `is_published`

`dji_wayline_id` may be blank before the first successful publish.

Fields removed from the model in this design:

- `sync_status`
- `last_sync_at`
- `error_msg`

Failure details are returned in the publish API response and audit log, not persisted as long-lived route sync state.

### 6.4 DJI Wayline Naming Policy

Local `Route.name` remains a business display field.

DJI publish does **not** use `Route.name` directly as the upstream wayline name.

Instead, each publish generates a system-owned unique upstream wayline name, for example:

- `route-{route.id}-{uuid}`

Consequences:

1. no public API depends on DJI name uniqueness
2. cross-tenant local route names do not conflict in the shared DJI resource pool
3. republish can upload a new wayline before deleting the old one
4. explicit duplicate-name precheck is no longer required for publish

## 7. Public API

## 7.1 Route List

### `GET /api/v1/routes`

Purpose:

- list visible routes for the current tenant

Returns summary fields only.

Does not return full `waypoints[]`.

Response shape example:

```json
{
  "id": 1,
  "name": "城市巡检航线",
  "route_type": 0,
  "drone_type_id": null,
  "total_distance": "1250.50",
  "estimated_duration": 780,
  "waypoint_count": 12,
  "creator_name": "航线管理员",
  "is_published": false,
  "created_at": "2026-03-30T10:00:00+08:00",
  "updated_at": "2026-03-30T10:20:00+08:00"
}
```

## 7.2 Route Detail

### `GET /api/v1/routes/{id}`

Purpose:

- read a single route including full editable waypoint data

Response shape example:

```json
{
  "id": 1,
  "name": "城市巡检航线",
  "route_type": 0,
  "drone_type_id": null,
  "total_distance": "1250.50",
  "estimated_duration": 780,
  "waypoint_count": 3,
  "creator_name": "航线管理员",
  "is_published": false,
  "waypoints": [
    {
      "sequence": 1,
      "latitude": "22.28612345",
      "longitude": "113.56781234",
      "altitude": "120.50"
    },
    {
      "sequence": 2,
      "latitude": "22.28622345",
      "longitude": "113.56791234",
      "altitude": "120.50"
    },
    {
      "sequence": 3,
      "latitude": "22.28632345",
      "longitude": "113.56801234",
      "altitude": "118.00"
    }
  ],
  "created_at": "2026-03-30T10:00:00+08:00",
  "updated_at": "2026-03-30T10:20:00+08:00"
}
```

The public `waypoints[]` items do not expose waypoint row IDs.

`sequence` is the business identifier inside a route.

## 7.3 Route Create

### `POST /api/v1/routes`

Purpose:

- create a local route draft

Request body includes route metadata plus complete `waypoints[]`.

Example:

```json
{
  "name": "城市巡检航线",
  "route_type": 0,
  "drone_type_id": null,
  "total_distance": "1250.50",
  "estimated_duration": 780,
  "waypoints": [
    {
      "sequence": 1,
      "latitude": "22.28612345",
      "longitude": "113.56781234",
      "altitude": "120.50"
    },
    {
      "sequence": 2,
      "latitude": "22.28622345",
      "longitude": "113.56791234",
      "altitude": "120.50"
    }
  ]
}
```

Behavior:

1. creates local route
2. stores waypoint draft data
3. calculates `waypoint_count`
4. creates route-to-DJI index with `is_published=false`
5. does not upload to DJI

## 7.4 Route Update

### `PUT /api/v1/routes/{id}`
### `PATCH /api/v1/routes/{id}`

Purpose:

- update route metadata
- optionally replace the full waypoint set

Rules:

1. if `waypoints` is omitted:
   - only route metadata changes
2. if `waypoints` is provided:
   - it replaces the full route waypoint set
   - no merge semantics
   - no single-waypoint partial patch semantics
3. any successful route change sets `is_published=false`

This applies to:

- route name changes
- route metadata changes
- waypoint set changes

## 7.5 Route Publish

### `POST /api/v1/routes/{id}/publish`

Purpose:

- publish the current local route draft to DJI

Request body:

- empty

Behavior:

1. load route and full waypoint set
2. validate publish preconditions
3. generate KMZ from current draft
4. upload new wayline to DJI
5. if successful:
   - update `dji_wayline_id`
   - set `is_published=true`
   - if an older `dji_wayline_id` existed, delete the old upstream wayline after the new publish succeeds
6. if upload fails:
   - keep local route and waypoint draft unchanged
   - keep `is_published=false`
   - return failure in the API response

The publish endpoint is the only route action that touches DJI route APIs.

## 7.6 Route Download

### `GET /api/v1/routes/{id}/download`

Purpose:

- download the currently published DJI route file

Rule:

- allowed only when `is_published=true`

If the current local route is not published, the API returns a state-conflict response.

## 7.7 Route Delete

### `DELETE /api/v1/routes/{id}`

Purpose:

- delete a route and its internal waypoint draft data

Rules:

1. if actively referenced by live mission usage, reject deletion
2. if deletable:
   - delete local route
   - delete internal waypoint storage
   - delete DJI wayline if one exists

## 7.8 Removed APIs

The following APIs are removed:

- `GET /api/v1/waypoints`
- `POST /api/v1/waypoints`
- `GET /api/v1/waypoints/{id}`
- `PUT /api/v1/waypoints/{id}`
- `PATCH /api/v1/waypoints/{id}`
- `DELETE /api/v1/waypoints/{id}`

There is no replacement top-level waypoint API.

All waypoint editing becomes part of route create / route update / route detail.

## 8. Validation Rules

### 8.1 Route Validation

1. `name` must be unique within the tenant.
2. request bodies cannot directly write:
   - `waypoint_count`
   - `creator_name`
   - `is_published`
3. `waypoint_count` is always system-calculated from `waypoints[]`.

### 8.2 Waypoint Array Validation

If `waypoints` is provided:

1. it must be an array
2. each item must include:
   - `sequence`
   - `latitude`
   - `longitude`
   - `altitude`
3. `sequence` must be unique within the route
4. server stores the full set ordered by `sequence`
5. `waypoints` may be an empty array for local draft storage

### 8.3 Publish Validation

Before publish:

1. route must exist and be visible in current tenant
2. route must contain at least one waypoint
3. current draft must be convertible into a valid KMZ
4. publish must generate a unique upstream wayline name according to the system naming policy

## 9. Error Semantics

### `B0001`

Parameter validation failure.

Examples:

- invalid `waypoints` shape
- missing waypoint fields
- duplicate `sequence`
- publish requested with zero waypoints

### `C0101`

Duplicate or conflict with existing unique resource.

Examples:

- local route name duplicate

### `C0201`

State conflict.

Examples:

- route download requested while `is_published=false`

### `C0404`

Resource not found or not visible in current tenant context.

## 10. Permissions

Waypoint permissions are removed.

There are no independent:

- `waypoint.view_waypoint`
- `waypoint.manage_waypoint`

Route permissions absorb waypoint editing:

- `route.view_route`
  - route summary
  - route detail including `waypoints[]`
- `route.manage_route`
  - route create
  - route update
  - route delete
  - route publish

Role-permission seeds and docs must be updated accordingly.

## 11. Audit

Audit should be route-centric only.

Kept actions:

- `ROUTE_CREATE`
- `ROUTE_UPDATE`
- `ROUTE_PUBLISH`
- `ROUTE_DELETE`

Removed actions:

- `WAYPOINT_CREATE`
- `WAYPOINT_UPDATE`
- `WAYPOINT_DELETE`

Waypoint changes are part of route edits and should be represented inside route audit snapshots.

## 12. Implementation Strategy

### Phase 1

1. Remove public waypoint API routes and public waypoint views.
2. Move waypoint serialization and validation into route-owned request/response handling.
3. Keep `waypoints` table as internal route storage.
4. Simplify `Route` and `TenantRouteIndex` models according to this spec.
5. Add route detail `waypoints[]`.
6. Add explicit route publish endpoint.
7. Update permissions, docs, tests, and seed data.

### Phase 2

Optional future simplification:

- decide whether internal waypoint table should later be replaced with embedded route JSON storage

This future step is intentionally out of scope for the current implementation plan.

## 13. Testing Requirements

Tests should cover at least:

1. route detail returns ordered `waypoints[]`
2. route create with full waypoint array
3. route update without `waypoints` keeps existing internal waypoint set
4. route update with `waypoints` fully replaces internal waypoint set
5. any route change sets `is_published=false`
6. route publish succeeds and sets `is_published=true`
7. route publish failure leaves local draft intact and keeps `is_published=false`
8. route download rejects unpublished route
9. route delete removes internal waypoint storage
10. old `/api/v1/waypoints*` endpoints are gone
11. waypoint permission seeds are removed and route permissions cover route detail/edit flows

## 14. Acceptance Criteria

This design is considered implemented when:

1. `Waypoint` no longer exists as a public business resource.
2. All public route editing flows operate through `Route`.
3. `waypoints[]` is visible only as route-owned data.
4. DJI route upload happens only through route publish.
5. No public API implies that local waypoint edits are already live in DJI.
6. Route read/write permissions fully replace waypoint permissions.
