from __future__ import annotations

from functools import wraps
from uuid import uuid4

from django.conf import settings
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from apps.common.request import load_json_body
from apps.dji_mock.state import mock_dji_state


def _success(data=None, *, status: int = 200):
    return JsonResponse(
        {
            "code": "00000",
            "msg": "success",
            "data": {} if data is None else data,
        },
        status=status,
    )


def _error(code: str, msg: str, *, status: int, data=None):
    return JsonResponse(
        {
            "code": code,
            "msg": msg,
            "data": {} if data is None else data,
        },
        status=status,
    )


def _enabled():
    if not getattr(settings, "ENABLE_DJI_MOCK_SERVER", False):
        raise Http404


def _require_token(request):
    expected = mock_dji_state.access_token
    actual = request.headers.get("x-auth-token", "")
    if actual != expected:
        return _error("A0401", "mock dji token missing or invalid", status=401)
    return None


def _query_int(request, key: str, *, default: int):
    raw = request.GET.get(key)
    if raw in (None, ""):
        return default, None
    try:
        return int(raw), None
    except (TypeError, ValueError):
        return None, _error("B0001", f"{key} is invalid", status=400, data={key: ["必须是整数。"]})


def mock_dji_view(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        _enabled()
        return view_func(request, *args, **kwargs)

    return wrapper


def protected_mock_dji_view(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        _enabled()
        auth_error = _require_token(request)
        if auth_error is not None:
            return auth_error
        return view_func(request, *args, **kwargs)

    return wrapper


@csrf_exempt
@mock_dji_view
def login(request):
    if request.method != "POST":
        raise Http404
    return _success(mock_dji_state.login_payload())


@csrf_exempt
@mock_dji_view
def refresh_token(request):
    if request.method != "POST":
        raise Http404
    return _success(mock_dji_state.refresh_payload())


@protected_mock_dji_view
def current_user(request):
    if request.method != "GET":
        raise Http404
    return _success(mock_dji_state.current_user_payload())


@protected_mock_dji_view
def current_workspace(request):
    if request.method != "GET":
        raise Http404
    return _success(mock_dji_state.current_workspace_payload())


@protected_mock_dji_view
def workspace_devices(request, workspace_id: str):
    if request.method != "GET":
        raise Http404
    if workspace_id != mock_dji_state.current_workspace_payload()["workspace_id"]:
        return _error("C0404", "workspace not found", status=404)
    page, page_error = _query_int(request, "page", default=1)
    if page_error is not None:
        return page_error
    page_size, page_size_error = _query_int(request, "page_size", default=50)
    if page_size_error is not None:
        return page_size_error
    return _success(mock_dji_state.list_devices(page=page, page_size=page_size))


@protected_mock_dji_view
def workspace_bound_devices(request, workspace_id: str):
    if request.method != "GET":
        raise Http404
    if workspace_id != mock_dji_state.current_workspace_payload()["workspace_id"]:
        return _error("C0404", "workspace not found", status=404)
    raw_domain = request.GET.get("domain")
    if raw_domain in (None, ""):
        return _error("B0001", "domain is required", status=400, data={"domain": ["该字段是必填项。"]})
    try:
        domain = int(raw_domain)
    except (TypeError, ValueError):
        return _error("B0001", "domain is invalid", status=400, data={"domain": ["必须是整数。"]})
    page, page_error = _query_int(request, "page", default=1)
    if page_error is not None:
        return page_error
    page_size, page_size_error = _query_int(request, "page_size", default=50)
    if page_size_error is not None:
        return page_size_error
    return _success(mock_dji_state.list_bound_devices(domain=domain, page=page, page_size=page_size))


@protected_mock_dji_view
def live_capacity(request):
    if request.method != "GET":
        raise Http404
    return _success(mock_dji_state.live_capacity_payload())


def _live_not_found():
    return JsonResponse({"code": "D0001", "msg": "No aircraft.", "data": None}, status=200)


@csrf_exempt
@protected_mock_dji_view
def live_start(request):
    if request.method != "POST":
        raise Http404
    payload = load_json_body(request)
    result = mock_dji_state.start_live(payload)
    if result is None:
        return _live_not_found()
    return _success(result)


@csrf_exempt
@protected_mock_dji_view
def live_stop(request):
    if request.method != "POST":
        raise Http404
    payload = load_json_body(request)
    result = mock_dji_state.stop_live(payload)
    if result is None:
        return _live_not_found()
    return _success(result)


@csrf_exempt
@protected_mock_dji_view
def live_update(request):
    if request.method != "POST":
        raise Http404
    payload = load_json_body(request)
    result = mock_dji_state.update_live(payload)
    if result is None:
        return _live_not_found()
    return _success(result)


@csrf_exempt
@protected_mock_dji_view
def live_switch(request):
    if request.method != "POST":
        raise Http404
    payload = load_json_body(request)
    result = mock_dji_state.switch_live(payload)
    if result is None:
        return _live_not_found()
    return _success(result)


@csrf_exempt
@protected_mock_dji_view
def payload_authority(request, gateway_sn: str):
    if request.method != "POST":
        raise Http404
    payload = load_json_body(request)
    payload_index = str(payload.get("payload_index") or "").strip()
    if not payload_index:
        return _error("B0001", "payload_index is required", status=400, data={"payload_index": ["该字段是必填项。"]})
    result = mock_dji_state.grab_payload_authority(gateway_sn=gateway_sn, payload=payload)
    if not result.get("ok"):
        return _error(str(result.get("code") or "E0001"), str(result.get("msg") or "mock payload authority failed"), status=200)
    result.pop("ok", None)
    return _success(result)


@csrf_exempt
@protected_mock_dji_view
def payload_commands(request, gateway_sn: str):
    if request.method != "POST":
        raise Http404
    payload = load_json_body(request)
    cmd = str(payload.get("cmd") or "").strip()
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    payload_index = str(data.get("payload_index") or "").strip()
    if not cmd:
        return _error("B0001", "cmd is required", status=400, data={"cmd": ["该字段是必填项。"]})
    if not payload_index:
        return _error("B0001", "payload_index is required", status=400, data={"payload_index": ["该字段是必填项。"]})
    result = mock_dji_state.send_payload_command(gateway_sn=gateway_sn, payload={"cmd": cmd, "data": data})
    if not result.get("ok"):
        return _error(str(result.get("code") or "E0001"), str(result.get("msg") or "mock payload command failed"), status=200)
    result.pop("ok", None)
    return _success(result)


@csrf_exempt
@protected_mock_dji_view
def drc_connect(request, workspace_id: str):
    if request.method != "POST":
        raise Http404
    payload = load_json_body(request)
    dock_sn = str(payload.get("dockSn") or "")
    client_id = str(payload.get("clientId") or f"mock-drc-{uuid4().hex[:12]}")
    return _success({
        "address": "mqtt://127.0.0.1:1883",
        "username": "mock-drc",
        "password": "mock-drc-password",
        "clientId": client_id,
        "expireTime": int(timezone.now().timestamp()) + int(payload.get("expireSec", 3600)),
        "enableTls": False,
        "dockSn": dock_sn,
    })


@csrf_exempt
@protected_mock_dji_view
def drc_enter(request, workspace_id: str):
    if request.method != "POST":
        raise Http404
    dock_sn = str(load_json_body(request).get("dockSn") or "")
    topic = f"thing/product/{dock_sn}/drc"
    return _success({"pub": [f"{topic}/down"], "sub": [f"{topic}/up"]})


@csrf_exempt
@protected_mock_dji_view
def drc_exit(request, workspace_id: str):
    if request.method != "POST":
        raise Http404
    return _success()


@protected_mock_dji_view
def wayline_list(request, workspace_id: str):
    if request.method != "GET":
        raise Http404
    if workspace_id != mock_dji_state.current_workspace_payload()["workspace_id"]:
        return _error("C0404", "workspace not found", status=404)
    return _success(mock_dji_state.list_waylines())


@protected_mock_dji_view
def duplicate_wayline_names(request, workspace_id: str):
    if request.method != "GET":
        raise Http404
    if workspace_id != mock_dji_state.current_workspace_payload()["workspace_id"]:
        return _error("C0404", "workspace not found", status=404)
    names = [name for name in request.GET.getlist("name") if name]
    return _success(mock_dji_state.duplicate_wayline_names(names))


@csrf_exempt
@protected_mock_dji_view
def upload_wayline(request, workspace_id: str):
    if request.method != "POST":
        raise Http404
    if workspace_id != mock_dji_state.current_workspace_payload()["workspace_id"]:
        return _error("C0404", "workspace not found", status=404)

    upload = request.FILES.get("file")
    if upload is None or getattr(upload, "size", 0) <= 0:
        return _error("B0001", "file is required", status=400, data={"file": ["该字段是必填项。"]})
    route_name = request.POST.get("name") or (upload.name if upload is not None else "") or "mock-route"
    created = mock_dji_state.create_wayline(name=route_name, file_name=getattr(upload, "name", ""))
    return _success(
        {
            "name": created["name"],
            "wayline_id": created["wayline_id"],
            "workspace_id": workspace_id,
            "download_url": f"/api/v1/wayline/workspaces/{workspace_id}/waylines/{created['wayline_id']}/url",
        }
    )


@protected_mock_dji_view
def wayline_download_url(request, workspace_id: str, wayline_id: str):
    if request.method != "GET":
        raise Http404
    if workspace_id != mock_dji_state.current_workspace_payload()["workspace_id"]:
        return _error("C0404", "workspace not found", status=404)
    if wayline_id not in mock_dji_state.waylines:
        return _error("C0404", "wayline not found", status=404)
    return HttpResponseRedirect(f"/__mock-dji__/_downloads/waylines/{wayline_id}.kmz")


@csrf_exempt
@protected_mock_dji_view
def delete_wayline(request, workspace_id: str, wayline_id: str):
    if request.method != "DELETE":
        raise Http404
    if workspace_id != mock_dji_state.current_workspace_payload()["workspace_id"]:
        return _error("C0404", "workspace not found", status=404)
    if not mock_dji_state.delete_wayline(wayline_id):
        return _error("C0404", "wayline not found", status=404)
    return _success({})


@csrf_exempt
@protected_mock_dji_view
def create_job(request, workspace_id: str):
    if request.method != "POST":
        raise Http404
    if workspace_id != mock_dji_state.current_workspace_payload()["workspace_id"]:
        return _error("C0404", "workspace not found", status=404)
    payload = load_json_body(request)
    if not str(payload.get("fileId") or payload.get("file_id") or "").strip():
        return _error("B0001", "file_id is required", status=400, data={"file_id": ["该字段是必填项。"]})
    if not str(payload.get("dockSn") or payload.get("dock_sn") or "").strip():
        return _error("B0001", "dock_sn is required", status=400, data={"dock_sn": ["该字段是必填项。"]})
    if payload.get("waylineType", payload.get("wayline_type")) is None:
        return _error("B0001", "wayline_type is required", status=400, data={"wayline_type": ["该字段是必填项。"]})
    if payload.get("taskType", payload.get("task_type")) is None:
        return _error("B0001", "task_type is required", status=400, data={"task_type": ["该字段是必填项。"]})
    rth_altitude = payload.get("rthAltitude", payload.get("rth_altitude"))
    if not isinstance(rth_altitude, int) or isinstance(rth_altitude, bool):
        return _error("B0001", "rth_altitude is required", status=400, data={"rth_altitude": ["该字段是必填项。"]})
    if rth_altitude < 20 or rth_altitude > 500:
        return _error("B0001", "rth_altitude is invalid", status=400, data={"rth_altitude": ["取值范围必须在 20 到 500 之间。"]})
    if payload.get("outOfControlAction", payload.get("out_of_control_action")) is None:
        return _error(
            "B0001",
            "out_of_control_action is required",
            status=400,
            data={"out_of_control_action": ["该字段是必填项。"]},
        )
    job = mock_dji_state.create_job(payload)
    return _success(
        {
            "dji_job_id": job["job_id"],
            "job_id": job["job_id"],
            "name": job["name"],
        }
    )


@csrf_exempt
@protected_mock_dji_view
def jobs_collection(request, workspace_id: str):
    if workspace_id != mock_dji_state.current_workspace_payload()["workspace_id"]:
        return _error("C0404", "workspace not found", status=404)
    if request.method == "GET":
        return _success(mock_dji_state.list_jobs())
    if request.method == "DELETE":
        cancelled = mock_dji_state.cancel_jobs(request.GET.getlist("job_id"))
        return _success({"cancelled_job_ids": cancelled})
    raise Http404


@protected_mock_dji_view
def list_media_files(request, workspace_id: str):
    if request.method != "GET":
        raise Http404
    if workspace_id != mock_dji_state.current_workspace_payload()["workspace_id"]:
        return _error("C0404", "workspace not found", status=404)
    return _success(mock_dji_state.list_media_files())


@protected_mock_dji_view
def media_download_url(request, workspace_id: str, file_id: str):
    if request.method != "GET":
        raise Http404
    if workspace_id != mock_dji_state.current_workspace_payload()["workspace_id"]:
        return _error("C0404", "workspace not found", status=404)
    if file_id not in mock_dji_state.media_files:
        return _error("C0404", "media file not found", status=404)
    return HttpResponseRedirect(f"/__mock-dji__/_downloads/media/{file_id}")


@protected_mock_dji_view
def media_playback_url(request, workspace_id: str, file_id: str):
    if request.method != "GET":
        raise Http404
    if workspace_id != mock_dji_state.current_workspace_payload()["workspace_id"]:
        return _error("C0404", "workspace not found", status=404)
    if file_id not in mock_dji_state.media_files:
        return _error("C0404", "media file not found", status=404)
    return HttpResponseRedirect(f"/__mock-dji__/_downloads/media/{file_id}")


@protected_mock_dji_view
def media_preview_url(request, workspace_id: str, file_id: str):
    if request.method != "GET":
        raise Http404
    if workspace_id != mock_dji_state.current_workspace_payload()["workspace_id"]:
        return _error("C0404", "workspace not found", status=404)
    if file_id not in mock_dji_state.media_files:
        return _error("C0404", "media file not found", status=404)
    return HttpResponseRedirect(f"/__mock-dji__/_downloads/media/{file_id}")


@mock_dji_view
def download_wayline_binary(request, filename: str):
    if request.method != "GET":
        raise Http404
    return HttpResponse(
        b"mock-kmz-binary",
        content_type="application/vnd.google-earth.kmz",
    )


@mock_dji_view
def download_media_binary(request, file_id: str):
    if request.method != "GET":
        raise Http404
    if file_id.endswith("/thumb"):
        return HttpResponse(f"mock media thumbnail for {file_id}\n", content_type="image/jpeg")
    return HttpResponse(
        f"mock media binary for {file_id}\n",
        content_type="application/octet-stream",
    )
