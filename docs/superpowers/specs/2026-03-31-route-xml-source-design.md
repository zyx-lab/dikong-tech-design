# Route XML Source Design

- date: 2026-03-31
- status: approved in conversation, written for implementation planning
- scope: refactor `Route` to use uploaded XML as the only editable source, store XML via Django default storage, and publish by converting XML to KMZ before uploading to DJI
- supersedes: `docs/superpowers/specs/2026-03-30-route-waypoints-aggregation-design.md` for the route editing boundary

## 1. Context

The current route implementation still treats route editing as structured local data:

1. `Route` exposes header fields such as `route_type`, `drone_type_id`, `total_distance`, `estimated_duration`, and `waypoint_count`.
2. Public route APIs expose editable `waypoints[]`.
3. Route publish builds KMZ from local waypoint rows.

The new product direction is different:

1. The frontend will upload a full XML route file.
2. Django must persist that XML file as the route draft source.
3. Django must convert the saved XML file to KMZ only when `publish` is called.
4. The public API should no longer expose `waypoints[]`.

This changes the route aggregate boundary again:

- route draft truth moves from structured waypoint rows to a stored XML file
- publish remains explicit
- DJI still only receives KMZ output, never raw editable route state

## 2. Design Goals

1. Make `xml_file` the only editable route source.
2. Keep the route API minimal and explicit.
3. Store XML using Django `FileField` and `default_storage`.
4. Keep development simple by using local `MEDIA_ROOT`.
5. Convert XML to KMZ only during `publish`.
6. Remove now-redundant waypoint-oriented public API semantics.

## 3. Non-Goals

1. No backward compatibility for old `waypoints[]` clients.
2. No route data migration work.
3. No object-storage-specific abstraction beyond Django storage.
4. No version history for XML or KMZ artifacts.
5. No XML semantic validation beyond “file is parseable XML” during create/update.
6. No frontend-driven direct upload to S3 / MinIO in this phase.

## 4. Core Decision

`Route` becomes an XML-backed aggregate.

Publicly:

1. The only editable inputs are:
   - `name`
   - `xml_file`
2. `POST /api/v1/routes` creates a route from a full XML file.
3. `PUT /api/v1/routes/{id}` replaces the route with a new full XML file.
4. `PATCH /api/v1/routes/{id}` is removed.
5. `waypoints[]` is removed from all public route responses and requests.

Internally:

1. The current route draft is the saved XML file.
2. Publish reads the current XML file, converts it to KMZ, and uploads the KMZ to DJI.
3. `TenantRouteIndex.is_published` still answers only one question:
   - does the currently saved XML draft match the latest successful DJI publish?

## 5. Chosen Architecture

### 5.1 Storage Model

Use Django `FileField` with `default_storage`.

This means:

1. Development uses local `MEDIA_ROOT`.
2. Deployment can later switch storage backend without changing route business semantics.
3. No extra object-storage integration layer is introduced now.

The backend owns the stored filename and should not rely on the uploaded client filename for uniqueness.

### 5.2 Draft / Publish Model

Route lifecycle is:

1. create local XML draft
2. replace local XML draft
3. explicitly publish current XML draft to DJI

Route updates do not auto-publish.

Any successful `PUT` must set `is_published=false`.

This preserves the same high-level rule already accepted for route drafts:

- local edits create divergence from the last published DJI version

### 5.3 Publish Boundary

`publish` is the only place where route XML is interpreted for DJI delivery.

The publish pipeline is:

1. read saved XML file
2. convert XML to KMZ
3. upload KMZ to DJI
4. mark publish success locally
5. if there was a previous `dji_wayline_id`, delete the old DJI wayline only after the new upload succeeds

This keeps local draft editing and upstream publication clearly separated.

## 6. Data Model

### 6.1 Route

`Route` is simplified to:

- `id`
- `tenant`
- `name`
- `xml_file`
- `created_at`
- `updated_at`

Removed from `Route`:

- `route_type`
- `drone_type_id`
- `total_distance`
- `estimated_duration`
- `waypoint_count`
- any public route-owned `waypoints[]` representation

### 6.2 TenantRouteIndex

`TenantRouteIndex` remains:

- `tenant`
- `route`
- `dji_wayline_id`
- `is_published`
- `created_at`
- `updated_at`

Semantics:

1. `dji_wayline_id=""` means the route has never been successfully published.
2. `is_published=false` means the current local XML draft is not the currently published DJI artifact.
3. `is_published=true` means the current local XML draft is the currently published DJI artifact.

### 6.3 Waypoint Storage

`waypoints` is no longer part of the route runtime chain.

This design assumes:

1. route create/update/read no longer depend on waypoint rows
2. route publish no longer reads waypoint rows
3. public API no longer exposes waypoint-derived data

For implementation planning, the target end state is:

- the route module no longer depends on `apps.waypoint` for business behavior

Whether the legacy waypoint table/model is deleted immediately or retired as dead code during implementation is an execution detail, but it is not part of the accepted route design anymore.

## 7. Public API

## 7.1 Route List

### `GET /api/v1/routes`

Purpose:

- list visible routes for the current tenant

Supported filters:

- `name`

Response fields:

- `id`
- `name`
- `is_published`
- `created_at`
- `updated_at`

Does not return:

- `waypoints[]`
- `xml_file`
- `dji_wayline_id`

Example item:

```json
{
  "id": 1,
  "name": "珠海巡检线 A",
  "is_published": false,
  "created_at": "2026-03-31T10:00:00+08:00",
  "updated_at": "2026-03-31T10:30:00+08:00"
}
```

## 7.2 Route Create

### `POST /api/v1/routes`

Purpose:

- create a local route draft from a full XML file

Request:

- `multipart/form-data`

Required fields:

- `name`
- `xml_file`

Validation:

1. `name` is required and non-empty
2. `xml_file` is required
3. `xml_file` content must be parseable XML

Behavior:

1. save XML through `Route.xml_file`
2. create `Route`
3. create `TenantRouteIndex(dji_wayline_id="", is_published=false)`
4. do not call DJI

Response fields:

- `id`
- `name`
- `is_published`
- `created_at`
- `updated_at`

## 7.3 Route Detail

### `GET /api/v1/routes/{id}`

Purpose:

- read route metadata for a single route

Response fields:

- `id`
- `name`
- `is_published`
- `created_at`
- `updated_at`

Does not return:

- `waypoints[]`
- XML file content
- XML storage path

## 7.4 Route XML Readback

### `GET /api/v1/routes/{id}/xml`

Purpose:

- return the currently stored raw XML route draft

Behavior:

1. read `Route.xml_file`
2. return file stream response
3. use XML content type

Recommended response headers:

- `Content-Type: application/xml`
- `Content-Disposition: attachment; filename="<generated-or-stored-name>.xml"`

This endpoint is the only public way to read back the editable route source file.

## 7.5 Route Replace

### `PUT /api/v1/routes/{id}`

Purpose:

- replace the full route draft with a new XML file and route name

Request:

- `multipart/form-data`

Required fields:

- `name`
- `xml_file`

Validation:

1. `name` is required and non-empty
2. `xml_file` is required
3. `xml_file` content must be parseable XML

Behavior:

1. store the new XML file
2. replace the previous XML file reference
3. delete the old local XML file after successful replacement
4. update `Route.name`
5. set `TenantRouteIndex.is_published=false`
6. keep existing `dji_wayline_id` untouched until a later successful publish replaces it
7. do not call DJI

`PATCH /api/v1/routes/{id}` is removed.

Reason:

- XML is the only editable route source
- partial update semantics would reintroduce ambiguity

## 7.6 Route Publish

### `POST /api/v1/routes/{id}/publish`

Purpose:

- publish the current XML draft to DJI

Request:

- request body must be empty

Behavior:

1. load current XML file
2. convert XML to KMZ
3. upload KMZ to DJI
4. write new `dji_wayline_id`
5. set `is_published=true`
6. if an old `dji_wayline_id` existed and differs from the new one, delete the old DJI wayline after the new publish succeeds

Failure behavior:

1. if the request body is not empty, return `400`
2. if XML cannot be converted into a publishable KMZ, return `400`
3. if DJI upload fails, return mapped upstream error
4. on failure, keep the local XML draft unchanged
5. on failure, do not delete the previously published DJI wayline

## 7.7 Route Delete

### `DELETE /api/v1/routes/{id}`

Purpose:

- delete the route and its stored XML draft

Request:

- request body must be empty

Behavior:

1. if there are `PENDING` or `RUNNING` missions referencing the route, reject delete
2. otherwise delete the local route
3. delete the local XML file
4. if `dji_wayline_id` exists, try to delete the corresponding DJI wayline

Delete semantics intentionally remain aligned with the current route lifecycle:

- local draft deletion
- upstream cleanup when applicable

## 8. Validation Rules

### 8.1 XML Validation on Create / Replace

Create and replace only validate:

1. uploaded file exists
2. file bytes can be parsed as XML

Create and replace do not validate:

1. DJI mission semantics
2. DJI wayline schema correctness
3. waypoint completeness rules
4. publishability

Those checks are deferred to publish-time conversion.

### 8.2 Publish-Time Validation

Publish validates:

1. route has an existing XML file
2. XML can be converted to KMZ
3. resulting KMZ upload succeeds against DJI

The exact XML-to-KMZ conversion rules belong to the implementation, but the public contract is:

- a route may be storable as XML but still fail publication

## 9. Error Model

Minimal expected error cases:

1. invalid or missing create/replace fields
   - `400 / B0001`
2. uploaded file is not parseable XML
   - `400 / B0001`
3. publish request contains body
   - `400 / B0001`
4. stored XML cannot be converted to KMZ
   - `400 / B0001`
5. route delete blocked by active mission usage
   - keep current business error behavior
6. route not found
   - `404 / C0404`
7. DJI upstream failure during publish/delete
   - keep current gateway error mapping

## 10. Permissions

Route permissions stay unchanged:

1. `route.view_route`
   - `GET /api/v1/routes`
   - `GET /api/v1/routes/{id}`
   - `GET /api/v1/routes/{id}/xml`
2. `route.manage_route`
   - `POST /api/v1/routes`
   - `PUT /api/v1/routes/{id}`
   - `POST /api/v1/routes/{id}/publish`
   - `DELETE /api/v1/routes/{id}`

No independent waypoint permission remains.

## 11. Required Code Simplification / Removal

This design requires removing the remaining public waypoint-oriented route semantics:

1. remove `waypoints[]` from route request serializers
2. remove `waypoints[]` from route read serializers
3. remove `RouteWaypointSerializer`
4. remove route runtime dependence on `replace_route_waypoints(...)`
5. replace current waypoint-row-based KMZ generation with XML-file-based KMZ generation
6. remove `PATCH /api/v1/routes/{id}`
7. remove `GET /api/v1/routes/{id}/download`
8. remove route queryset prefetches and response fields that only exist for waypoint-backed route editing

## 12. Testing Requirements

Minimum required coverage:

1. create route with valid XML file succeeds
2. create route with invalid XML fails with `400`
3. route detail no longer returns `waypoints`
4. route list no longer returns waypoint-derived fields
5. `PUT /api/v1/routes/{id}` requires full replacement payload
6. replace route stores new XML and resets `is_published=false`
7. `PATCH /api/v1/routes/{id}` is not available
8. `GET /api/v1/routes/{id}/xml` returns the stored XML
9. publish reads XML, converts to KMZ, and uploads through `DjiGateway`
10. publish with non-empty body returns `400`
11. publish conversion failure returns `400`
12. delete removes local XML file
13. route OpenAPI no longer documents `waypoints[]`
14. route OpenAPI no longer documents `download`

## 13. Acceptance Criteria

The refactor is complete when all of the following are true:

1. route create/update public write model is only `name + xml_file`
2. route public read model contains no `waypoints[]`
3. XML is stored through Django `FileField` and `default_storage`
4. route publish converts stored XML to KMZ before DJI upload
5. any route replacement resets `is_published=false`
6. route XML can be read back through `GET /api/v1/routes/{id}/xml`
7. `PATCH /api/v1/routes/{id}` does not exist
8. `GET /api/v1/routes/{id}/download` does not exist
