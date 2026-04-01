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
                "workspace_id": "mock-workspace-001",
                "workspace_name": "Mock DJI Workspace",
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

            self.seed_device(device_sn="MOCK-DRONE-001", name="Mock Drone 1", model="Matrice 30T")
            self.seed_device(device_sn="MOCK-DRONE-002", name="Mock Drone 2", model="Matrice 3D")
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

    def current_workspace_payload(self) -> dict:
        with self._lock:
            return deepcopy(self.workspace)

    def seed_device(self, *, device_sn: str, name: str, model: str, domain: int = 0, bound: bool = True):
        with self._lock:
            seen_at = _iso()
            device = {
                "device_sn": device_sn,
                "name": name,
                "model": model,
                "domain": domain,
                "bound": bound,
                "last_seen_at": seen_at,
                "firmware_version": "v1.0.0",
                "firmware_status": "latest",
                "created_at": seen_at,
                "updated_at": seen_at,
            }
            self.devices[device_sn] = device
            if bound:
                self.bound_device_sns.add(device_sn)
            else:
                self.bound_device_sns.discard(device_sn)
            self.live_capacity[device_sn] = {
                "device_sn": device_sn,
                "name": name,
                "model": model,
                "status": "online",
                "cameras": [
                    {
                        "index": "camera-0",
                        "videos": [
                            {
                                "index": "normal-0",
                                "videoType": "normal",
                            }
                        ],
                    }
                ],
            }

    def list_devices(self) -> dict:
        with self._lock:
            items = [deepcopy(self.devices[key]) for key in sorted(self.devices.keys())]
            return {
                "list": items,
                "pagination": {
                    "page": 1,
                    "page_size": len(items) or 1,
                    "total": len(items),
                },
            }

    def list_bound_devices(self, *, domain: int | None = None) -> dict:
        with self._lock:
            items = [
                deepcopy(self.devices[key])
                for key in sorted(self.bound_device_sns)
                if key in self.devices and (domain is None or self.devices[key].get("domain") == domain)
            ]
            return {
                "list": items,
                "pagination": {
                    "page": 1,
                    "page_size": len(items) or 1,
                    "total": len(items),
                },
            }

    def live_capacity_payload(self) -> dict:
        with self._lock:
            items = [deepcopy(self.live_capacity[key]) for key in sorted(self.live_capacity.keys())]
            return {"list": items}

    def create_wayline(self, *, name: str, file_name: str | None = None) -> dict:
        with self._lock:
            wayline_id = f"mock-wayline-{uuid4().hex[:8]}"
            created_at = _iso()
            payload = {
                "wayline_id": wayline_id,
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
                "wayline_type": payload.get("waylineType"),
                "task_type": payload.get("taskType"),
                "rth_altitude": payload.get("rthAltitude"),
                "out_of_control_action": payload.get("outOfControlAction"),
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
