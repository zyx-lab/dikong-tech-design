# API v2 Resource And Permission Design

## Status

Confirmed design direction from the 2026-05-29 discussion.

This document records the intended v2 backend boundary and data model direction. It is not an implementation plan and does not imply that all endpoints should be implemented in one change.

## Core Decision

API v2 must not expose the old `dji_bff` route surface.

The v1 `dji_bff` model treats DJI as a shared upstream resource pool behind an internal bridge. API v2 changes the resource model: a department administrator configures DJI Cloud API connections, discovers devices, binds drones or docks to their department, and then grants visibility and operation permissions through department hierarchy and resource share groups.

Because of that, v2 should use new IAM/resource tables and new v2 route boundaries instead of reusing `dji_bff` as a public or internal v2 API surface.

## Scope

This design covers the first backend iteration for:

- v2 account context
- department tree
- fixed v2 roles
- department administrators
- platform super administrators
- department-owned DJI Cloud API connections
- drone and dock resource binding
- resource visibility
- resource share groups
- resource share permissions
- binding history
- audit logs and audit query API

This design does not cover:

- frontend management console implementation
- dictionary management implementation
- algorithm repository implementation
- work-order rule implementation
- department node movement
- direct resource transfer between departments
- migration or replacement of v1 business tables
- exposing `apps.dji_bff.urls` under `/api/v2/`

## Tenant And Department Boundary

The existing `Tenant` can remain as a system compatibility boundary. In v2, the expected deployment mode is effectively single-tenant.

Within that tenant, v2 introduces a root `Department`. Real resource ownership, visibility, and inherited access are based on the department tree, not on the v1 tenant-scoped resource model.

Department tree should use a materialized path model:

- `parent_id`
- `path`
- `depth`

The `path` uses stable department IDs, not department names.

Example:

```text
root department: id=1, path=/1/, depth=0
child department: id=2, path=/1/2/, depth=1
grandchild department: id=8, path=/1/2/8/, depth=2
```

Parent departments can query child department resources at SQL level with a prefix condition such as:

```sql
owner_department.path LIKE '/1/2/%'
```

The first version supports department creation, rename, enable, disable, and tree query. It does not support moving a department node. Moving a node would rewrite permission meaning for resources and audit history, so it should be designed separately if needed.

## App Layout

Create two new domain apps and keep `apps/api_v2` as a routing/API aggregation layer.

`apps/iam_v2` owns v2 identity and organization concepts:

- departments
- account profile in v2
- fixed roles
- account-role assignment
- share groups
- share group target departments

`apps/resource_v2` owns resource and resource-permission concepts:

- DJI Cloud API connections
- drone resources
- dock resources
- resource bindings
- binding history
- resource share permissions
- resource audit logs
- resource visibility query helpers

`apps/api_v2` should not own core domain models.

## Account And Role Model

Login/authentication should continue to reuse the existing Bearer Token/session mechanism. API v2 should rebuild authorization, not authentication.

After authentication, v2 loads a separate account context:

- one account belongs to exactly one department
- one account can have multiple fixed v2 roles
- groups are not direct user membership groups in the first version
- groups are resource-sharing policy containers

Fixed v2 roles:

- `platform_super_admin`
- `department_admin`
- `task_monitor_dispatcher`
- `pilot`
- `work_order_handler`

The three business roles have these initial meanings:

- `task_monitor_dispatcher`: monitors real-time and historical flight data, and dispatches flight tasks
- `pilot`: executes tasks, reviews tasks, and maintains their own qualification data
- `work_order_handler`: handles assigned work orders and submits feedback

`department_admin` is a separate system role. It is not one of the three business roles.

## Platform Super Administrator

The v2 platform super administrator is different from the v1 platform admin meaning.

The platform super administrator can:

- create, edit, enable, and disable departments
- create, edit, enable, and disable all accounts
- assign any account's department and fixed roles
- view and maintain all department DJI Cloud API connections
- view plaintext DJI connection credentials
- unbind any resource
- query global audit logs

The platform super administrator boundary should also leave room for future backend modules such as dictionaries, algorithm repositories, and work-order rules.

## Department Administrator

A department administrator manages only their own department.

A department administrator can:

- create or invite members in their own department
- edit business roles for members in their own department
- create and update DJI Cloud API connections in their own department
- view plaintext credentials for their own department's DJI connections
- discover drones and docks from their own department's DJI connections
- bind drones and docks to their own department
- create and manage share groups owned by their own department
- share resources owned by their own department
- unbind resources owned by their own department
- query audit logs for their own department

A department administrator cannot:

- manage child departments as an administrator
- change a member's department
- grant `platform_super_admin`
- grant `department_admin`
- bind resources into another department
- unbind resources owned by another department

Parent department members may use child department resources according to their role permissions, but parent department administrators do not become administrators of child departments.

## Resource Model

Drone and dock should use separate entity tables, but a shared visibility and sharing model.

Recommended entity tables:

- `DroneResource`
- `DockResource`

Recommended shared policy tables:

- `ResourceBinding`
- `ResourceBindingHistory`
- `ResourceSharePermission`

The shared policy tables should identify resource objects with a resource type and resource id, for example:

- `resource_type = drone`
- `resource_type = dock`

Drones and docks may have optional relationships, such as a drone's home dock or current dock. They should not be strongly bound in the first version. A drone and a dock can each be independently bound, unbound, shared, and queried.

## DJI Cloud API Connections

A department administrator first creates one or more DJI Cloud API connections under their own department.

A DJI connection should contain fields such as:

- owning department
- base URL
- username
- password
- login flag
- additional connection parameters
- status
- creator
- timestamps

One department may have multiple DJI connections. A connection can discover multiple drones or docks.

Connection rules:

- only the owning department administrator can use the connection to bind resources into that department
- platform super administrator can view and maintain all connections
- department administrator can view and update plaintext credentials for their own connections
- plaintext credential access must be audited

Binding flow:

1. Department administrator creates a DJI connection.
2. Department administrator uses the connection to discover drones and docks.
3. Department administrator selects a drone or dock and binds it to their own department.
4. If the resource is already actively bound to another department, the API rejects the binding and returns the occupying department information.
5. The bound resource keeps a reference to the DJI connection for later status sync, task dispatch, media refresh, and related upstream operations.

Same DJI credentials may be reused through a single connection for multiple resources. The first version should not introduce a separate shared credential pool.

## Binding Rules

Resource ownership belongs to the department that binds the resource.

Rules:

- a drone or dock cannot be actively bound by two departments at the same time
- after unbinding, another department may bind the same drone or dock
- direct resource transfer between departments is not supported
- unbinding must preserve binding history
- platform super administrator can unbind any resource
- owning department administrator can unbind resources owned by their own department
- shared permissions can never grant unbind permission

Binding history must preserve enough data for audit, including previous department, resource identifier, DJI connection snapshot, actor, timestamp, and action type.

## Visibility Rules

Visibility must be implemented at SQL/queryset level, not by loading full result sets and filtering in Python.

The same visibility rules should apply to all v2 business data that can be traced to a drone or dock:

- drone
- dock
- route
- mission
- media file
- flight record
- future work order

Visibility sources:

1. User's own department resources are visible.
2. Parent department users can see child department resources.
3. Resource-owning departments can share resources through share groups.
4. Share groups target one or more departments.
5. Members of target departments can see resources shared to those departments.

Parent department users treat child department resources like their own department's resources for use, except unbinding. Actual operation ability is still limited by their fixed roles.

## Share Group Model

In the first version, groups are only resource-sharing policy containers. They should not implement a separate "group members can see all data from each other" rule.

Group rules:

- a group is owned by one department
- the owning department administrator creates and manages the group
- a group links one or more target departments
- a group can carry multiple resource share records
- each shared resource has its own operation permission set
- target department members can see resources shared through the group

The final operation permission is:

```text
user fixed role permissions INTERSECT resource share permissions
```

Unbind is excluded from share permissions.

First version resource share permission enum:

- `view`
- `monitor`
- `dispatch_task`
- `review_task`
- `edit_config`

Meanings:

- `view`: view basic drone or dock information, status, and visible related business data
- `monitor`: view real-time flight, historical flight, media, and playback data
- `dispatch_task`: dispatch flight tasks using the resource
- `review_task`: review task execution results related to the resource
- `edit_config`: edit resource business configuration, such as name, notes, grouping metadata, or task parameter configuration

## Authorization Summary

Authorization is layered:

1. Authentication identifies the account through the existing Bearer Token/session mechanism.
2. API v2 loads the account's v2 department and fixed roles.
3. Visibility determines which resources and resource-derived business records are visible.
4. Role permissions determine what the account can do in general.
5. Share permissions narrow operations for cross-department shared resources.
6. Unbind remains restricted to platform super administrator or owning department administrator.

## Audit Logs

Sensitive v2 actions must be audited.

Required audit actions include:

- create DJI connection
- update DJI connection
- view plaintext DJI credentials
- bind resource
- unbind resource
- create share group
- update share group
- add or remove target department from share group
- share resource to group
- update resource share permissions
- remove resource sharing
- change account department
- change account roles
- platform super administrator operation

Audit logs use two department ownership fields:

- `actor_department`: department of the actor who performed the action
- `resource_owner_department`: owning department of the affected resource, when the action involves a resource

Department administrator default audit query should use `actor_department = current department`, which answers "what did people in my department do?"

Resource detail pages can query audit logs by resource owner and resource identity, which answers "what happened to this resource?"

Platform super administrator can query global audit logs.

## API Boundary

All v2 business paths should live under `/api/v2/`.

Responses continue to use the existing `code/msg/data` envelope.

Recommended IAM routes:

```text
GET  /api/v2/iam/me/context
GET  /api/v2/iam/departments
POST /api/v2/iam/departments
PUT  /api/v2/iam/departments/{id}
POST /api/v2/iam/departments/{id}/enable
POST /api/v2/iam/departments/{id}/disable
GET  /api/v2/iam/accounts
POST /api/v2/iam/accounts
PUT  /api/v2/iam/accounts/{id}
PUT  /api/v2/iam/accounts/{id}/roles
GET  /api/v2/iam/roles
```

Recommended resource routes:

```text
GET    /api/v2/resource/dji-connections
POST   /api/v2/resource/dji-connections
GET    /api/v2/resource/dji-connections/{id}
PUT    /api/v2/resource/dji-connections/{id}
POST   /api/v2/resource/dji-connections/{id}/discover
GET    /api/v2/resource/drones
GET    /api/v2/resource/docks
POST   /api/v2/resource/bindings
DELETE /api/v2/resource/bindings/{id}
GET    /api/v2/resource/share-groups
POST   /api/v2/resource/share-groups
PUT    /api/v2/resource/share-groups/{id}
POST   /api/v2/resource/share-groups/{id}/departments
DELETE /api/v2/resource/share-groups/{id}/departments/{department_id}
POST   /api/v2/resource/share-groups/{id}/resources
PUT    /api/v2/resource/share-groups/{id}/resources/{resource_share_id}
DELETE /api/v2/resource/share-groups/{id}/resources/{resource_share_id}
GET    /api/v2/resource/audit-logs
```

Recommended docs routes:

```text
GET /api/v2/docs/
GET /api/v2/docs/schema/
```

The v2 schema urlconf must include only `/api/v2/*` paths. It must not include `apps.dji_bff.urls`.

## Suggested Implementation Order

1. Keep `apps/api_v2/urls.py` separate from `apps/api_v1/urls.py`.
2. Ensure `apps.dji_bff.urls` is not mounted under `/api/v2/`.
3. Add `/api/v2/docs/` and `/api/v2/docs/schema/` using a v2-only urlconf.
4. Add tests proving v2 docs exist and old `__internal__/dji` paths are absent from v2 schema.
5. Create `apps/iam_v2` and `apps/resource_v2`.
6. Add models and migrations for department, roles, account context, connections, resources, sharing, binding history, and audit logs.
7. Implement the first vertical slice: department tree, me context, DJI connection CRUD, resource discovery, resource binding, and visible resource list.
8. Apply the v2 visibility helper to future v2 business resources that derive ownership from drone or dock.

