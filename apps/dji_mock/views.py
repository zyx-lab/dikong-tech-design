from __future__ import annotations

import json
from functools import wraps

from django.conf import settings
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.views.decorators.csrf import csrf_exempt

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


def _load_json(request) -> dict:
    if not request.body:
        return {}
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


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
    return _success(mock_dji_state.list_devices())


@protected_mock_dji_view
def live_capacity(request):
    if request.method != "GET":
        raise Http404
    return _success(mock_dji_state.live_capacity_payload())


def _live_echo(action: str, request):
    payload = _load_json(request)
    return _success(
        {
            "action": action,
            "accepted": True,
            "payload": payload,
        }
    )


@csrf_exempt
@protected_mock_dji_view
def live_start(request):
    if request.method != "POST":
        raise Http404
    return _live_echo("start", request)


@csrf_exempt
@protected_mock_dji_view
def live_stop(request):
    if request.method != "POST":
        raise Http404
    return _live_echo("stop", request)


@csrf_exempt
@protected_mock_dji_view
def live_update(request):
    if request.method != "POST":
        raise Http404
    return _live_echo("update", request)


@csrf_exempt
@protected_mock_dji_view
def live_switch(request):
    if request.method != "POST":
        raise Http404
    return _live_echo("switch", request)


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
    route_name = request.POST.get("name") or (upload.name if upload is not None else "") or "mock-route"
    created = mock_dji_state.create_wayline(name=route_name, file_name=getattr(upload, "name", ""))
    return _success(
        {
            "dji_wayline_id": created["wayline_id"],
            "name": created["name"],
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
    job = mock_dji_state.create_job(_load_json(request))
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


@mock_dji_view
def download_wayline_binary(request, filename: str):
    if request.method != "GET":
        raise Http404
    return HttpResponse(
        f"mock wayline binary for {filename}\n",
        content_type="application/octet-stream",
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
