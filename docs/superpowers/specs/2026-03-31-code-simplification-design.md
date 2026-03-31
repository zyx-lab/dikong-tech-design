# Code Simplification Design

- date: 2026-03-31
- status: approved in conversation, written for implementation planning
- scope: simplify the current Django implementation without changing accepted business semantics

## 1. Context

The current codebase has already converged on a newer business boundary:

1. `Route` is the public aggregate root for route editing.
2. `waypoints[]` is route-owned internal editing data, not a public business resource.
3. `Mission` is created locally and synchronized to DJI on create; local action APIs such as `start/pause/resume/complete/fail` are gone.
4. `MediaFile` is a read model driven by local DJI indexes.
5. `Drone.status` has already been reduced to the online summary states `ENABLED` and `DISABLED`.
6. `dji_bff` now provides the actual runtime loop through:
   - `run_dji_sync_scheduler`
   - `sync/*` internal HTTP wrappers
   - `callbacks/*` internal callback receivers

However, the implementation still contains two kinds of excess:

1. obsolete semantic shells
   - code that still reflects older public semantics that are no longer part of the accepted design
   - examples: old waypoint-resource helpers, stale validation logic tied to removed route status concepts, historical comments that describe no-longer-supported behavior

2. duplicated implementation patterns
   - repeated unknown-field validation
   - repeated create/update/partial-update response assembly
   - repeated payload serialization helpers
   - repeated request-body-empty validation for action endpoints
   - large files with excessive inline explanation that no longer adds operational value

The goal of this work is to remove that excess while keeping the current runtime behavior intact.

## 2. Design Goals

1. Remove implementation that belongs to already-abandoned business/API semantics.
2. Reduce duplicated code in current live APIs.
3. Keep the current public API surface and current business meaning unchanged.
4. Keep the smallest correct change set.
5. Prefer local simplification over new infrastructure.
6. Use tests as the proof that semantics were preserved.

## 3. Non-Goals

1. No new business capability.
2. No API redesign in this spec.
3. No database migration compatibility work.
4. No large-scale module relocation for aesthetic reasons.
5. No new abstraction framework for all business viewsets.
6. No rewrite of DJI gateway behavior or current scheduler topology.

## 4. Core Decision

This work is a cleanup and consolidation pass, not a new feature refactor.

The implementation should be simplified in this exact order:

1. delete obsolete semantic shells first
2. consolidate duplicate implementation second
3. verify full test compatibility last

If a simplification candidate would require changing current public behavior, it is out of scope for this pass.

## 5. Boundary Decisions

### 5.1 Waypoint boundary

`Waypoint` remains an internal storage model owned by `Route`.

What stays:

1. the `waypoints` database table
2. `apps.waypoint.models.Waypoint`
3. route-side nested waypoint editing through `/api/v1/routes*`
4. route-side waypoint rendering in route detail responses

What goes:

1. any leftover code that assumes `Waypoint` is still a public CRUD resource
2. any serializer or validation branch that depends on old public waypoint API semantics
3. any stale imports or dead tests that only existed to support that removed public resource

This means the `waypoint` app is retained only as data storage, not as a public API module.

### 5.2 Current business modules stay in place

This cleanup does not re-partition the active modules.

`drone`, `route`, `mission`, `flight_record`, `media_file`, and `dji_bff` remain the active business/runtime modules.

The simplification work must happen inside the current structure unless a tiny helper extraction is clearly the smallest correct move.

### 5.3 No contract drift

The following accepted semantics must remain unchanged:

1. `Drone.status` only expresses current online summary as `ENABLED` or `DISABLED`
2. `Route` has no public `status` field
3. `Route` publish state is expressed by `TenantRouteIndex.is_published`
4. route editing remains local until explicit publish
5. `Mission` create pushes to DJI, and `cancel` remains the only public mission action
6. `MediaFile` remains read-only from the tenant business API perspective
7. `run_dji_sync_scheduler` remains the primary runtime scheduler entry
8. `sync/*` internal endpoints remain HTTP wrappers over the same sync functions
9. `callbacks/*` remain asynchronous event receivers

## 6. Simplification Targets

### 6.1 Delete obsolete semantic shells

The first pass should delete code that is no longer justified by the current design.

Target category A: removed public waypoint resource leftovers

1. delete unused waypoint serializers that still encode the old standalone waypoint CRUD model
2. delete validation that references removed route-status workflows
3. delete imports or branches that only existed for the removed waypoint API

Target category B: stale behavior descriptions embedded in code

1. remove large blocks of explanatory comments that describe obvious code flow or historical semantics
2. keep only comments that still explain a non-obvious business rule or integration constraint

Target category C: dead compatibility branches

1. delete branches that exist only to preserve removed status/action behavior
2. delete dead test helpers that only exercised already-removed API surfaces

The deletion rule is strict:

- if the behavior is not part of the accepted design and has no live route in the current URL surface, remove it

### 6.2 Consolidate repeated serializer logic

Multiple active serializers currently repeat the same unknown-field rejection pattern.

The design decision is to keep that behavior but express it once.

Requirements:

1. unknown request fields must still be rejected
2. error wording must stay compatible with current tests unless a test is intentionally updated for equivalent semantics
3. consolidation must be light-weight, such as a shared mixin or tiny helper in the shared API layer
4. consolidation must not introduce a broad serializer framework

This consolidation applies to active modules only:

1. `drone`
2. `route`
3. `mission`
4. `flight_record`

### 6.3 Consolidate repeated view logic

Several active viewsets repeat nearly identical patterns for:

1. serializer validation and error translation
2. create response assembly
3. update/partial update response assembly
4. read payload generation
5. empty-body validation for action endpoints
6. audit logging with before/after snapshots

The design decision is not to introduce a deep inheritance hierarchy.

Instead:

1. consolidate only the repeated local patterns that are already clearly identical
2. prefer file-local helpers or very small shared helpers
3. do not hide business rules behind generic magic

Expected primary targets:

1. `apps/drone/views.py`
2. `apps/route/views.py`
3. `apps/mission/views.py`
4. `apps/flight_record/views.py`

### 6.4 Reduce oversized low-signal code

`apps/flight_record/views.py` is the clearest example of implementation bulk that no longer pays for itself.

This file should be simplified by:

1. removing repetitive explanatory comments
2. extracting tiny helpers for repeated transition handling where doing so clearly reduces duplication
3. keeping OpenAPI descriptions and real state-transition rules intact

The same principle may be applied to other active modules when the simplification is obviously local and safe.

### 6.5 Keep DJI runtime behavior stable

`apps/dji_bff` may be simplified only where duplication is purely internal.

Allowed:

1. helper consolidation inside sync/callback code
2. removal of dead branches or dead helpers
3. clearer organization of shared callback/sync response helpers

Not allowed:

1. changing scheduler semantics
2. changing internal endpoint purpose
3. changing callback/write-side business meaning
4. changing gateway request/response contracts

## 7. Testing Strategy

This cleanup is validated by behavior preservation, not by subjective code style.

The implementation must use tests in three layers:

### 7.1 Guard tests for accepted current behavior

Before cleanup or during the first task in each area, confirm coverage for:

1. `/api/v1/waypoints` remains unmounted
2. waypoint permissions remain absent from seeded permission state
3. route detail continues to expose nested `waypoints[]`
4. route create/update with nested `waypoints[]` still works
5. route publish/download behavior stays unchanged
6. mission create/cancel behavior stays unchanged
7. media file read/download behavior stays unchanged
8. drone available/claim/live behavior stays unchanged

### 7.2 Focused module tests during cleanup

Each simplification step should run the smallest relevant test subset first.

Examples:

1. route tests when removing waypoint-public-resource leftovers
2. serializer-focused tests when extracting unknown-field validation helpers
3. flight record tests when simplifying action/update flows
4. `dji_bff` tests when consolidating sync/callback internals

### 7.3 Final full verification

After all cleanup tasks:

1. run the full Django test suite
2. fix any failures caused by accidental contract drift
3. do not declare completion until the full suite passes

## 8. Acceptance Criteria

This work is complete only if all of the following are true:

1. obsolete waypoint-public-resource code has been removed
2. active business APIs still behave the same from the caller perspective
3. duplicated serializer unknown-field logic is consolidated
4. duplicated active view logic is materially reduced without adding heavy abstraction
5. `flight_record` and similar oversized files are smaller and easier to follow
6. `dji_bff` runtime behavior remains unchanged
7. the full test suite passes

## 9. Risks and Controls

### 9.1 Risk: over-refactoring

If cleanup starts changing architecture rather than removing duplication, the work will exceed scope.

Control:

1. prefer local helpers over new frameworks
2. stop before cross-module redesign

### 9.2 Risk: deleting still-used code

Some old-looking code may still support active tests or active behavior.

Control:

1. remove only code with no live URL or no active call path
2. run focused tests immediately after deletion

### 9.3 Risk: silent API contract drift

Small helper refactors can accidentally change response shapes or error handling.

Control:

1. keep response payloads and status codes aligned with current tests
2. run the full suite before completion

## 10. Implementation Posture

The implementation posture for this spec is:

1. delete first when the code represents removed semantics
2. consolidate second when multiple active paths already do the same thing
3. avoid speculative abstractions
4. keep edits small, local, and test-backed

This is intentionally a restrained simplification pass.
