# PlantUML component analysis

This directory contains one folder per runtime component analyzed from the
current Django API v2 codebase.

Each component folder contains:

- `internal.puml`: implementation structure inside the component.
- `external.puml`: interactions between the component and other system or
  external dependencies.

Component scope used for this pass:

- `access`
- `api_v2`
- `common`
- `config`
- `dji_cloud`
- `dji_mock`
- `iam_v2`
- `inspection_v2`
- `resource_v2`
- `system_v2`

Cross-cutting review aids:

- `engineering_review`: stakeholder context, runtime architecture, core domain
  relationships, request sequence, DJI mission sequence, mission lifecycle,
  review activity, and audit traceability diagrams.
