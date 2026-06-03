#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Final, Sequence

JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
JsonDict = dict[str, JsonValue]

HTTP_METHODS: Final = frozenset({"get", "post", "put", "patch", "delete"})
MAX_INPUT_BYTES: Final = 10 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS: Final = 15


class SchemaLoadError(RuntimeError):
    pass


def _as_dict(value: JsonValue) -> JsonDict:
    if isinstance(value, dict):
        return value
    return {}


def _canonical_json(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return {key: _canonical_json(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonical_json(item) for item in value]
    return value


def canonicalize_schema(schema: JsonDict) -> JsonDict:
    components = _as_dict(schema.get("components", {}))
    return {
        "info": _canonical_json(_as_dict(schema.get("info", {}))),
        "paths": _canonical_json(_as_dict(schema.get("paths", {}))),
        "components": {"schemas": _canonical_json(_as_dict(components.get("schemas", {})))},
    }


def _operation_methods(path_item: JsonValue) -> dict[str, JsonValue]:
    methods: dict[str, JsonValue] = {}
    if not isinstance(path_item, dict):
        return methods
    for key, value in path_item.items():
        method = key.lower()
        if method in HTTP_METHODS:
            methods[method.upper()] = _canonical_json(value)
    return methods


def _schema_diffs(live_schemas: JsonDict, local_schemas: JsonDict) -> JsonDict:
    live_names = set(live_schemas)
    local_names = set(local_schemas)
    shared_names = sorted(live_names & local_names)
    schema_diffs: list[JsonValue] = []
    for name in shared_names:
        if live_schemas[name] != local_schemas[name]:
            schema_diffs.append({"schema": name, "live": live_schemas[name], "local": local_schemas[name]})
    return {
        "schemas_only_live": sorted(live_names - local_names),
        "schemas_only_local": sorted(local_names - live_names),
        "schema_diffs": schema_diffs,
    }


def diff_schemas(live: JsonDict, local: JsonDict) -> JsonDict:
    live_schema = canonicalize_schema(live)
    local_schema = canonicalize_schema(local)
    live_paths = _as_dict(live_schema["paths"])
    local_paths = _as_dict(local_schema["paths"])
    shared_paths = sorted(set(live_paths) & set(local_paths))
    method_diffs: list[JsonValue] = []
    operation_diffs: list[JsonValue] = []

    for path in shared_paths:
        live_methods = _operation_methods(live_paths[path])
        local_methods = _operation_methods(local_paths[path])
        if set(live_methods) != set(local_methods):
            method_diffs.append(
                {
                    "path": path,
                    "live_methods": sorted(live_methods),
                    "local_methods": sorted(local_methods),
                }
            )
        for method in sorted(set(live_methods) & set(local_methods)):
            if live_methods[method] != local_methods[method]:
                operation_diffs.append(
                    {
                        "path": path,
                        "method": method,
                        "live": live_methods[method],
                        "local": local_methods[method],
                    }
                )

    live_components = _as_dict(live_schema["components"])
    local_components = _as_dict(local_schema["components"])
    component_diffs = _schema_diffs(
        _as_dict(live_components.get("schemas", {})),
        _as_dict(local_components.get("schemas", {})),
    )
    return {
        "info_diff": {}
        if live_schema["info"] == local_schema["info"]
        else {"live": live_schema["info"], "local": local_schema["info"]},
        "paths_only_live": sorted(set(live_paths) - set(local_paths)),
        "paths_only_local": sorted(set(local_paths) - set(live_paths)),
        "method_diffs": method_diffs,
        "operation_diffs": operation_diffs,
        "component_diffs": component_diffs,
    }


def _has_drift(diff: JsonDict) -> bool:
    component_diffs = _as_dict(diff["component_diffs"])
    return bool(
        diff["info_diff"]
        or diff["paths_only_live"]
        or diff["paths_only_local"]
        or diff["method_diffs"]
        or diff["operation_diffs"]
        or component_diffs["schemas_only_live"]
        or component_diffs["schemas_only_local"]
        or component_diffs["schema_diffs"]
    )


def _strip_http_wrapper(raw_text: str) -> str:
    stripped = raw_text.lstrip()
    if stripped.startswith("{"):
        return stripped
    json_start = stripped.find("{")
    if json_start < 0:
        raise SchemaLoadError("schema source does not contain a JSON object")
    return stripped[json_start:]


def _json_schema_from_text(raw_text: str) -> JsonDict:
    json_text = _strip_http_wrapper(raw_text)
    try:
        parsed, _end = json.JSONDecoder().raw_decode(json_text)
    except json.JSONDecodeError as exc:
        raise SchemaLoadError(f"schema source is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SchemaLoadError("schema source must be a JSON object")
    return parsed


def _read_file_schema(path: Path) -> JsonDict:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise SchemaLoadError(f"cannot read {path}: {exc}") from exc
    if len(payload) > MAX_INPUT_BYTES:
        raise SchemaLoadError(f"{path} exceeds {MAX_INPUT_BYTES} bytes")
    return _json_schema_from_text(payload.decode("utf-8"))


def _fetch_url_schema(url: str, *, timeout_seconds: int) -> JsonDict:
    request = urllib.request.Request(url, headers={"User-Agent": "dikong-v2-docs-sync/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read(MAX_INPUT_BYTES + 1)
    except urllib.error.URLError as exc:
        raise SchemaLoadError(f"cannot fetch {url}: {exc}") from exc
    if len(payload) > MAX_INPUT_BYTES:
        raise SchemaLoadError(f"{url} exceeds {MAX_INPUT_BYTES} bytes")
    return _json_schema_from_text(payload.decode("utf-8"))


def _django_schema() -> JsonDict:
    project_root = Path(__file__).resolve().parents[1]
    project_root_text = str(project_root)
    if project_root_text not in sys.path:
        sys.path.insert(0, project_root_text)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()
    from rest_framework.test import APIClient

    response = APIClient().get("/api/v2/docs/schema/")
    if response.status_code != 200:
        raise SchemaLoadError(f"local Django schema returned HTTP {response.status_code}")
    return response.json()


def _write_json(path: Path, value: JsonDict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _result(live_source: str, local_source: str, live: JsonDict, local: JsonDict) -> JsonDict:
    diff = diff_schemas(live, local)
    live_components = _as_dict(canonicalize_schema(live)["components"])
    local_components = _as_dict(canonicalize_schema(local)["components"])
    return {
        "live_source": live_source,
        "local_source": local_source,
        "live_path_count": len(_as_dict(canonicalize_schema(live)["paths"])),
        "local_path_count": len(_as_dict(canonicalize_schema(local)["paths"])),
        "live_component_schema_count": len(_as_dict(live_components.get("schemas", {}))),
        "local_component_schema_count": len(_as_dict(local_components.get("schemas", {}))),
        "has_drift": _has_drift(diff),
        "diff": diff,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare deployed and local API v2 OpenAPI schemas.")
    parser.add_argument("--live-url")
    parser.add_argument("--live-file", type=Path)
    parser.add_argument("--local-django", action="store_true")
    parser.add_argument("--local-url")
    parser.add_argument("--local-file", type=Path)
    parser.add_argument("--write-local", type=Path)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    live_source_count = sum(source is not None for source in (args.live_url, args.live_file))
    local_source_count = sum(source is not None and source is not False for source in (args.local_django, args.local_url, args.local_file))
    if live_source_count != 1:
        parser.error("provide exactly one live source: --live-url or --live-file")
    if local_source_count != 1:
        parser.error("provide exactly one local source: --local-django, --local-url, or --local-file")
    if args.write_local and not args.local_django:
        parser.error("--write-local requires --local-django")

    try:
        live_schema = _fetch_url_schema(args.live_url, timeout_seconds=args.timeout) if args.live_url else _read_file_schema(args.live_file)
        live_source = args.live_url if args.live_url else str(args.live_file)
        if args.local_django:
            local_schema = _django_schema()
            local_source = "django:/api/v2/docs/schema/"
            if args.write_local:
                _write_json(args.write_local, local_schema)
        elif args.local_url:
            local_schema = _fetch_url_schema(args.local_url, timeout_seconds=args.timeout)
            local_source = args.local_url
        else:
            local_schema = _read_file_schema(args.local_file)
            local_source = str(args.local_file)
        result = _result(str(live_source), str(local_source), live_schema, local_schema)
        if args.output_json:
            _write_json(args.output_json, result)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 1 if result["has_drift"] else 0
    except SchemaLoadError as exc:
        print(f"schema comparison failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
