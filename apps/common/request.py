import json


def load_json_body(request) -> dict:
    if not getattr(request, "body", None):
        return {}
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def resolve_client_ip(request) -> str | None:
    if request is None:
        return None

    meta = getattr(request, "META", {}) or {}
    forwarded_for = meta.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return meta.get("REMOTE_ADDR")
