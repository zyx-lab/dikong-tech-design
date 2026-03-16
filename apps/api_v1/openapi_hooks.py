import copy

from apps.api_v1.business_response import build_standard_response


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


def _strip_legacy_envelope(schema, result):
    if not isinstance(schema, dict):
        return schema

    if "$ref" in schema:
        resolved = _get_component(result, schema["$ref"])
        stripped = _strip_legacy_envelope(resolved, result)
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
    cleaned_data_schema = _strip_legacy_envelope(schema, result)

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


def _normalize_example_payload(value):
    if not isinstance(value, dict):
        return value

    return value


def _wrap_example(example_value, status_code):
    normalized = _normalize_example_payload(example_value)
    return build_standard_response(normalized, int(status_code))


def standardize_response_schema_hook(result, generator, request, public):
    del generator, request, public

    paths = result.get("paths", {})
    for path_item in paths.values():
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
