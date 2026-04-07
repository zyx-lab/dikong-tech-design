"""
Test script: upload routes/test.kmz to the real DJI upstream using the
backend DjiGateway.

Usage:
    cd /home/charles/dikong-tech-design
    source .venv/bin/activate
    export DJI_UPSTREAM_BASE_URL=http://8.129.135.140
    export DJI_UPSTREAM_USERNAME=adminPC
    export DJI_UPSTREAM_PASSWORD=adminPC1234567890
    export DJI_UPSTREAM_LOGIN_FLAG=1
    python scripts/test_dji_route_upload.py
"""
import json
import os
import sys
import uuid

# Add project root to path so 'config' module resolves
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from apps.dji_bff.gateway import DjiGateway, DjiGatewayError


def main():
    base_url = os.environ.get("DJI_UPSTREAM_BASE_URL", "")
    username = os.environ.get("DJI_UPSTREAM_USERNAME", "")
    login_flag = os.environ.get("DJI_UPSTREAM_LOGIN_FLAG", "1")

    print("=" * 60)
    print("DJI Route Upload Test")
    print("=" * 60)
    print(f"Base URL:  {base_url}")
    print(f"Username:  {username}")
    print(f"Login flag: {login_flag}")

    kmz_path = os.path.join(os.path.dirname(__file__), "..", "routes", "test.kmz")
    kmz_path = os.path.abspath(kmz_path)
    if not os.path.exists(kmz_path):
        print(f"ERROR: KMZ file not found: {kmz_path}")
        sys.exit(1)

    kmz_size = os.path.getsize(kmz_path)
    print(f"KMZ file:  {kmz_path} ({kmz_size} bytes)")

    gateway = DjiGateway()

    print("\n[1] Authenticating with DJI upstream...")
    try:
        config = gateway._ensure_authenticated()
        print(f"    OK — workspace_id={config.workspace_id}")
        print(f"    token prefix: {config.access_token[:20]}...")
    except Exception as exc:
        print(f"    FAILED: {exc}")
        sys.exit(1)

    route_name = f"test-upload-{uuid.uuid4().hex[:8]}"
    print(f"\n[2] Uploading route '{route_name}' via gateway.upload_route()...")
    try:
        with open(kmz_path, "rb") as f:
            payload = gateway.upload_route(route_name=route_name, file_obj=f)
        print(f"    Upload succeeded: dji_wayline_id={payload['dji_wayline_id']}, download_url={payload['download_url']}")
    except DjiGatewayError as exc:
        print(f"    ERROR: {exc}")
        print(f"    status_code={exc.status_code}")
        print(f"    data={json.dumps(exc.data, indent=4, ensure_ascii=False) if exc.data else None}")
        sys.exit(1)
    except Exception as exc:
        print(f"    FAILED (unexpected): {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
