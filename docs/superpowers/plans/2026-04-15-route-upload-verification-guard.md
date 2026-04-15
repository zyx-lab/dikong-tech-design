# Route Upload Verification Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reject route create/update when upstream returns a wayline record but the uploaded KMZ object is not actually downloadable.

**Architecture:** Keep the existing route create/update flow, but insert a synchronous upstream object verification step immediately after `upload_route`. Reuse `DjiGateway.download_route_file` and `get_route_download_url` for verification, and let the existing cleanup hooks delete the newly uploaded wayline on failure.

**Tech Stack:** Django, DRF, existing `DjiGateway`, Django test suite with `unittest.mock`

---

### Task 1: Add create-path regression test for upload verification failure

**Files:**
- Modify: `apps/route/tests.py`
- Test: `apps/route/tests.py`

- [ ] **Step 1: Write the failing test**

```python
def test_create_should_rollback_when_uploaded_kmz_cannot_be_downloaded(self):
    with patch(
        "apps.route.views.DjiGateway.upload_route",
        return_value={"dji_wayline_id": "broken-wayline", "download_url": "https://upstream/broken.kmz"},
    ), patch(
        "apps.route.views.DjiGateway.download_route_file",
        side_effect=[
            DjiGatewayUpstreamError("missing", status_code=404),
            DjiGatewayUpstreamError("missing", status_code=404),
        ],
    ), patch(
        "apps.route.views.DjiGateway.get_route_download_url",
        return_value="https://upstream/refreshed-broken.kmz",
    ), patch(
        "apps.route.views.RouteViewSet._delete_upstream_wayline_if_exists",
    ) as delete_mock:
        response = self._upload_kmz_route(name="坏航线")

    self.assertEqual(response.status_code, 502, response.data)
    self.assertFalse(Route.objects.filter(name="坏航线").exists())
    self.assertFalse(TenantRouteIndex.objects.filter(dji_wayline_id="broken-wayline").exists())
    delete_mock.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python manage.py test apps.route.tests.RouteKmzApiTests.test_create_should_rollback_when_uploaded_kmz_cannot_be_downloaded -v 2`
Expected: FAIL because create currently persists route/index after upload success.

- [ ] **Step 3: Commit test-first checkpoint only after red is observed locally**

No commit at this step. Continue to green implementation first.

### Task 2: Add update-path regression test for upload verification failure

**Files:**
- Modify: `apps/route/tests.py`
- Test: `apps/route/tests.py`

- [ ] **Step 1: Write the failing test**

```python
def test_put_should_keep_existing_route_when_new_kmz_cannot_be_downloaded(self):
    route = Route.objects.create(tenant=self.tenant, name="旧航线")
    TenantRouteIndex.objects.create(
        tenant=self.tenant,
        route=route,
        dji_wayline_id="stable-wayline",
        download_url="https://upstream/stable.kmz",
        is_published=True,
    )

    with patch(
        "apps.route.views.DjiGateway.upload_route",
        return_value={"dji_wayline_id": "broken-wayline-update", "download_url": "https://upstream/broken-update.kmz"},
    ), patch(
        "apps.route.views.DjiGateway.download_route_file",
        side_effect=[
            DjiGatewayUpstreamError("missing", status_code=404),
            DjiGatewayUpstreamError("missing", status_code=404),
        ],
    ), patch(
        "apps.route.views.DjiGateway.get_route_download_url",
        return_value="https://upstream/refreshed-broken-update.kmz",
    ), patch(
        "apps.route.views.RouteViewSet._delete_upstream_wayline_if_exists",
    ) as delete_mock:
        response = self.client.put(
            f"/api/v1/routes/{route.id}",
            {
                "name": "新航线",
                "kmz_file": SimpleUploadedFile("broken.kmz", self._build_test_kmz(), content_type=self.KMZ_CONTENT_TYPE),
            },
            format="multipart",
        )

    self.assertEqual(response.status_code, 502, response.data)
    route.refresh_from_db()
    self.assertEqual(route.name, "旧航线")
    route_index = TenantRouteIndex.objects.get(route=route)
    self.assertEqual(route_index.dji_wayline_id, "stable-wayline")
    self.assertEqual(route_index.download_url, "https://upstream/stable.kmz")
    delete_mock.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python manage.py test apps.route.tests.RouteKmzApiTests.test_put_should_keep_existing_route_when_new_kmz_cannot_be_downloaded -v 2`
Expected: FAIL because update currently overwrites route/index after upload success.

- [ ] **Step 3: Commit test-first checkpoint only after red is observed locally**

No commit at this step. Continue to green implementation first.

### Task 3: Implement synchronous upstream object verification in route viewset

**Files:**
- Modify: `apps/route/views.py`
- Test: `apps/route/tests.py`

- [ ] **Step 1: Add a helper that validates the uploaded wayline is downloadable**

```python
def _verify_uploaded_route_is_downloadable(self, *, gateway: DjiGateway, dji_wayline_id: str, download_url: str) -> str:
    try:
        gateway.download_route_file(download_url)
        return download_url
    except DjiGatewayUpstreamError as exc:
        if exc.status_code != status.HTTP_404_NOT_FOUND:
            raise
    refreshed_download_url = self._refresh_route_download_url(
        gateway=gateway,
        route_index=SimpleNamespace(dji_wayline_id=dji_wayline_id, download_url=download_url),
    )
    if not refreshed_download_url:
        raise DjiGatewayUpstreamError("上传后对象校验失败", status_code=502)
    try:
        gateway.download_route_file(refreshed_download_url)
    except DjiGatewayUpstreamError as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise DjiGatewayUpstreamError("上传后对象校验失败", status_code=502) from exc
        raise
    return refreshed_download_url
```

- [ ] **Step 2: Call the helper in both create and update before local persistence**

```python
dji_wayline_id, download_url = _upload_route_to_upstream(...)
download_url = self._verify_uploaded_route_is_downloadable(
    gateway=gateway,
    dji_wayline_id=dji_wayline_id,
    download_url=download_url,
)
```

- [ ] **Step 3: Keep cleanup behavior focused on the new upstream wayline**

Do not add new background jobs or status fields. Reuse the existing `cleanup_state["wayline_id"]` path so create/update failures trigger best-effort `delete_route` on the new upstream record.

- [ ] **Step 4: Run the two regression tests and make sure they pass**

Run:
- `./.venv/bin/python manage.py test apps.route.tests.RouteKmzApiTests.test_create_should_rollback_when_uploaded_kmz_cannot_be_downloaded -v 2`
- `./.venv/bin/python manage.py test apps.route.tests.RouteKmzApiTests.test_put_should_keep_existing_route_when_new_kmz_cannot_be_downloaded -v 2`

Expected: PASS

### Task 4: Verify success paths still work and commit

**Files:**
- Modify: `apps/route/views.py`
- Modify: `apps/route/tests.py`
- Create: `docs/superpowers/specs/2026-04-15-route-upload-verification-guard-design.md`
- Create: `docs/superpowers/plans/2026-04-15-route-upload-verification-guard.md`

- [ ] **Step 1: Run a focused route test subset**

Run: `./.venv/bin/python manage.py test apps.route.tests.RouteKmzApiTests -v 2`
Expected: PASS

- [ ] **Step 2: Review diff for scope control**

Run: `git diff -- apps/route/views.py apps/route/tests.py docs/superpowers/specs/2026-04-15-route-upload-verification-guard-design.md docs/superpowers/plans/2026-04-15-route-upload-verification-guard.md`
Expected: Only route verification guard and related tests/docs are changed.

- [ ] **Step 3: Commit the change**

```bash
git add apps/route/views.py apps/route/tests.py docs/superpowers/specs/2026-04-15-route-upload-verification-guard-design.md docs/superpowers/plans/2026-04-15-route-upload-verification-guard.md
git commit -m "fix: verify uploaded route artifact before publish"
```
