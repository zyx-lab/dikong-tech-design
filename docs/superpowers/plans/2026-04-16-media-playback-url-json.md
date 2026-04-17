# Media Playback URL JSON Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `GET /api/v1/media-files/{id}/playback-url` so the media API can return a standard JSON envelope whose `data.playback_url` contains the DJI-side video playback URL, while keeping the existing `/playback` redirect contract unchanged.

**Architecture:** Reuse the current `MediaFileViewSet.get_object()` access control and the existing `DjiGateway.get_media_playback_url()` helper. Implement the new endpoint as a separate read-only action that returns JSON instead of `302`, then add explicit OpenAPI coverage so the generated schema documents the new path, status codes, and response shape.

**Tech Stack:** Django, Django REST Framework, drf-spectacular OpenAPI generation, Django `TestCase`, existing mock DJI gateway/live HTTP test harness

---

## File Structure

- Reference: `docs/superpowers/specs/2026-04-16-media-playback-url-json-design.md`
  - Approved design for the new `/playback-url` JSON contract.
- Reference only: `apps/dji_bff/gateway.py:248-265`
  - Existing `get_media_playback_url(dji_file_id)` implementation. Reuse it as-is.
- Reference only: `apps/flight_record/serializers.py:30-48`
  - Keep `flight_record` detail `playback_url` pointing at `/api/v1/media-files/{id}/playback`; do not retarget it to the new JSON endpoint.
- Modify: `apps/media_file/views.py:32-41`
  - Add a response serializer constant for schema generation in Task 2.
- Modify: `apps/media_file/views.py:120-135`
  - Extend `permission_map` with the new `playback_url` action.
- Modify: `apps/media_file/views.py:227-250`
  - Keep existing `/playback` redirect behavior.
  - Add the new `/playback-url` JSON action next to it.
- Modify: `apps/media_file/urls.py:5-17`
  - Register the new `media-file-playback-url` route without disturbing existing routes.
- Modify: `apps/media_file/tests.py:193-233`
  - Add API regression coverage for the JSON endpoint success and non-video rejection paths.
- Modify: `apps/media_file/test_live_api.py:95-151`
  - Add a live HTTP smoke test that exercises `/playback-url` through the mock DJI stack.
- Modify: `apps/api_v1/tests.py:94-113`
  - Add the new path to the public schema surface assertions.
- Modify: `apps/api_v1/tests.py:229-270`
  - Add the new path to method/no-request-body assertions.
- Modify: `apps/api_v1/tests.py:369-405`
  - Add response-code coverage for the new endpoint.
- Modify: `apps/api_v1/tests.py`
  - Add a dedicated schema-shape regression test proving `data.playback_url` is the only success payload field.

### Task 1: Add Runtime Tests and Implement the JSON Endpoint

**Files:**
- Modify: `apps/media_file/tests.py:193-233`
- Modify: `apps/media_file/test_live_api.py:125-151`
- Modify: `apps/media_file/views.py:120-135`
- Modify: `apps/media_file/views.py:227-250`
- Modify: `apps/media_file/urls.py:5-17`
- Test: `apps/media_file/tests.py`
- Test: `apps/media_file/test_live_api.py`

- [ ] **Step 1: Add failing unit tests for the JSON endpoint**

Insert these tests in `apps/media_file/tests.py` immediately after `test_playback_should_redirect_to_mock_dji_download_url_via_real_gateway`:

```python
def test_playback_url_should_return_standard_json_for_video(self):
    media_file = self._create_media(file_name="VID_PLAYBACK_URL.MP4", device_sn="MEDIA-SN-001")
    media_file.media_type = MediaType.VIDEO
    media_file.save(update_fields=["media_type"])

    with patch(
        "apps.media_file.views.DjiGateway.get_media_playback_url",
        return_value="https://playback.example/dji-VID_PLAYBACK_URL.MP4.m3u8",
    ) as get_media_playback_url:
        response = self.client.get(f"/api/v1/media-files/{media_file.id}/playback-url")

    get_media_playback_url.assert_called_once_with("dji-VID_PLAYBACK_URL.MP4")
    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.data["code"], "00000")
    self.assertEqual(response.data["msg"], "success")
    self.assertEqual(
        response.data["data"],
        {"playback_url": "https://playback.example/dji-VID_PLAYBACK_URL.MP4.m3u8"},
    )


def test_playback_url_should_reject_photo_media_file(self):
    media_file = self._create_media(file_name="IMG_PLAYBACK_URL.JPG", device_sn="MEDIA-SN-001")

    response = self.client.get(f"/api/v1/media-files/{media_file.id}/playback-url")

    self.assertEqual(response.status_code, 400)
    self.assertEqual(response.data["code"], "B0001")
    self.assertEqual(response.data["data"], {"media_type": ["该媒体不支持 playback"]})
```

- [ ] **Step 2: Add a failing live HTTP smoke test**

Insert this test at the end of `LiveMediaFileApiTests` in `apps/media_file/test_live_api.py`:

```python
def test_playback_url_should_follow_live_http_contract(self):
    self.media_file.media_type = MediaType.VIDEO
    self.media_file.file_name = "MEDIA_LIVE.MP4"
    self.media_file.file_url = "https://example.com/MEDIA_LIVE.MP4"
    self.media_file.save(update_fields=["media_type", "file_name", "file_url"])

    response = self.client.get(f"/api/v1/media-files/{self.media_file.id}/playback-url")

    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.json()["code"], "00000")
    self.assertEqual(response.json()["msg"], "success")
    self.assertEqual(
        response.json()["data"],
        {"playback_url": "/__mock-dji__/_downloads/media/media-live-file"},
    )
```

- [ ] **Step 3: Run the targeted tests to verify red**

Run:

```bash
./.venv/bin/python manage.py test \
  apps.media_file.tests.MediaFileApiTests.test_playback_url_should_return_standard_json_for_video \
  apps.media_file.tests.MediaFileApiTests.test_playback_url_should_reject_photo_media_file \
  apps.media_file.test_live_api.LiveMediaFileApiTests.test_playback_url_should_follow_live_http_contract \
  -v 2
```

Expected: the suite fails because `/api/v1/media-files/{id}/playback-url` is not registered yet, so the new tests should observe `404` instead of the documented `200`/`400` contract.

- [ ] **Step 4: Implement the minimal JSON endpoint**

In `apps/media_file/views.py`, extend `permission_map` and add the new action directly below `playback()`:

```python
permission_map = {
    "list": "media_file.view_media_file",
    "retrieve": "media_file.view_media_file",
    "download": "media_file.view_media_file",
    "playback": "media_file.view_media_file",
    "playback_url": "media_file.view_media_file",
    "destroy": "media_file.manage_media_file",
    "bind_mission": "media_file.manage_media_file",
}
```

```python
@action(detail=True, methods=["get"], url_path="playback-url")
def playback_url(self, request, *args, **kwargs):
    media_file = self.get_object()
    if media_file.media_type != MediaType.VIDEO:
        return Response(
            validation_error_payload({"media_type": ["该媒体不支持 playback"]}),
            status=status.HTTP_400_BAD_REQUEST,
        )
    playback_url = DjiGateway().get_media_playback_url(media_file.dji_index.dji_file_id)
    return Response({"playback_url": playback_url}, status=status.HTTP_200_OK)
```

In `apps/media_file/urls.py`, register the route alongside the existing redirect action:

```python
media_file_playback_url = MediaFileViewSet.as_view({"get": "playback_url"})
```

```python
path("media-files/<int:pk>/playback-url", media_file_playback_url, name="media-file-playback-url"),
```

- [ ] **Step 5: Run the runtime tests to verify green**

Run:

```bash
./.venv/bin/python manage.py test \
  apps.media_file.tests.MediaFileApiTests.test_playback_url_should_return_standard_json_for_video \
  apps.media_file.tests.MediaFileApiTests.test_playback_url_should_reject_photo_media_file \
  apps.media_file.tests.MediaFileApiTests.test_playback_should_redirect_to_dji_playback_url_for_video \
  apps.media_file.test_live_api.LiveMediaFileApiTests.test_playback_url_should_follow_live_http_contract \
  -v 2
```

Expected: all four tests PASS. The new unit test should show a standard business envelope, the existing `/playback` redirect regression should stay green, and the live test should return the mock DJI playback URL inside `data.playback_url`.

- [ ] **Step 6: Commit the runtime endpoint**

Run:

```bash
git add apps/media_file/tests.py apps/media_file/test_live_api.py apps/media_file/views.py apps/media_file/urls.py
git commit -m "feat(media_file): add playback-url json endpoint"
```

### Task 2: Add OpenAPI Schema Regression Coverage and Explicit Documentation

**Files:**
- Modify: `apps/api_v1/tests.py:94-113`
- Modify: `apps/api_v1/tests.py:229-270`
- Modify: `apps/api_v1/tests.py:369-405`
- Modify: `apps/api_v1/tests.py` (new schema-shape test near the existing media schema assertions)
- Modify: `apps/media_file/views.py:1-41`
- Modify: `apps/media_file/views.py:241-260`
- Test: `apps/api_v1/tests.py`
- Test: `apps/media_file/tests.py`
- Test: `apps/media_file/test_live_api.py`

- [ ] **Step 1: Extend the schema surface assertions**

In `apps/api_v1/tests.py`, update the existing assertions with these exact additions:

Inside `test_business_schema_should_expose_refactored_paths`:

```python
self.assertIn("/api/v1/media-files/{id}/playback-url", paths)
```

Inside the `expected_methods` dict in `test_business_schema_should_lock_current_operation_surface_and_bodyless_actions`:

```python
"/api/v1/media-files/{id}/playback-url": {"get"},
```

Inside the no-request-body assertion tuple in that same test:

```python
("/api/v1/media-files/{id}/playback-url", "get"),
```

Inside the `expected_responses` dict in `test_business_schema_should_describe_current_business_error_responses`:

```python
("/api/v1/media-files/{id}/playback-url", "get"): {"200", "400", "401", "403", "404", "500"},
```

- [ ] **Step 2: Add a failing schema-shape regression test**

Insert this test in `OpenApiDocsTests` near the existing media/flight-record schema assertions:

```python
def test_business_schema_should_describe_media_playback_url_response_shape(self):
    response = self.client.get("/api/v1/docs/schema/")
    self.assertEqual(response.status_code, 200)
    schema = response.json()

    operation = self._operation(schema, path="/api/v1/media-files/{id}/playback-url", method="get")
    success_schema = self._resolve_component_schema(
        schema,
        operation["responses"]["200"]["content"]["application/json"]["schema"],
    )
    self.assertIn("data", success_schema["properties"])
    data_schema = self._resolve_component_schema(schema, success_schema["properties"]["data"])

    self.assertEqual(set(data_schema["properties"].keys()), {"playback_url"})
    self.assertEqual(data_schema["properties"]["playback_url"]["type"], "string")
```

- [ ] **Step 3: Run the schema tests to verify red**

Run:

```bash
./.venv/bin/python manage.py test \
  apps.api_v1.tests.OpenApiDocsTests.test_business_schema_should_expose_refactored_paths \
  apps.api_v1.tests.OpenApiDocsTests.test_business_schema_should_lock_current_operation_surface_and_bodyless_actions \
  apps.api_v1.tests.OpenApiDocsTests.test_business_schema_should_describe_media_playback_url_response_shape \
  apps.api_v1.tests.OpenApiDocsTests.test_business_schema_should_describe_current_business_error_responses \
  -v 2
```

Expected: at least `test_business_schema_should_describe_media_playback_url_response_shape` fails because the new action is still documented with the wrong success payload shape and without the explicit business error responses.

- [ ] **Step 4: Add the explicit OpenAPI response contract**

In `apps/media_file/views.py`, extend the imports and add a dedicated response serializer constant near the other media response constants:

```python
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view, inline_serializer
from rest_framework import mixins, serializers, status, viewsets
```

```python
MEDIA_FILE_PLAYBACK_URL_RESULT_SERIALIZER = inline_serializer(
    name="MediaFilePlaybackUrlResult",
    fields={"playback_url": serializers.CharField(help_text="DJI 侧视频播放地址。")},
)
MEDIA_FILE_PLAYBACK_URL_RESPONSE = object_envelope_serializer(
    "MediaFilePlaybackUrlResponse",
    MEDIA_FILE_PLAYBACK_URL_RESULT_SERIALIZER,
)
```

Then decorate the new action with the explicit schema contract:

```python
@extend_schema(
    summary="获取媒体播放地址（JSON）",
    parameters=[TENANT_CODE_HEADER_PARAMETER],
    responses={
        200: OpenApiResponse(response=MEDIA_FILE_PLAYBACK_URL_RESPONSE),
        400: BUSINESS_INVALID_PARAMS_RESPONSE,
        401: BUSINESS_PERMISSION_DENIED_RESPONSE,
        403: BUSINESS_PERMISSION_DENIED_RESPONSE,
        404: BUSINESS_NOT_FOUND_RESPONSE,
        500: BUSINESS_INTERNAL_ERROR_RESPONSE,
    },
    tags=["Business API - Media File"],
)
@action(detail=True, methods=["get"], url_path="playback-url")
def playback_url(self, request, *args, **kwargs):
    media_file = self.get_object()
    if media_file.media_type != MediaType.VIDEO:
        return Response(
            validation_error_payload({"media_type": ["该媒体不支持 playback"]}),
            status=status.HTTP_400_BAD_REQUEST,
        )
    playback_url = DjiGateway().get_media_playback_url(media_file.dji_index.dji_file_id)
    return Response({"playback_url": playback_url}, status=status.HTTP_200_OK)
```

- [ ] **Step 5: Run schema and runtime tests to verify green**

Run:

```bash
./.venv/bin/python manage.py test \
  apps.media_file.tests.MediaFileApiTests.test_playback_url_should_return_standard_json_for_video \
  apps.media_file.tests.MediaFileApiTests.test_playback_url_should_reject_photo_media_file \
  apps.media_file.tests.MediaFileApiTests.test_playback_should_redirect_to_dji_playback_url_for_video \
  apps.media_file.test_live_api.LiveMediaFileApiTests.test_playback_url_should_follow_live_http_contract \
  apps.api_v1.tests.OpenApiDocsTests.test_business_schema_should_expose_refactored_paths \
  apps.api_v1.tests.OpenApiDocsTests.test_business_schema_should_lock_current_operation_surface_and_bodyless_actions \
  apps.api_v1.tests.OpenApiDocsTests.test_business_schema_should_describe_media_playback_url_response_shape \
  apps.api_v1.tests.OpenApiDocsTests.test_business_schema_should_describe_current_business_error_responses \
  -v 2
```

Expected: all tests PASS. The schema should now expose `/api/v1/media-files/{id}/playback-url` as a `GET` operation with no request body, standard error responses, and a success payload whose `data` object contains only `playback_url`.

- [ ] **Step 6: Commit the schema/documentation work**

Run:

```bash
git add apps/api_v1/tests.py apps/media_file/views.py
git commit -m "docs(api): document media playback-url endpoint"
```

### Task 3: Run the Full Verification Bundle

**Files:**
- Test: `apps/media_file/tests.py`
- Test: `apps/media_file/test_live_api.py`
- Test: `apps/api_v1/tests.py`

- [ ] **Step 1: Run the full regression bundle**

Run:

```bash
./.venv/bin/python manage.py test \
  apps.media_file.tests.MediaFileApiTests \
  apps.media_file.test_live_api.LiveMediaFileApiTests \
  apps.api_v1.tests.OpenApiDocsTests \
  -v 2
```

Expected: the media unit suite, the live HTTP smoke suite, and the OpenAPI docs suite all PASS without touching unrelated modules.

- [ ] **Step 2: Confirm the working tree is clean except for intentional plan/spec docs**

Run:

```bash
git status --short
```

Expected: no unexpected modifications in code files. If unrelated untracked docs or local research files already existed before execution, leave them untouched and do not stage them into the feature commits.
