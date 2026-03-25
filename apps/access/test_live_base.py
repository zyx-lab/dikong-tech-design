import json
from io import StringIO
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import LiveServerTestCase

from apps.access.test_support import ensure_staff_profile, ensure_tenant_role_binding

User = get_user_model()


def _normalize_header_name(name: str) -> str:
    if name.startswith("HTTP_"):
        name = name[5:]
    elif name == "CONTENT_TYPE":
        return "Content-Type"
    elif name == "CONTENT_LENGTH":
        return "Content-Length"
    return "-".join(part.capitalize() for part in name.split("_"))


class HttpResponseAdapter:
    def __init__(self, *, status_code: int, body: bytes, headers: dict[str, str]):
        self.status_code = status_code
        self._body = body
        self.headers = headers

    @property
    def text(self) -> str:
        return self._body.decode("utf-8") if self._body else ""

    def json(self):
        if not self._body:
            return None
        return json.loads(self.text)


class HttpJsonClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._credentials: dict[str, str] = {}

    def credentials(self, **headers):
        self._credentials = dict(headers)

    def get(self, path: str, data=None, format=None, **extra):
        return self._request("GET", path, params=data, headers=extra, format=format)

    def post(self, path: str, data=None, format=None, **extra):
        return self._request("POST", path, data=data, headers=extra, format=format)

    def put(self, path: str, data=None, format=None, **extra):
        return self._request("PUT", path, data=data, headers=extra, format=format)

    def patch(self, path: str, data=None, format=None, **extra):
        return self._request("PATCH", path, data=data, headers=extra, format=format)

    def delete(self, path: str, data=None, format=None, **extra):
        return self._request("DELETE", path, data=data, headers=extra, format=format)

    def _request(self, method: str, path: str, *, data=None, params=None, headers=None, format=None):
        if format not in (None, "json"):
            raise ValueError(f"Unsupported format: {format}")

        request_headers = self._merge_headers(headers or {})
        body = None
        if method in {"POST", "PUT", "PATCH", "DELETE"} and data is not None:
            body = json.dumps(data).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")

        request = Request(
            url=self._build_url(path, params=params),
            data=body,
            headers=request_headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=10) as response:
                return HttpResponseAdapter(
                    status_code=response.status,
                    body=response.read(),
                    headers=dict(response.headers.items()),
                )
        except HTTPError as exc:
            return HttpResponseAdapter(
                status_code=exc.code,
                body=exc.read(),
                headers=dict(exc.headers.items()) if exc.headers else {},
            )

    def _build_url(self, path: str, *, params=None) -> str:
        suffix = path if path.startswith("/") else f"/{path}"
        url = f"{self.base_url}{suffix}"
        if params:
            if hasattr(params, "items"):
                query_params = list(params.items())
            else:
                query_params = params
            return f"{url}?{urlencode(query_params, doseq=True)}"
        return url

    def _merge_headers(self, headers: dict[str, str]) -> dict[str, str]:
        merged: dict[str, str] = {}
        for source in (self._credentials, headers):
            for key, value in source.items():
                merged[_normalize_header_name(key)] = value
        return merged


class LiveIamApiTestCase(LiveServerTestCase):
    def setUp(self):
        super().setUp()
        self.client = self.new_client()

    def new_client(self) -> HttpJsonClient:
        return HttpJsonClient(self.live_server_url)

    def authenticate_client(
        self,
        client: HttpJsonClient,
        *,
        username: str,
        password: str,
        tenant_code: str | None = None,
    ) -> dict:
        response = client.post(
            "/api/v1/iam/session/login",
            {"username": username, "password": password},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.json())
        data = response.json()["data"]
        credentials = {"HTTP_AUTHORIZATION": f"Bearer {data['accessToken']}"}
        if tenant_code is not None:
            credentials["HTTP_X_TENANT_CODE"] = tenant_code
        client.credentials(**credentials)
        return data

    def login(self, *, username: str, password: str, tenant_code: str | None = None) -> dict:
        return self.authenticate_client(self.client, username=username, password=password, tenant_code=tenant_code)


class LiveTenantAdminApiTestCase(LiveIamApiTestCase):
    def setUp(self):
        super().setUp()
        call_command("seed_role_permissions", stdout=StringIO())
        self.admin_user = User.objects.create_user(username="tenant_admin_api", password="pass1234", status=1)
        ensure_staff_profile(self.admin_user, name="租户管理员")
        self.tenant, self.admin_member, self.admin_role = ensure_tenant_role_binding(
            self.admin_user,
            tenant_code="tenant_api_demo",
            role_code="tenant_admin",
            role_name="租户管理员",
            display_name="管理员",
        )
        self.login(username="tenant_admin_api", password="pass1234", tenant_code=self.tenant.code)


class LivePlatformOperatorApiTestCase(LiveIamApiTestCase):
    def setUp(self):
        super().setUp()
        call_command("seed_role_permissions", stdout=StringIO())
        self.platform_user = User.objects.create_user(
            username="platform_operator_api",
            password="pass1234",
            status=1,
            is_platform_admin=True,
        )
        ensure_staff_profile(self.platform_user, name="平台运营管理员")
        self.login(username="platform_operator_api", password="pass1234")
