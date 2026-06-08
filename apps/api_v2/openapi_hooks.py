import copy

from apps.common.api_response import build_standard_response
from apps.api_v2.docs_metadata import (
    API_V2_FRONTEND_GUIDE_DESCRIPTION,
    PUBLIC_METHODS,
    frontend_description,
    frontend_summary,
    request_examples,
)


def _get_component(result, ref):
    if not ref.startswith("#/components/schemas/"):
        return None
    name = ref.split("/")[-1]
    return result.get("components", {}).get("schemas", {}).get(name)


def _clone(value):
    return copy.deepcopy(value)


def _unwrap_standard_data_schema(schema, result):
    if not isinstance(schema, dict):
        return schema
    if "$ref" in schema:
        resolved = _get_component(result, schema["$ref"])
        return _unwrap_standard_data_schema(resolved, result) or schema

    properties = schema.get("properties", {})
    if {"code", "msg", "data"}.issubset(properties.keys()):
        return _unwrap_standard_data_schema(properties.get("data"), result)
    return _clone(schema)


def _flatten_nested_list_items(schema, result):
    if not isinstance(schema, dict):
        return schema

    properties = schema.get("properties", {})
    data_schema = properties.get("data")
    if not isinstance(data_schema, dict):
        return schema

    data_properties = data_schema.get("properties", {})
    list_schema = data_properties.get("list")
    if not isinstance(list_schema, dict):
        return schema

    items_schema = list_schema.get("items")
    nested_item_schema = _unwrap_standard_data_schema(items_schema, result)
    if not isinstance(nested_item_schema, dict):
        return schema

    nested_properties = nested_item_schema.get("properties", {})
    if "list" in nested_properties and isinstance(nested_properties["list"], dict):
        nested_list_items = nested_properties["list"].get("items")
        if nested_list_items:
            flattened = _clone(schema)
            flattened["properties"]["data"]["properties"]["list"]["items"] = _clone(nested_list_items)
            return flattened
    return schema


def _strip_standard_envelope(schema, result):
    if not isinstance(schema, dict):
        return schema

    if "$ref" in schema:
        resolved = _get_component(result, schema["$ref"])
        stripped = _strip_standard_envelope(resolved, result)
        return stripped or schema

    properties = _clone(schema.get("properties", {}))
    required = list(schema.get("required", []))

    if "data" in properties and "code" in properties and "msg" in properties:
        return _clone(schema)

    if "list" in properties and "total" in properties:
        return {
            "type": "object",
            "properties": {
                "list": _clone(properties["list"]),
                "total": _clone(properties["total"]),
            },
            "required": [name for name in ("list", "total") if name in required or name in properties],
        }

    return _clone(schema)


def _wrap_schema(schema, status_code, result):
    cleaned_data_schema = _strip_standard_envelope(schema, result)

    if isinstance(cleaned_data_schema, dict) and {"code", "msg", "data"}.issubset(cleaned_data_schema.get("properties", {}).keys()):
        return _flatten_nested_list_items(cleaned_data_schema, result)

    wrapped = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "业务码。成功固定为 00000，失败按 A/B/C/E 码段区分。",
            },
            "msg": {
                "type": "string",
                "description": "响应消息。成功通常为 success，失败为可直接展示的错误提示。",
            },
            "data": _clone(cleaned_data_schema) if cleaned_data_schema else {"nullable": True},
        },
        "required": ["code", "msg", "data"],
    }

    if str(status_code).startswith("2"):
        wrapped["properties"]["code"]["example"] = "00000"
        wrapped["properties"]["msg"]["example"] = "success"

    return _flatten_nested_list_items(wrapped, result)


def _wrap_example(example_value, status_code):
    return build_standard_response(example_value, int(status_code))


def standardize_v2_response_schema_hook(result, generator, request, public):
    del generator, request, public

    for path, path_item in result.get("paths", {}).items():
        if not path.startswith("/api/v2/"):
            continue
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            responses = operation.get("responses", {})
            for status_code, response in responses.items():
                content = response.get("content", {})
                for media_type, media_schema in content.items():
                    if "json" not in media_type:
                        continue
                    schema = media_schema.get("schema")
                    if schema:
                        media_schema["schema"] = _wrap_schema(schema, status_code, result)
                    examples = media_schema.get("examples", {})
                    for example in examples.values():
                        if "value" in example:
                            example["value"] = _wrap_example(example["value"], status_code)

    return result


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
