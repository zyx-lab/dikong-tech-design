# Flight Record Media Files Detail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `media_files` list to `GET /api/v1/flight-records/{id}` so the flight record detail can show its current downloadable media items without expanding the flight record list API.

**Architecture:** Keep `MediaFile.flight_record` as the only source of truth and dynamically serialize related media in the flight record detail response. Split the current flight record read serializer into summary and detail serializers so `media_files` appears only on retrieve, while list and update responses stay lean.

**Tech Stack:** Django, Django REST Framework, drf-spectacular OpenAPI schema generation, existing business API test suite

---

## File Structure

- Modify: `apps/flight_record/serializers.py`
  - Introduce a summary serializer for list/update responses.
  - Introduce a small nested media serializer that exposes the platform download path.
  - Introduce a detail serializer that adds `media_files` only for retrieve.
- Modify: `apps/flight_record/views.py`
  - Wire `retrieve` to the detail serializer.
  - Keep update requests on the existing write serializer.
  - Keep `_payload()` on the summary serializer so non-retrieve responses do not bloat.
  - Update retrieve description so `/api/v1/docs/` explains the new field.
- Modify: `apps/flight_record/tests.py`
  - Add API regression coverage for detail-only `media_files` behavior.
  - Add a test helper that creates media records plus `TenantMediaIndex` rows.
- Modify: `apps/api_v1/tests.py`
  - Add schema regression coverage proving detail includes `media_files` and list does not.

### Task 1: Add Flight Record API Regression Tests

**Files:**
- Modify: `apps/flight_record/tests.py`
- Test: `apps/flight_record/tests.py`

- [ ] **Step 1: Extend imports and add a media fixture helper**

```python
from apps.dji_bff.models import SyncStatus, TenantMediaIndex
from apps.media_file.models import MediaFile, MediaType
```

Add this helper inside `FlightRecordApiTests` after `_create_flight_record`:

```python
def _create_media_file(
    self,
    *,
    flight_record: FlightRecord,
    mission: Mission,
    file_name: str,
    captured_at=None,
    is_deleted: bool = False,
    with_dji_index: bool = True,
) -> MediaFile:
    media_file = MediaFile.objects.create(
        tenant=self.tenant,
        flight_record=flight_record,
        mission=mission,
        device_sn=mission.device_sn,
        media_type=MediaType.VIDEO,
        file_name=file_name,
        file_url=f"https://example.com/{file_name}",
        thumbnail_url=f"https://example.com/thumb/{file_name}",
        file_size=2048,
        captured_at=captured_at or timezone.now(),
        is_deleted=is_deleted,
        deleted_at=timezone.now() if is_deleted else None,
    )
    if with_dji_index:
        TenantMediaIndex.objects.create(
            tenant=self.tenant,
            media_file=media_file,
            dji_file_id=f"dji-{file_name}",
            device_sn=mission.device_sn,
            mission=mission,
            sync_status=SyncStatus.SYNCED,
            last_sync_at=timezone.now(),
        )
    return media_file
```

- [ ] **Step 2: Write the failing detail-behavior tests**

Add these tests near the existing retrieve/list tests:

```python
def test_retrieve_flight_record_should_include_only_current_downloadable_media_files(self):
    self._grant_permission("flight_record.view_flight_record")
    self.client.force_authenticate(self.viewer_user)
    record = self._create_flight_record()
    visible = self._create_media_file(
        flight_record=record,
        mission=record.mission,
        file_name="VISIBLE.MP4",
        captured_at=timezone.now() - timedelta(minutes=1),
    )
    self._create_media_file(
        flight_record=record,
        mission=record.mission,
        file_name="DELETED.MP4",
        is_deleted=True,
    )
    self._create_media_file(
        flight_record=record,
        mission=record.mission,
        file_name="NOINDEX.MP4",
        with_dji_index=False,
    )
    other_record = self._create_flight_record(flight_no=self._next_flight_no())
    self._create_media_file(
        flight_record=other_record,
        mission=other_record.mission,
        file_name="OTHER.MP4",
    )

    response = self.client.get(f"/api/v1/flight-records/{record.id}")

    self.assertEqual(response.status_code, 200)
    payload = response.data["data"]
    self.assertIn("media_files", payload)
    self.assertEqual([item["id"] for item in payload["media_files"]], [visible.id])
    self.assertEqual(payload["media_files"][0]["media_type"], MediaType.VIDEO)
    self.assertEqual(payload["media_files"][0]["file_name"], "VISIBLE.MP4")
    self.assertEqual(payload["media_files"][0]["download_url"], f"/api/v1/media-files/{visible.id}/download")


def test_list_flight_records_should_not_include_media_files_field(self):
    self._grant_permission("flight_record.view_flight_record")
    self.client.force_authenticate(self.viewer_user)
    record = self._create_flight_record()
    self._create_media_file(
        flight_record=record,
        mission=record.mission,
        file_name="LIST-HIDDEN.MP4",
    )

    response = self.client.get("/api/v1/flight-records")

    self.assertEqual(response.status_code, 200)
    self.assertNotIn("media_files", response.data["data"]["list"][0])
```

- [ ] **Step 3: Run the targeted tests to verify red**

Run:

```bash
./.venv/bin/python manage.py test \
  apps.flight_record.tests.FlightRecordApiTests.test_retrieve_flight_record_should_include_only_current_downloadable_media_files \
  apps.flight_record.tests.FlightRecordApiTests.test_list_flight_records_should_not_include_media_files_field \
  -v 2
```

Expected: `test_retrieve_flight_record_should_include_only_current_downloadable_media_files` FAILS because `media_files` is missing from the detail payload. The list-field regression may already pass; that is acceptable as long as at least one new test is red before implementation.

### Task 2: Add OpenAPI Schema Regression Test

**Files:**
- Modify: `apps/api_v1/tests.py`
- Test: `apps/api_v1/tests.py`

- [ ] **Step 1: Write the failing schema test**

Add this test inside `OpenApiDocsTests` near the existing flight-record schema assertions:

```python
def test_business_schema_should_expose_media_files_only_on_flight_record_detail(self):
    response = self.client.get("/api/v1/docs/schema/")
    self.assertEqual(response.status_code, 200)
    schema = response.json()

    detail_response = self._operation(schema, path="/api/v1/flight-records/{id}", method="get")["responses"]["200"]["content"]["application/json"]["schema"]
    detail_data_schema = detail_response["properties"]["data"]
    if "$ref" in detail_data_schema:
        detail_data_schema = schema["components"]["schemas"][detail_data_schema["$ref"].split("/")[-1]]

    self.assertIn("media_files", detail_data_schema["properties"])
    media_items_ref = detail_data_schema["properties"]["media_files"]["items"]["$ref"].split("/")[-1]
    media_items_schema = schema["components"]["schemas"][media_items_ref]
    self.assertEqual(
        set(media_items_schema["properties"].keys()),
        {"id", "media_type", "file_name", "thumbnail_url", "captured_at", "download_url"},
    )

    list_response = self._operation(schema, path="/api/v1/flight-records", method="get")["responses"]["200"]["content"]["application/json"]["schema"]
    list_data_schema = list_response["properties"]["data"]
    if "$ref" in list_data_schema:
        list_data_schema = schema["components"]["schemas"][list_data_schema["$ref"].split("/")[-1]]
    list_item_ref = list_data_schema["properties"]["list"]["items"]["$ref"].split("/")[-1]
    list_item_schema = schema["components"]["schemas"][list_item_ref]
    self.assertNotIn("media_files", list_item_schema["properties"])
```

- [ ] **Step 2: Run the schema test to verify red**

Run:

```bash
./.venv/bin/python manage.py test \
  apps.api_v1.tests.OpenApiDocsTests.test_business_schema_should_expose_media_files_only_on_flight_record_detail \
  -v 2
```

Expected: FAIL because the current retrieve schema does not expose `media_files`.

### Task 3: Implement Detail-Only Media Serialization

**Files:**
- Modify: `apps/flight_record/serializers.py`
- Modify: `apps/flight_record/views.py`
- Test: `apps/flight_record/tests.py`
- Test: `apps/api_v1/tests.py`

- [ ] **Step 1: Replace the current read serializer with summary + detail serializers**

In `apps/flight_record/serializers.py`, replace the current single read serializer with these classes:

```python
from django.urls import reverse
from rest_framework import serializers

from apps.api_v1.serializers import RejectUnknownFieldsMixin
from apps.flight_record.models import FlightRecord
from apps.media_file.models import MediaFile


class FlightRecordSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = FlightRecord
        fields = [
            "id",
            "flight_no",
            "mission",
            "mission_name",
            "route_name",
            "airport_name",
            "drone",
            "device_sn",
            "drone_name",
            "pilot",
            "pilot_name",
            "start_time",
            "end_time",
            "flight_duration",
            "photo_count",
            "video_count",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class FlightRecordMediaFileSerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField(help_text="平台媒体下载接口。")

    class Meta:
        model = MediaFile
        fields = [
            "id",
            "media_type",
            "file_name",
            "thumbnail_url",
            "captured_at",
            "download_url",
        ]
        read_only_fields = fields

    def get_download_url(self, obj) -> str:
        return reverse("media-file-download", kwargs={"pk": obj.pk})


class FlightRecordDetailSerializer(FlightRecordSummarySerializer):
    media_files = serializers.SerializerMethodField(help_text="当前绑定到该飞行记录的媒体列表。")

    class Meta(FlightRecordSummarySerializer.Meta):
        fields = FlightRecordSummarySerializer.Meta.fields + ["media_files"]
        read_only_fields = fields

    def get_media_files(self, obj):
        queryset = (
            obj.media_files.select_related("dji_index")
            .filter(is_deleted=False, dji_index__isnull=False)
            .order_by("-captured_at", "-id")
        )
        return FlightRecordMediaFileSerializer(queryset, many=True, context=self.context).data
```

Keep `FlightRecordWriteSerializer` unchanged.

- [ ] **Step 2: Wire retrieve to the new detail serializer and keep update/list responses lean**

In `apps/flight_record/views.py`, make these changes:

```python
from apps.flight_record.serializers import (
    FlightRecordDetailSerializer,
    FlightRecordSummarySerializer,
    FlightRecordWriteSerializer,
)

FLIGHT_RECORD_LIST_RESPONSE = paginated_envelope_serializer("FlightRecordListResponse", FlightRecordSummarySerializer)
FLIGHT_RECORD_DETAIL_RESPONSE = object_envelope_serializer("FlightRecordDetailResponse", FlightRecordDetailSerializer)
FLIGHT_RECORD_UPDATE_RESPONSE = object_envelope_serializer("FlightRecordUpdateResponse", FlightRecordSummarySerializer)
```

Update the retrieve and update schema decorators:

```python
retrieve=extend_schema(
    summary="读取飞行记录详情",
    description="按飞行记录 ID 读取单条历史快照。详情额外返回当前绑定媒体的 `media_files` 列表，每项包含平台下载入口。",
    ...
    responses={200: OpenApiResponse(response=FLIGHT_RECORD_DETAIL_RESPONSE, description="读取成功。"), ...},
)
```

```python
update=extend_schema(
    ...
    responses={200: OpenApiResponse(response=FLIGHT_RECORD_UPDATE_RESPONSE, description="更新成功。"), ...},
)
```

Update serializer selection and payload shaping:

```python
def get_serializer_class(self):
    if self.action == "update":
        return FlightRecordWriteSerializer
    if self.action == "retrieve":
        return FlightRecordDetailSerializer
    return FlightRecordSummarySerializer


def _payload(self, record: FlightRecord) -> dict:
    return dict(FlightRecordSummarySerializer(record, context={"request": self.request}).data)
```

Do not change the queryset shape or the write serializer.

- [ ] **Step 3: Run the new focused tests and make sure they pass**

Run:

```bash
./.venv/bin/python manage.py test \
  apps.flight_record.tests.FlightRecordApiTests.test_retrieve_flight_record_should_include_only_current_downloadable_media_files \
  apps.flight_record.tests.FlightRecordApiTests.test_list_flight_records_should_not_include_media_files_field \
  apps.api_v1.tests.OpenApiDocsTests.test_business_schema_should_expose_media_files_only_on_flight_record_detail \
  -v 2
```

Expected: PASS

- [ ] **Step 4: Commit the implementation slice**

```bash
git add apps/flight_record/serializers.py apps/flight_record/views.py apps/flight_record/tests.py apps/api_v1/tests.py
git commit -m "feat: expose flight record media detail links"
```

### Task 4: Full Verification and Scope Check

**Files:**
- Modify: `apps/flight_record/serializers.py`
- Modify: `apps/flight_record/views.py`
- Modify: `apps/flight_record/tests.py`
- Modify: `apps/api_v1/tests.py`

- [ ] **Step 1: Run the relevant test suites**

Run:

```bash
./.venv/bin/python manage.py test apps.flight_record.tests -v 2
./.venv/bin/python manage.py test apps.api_v1.tests.OpenApiDocsTests -v 2
```

Expected: PASS

- [ ] **Step 2: Review the diff for scope control**

Run:

```bash
git diff -- apps/flight_record/serializers.py apps/flight_record/views.py apps/flight_record/tests.py apps/api_v1/tests.py
```

Expected: only detail-only `media_files` serialization, retrieve schema wiring, and related tests are changed.

- [ ] **Step 3: Verify no placeholder drift remains in the docs-driven flow**

Run:

```bash
python - <<'PY'
from pathlib import Path

text = Path("docs/superpowers/specs/2026-04-15-flight-record-media-files-detail-design.md").read_text(encoding="utf-8")
patterns = [
    "TO" + "DO",
    "TB" + "D",
    "implement " + "later",
    "fill in " + "details",
]
hits = [pattern for pattern in patterns if pattern in text]
if hits:
    raise SystemExit(f"placeholder markers found: {hits}")
print("no placeholder markers")
PY
```

Expected: prints `no placeholder markers`.

- [ ] **Step 4: If verification required follow-up edits, amend with a final clean commit**

```bash
git add apps/flight_record/serializers.py apps/flight_record/views.py apps/flight_record/tests.py apps/api_v1/tests.py
git commit -m "test: lock flight record detail media schema" || true
```

If there are no further code changes after Step 2 and Step 3, skip this commit.
