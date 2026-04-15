# Route PUT Partial Update Design

## Goal

Make `PUT /api/v1/routes/{id}` support partial updates instead of requiring both `name` and `kmz_file`.

## Scope

This change only affects the route update contract.

Unchanged behavior:

- `POST /api/v1/routes` still creates a route and still requires `name` and `kmz_file`.
- `PATCH /api/v1/routes/{id}` remains unsupported.
- Unknown request fields are still rejected.
- The existing blocker that rejects updates when the route is referenced by a non-deleted mission with a bound drone remains in place.

## Desired Contract

`PUT /api/v1/routes/{id}` accepts these combinations:

- `name` only: update local `Route.name` only.
- `kmz_file` only: keep the current name and replace the upstream wayline KMZ.
- `name` and `kmz_file`: update both.
- neither field: return `200` and treat the request as a no-op.

Request formats:

- `multipart/form-data` must continue to work for file uploads.
- `application/json` should be accepted for the `name`-only update path.

## Behavior Rules

### Name-only update

- Update only the local `Route.name`.
- Do not upload a new wayline to DJI.
- Do not modify `TenantRouteIndex.dji_wayline_id`.
- Do not schedule deletion of the existing upstream wayline.

### KMZ-only update

- Preserve the current route name.
- Run the existing upload, verification, route-index sync, and old-wayline cleanup flow.

### Name + KMZ update

- Use the provided name as the new local route name.
- Use the provided name when generating the new upstream wayline upload name.
- Keep the existing upload verification and old-wayline cleanup flow.

### Empty update

- Return the current route snapshot with HTTP `200`.
- Do not write to the database.
- Do not call DJI.
- Do not emit update-side effects beyond returning the current serialized route.

## Implementation Shape

### Serializer changes

The update serializer should no longer require either field.

- `RouteCreateSerializer` keeps the current strict create contract.
- `RouteUpdateSerializer` becomes optional-field based:
  - `name`: optional
  - `kmz_file`: optional

KMZ validation still runs when `kmz_file` is present.

### View changes

`RouteViewSet.update()` should continue to enforce the current mission blocker first.

After serializer validation:

- If both `name` and `kmz_file` are absent, return the current route payload with `200`.
- If `kmz_file` is absent, perform a local-only `serializer.save()` and return `200`.
- If `kmz_file` is present, keep the current upstream upload flow and only use the provided `name` when it exists.

### Parser support

The update path must accept both:

- `multipart/form-data`
- `application/json`

This is needed so `name`-only updates do not require form submission.

## Error Handling

The change should preserve existing error behavior for:

- blocked updates because the route is in use by a bound-drone mission
- invalid KMZ uploads
- unknown fields
- upstream upload or verification failures during KMZ replacement

## Tests

Add or update tests for:

- `PUT` with only `name` using JSON returns `200`, updates `Route.name`, and does not call DJI upload.
- `PUT` with only `kmz_file` using multipart returns `200`, preserves the old name, and updates the DJI wayline.
- `PUT` with `name` and `kmz_file` still updates both.
- `PUT` with an empty body returns `200` and performs no change.
- `PUT` with unknown fields still returns `400`.
- `PUT` with a bound-mission blocker still returns `400`.
- OpenAPI/schema expectations reflect that route `PUT` is now partial-update capable and may accept JSON for the name-only path.

## Risks

- Allowing JSON on the update path changes the request-body shape documented in the schema and may require schema assertions to be updated carefully.
- A no-op `PUT` returning `200` is intentional, but tests must ensure it does not accidentally mutate `updated_at` or trigger upstream cleanup.

