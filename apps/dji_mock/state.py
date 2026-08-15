from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from threading import RLock
from uuid import uuid4

from django.utils import timezone


def _iso(dt: datetime | None = None) -> str:
    value = dt or timezone.now()
    return value.isoformat()


class MockDjiState:
    """In-memory state store for the test-only DJI mock server."""

    def __init__(self):
        self._lock = RLock()
        self.reset()

    def reset(self):
        with self._lock:
            self.workspace = {
                "id": 1,
                "workspace_id": "mock-workspace-001",
                "workspace_name": "Mock DJI Workspace",
                "workspace_desc": "Mock DJI Workspace for test-only upstream simulation",
                "platform_name": "Mock DJI Cloud",
                "bind_code": "MOCK-BIND-CODE",
            }
            self.user = {
                "username": "mock-admin",
                "user_id": "mock-user-001",
                "workspace_id": self.workspace["workspace_id"],
                "user_type": 1,
                "mqtt_username": "mock-mqtt-user",
                "mqtt_password": "mock-mqtt-pass",
                "access_token": "mock-access-token",
                "mqtt_addr": "tcp://mock-broker:1883",
            }
            self.devices: dict[str, dict] = {}
            self.bound_device_sns: set[str] = set()
            self.live_capacity: dict[str, dict] = {}
            self.waylines: dict[str, dict] = {}
            self.jobs: dict[str, dict] = {}
            self.media_files: dict[str, dict] = {}
            self.live_streams: dict[str, dict] = {}
            self.payload_authority_requests: list[dict] = []
            self.payload_command_requests: list[dict] = []
            self.payload_authority_errors: dict[tuple[str, str], dict] = {}
            self.payload_command_errors: dict[tuple[str, str], dict] = {}
            self.seed_device(device_sn="MOCK-DRONE-001", name="Mock Drone 1", model="Matrice 30T")
            self.seed_device(device_sn="MOCK-DRONE-002", name="Mock Drone 2", model="Matrice 3D")
            self.seed_device(device_sn="MOCK-DOCK-001", name="Mock Dock 1", model="Dock 2", domain=3)
            self.seed_media_file(
                file_id="mock-file-001",
                name="DJI_0001.JPG",
                device_sn="MOCK-DRONE-001",
                job_id="",
            )

    @property
    def access_token(self) -> str:
        return self.user["access_token"]

    def login_payload(self) -> dict:
        with self._lock:
            return deepcopy(self.user)

    def refresh_payload(self) -> dict:
        with self._lock:
            self.user["access_token"] = f"mock-access-token-{uuid4().hex[:8]}"
            return deepcopy(self.user)

    def current_user_payload(self) -> dict:
        return self.login_payload()

    @staticmethod
    def _device_response_payload(device: dict) -> dict:
        fields = (
            "bound_status",
            "bound_time",
            "child_device_sn",
            "control_source",
            "device_desc",
            "device_name",
            "device_sn",
            "domain",
            "firmware_status",
            "firmware_version",
            "icon_url",
            "login_time",
            "nickname",
            "status",
            "sub_type",
            "thing_version",
            "type",
            "user_id",
            "workspace_id",
            "workspace_name",
        )
        return {field: deepcopy(device.get(field)) for field in fields if field in device}

    def current_workspace_payload(self) -> dict:
        with self._lock:
            return deepcopy(self.workspace)

    def seed_device(self, *, device_sn: str, name: str, model: str, domain: int = 0, bound: bool = True):
        with self._lock:
            seen_at = _iso()
            device = {
                "device_sn": device_sn,
                "device_name": name,
                "model": model,
                "workspace_id": self.workspace["workspace_id"],
                "workspace_name": self.workspace["workspace_name"],
                "control_source": "",
                "device_desc": "",
                "child_device_sn": "",
                "domain": domain,
                "status": True,
                "bound_status": bound,
                "login_time": seen_at,
                "bound_time": seen_at if bound else "",
                "nickname": name,
                "user_id": self.user["user_id"],
                "firmware_version": "v1.0.0",
                "firmware_status": "1",
                "thing_version": "1.0.0",
                "type": 67,
                "sub_type": 0,
                "icon_url": {},
                "payloads_list": [],
            }
            self.devices[device_sn] = device
            if bound:
                self.bound_device_sns.add(device_sn)
            else:
                self.bound_device_sns.discard(device_sn)
            if domain == 0:
                self.live_capacity[device_sn] = {
                    "sn": device_sn,
                    "name": name,
                    "cameras_list": [
                        {
                            "id": f"{device_sn}-camera-0",
                            "index": "88-0-0",
                            "videos_list": [
                                {
                                    "id": f"{device_sn}-video-normal-0",
                                    "index": "normal-0",
                                    "type": "normal",
                                }
                            ],
                        }
                    ],
                }
            else:
                self.live_capacity.pop(device_sn, None)

    @staticmethod
    def _paginate(items: list[dict], *, page: int, page_size: int) -> dict:
        normalized_page = max(1, int(page))
        normalized_page_size = max(1, int(page_size))
        start = (normalized_page - 1) * normalized_page_size
        end = start + normalized_page_size
        paged_items = items[start:end]
        return {
            "list": paged_items,
            "pagination": {
                "page": normalized_page,
                "page_size": normalized_page_size,
                "total": len(items),
            },
        }

    def list_devices(self, *, page: int = 1, page_size: int = 50) -> dict:
        with self._lock:
            items = [self._device_response_payload(self.devices[key]) for key in sorted(self.devices.keys())]
            return self._paginate(items, page=page, page_size=page_size)

    def list_bound_devices(self, *, domain: int | None = None, page: int = 1, page_size: int = 50) -> dict:
        with self._lock:
            items = [
                self._device_response_payload(self.devices[key])
                for key in sorted(self.bound_device_sns)
                if key in self.devices and (domain is None or self.devices[key].get("domain") == domain)
            ]
            return self._paginate(items, page=page, page_size=page_size)

    def live_capacity_payload(self) -> list[dict]:
        with self._lock:
            items = [deepcopy(self.live_capacity[key]) for key in sorted(self.live_capacity.keys())]
            return items

    def _resolve_live_device(self, payload: dict) -> tuple[str, str]:
        if not isinstance(payload, dict):
            return "", ""
        device_sn = str(payload.get("device_sn") or "").strip()
        video_id = str(payload.get("video_id") or "").strip()
        camera_index = ""
        if video_id:
            parts = [part for part in video_id.split("/") if part]
            if len(parts) >= 1 and not device_sn:
                device_sn = parts[0]
            if len(parts) >= 2:
                camera_index = parts[1]
        if not camera_index:
            camera_index = "88-0-0"
        return device_sn, camera_index

    def start_live(self, payload: dict) -> dict | None:
        with self._lock:
            device_sn, camera_index = self._resolve_live_device(payload)
            if not device_sn or device_sn not in self.bound_device_sns:
                return None
            stream_key = f"{device_sn}-{camera_index}"
            stream_payload = {
                "url": f"rtmp://mock-mediamtx:1935/live/{stream_key}",
                "rtmp_url": f"rtmp://mock-mediamtx:1935/live/{stream_key}",
                "webrtc_url": f"http://mock-mediamtx:8889/live/{stream_key}",
                "play_url": f"http://mock-mediamtx:8889/live/{stream_key}",
                "whep_url": f"http://mock-mediamtx:8889/live/{stream_key}/whep",
                "hls_url": f"http://mock-mediamtx:8888/live/{stream_key}/index.m3u8",
            }
            self.live_streams[stream_key] = {"payload": deepcopy(payload), "stream": stream_payload}
            return deepcopy(stream_payload)

    def stop_live(self, payload: dict) -> dict | None:
        with self._lock:
            device_sn, camera_index = self._resolve_live_device(payload)
            if not device_sn or device_sn not in self.bound_device_sns:
                return None
            stream_key = f"{device_sn}-{camera_index}"
            self.live_streams.pop(stream_key, None)
            return {}

    def update_live(self, payload: dict) -> dict | None:
        with self._lock:
            device_sn, _ = self._resolve_live_device(payload)
            if not device_sn or device_sn not in self.bound_device_sns:
                return None
            return {}

    def switch_live(self, payload: dict) -> dict | None:
        with self._lock:
            device_sn, _ = self._resolve_live_device(payload)
            if not device_sn or device_sn not in self.bound_device_sns:
                return None
            return {}

    def change_live_camera(self, payload: dict) -> dict | None:
        with self._lock:
            device_sn, _ = self._resolve_live_device(payload)
            if not device_sn or device_sn not in self.bound_device_sns:
                return None
            return {}

    def set_payload_authority_error(self, *, gateway_sn: str, payload_index: str, code: str = "E0001", msg: str = "mock payload authority failed"):
        with self._lock:
            self.payload_authority_errors[(gateway_sn, payload_index)] = {"code": code, "msg": msg}

    def set_payload_command_error(self, *, gateway_sn: str, cmd: str, code: str = "E0001", msg: str = "mock payload command failed"):
        with self._lock:
            self.payload_command_errors[(gateway_sn, cmd)] = {"code": code, "msg": msg}

    def grab_payload_authority(self, *, gateway_sn: str, payload: dict) -> dict:
        with self._lock:
            payload_index = str(payload.get("payload_index") or "").strip()
            request = {"gateway_sn": gateway_sn, "payload": deepcopy(payload)}
            self.payload_authority_requests.append(request)
            error = self.payload_authority_errors.get((gateway_sn, payload_index))
            if error:
                return {"ok": False, **deepcopy(error)}
            return {"ok": True, "gateway_sn": gateway_sn, "payload_index": payload_index}

    def send_payload_command(self, *, gateway_sn: str, payload: dict) -> dict:
        with self._lock:
            cmd = str(payload.get("cmd") or "").strip()
            request = {"gateway_sn": gateway_sn, "payload": deepcopy(payload)}
            self.payload_command_requests.append(request)
            error = self.payload_command_errors.get((gateway_sn, cmd))
            if error:
                return {"ok": False, **deepcopy(error)}
            return {"ok": True, "gateway_sn": gateway_sn, "cmd": cmd, "data": deepcopy(payload.get("data") or {})}

    def create_wayline(self, *, name: str, file_name: str | None = None) -> dict:
        with self._lock:
            wayline_id = f"mock-wayline-{uuid4().hex[:8]}"
            created_at = _iso()
            payload = {
                "wayline_id": wayline_id,
                "id": wayline_id,
                "name": name,
                "file_name": file_name or "",
                "created_at": created_at,
                "updated_at": created_at,
            }
            self.waylines[wayline_id] = payload
            return deepcopy(payload)

    def list_waylines(self) -> dict:
        with self._lock:
            items = sorted(self.waylines.values(), key=lambda item: item["created_at"], reverse=True)
            return {
                "list": deepcopy(items),
                "pagination": {
                    "page": 1,
                    "page_size": len(items) or 1,
                    "total": len(items),
                },
            }

    def duplicate_wayline_names(self, names: list[str]) -> list[str]:
        with self._lock:
            existing = {item["name"] for item in self.waylines.values()}
            return [name for name in names if name in existing]

    def delete_wayline(self, wayline_id: str) -> bool:
        with self._lock:
            return self.waylines.pop(wayline_id, None) is not None

    def create_job(self, payload: dict) -> dict:
        with self._lock:
            job_id = f"mock-job-{uuid4().hex[:8]}"
            created_at = _iso()
            job = {
                "job_id": job_id,
                "name": payload.get("name") or f"Mock Job {job_id[-4:]}",
                "file_id": payload.get("fileId") or payload.get("file_id", ""),
                "dock_sn": payload.get("dockSn") or payload.get("dock_sn", ""),
                "wayline_type": payload.get("waylineType", payload.get("wayline_type")),
                "task_type": payload.get("taskType", payload.get("task_type")),
                "rth_altitude": payload.get("rthAltitude", payload.get("rth_altitude")),
                "out_of_control_action": payload.get("outOfControlAction", payload.get("out_of_control_action")),
                "status": payload.get("status", "READY"),
                "created_at": created_at,
                "updated_at": created_at,
            }
            self.jobs[job_id] = job
            return deepcopy(job)

    def list_jobs(self) -> dict:
        with self._lock:
            items = sorted(self.jobs.values(), key=lambda item: item["created_at"], reverse=True)
            return {
                "list": deepcopy(items),
                "pagination": {
                    "page": 1,
                    "page_size": len(items) or 1,
                    "total": len(items),
                },
            }

    def cancel_jobs(self, job_ids: list[str]) -> list[str]:
        cancelled: list[str] = []
        with self._lock:
            for job_id in job_ids:
                job = self.jobs.get(job_id)
                if job is None:
                    continue
                job["status"] = "CANCELED"
                job["updated_at"] = _iso()
                cancelled.append(job_id)
        return cancelled

    def seed_media_file(
        self,
        *,
        file_id: str,
        name: str,
        device_sn: str,
        job_id: str,
        captured_at: str | None = None,
    ):
        with self._lock:
            self.media_files[file_id] = {
                "file_id": file_id,
                "media_type": 1,
                "name": name,
                "device_sn": device_sn,
                "job_id": job_id,
                "captured_at": captured_at or _iso(),
                "object_key": f"media/{name}",
                "file_size": 1024,
                "thumbnail_url": f"/__mock-dji__/_downloads/media/{file_id}/thumb",
            }

    def list_media_files(self) -> dict:
        with self._lock:
            items = sorted(self.media_files.values(), key=lambda item: item["captured_at"], reverse=True)
            return {
                "list": deepcopy(items),
                "pagination": {
                    "page": 1,
                    "page_size": len(items) or 1,
                    "total": len(items),
                },
            }


mock_dji_state = MockDjiState()
