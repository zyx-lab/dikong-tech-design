"""
Test script: upload routes/test.kmz to the real DJI upstream using the
backend DjiGateway.

Usage:
    cd /home/charles/dikong-tech-design
    source .venv/bin/activate
    export DJI_UPSTREAM_BASE_URL=<your-base-url>
    export DJI_UPSTREAM_USERNAME=<your-username>
    export DJI_UPSTREAM_PASSWORD=<your-password>
    export DJI_UPSTREAM_LOGIN_FLAG=<your-login-flag>
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
    kmz_path = os.path.join(os.path.dirname(__file__), "..", "routes", "test.kmz")
    kmz_path = os.path.abspath(kmz_path)
    if not os.path.exists(kmz_path):
        print(f"ERROR: KMZ file not found: {kmz_path}")
        sys.exit(1)

    gateway = DjiGateway()
    route_name = f"test-upload-{uuid.uuid4().hex[:8]}"
    payload = None

    try:
        with open(kmz_path, "rb") as f:
            payload = gateway.upload_route(route_name=route_name, file_obj=f)
        print(f"dji_wayline_id={payload['dji_wayline_id']}")
        print(f"download_url={payload['download_url']}")
    except DjiGatewayError as exc:
        print(f"ERROR: {exc}")
        print(f"status_code={exc.status_code}")
        print(f"data={json.dumps(exc.data, indent=4, ensure_ascii=False) if exc.data else None}")
        sys.exit(1)
    except Exception as exc:
        print(f"FAILED: {exc}")
        sys.exit(1)
    else:
        try:
            gateway.delete_route(payload["dji_wayline_id"])
            print(f"cleanup: deleted {payload['dji_wayline_id']}")
        except DjiGatewayError as exc:
            print(f"cleanup: delete_route failed: {exc}")
        except Exception as exc:
            print(f"cleanup: unexpected failure: {exc}")


if __name__ == "__main__":
    main()
