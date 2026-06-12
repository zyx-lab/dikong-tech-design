# Independent Algorithm Platform Design

Date: 2026-06-12
Status: accepted for implementation planning

## Summary

Build the algorithm platform as a separate repository and independently deployable service. It must not depend on this Django codebase, Django models, or the Django database. Django is only one future API consumer.

The first implementation should use NVIDIA Triton Inference Server as the model-serving runtime and a custom lightweight management layer for the product-specific parts that Triton does not provide: algorithm registration, plugin configuration, pipeline composition, image inference requests, structured detection-result storage, and a minimal management UI.

The first milestone is intentionally narrow:

- Register model artifacts and Triton deployments.
- Explicitly load and unload Triton models.
- Register algorithms and algorithm versions.
- Enable and disable algorithm versions.
- Configure a parallel pipeline with multiple algorithm nodes.
- Run image single-frame inference through the pipeline.
- Persist structured object-detection results.
- Provide a minimal standalone Web UI.

The first milestone does not include Django integration code, permission systems, multi-tenancy, training, labeling, video-file inference, live-stream inference, complex DAG execution, or runtime upload of arbitrary Python plugin code.

## Relationship To Existing Django Algorithm Design

This document is not a replacement for `docs/superpowers/specs/2026-05-29-v2-algorithm-realtime-analysis-design.md`.

That earlier document describes a Django-side integration pattern for mission-driven real-time algorithm sessions, live-stream startup, algorithm callbacks, event storage, and SSE event delivery.

This document describes the independent algorithm platform that can later serve as the external algorithm system behind that Django integration. The boundary is:

- Django owns missions, drones, DJI live startup, business permissions, and business-facing algorithm events.
- The independent algorithm platform owns model serving, algorithm definitions, algorithm versions, pipeline execution, and raw structured inference results.
- The two systems communicate through stable REST APIs when integration begins.

## Hard Decisions

1. The algorithm platform will live in an independent repository.
2. The platform is an internal infrastructure service and will not implement users, roles, tenants, or API tokens.
3. Network access control, firewall rules, Docker networks, Kubernetes network policies, or reverse-proxy restrictions must protect the service.
4. Triton is the inference runtime, not the algorithm management platform.
5. The management layer is custom because the needed product shape is narrower and more specific than general MLOps, training, labeling, or experiment platforms.
6. First milestone inference input is image single-frame only.
7. The platform stores structured object-detection results in PostgreSQL.
8. First milestone pipeline execution is parallel only.
9. Serial or DAG pipeline shape may be represented as metadata, but the executor will reject unsupported execution modes.
10. Built-in plugin types are allowed; runtime upload of arbitrary Python code is not allowed in the first milestone.

## Source Notes

- Triton supports explicit model management through model control modes. The platform should use explicit model loading and unloading rather than relying on repository polling.
  Source: https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/user_guide/model_management.html
- Triton exposes HTTP and gRPC inference protocols. The first milestone should use HTTP for easier debugging.
  Source: https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/protocol/README.html
- Triton supports multiple backends, including ONNX Runtime, TensorRT, PyTorch, and Python backend.
  Source: https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/backend/README.html
- Triton ensemble models are useful for fixed tensor-level pipelines, but they should not be the first milestone's business pipeline engine.
  Source: https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/user_guide/ensemble_models.html
- Ultralytics YOLO has licensing implications for closed commercial usage. The first demo should prefer a permissively licensed model path such as YOLOX or MMDetection-derived ONNX where practical.
  Sources: https://www.ultralytics.com/license, https://github.com/Megvii-BaseDetection/YOLOX, https://github.com/open-mmlab/mmdetection

## Architecture

The first milestone consists of five components.

### Algorithm API

The API service is the platform's control plane and inference entry point. It should be implemented with FastAPI and expose OpenAPI documentation.

Responsibilities:

- model artifact registration
- Triton deployment registration
- model load and unload operations
- algorithm and algorithm-version management
- plugin configuration validation
- pipeline management
- image inference request handling
- structured result query APIs
- health checks

The API service must not import Django modules, read Django settings, or use the Django database.

### Triton Inference Server

Triton serves model inference requests and owns runtime model availability.

Startup constraints:

- run from the official Triton container
- mount a model repository volume
- start with explicit model control mode
- expose HTTP to the Algorithm API service

The Algorithm API maps its `ModelDeployment` records to Triton model names and versions.

### Algorithm Executor

The executor performs image preprocessing, plugin execution, Triton calls, postprocessing, result normalization, and database writes.

For the first milestone it may run in the same process as the API service. It should still be isolated behind service classes so that live-stream workers can reuse the same pipeline execution logic later.

### PostgreSQL

PostgreSQL stores all platform metadata and structured inference results.

It is separate from the Django database.

### Minimal Web UI

The Web UI is an internal operations and validation interface. It is not the primary integration point for Django.

The UI should cover:

- dashboard
- models
- algorithms
- pipelines
- image test and result viewer

It must not include login, roles, tenant selection, labeling tools, dataset management, or training workflows.

## Repository And Deployment Shape

Implementation should happen in a new independent repository. A suggested repository structure:

```text
algorithm-platform/
  backend/
    app/
      main.py
      api/
      core/
      db/
      models/
      schemas/
      services/
      plugins/
      triton/
      inference/
      storage/
    tests/
    alembic/
    pyproject.toml
  frontend/
    src/
    package.json
    vite.config.ts
  deploy/
    docker-compose.yml
    triton/
      model-repository/
  docs/
    api.md
    deployment.md
```

First deployment target:

- Docker Compose on a single NVIDIA GPU server.
- The same component boundaries should be compatible with later Kubernetes deployment.

Compose services:

- `algorithm-api`
- `algorithm-web`
- `postgres`
- `triton`

Optional later services:

- `minio` for object storage
- stream worker for live-stream inference
- Redis or another queue only when asynchronous video or live-stream workloads require it

## Technology Stack

Backend:

- FastAPI
- SQLAlchemy 2.x
- Alembic
- PostgreSQL
- Pydantic
- `tritonclient[http]`
- Pillow or `opencv-python-headless`
- NumPy
- pytest

Inference:

- NVIDIA Triton Inference Server
- ONNX Runtime backend for the first demo model
- TensorRT backend can be added later

Frontend:

- React
- Vite
- TypeScript
- Ant Design or another table/form-oriented component library

The interface should look like an operations console, not a marketing site.

## Data Model

The model should keep algorithm-management state separate from inference-result state.

### ModelArtifact

Represents a model file or model package.

Fields:

- `id`
- `name`
- `format`: `onnx`, `tensorrt`, `torchscript`, or `python`
- `backend`: `onnxruntime`, `tensorrt`, `pytorch`, or `python`
- `artifact_uri`
- `artifact_sha256`
- `input_schema`
- `output_schema`
- `created_at`
- `updated_at`

### ModelDeployment

Represents a Triton-loadable deployment of a model artifact.

Fields:

- `id`
- `model_artifact_id`
- `triton_model_name`
- `triton_model_version`
- `repository_path`
- `status`: `registered`, `loaded`, `unloaded`, or `failed`
- `last_loaded_at`
- `last_unloaded_at`
- `last_error`
- `created_at`
- `updated_at`

### Algorithm

Represents the business-level algorithm identity.

Examples:

- `person_detector`
- `vehicle_detector`
- `smoke_detector`

Fields:

- `id`
- `code`
- `name`
- `description`
- `enabled`
- `created_at`
- `updated_at`

### AlgorithmVersion

Represents one executable version of an algorithm.

Fields:

- `id`
- `algorithm_id`
- `version`
- `model_deployment_id`
- `plugin_type`
- `plugin_config`
- `class_map`
- `default_confidence_threshold`
- `enabled`
- `created_at`
- `updated_at`

### AlgorithmPlugin

Plugins should be built into the platform for the first milestone. They are selected through `plugin_type` and configured through validated JSON.

Initial plugin types:

- `triton_yolo_onnx`
- `triton_generic_detector`

Plugin responsibilities:

- validate configuration
- preprocess image input
- call Triton through the deployment abstraction
- postprocess Triton outputs
- return canonical detection results

### Pipeline

Represents an algorithm combination.

Fields:

- `id`
- `code`
- `name`
- `description`
- `mode`: `parallel` or `dag`
- `enabled`
- `created_at`
- `updated_at`

First milestone execution only supports `parallel`.

### PipelineNode

Represents one algorithm node in a pipeline.

Fields:

- `id`
- `pipeline_id`
- `algorithm_version_id`
- `node_key`
- `display_name`
- `enabled`
- `depends_on`
- `config_override`
- `created_at`
- `updated_at`

`depends_on` is reserved for future DAG execution. It must not affect first milestone execution.

### InputAsset

Represents a source image.

Fields:

- `id`
- `source_type`: first milestone fixed to `image`
- `uri`
- `sha256`
- `width`
- `height`
- `mime_type`
- `created_at`

Future source types may include `video_file` and `live_stream`.

### InferenceRun

Represents one pipeline execution.

Fields:

- `id`
- `pipeline_id`
- `input_asset_id`
- `status`: `queued`, `running`, `succeeded`, `failed`, or `cancelled`
- `started_at`
- `finished_at`
- `duration_ms`
- `error_code`
- `error_message`
- `created_at`

Even if first milestone image inference is synchronous at the HTTP layer, every request must create an `InferenceRun`.

### DetectionResult

Represents one normalized object-detection result.

Fields:

- `id`
- `run_id`
- `pipeline_node_id`
- `algorithm_id`
- `algorithm_version_id`
- `class_id`
- `class_label`
- `confidence`
- `x_min_px`
- `y_min_px`
- `x_max_px`
- `y_max_px`
- `raw_result`
- `created_at`

Pixel coordinates are canonical. Normalized coordinates should not be stored redundantly in the first milestone.

## API Design

Base path: `/api/v1`.

### Health

```text
GET /api/v1/health
GET /api/v1/health/db
GET /api/v1/health/triton
```

### Models

```text
POST   /api/v1/models/artifacts
GET    /api/v1/models/artifacts
GET    /api/v1/models/artifacts/{id}

POST   /api/v1/models/deployments
GET    /api/v1/models/deployments
GET    /api/v1/models/deployments/{id}
POST   /api/v1/models/deployments/{id}/load
POST   /api/v1/models/deployments/{id}/unload
GET    /api/v1/models/deployments/{id}/status
```

### Algorithms

```text
POST   /api/v1/algorithms
GET    /api/v1/algorithms
GET    /api/v1/algorithms/{id}
PATCH  /api/v1/algorithms/{id}

POST   /api/v1/algorithms/{id}/versions
GET    /api/v1/algorithms/{id}/versions
POST   /api/v1/algorithm-versions/{id}/enable
POST   /api/v1/algorithm-versions/{id}/disable
```

Algorithm version enablement is platform-level availability. It is not equivalent to Triton model loading.

### Pipelines

```text
POST   /api/v1/pipelines
GET    /api/v1/pipelines
GET    /api/v1/pipelines/{id}
PATCH  /api/v1/pipelines/{id}
POST   /api/v1/pipelines/{id}/nodes
PATCH  /api/v1/pipelines/{id}/nodes/{node_id}
DELETE /api/v1/pipelines/{id}/nodes/{node_id}
POST   /api/v1/pipelines/{id}/enable
POST   /api/v1/pipelines/{id}/disable
```

### Inference

```text
POST /api/v1/inference/image
GET  /api/v1/inference/runs
GET  /api/v1/inference/runs/{id}
GET  /api/v1/inference/runs/{id}/results
```

`POST /api/v1/inference/image` should accept multipart form data:

```text
pipeline_id=<uuid>
image=<file>
options={"confidence_threshold":0.35}
```

Response:

```json
{
  "run_id": "uuid",
  "status": "succeeded",
  "input_asset_id": "uuid",
  "summary": {
    "total_detections": 8,
    "by_algorithm": [
      {"algorithm": "person_detector", "count": 3},
      {"algorithm": "vehicle_detector", "count": 5}
    ]
  }
}
```

Detection details should be fetched through `GET /api/v1/inference/runs/{id}/results`.

## Execution Flow

Image pipeline execution:

1. Receive multipart image request and `pipeline_id`.
2. Validate image format.
3. Save image and create `InputAsset`.
4. Create `InferenceRun` with `running` status.
5. Load pipeline and enabled nodes.
6. Validate that the pipeline is enabled.
7. Reject non-`parallel` execution modes with `unsupported_pipeline_mode`.
8. Validate every algorithm version is enabled.
9. Validate every bound model deployment is loaded in Triton.
10. Execute each enabled pipeline node concurrently.
11. For each node: preprocess image, call Triton, postprocess outputs, normalize detections.
12. If any node fails, mark the whole run as failed and do not persist partial detection results.
13. If all nodes succeed, persist `DetectionResult` rows.
14. Mark run as succeeded.
15. Return run summary.

The first milestone uses all-or-nothing run semantics. Partial success is intentionally deferred.

## Error Codes

Stable error codes:

- `model_not_loaded`
- `algorithm_disabled`
- `pipeline_disabled`
- `unsupported_pipeline_mode`
- `invalid_image`
- `triton_unavailable`
- `triton_inference_failed`
- `plugin_config_invalid`
- `inference_run_failed`

Every failed inference run must store `error_code` and `error_message`.

## Minimal Web UI

Pages:

1. Dashboard
2. Models
3. Algorithms
4. Pipelines
5. Image Test

Dashboard:

- API health
- DB health
- Triton health
- loaded model count
- enabled algorithm count
- enabled pipeline count
- recent inference runs

Models:

- list artifacts
- list deployments
- load and unload deployment
- show Triton status and last error

Algorithms:

- create and edit algorithms
- create versions
- bind model deployment
- choose plugin type
- edit plugin config
- edit class map
- enable and disable versions

Pipelines:

- create and edit pipelines
- add and remove parallel nodes
- enable and disable pipelines

Image Test:

- choose pipeline
- upload image
- run inference
- display original image with detection boxes
- display result table
- link to run detail

## First Demo Model Path

The first demo should use an ONNX object-detection model with permissive licensing where practical.

Recommended path:

1. Choose YOLOX or another permissively licensed detector.
2. Prepare ONNX model.
3. Place it in Triton model repository:

```text
model-repository/
  yolox_s/
    config.pbtxt
    1/
      model.onnx
```

4. Register `ModelArtifact`.
5. Register `ModelDeployment` with `triton_model_name=yolox_s`.
6. Load deployment through the platform API.
7. Register `Algorithm`.
8. Register `AlgorithmVersion` using `triton_yolo_onnx`.
9. Create a parallel pipeline with at least two nodes.
10. Upload a test image and verify structured results.

## Future Live-Stream Extension

The first milestone must not implement live-stream processing, but the design must leave a clean path.

Future additions:

- `StreamSource`
- `StreamSession`
- stream worker process
- frame sampling policy
- frame references or frame assets
- frame timestamp fields in detection results
- event emission to Django or another business system

Current fields that intentionally support this future:

- `InputAsset.source_type`
- `InferenceRun` naming rather than `ImageRun`
- plugin interface based on image/frame processing
- pipeline executor separated from API route handlers

Do not add RTSP, RTMP, WebRTC, GPU decode, message queues, or SSE in the first milestone.

## Django Integration Contract

Django integration is not part of the first milestone implementation, but the platform should be callable by Django through:

```text
GET  /api/v1/pipelines
POST /api/v1/inference/image
GET  /api/v1/inference/runs/{id}
GET  /api/v1/inference/runs/{id}/results
```

Future live-stream integration may add:

```text
POST /api/v1/streams
POST /api/v1/stream-sessions
GET  /api/v1/stream-sessions/{id}/events
```

The algorithm platform must not require Django session cookies, Django bearer tokens, Django users, Django tenant IDs, or Django database access.

## First Milestone Acceptance Criteria

1. The independent repository starts API, Web UI, PostgreSQL, and Triton through Docker Compose.
2. The Web UI shows API, DB, and Triton health.
3. A demo ONNX detection model can be registered.
4. A Triton deployment can be explicitly loaded and unloaded through the platform API.
5. At least two algorithm versions can be created.
6. Those algorithm versions can bind to the same demo model with different configuration.
7. A parallel pipeline can include at least two enabled algorithm nodes.
8. `POST /api/v1/inference/image` runs the pipeline against one uploaded image.
9. The pipeline actually executes multiple algorithm nodes.
10. Successful inference creates an `InferenceRun` with `succeeded` status.
11. Failed inference creates an `InferenceRun` with a stable `error_code`.
12. Detection results are stored with class, confidence, pixel bounding box, algorithm version, and pipeline node.
13. `GET /api/v1/inference/runs/{id}/results` returns structured results.
14. The Image Test UI displays the uploaded image, detection boxes, and result table.
15. No Django process, module, database, or settings file is required to run the platform.

## Non-Goals

- Django app implementation
- user login
- role-based access control
- tenant isolation
- API token management
- public Internet exposure
- training
- labeling
- dataset management
- experiment tracking
- model conversion service
- automatic TensorRT engine build service
- video-file inference
- live-stream inference
- complex DAG execution
- cross-algorithm result fusion
- runtime upload of arbitrary Python plugin code
- Kubernetes-first deployment

## Open Implementation Notes

- The first implementation plan should start by scaffolding the independent repository and Docker Compose stack.
- Tests should be written around the API and service boundaries before the full UI is built.
- Triton calls should be abstracted behind a small client interface so tests can mock model loading and inference.
- The first UI can be plain and operational; visual polish is secondary to state correctness.
