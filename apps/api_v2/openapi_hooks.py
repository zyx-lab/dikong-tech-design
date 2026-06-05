from apps.api_v2.docs_metadata import (
    API_V2_FRONTEND_GUIDE_DESCRIPTION,
    PUBLIC_METHODS,
    frontend_description,
    frontend_summary,
    request_examples,
)


def _iter_v2_operations(result):
    for path, path_item in result.get("paths", {}).items():
        if not path.startswith("/api/v2/"):
            continue
        for method, operation in path_item.items():
            if method.lower() not in PUBLIC_METHODS or not isinstance(operation, dict):
                continue
            yield path, method, operation


def _enrich_request_examples(path: str, method: str, operation: dict) -> None:
    request_body = operation.get("requestBody")
    if not isinstance(request_body, dict):
        return
    content = request_body.get("content")
    if not isinstance(content, dict):
        return

    for media_type, media in content.items():
        if not isinstance(media, dict):
            continue
        if not ("json" in media_type or media_type == "multipart/form-data"):
            continue
        media.setdefault("examples", request_examples(method, path, media_type))


def enrich_v2_frontend_docs_hook(result, generator, request, public):
    del generator, request, public

    info = result.setdefault("info", {})
    info["description"] = API_V2_FRONTEND_GUIDE_DESCRIPTION

    for path, method, operation in _iter_v2_operations(result):
        operation["summary"] = frontend_summary(method, path, operation)
        operation["description"] = frontend_description(method, path, operation)
        _enrich_request_examples(path, method, operation)

    return result
