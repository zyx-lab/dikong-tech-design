# Flight Record Playback URL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose a platform `playback_url` for `flight_record` detail media items and add a media playback proxy endpoint backed by DJI `.../playback-url`.

**Architecture:** Keep the existing `download_url` model as the reference pattern. Add one new DJI gateway method and mock endpoint for playback URL resolution, one new business API action at `/api/v1/media-files/{id}/playback`, and one new read-only `playback_url` field on `FlightRecordDetailSerializer`’s nested `media_files[]` items. Do not expand `MediaFileReadSerializer` or change flight-record list responses.

**Tech Stack:** Django ORM, DRF viewsets/actions, drf-spectacular schema tests, Django TestCase, mock DJI upstream.

---

## File Structure

- Modify: `apps/dji_bff/gateway.py`
  - Add `get_media_playback_url()` mirroring existing `get_media_url()` semantics.
- Modify: `apps/dji_bff/tests.py`
  - Add gateway unit coverage for the new playback URL method.
- Modify: `apps/dji_mock/urls.py`
  - Register mock DJI `.../playback-url` route.
- Modify: `apps/dji_mock/views.py`
  - Add a playback redirect handler parallel to `media_download_url`.
- Modify: `apps/dji_mock/tests.py`
  - Add mock upstream playback redirect coverage.
- Modify: `apps/media_file/views.py`
  - Add `playback()` action beside `download()`.
- Modify: `apps/media_file/urls.py`
  - Expose `/api/v1/media-files/{id}/playback`.
- Modify: `apps/media_file/tests.py`
  - Add business API coverage for video redirect and photo rejection.
- Modify: `apps/flight_record/serializers.py`
  - Add nested `playback_url` field only on `FlightRecordMediaFileSerializer`.
- Modify: `apps/flight_record/tests.py`
  - Extend flight-record detail tests for `playback_url` on video and photo media.
- Modify: `apps/api_v1/tests.py`
  - Update schema assertion for `flight_record` detail media item fields.

### Task 1: Add DJI Playback URL Support in the Gateway and Mock Upstream

**Files:**
- Modify: `apps/dji_mock/urls.py`
- Modify: `apps/dji_mock/views.py`
- Modify: `apps/dji_mock/tests.py`
- Modify: `apps/dji_bff/gateway.py`
- Modify: `apps/dji_bff/tests.py`

- [ ] **Step 1: Write the failing mock upstream playback redirect test**

In `apps/dji_mock/tests.py`, add this assertion block right after the existing media `/url` assertion in `test_wayline_job_and_media_endpoints_should_offer_stateful_minimal_behaviour`:

```python
        playback_response = self.client.get(
            "/__mock-dji__/api/v1/media/workspaces/mock-workspace-001/files/mock-file-001/playback-url",
            HTTP_X_AUTH_TOKEN=token,
            follow=False,
        )
        self.assertEqual(playback_response.status_code, 302)
        self.assertEqual(playback_response["Location"], "/__mock-dji__/_downloads/media/mock-file-001")
```

- [ ] **Step 2: Write the failing gateway playback URL test**

In `apps/dji_bff/tests.py`, add this test after `test_download_route_file_should_reject_blank_url`:

```python
    def test_get_media_playback_url_should_return_redirect_location(self):
        gateway = DjiGateway(base_url="http://mock-dji")
        upstream_response = GatewayResponse(
            status_code=302,
            headers={"Location": "https://playback.example/media-001.m3u8"},
            data=None,
        )

        with patch.object(gateway, "_workspace_id", return_value="mock-workspace-001"):
            with patch.object(gateway, "_request_json", return_value=upstream_response) as request_mock:
                playback_url = gateway.get_media_playback_url("media-001")

        self.assertEqual(playback_url, "https://playback.example/media-001.m3u8")
        self.assertEqual(
            request_mock.call_args.args,
            ("GET", "/api/v1/media/workspaces/mock-workspace-001/files/media-001/playback-url"),
        )
        self.assertFalse(request_mock.call_args.kwargs["follow_redirects"])
```

- [ ] **Step 3: Run the focused tests to confirm RED**

Run:

```bash
./.venv/bin/python manage.py test \
  apps.dji_mock.tests.DjiMockServerTests.test_wayline_job_and_media_endpoints_should_offer_stateful_minimal_behaviour \
  apps.dji_bff.tests.DjiGatewayPaginationTests.test_get_media_playback_url_should_return_redirect_location
```

Expected:

- The mock test fails because `/playback-url` is not registered yet
- The gateway test fails because `get_media_playback_url()` does not exist yet

- [ ] **Step 4: Add the mock DJI playback route**

In `apps/dji_mock/urls.py`, add the new path beside the existing media `/url` route:

```python
    path("api/v1/media/workspaces/<str:workspace_id>/files/<str:file_id>/url", views.media_download_url),
    path("api/v1/media/workspaces/<str:workspace_id>/files/<str:file_id>/playback-url", views.media_playback_url),
```

In `apps/dji_mock/views.py`, add the handler directly below `media_download_url`:

```python
@protected_mock_dji_view
def media_playback_url(request, workspace_id: str, file_id: str):
    if request.method != "GET":
        raise Http404
    if workspace_id != mock_dji_state.current_workspace_payload()["workspace_id"]:
        return _error("C0404", "workspace not found", status=404)
    if file_id not in mock_dji_state.media_files:
        return _error("C0404", "media file not found", status=404)
    return HttpResponseRedirect(f"/__mock-dji__/_downloads/media/{file_id}")
```

- [ ] **Step 5: Add the minimal gateway method**

In `apps/dji_bff/gateway.py`, add this method right below `get_media_url()`:

```python
    def get_media_playback_url(self, dji_file_id: str):
        workspace_id = self._workspace_id()
        response = self._request_json(
            "GET",
            f"/api/v1/media/workspaces/{workspace_id}/files/{dji_file_id}/playback-url",
            follow_redirects=False,
        )
        if "Location" in response.headers:
            return response.headers["Location"]
        payload = response.data
        if isinstance(payload, dict):
            playback_url = self._extract_download_url(payload)
            if playback_url:
                return playback_url
        raise DjiGatewayUpstreamError("未获取到媒体播放地址", status_code=502, data=payload)
```

- [ ] **Step 6: Re-run the focused tests to confirm GREEN**

Run:

```bash
./.venv/bin/python manage.py test \
  apps.dji_mock.tests.DjiMockServerTests.test_wayline_job_and_media_endpoints_should_offer_stateful_minimal_behaviour \
  apps.dji_bff.tests.DjiGatewayPaginationTests.test_get_media_playback_url_should_return_redirect_location
```

Expected:

- Both tests pass
- Django reports `Ran 2 tests` and `OK`

- [ ] **Step 7: Run the affected suites**

Run:

```bash
./.venv/bin/python manage.py test apps.dji_mock.tests
./.venv/bin/python manage.py test apps.dji_bff.tests
```

Expected:

- Both suites pass
- No regressions in existing media download behavior

- [ ] **Step 8: Commit the gateway/mock support**

```bash
git add apps/dji_mock/urls.py apps/dji_mock/views.py apps/dji_mock/tests.py apps/dji_bff/gateway.py apps/dji_bff/tests.py
git commit -m "feat(dji_bff): add media playback url gateway support"
```

### Task 2: Add the Media Playback Business Endpoint

**Files:**
- Modify: `apps/media_file/views.py`
- Modify: `apps/media_file/urls.py`
- Modify: `apps/media_file/tests.py`

- [ ] **Step 1: Write the failing redirect test for video media**

In `apps/media_file/tests.py`, add this test right after `test_download_should_redirect_to_dji_url`:

```python
    def test_playback_should_redirect_to_dji_playback_url_for_video(self):
        media_file = self._create_media(file_name="VID_PLAYBACK.MP4", device_sn="MEDIA-SN-001")
        media_file.media_type = MediaType.VIDEO
        media_file.save(update_fields=["media_type"])

        response = self.client.get(f"/api/v1/media-files/{media_file.id}/playback")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/__mock-dji__/_downloads/media/dji-VID_PLAYBACK.MP4")
```

- [ ] **Step 2: Write the failing rejection test for photo media**

Add this test immediately after it:

```python
    def test_playback_should_reject_photo_media_file(self):
        media_file = self._create_media(file_name="IMG_PLAYBACK.JPG", device_sn="MEDIA-SN-001")

        response = self.client.get(f"/api/v1/media-files/{media_file.id}/playback")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "B0001")
        self.assertEqual(response.data["data"], {"media_type": ["该媒体不支持 playback"]})
```

- [ ] **Step 3: Run the focused tests to confirm RED**

Run:

```bash
./.venv/bin/python manage.py test \
  apps.media_file.tests.MediaFileApiTests.test_playback_should_redirect_to_dji_playback_url_for_video \
  apps.media_file.tests.MediaFileApiTests.test_playback_should_reject_photo_media_file
```

Expected:

- Both tests fail because the `/playback` endpoint does not exist yet

- [ ] **Step 4: Add the playback route**

In `apps/media_file/urls.py`, add the new view binding and URL:

```python
media_file_playback = MediaFileViewSet.as_view({"get": "playback"})
```

```python
    path("media-files/<int:pk>/download", media_file_download, name="media-file-download"),
    path("media-files/<int:pk>/playback", media_file_playback, name="media-file-playback"),
```

- [ ] **Step 5: Add the playback action with minimal validation**

In `apps/media_file/views.py`, first extend `permission_map`:

```python
        "download": "media_file.view_media_file",
        "playback": "media_file.view_media_file",
```

Then add this schema block and action immediately below `download()`:

```python
    @extend_schema(
        summary="获取媒体播放地址",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        responses={
            302: OpenApiResponse(description="302 重定向到 DJI 播放地址。"),
            400: BUSINESS_INVALID_PARAMS_RESPONSE,
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Media File"],
    )
    @action(detail=True, methods=["get"])
    def playback(self, request, *args, **kwargs):
        media_file = self.get_object()
        if media_file.media_type != MediaType.VIDEO:
            return Response(
                validation_error_payload({"media_type": ["该媒体不支持 playback"]}),
                status=status.HTTP_400_BAD_REQUEST,
            )
        playback_url = DjiGateway().get_media_playback_url(media_file.dji_index.dji_file_id)
        return HttpResponseRedirect(playback_url)
```

- [ ] **Step 6: Re-run the focused tests to confirm GREEN**

Run:

```bash
./.venv/bin/python manage.py test \
  apps.media_file.tests.MediaFileApiTests.test_playback_should_redirect_to_dji_playback_url_for_video \
  apps.media_file.tests.MediaFileApiTests.test_playback_should_reject_photo_media_file
```

Expected:

- Both tests pass

- [ ] **Step 7: Run the affected suite**

Run:

```bash
./.venv/bin/python manage.py test apps.media_file.tests
```

Expected:

- Full media-file suite passes
- Existing download/delete/bind behavior remains green

- [ ] **Step 8: Commit the business endpoint**

```bash
git add apps/media_file/views.py apps/media_file/urls.py apps/media_file/tests.py
git commit -m "feat(media_file): add playback redirect endpoint"
```

### Task 3: Expose `playback_url` on Flight Record Detail and Update Schema Tests

**Files:**
- Modify: `apps/flight_record/serializers.py`
- Modify: `apps/flight_record/tests.py`
- Modify: `apps/api_v1/tests.py`

- [ ] **Step 1: Write the failing flight-record detail test for video playback URL**

In `apps/flight_record/tests.py`, update `test_retrieve_flight_record_should_include_only_current_downloadable_media_files` so it also asserts:

```python
        self.assertEqual(payload["media_files"][0]["playback_url"], f"/api/v1/media-files/{visible.id}/playback")
```

- [ ] **Step 2: Add a failing test for photo playback URL being blank**

Add this test right after `test_retrieve_flight_record_should_include_only_current_downloadable_media_files`:

```python
    def test_retrieve_flight_record_should_expose_blank_playback_url_for_photo_media(self):
        self._grant_permission("flight_record.view_flight_record")
        self.client.force_authenticate(self.viewer_user)
        record = self._create_flight_record()
        photo = self._create_media_file(
            flight_record=record,
            mission=record.mission,
            file_name="VISIBLE.JPG",
        )
        photo.media_type = MediaType.PHOTO
        photo.save(update_fields=["media_type"])

        response = self.client.get(f"/api/v1/flight-records/{record.id}")

        self.assertEqual(response.status_code, 200)
        payload = response.data["data"]
        self.assertEqual([item["id"] for item in payload["media_files"]], [photo.id])
        self.assertEqual(payload["media_files"][0]["download_url"], f"/api/v1/media-files/{photo.id}/download")
        self.assertEqual(payload["media_files"][0]["playback_url"], "")
```

- [ ] **Step 3: Update the failing schema assertion**

In `apps/api_v1/tests.py`, change the expected media item field set in `test_business_schema_should_expose_media_files_only_on_flight_record_detail` to:

```python
        self.assertEqual(
            set(media_items_schema["properties"].keys()),
            {"id", "media_type", "file_name", "thumbnail_url", "captured_at", "download_url", "playback_url"},
        )
```

- [ ] **Step 4: Run the focused tests to confirm RED**

Run:

```bash
./.venv/bin/python manage.py test \
  apps.flight_record.tests.FlightRecordApiTests.test_retrieve_flight_record_should_include_only_current_downloadable_media_files \
  apps.flight_record.tests.FlightRecordApiTests.test_retrieve_flight_record_should_expose_blank_playback_url_for_photo_media \
  apps.api_v1.tests.OpenApiDocsTests.test_business_schema_should_expose_media_files_only_on_flight_record_detail
```

Expected:

- Flight-record tests fail because `playback_url` is missing
- Schema test fails because the new property is absent

- [ ] **Step 5: Add the serializer field**

In `apps/flight_record/serializers.py`, update the import and `FlightRecordMediaFileSerializer` like this:

```python
from apps.media_file.models import MediaFile, MediaType


class FlightRecordMediaFileSerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField(help_text="平台媒体下载接口。")
    playback_url = serializers.SerializerMethodField(help_text="平台媒体播放接口；仅视频媒体返回非空。")

    class Meta:
        model = MediaFile
        fields = [
            "id",
            "media_type",
            "file_name",
            "thumbnail_url",
            "captured_at",
            "download_url",
            "playback_url",
        ]
        read_only_fields = fields

    def get_download_url(self, obj: MediaFile) -> str:
        return reverse("media-file-download", kwargs={"pk": obj.id})

    def get_playback_url(self, obj: MediaFile) -> str:
        if obj.media_type != MediaType.VIDEO:
            return ""
        return reverse("media-file-playback", kwargs={"pk": obj.id})
```

- [ ] **Step 6: Re-run the focused tests to confirm GREEN**

Run:

```bash
./.venv/bin/python manage.py test \
  apps.flight_record.tests.FlightRecordApiTests.test_retrieve_flight_record_should_include_only_current_downloadable_media_files \
  apps.flight_record.tests.FlightRecordApiTests.test_retrieve_flight_record_should_expose_blank_playback_url_for_photo_media \
  apps.api_v1.tests.OpenApiDocsTests.test_business_schema_should_expose_media_files_only_on_flight_record_detail
```

Expected:

- All three tests pass

- [ ] **Step 7: Run the affected suites**

Run:

```bash
./.venv/bin/python manage.py test apps.flight_record.tests
./.venv/bin/python manage.py test apps.api_v1.tests
```

Expected:

- Both suites pass
- Flight-record list responses remain unchanged

- [ ] **Step 8: Commit the detail serializer exposure**

```bash
git add apps/flight_record/serializers.py apps/flight_record/tests.py apps/api_v1/tests.py
git commit -m "feat(flight_record): expose media playback urls"
```

### Task 4: Final Verification Against the Approved Design

**Files:**
- Verify changed: `apps/dji_bff/gateway.py`
- Verify changed: `apps/dji_bff/tests.py`
- Verify changed: `apps/dji_mock/urls.py`
- Verify changed: `apps/dji_mock/views.py`
- Verify changed: `apps/dji_mock/tests.py`
- Verify changed: `apps/media_file/views.py`
- Verify changed: `apps/media_file/urls.py`
- Verify changed: `apps/media_file/tests.py`
- Verify changed: `apps/flight_record/serializers.py`
- Verify changed: `apps/flight_record/tests.py`
- Verify changed: `apps/api_v1/tests.py`

- [ ] **Step 1: Confirm the field did not leak into media-file read serializers**

Run:

```bash
git diff -- apps/media_file/serializers.py
```

Expected:

- No diff
- `MediaFileReadSerializer` remains unchanged

- [ ] **Step 2: Re-run the exact verification suites for the feature**

Run:

```bash
./.venv/bin/python manage.py test apps.dji_mock.tests
./.venv/bin/python manage.py test apps.dji_bff.tests
./.venv/bin/python manage.py test apps.media_file.tests
./.venv/bin/python manage.py test apps.flight_record.tests
./.venv/bin/python manage.py test apps.api_v1.tests
```

Expected:

- All five commands report `OK`
- No regressions in existing download URL behavior

- [ ] **Step 3: Capture deploy-time smoke checks for the human operator**

After deployment, run:

```bash
curl -sS "$BASE_URL/api/v1/flight-records/$FLIGHT_RECORD_ID" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-Code: $TENANT_CODE" \
  | jq '.data.media_files[] | {id, media_type, file_name, download_url, playback_url}'
```

```bash
curl -sS -D /tmp/media-playback.headers -o /dev/null \
  "$BASE_URL/api/v1/media-files/$MEDIA_FILE_ID/playback" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-Code: $TENANT_CODE"
```

Expected:

- Video media rows show non-empty `playback_url`
- Photo media rows show `playback_url` as `""`
- Video playback endpoint returns `302` with a playable upstream `Location`
