import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpResponseForbidden
from django.shortcuts import render
from django.views import View


LAUNCH_MODE = "LAUNCH_MODE"
FIELDS = (
    "DB_NAME",
    "DB_USER",
    "DB_PASSWORD",
    "DJANGO_SECRET_KEY",
    "DJANGO_ALLOWED_HOSTS",
    "MINIO_ROOT_USER",
    "MINIO_ROOT_PASSWORD",
    "OBJECT_STORAGE_BUCKET_NAME",
    "OBJECT_STORAGE_ENDPOINT_URL",
    "DJI_INTERNAL_API_TOKEN",
)
FORM_FIELDS = (LAUNCH_MODE,) + FIELDS
VALID_LAUNCH_MODES = {"local", "production"}
SECRET_FIELDS = {"DB_PASSWORD", "DJANGO_SECRET_KEY", "MINIO_ROOT_PASSWORD", "DJI_INTERNAL_API_TOKEN"}
LOCAL_DEFAULTS = {
    LAUNCH_MODE: "local",
    "DB_NAME": "dikong",
    "DB_USER": "dikong_app",
    "DJANGO_ALLOWED_HOSTS": "localhost,127.0.0.1",
    "MINIO_ROOT_USER": "minioadmin",
    "OBJECT_STORAGE_BUCKET_NAME": "dikong-route-covers",
    "OBJECT_STORAGE_ENDPOINT_URL": "http://127.0.0.1:9000",
}
SAFE_SECRET = re.compile(r"^[A-Za-z0-9._~!@%+=:,/-]+$")
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
HOSTS = re.compile(r"^[A-Za-z0-9.-]+(?:,[A-Za-z0-9.-]+)*$")
BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")


def _posted_values(post):
    values = {name: post.get(name, "").strip() for name in FORM_FIELDS}
    if values[LAUNCH_MODE] not in VALID_LAUNCH_MODES:
        values[LAUNCH_MODE] = "local"
    return values


def _display_values(values):
    return {name: value for name, value in values.items() if name not in SECRET_FIELDS}


def _validate(values):
    errors = {}
    is_production = values[LAUNCH_MODE] == "production"
    for name in FIELDS:
        if name == "DJI_INTERNAL_API_TOKEN":
            continue
        if not values[name]:
            errors[name] = "此项不能为空"

    for name in ("DB_NAME", "DB_USER"):
        if values[name] and not IDENTIFIER.fullmatch(values[name]):
            errors[name] = "只能包含字母、数字、下划线和连字符"

    for name, minimum in (
        ("DB_PASSWORD", 12),
        ("DJANGO_SECRET_KEY", 32),
        ("MINIO_ROOT_PASSWORD", 12),
        ("DJI_INTERNAL_API_TOKEN", 24),
    ):
        value = values[name]
        if value and (len(value) < minimum or not SAFE_SECRET.fullmatch(value)):
            errors[name] = f"至少 {minimum} 位，且不能包含空格、引号、$、# 或反斜杠"

    allowed_hosts = values["DJANGO_ALLOWED_HOSTS"]
    if allowed_hosts and (not HOSTS.fullmatch(allowed_hosts) or (is_production and allowed_hosts == "*")):
        errors["DJANGO_ALLOWED_HOSTS"] = "填写逗号分隔的域名或 IPv4 地址，正式部署不能使用 *"

    if values["MINIO_ROOT_USER"] and not SAFE_SECRET.fullmatch(values["MINIO_ROOT_USER"]):
        errors["MINIO_ROOT_USER"] = "不能包含空格、引号、$、# 或反斜杠"

    if values["OBJECT_STORAGE_BUCKET_NAME"] and not BUCKET.fullmatch(values["OBJECT_STORAGE_BUCKET_NAME"]):
        errors["OBJECT_STORAGE_BUCKET_NAME"] = "必须是 3-63 位小写 S3 bucket 名称"

    endpoint = urlsplit(values["OBJECT_STORAGE_ENDPOINT_URL"])
    allowed_schemes = {"https"} if is_production else {"http", "https"}
    if values["OBJECT_STORAGE_ENDPOINT_URL"] and (
        endpoint.scheme not in allowed_schemes or not endpoint.netloc or endpoint.path not in {"", "/"}
    ):
        errors["OBJECT_STORAGE_ENDPOINT_URL"] = "填写浏览器可访问的根地址；正式部署必须使用 HTTPS"
    return errors


def _write_env(path, values):
    managed = set(FIELDS) | {"DB_ENGINE", "DJANGO_DEBUG"}
    path = Path(path)
    preserved = []
    if path.exists():
        for line in path.read_text().splitlines():
            match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", line)
            if not match or match.group(1) not in managed:
                preserved.append(line)
    lines = preserved + ([""] if preserved and preserved[-1] else []) + [
        "DB_ENGINE=postgres",
        f"DB_NAME={values['DB_NAME']}",
        f"DB_USER={values['DB_USER']}",
        f"DB_PASSWORD={values['DB_PASSWORD']}",
        f"DJANGO_SECRET_KEY={values['DJANGO_SECRET_KEY']}",
        f"DJANGO_DEBUG={'false' if values[LAUNCH_MODE] == 'production' else 'true'}",
        f"DJANGO_ALLOWED_HOSTS={values['DJANGO_ALLOWED_HOSTS']}",
        f"MINIO_ROOT_USER={values['MINIO_ROOT_USER']}",
        f"MINIO_ROOT_PASSWORD={values['MINIO_ROOT_PASSWORD']}",
        f"OBJECT_STORAGE_BUCKET_NAME={values['OBJECT_STORAGE_BUCKET_NAME']}",
        f"OBJECT_STORAGE_ENDPOINT_URL={values['OBJECT_STORAGE_ENDPOINT_URL'].rstrip('/')}",
        f"DJI_INTERNAL_API_TOKEN={values['DJI_INTERNAL_API_TOKEN']}",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".env.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w") as env_file:
            env_file.write("\n".join(lines) + "\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class OnboardingView(View):
    template_name = "onboarding.html"

    def get(self, request):
        return render(request, self.template_name, {"config_values": LOCAL_DEFAULTS})

    def post(self, request):
        if not settings.ONBOARDING_CONFIG_WRITABLE:
            return HttpResponseForbidden("当前服务不允许修改生产配置")
        values = _posted_values(request.POST)
        errors = _validate(values)
        if errors:
            return render(
                request,
                self.template_name,
                {"config_values": _display_values(values), "config_errors": errors},
                status=400,
            )
        _write_env(settings.ONBOARDING_ENV_PATH, values)
        return render(request, self.template_name, {"config_saved": True, "config_values": _display_values(values)})
