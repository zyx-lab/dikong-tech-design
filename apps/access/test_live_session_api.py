"""Session IAM test events.

- 用户名注册 -> 登录 -> refresh rotation -> 登出失效
- 手机号注册校验 mock SMS 与重复手机号冲突
- 严格请求体拒绝旧字段和未知字段
- 平台 superuser 禁止登录 formal IAM
- 禁用用户后撤销全部活跃 Bearer 会话
"""

from apps.access.models import AuthSession, UserStatus
from apps.access.test_live_base import LiveIamApiTestCase, User
from apps.access.test_support import ensure_staff_profile


class LiveSessionApiTests(LiveIamApiTestCase):
    def test_register_login_refresh_logout_should_follow_bearer_session_contract(self):
        register_response = self.client.post(
            "/api/v1/iam/session/register",
            {
                "username": "iam_register_user",
                "password": "pass1234",
                "name": "张三",
                "phone": "13800138000",
            },
            format="json",
        )
        self.assertEqual(register_response.status_code, 201)
        self.assertEqual(register_response.json()["code"], "00000")
        self.assertEqual(register_response.json()["data"]["status"], "ACTIVE")
        self.assertNotIn("accessToken", register_response.json()["data"])

        login_response = self.client.post(
            "/api/v1/iam/session/login",
            {
                "username": "iam_register_user",
                "password": "pass1234",
            },
            format="json",
        )
        self.assertEqual(login_response.status_code, 200)
        login_data = login_response.json()["data"]
        self.assertEqual(login_data["tokenType"], "Bearer")
        self.assertEqual(login_data["expiresIn"], 7200)
        self.assertEqual(login_data["refreshExpiresIn"], 604800)
        self.assertEqual(login_data["user"]["status"], "ACTIVE")
        self.assertFalse(login_data["user"]["hasPlatformAccess"])
        access_token = login_data["accessToken"]
        refresh_token = login_data["refreshToken"]

        refresh_response = self.client.post(
            "/api/v1/iam/session/refresh",
            {"refreshToken": refresh_token},
            format="json",
        )
        self.assertEqual(refresh_response.status_code, 200)
        refresh_data = refresh_response.json()["data"]
        self.assertNotEqual(refresh_data["refreshToken"], refresh_token)
        self.assertNotEqual(refresh_data["accessToken"], access_token)

        old_refresh_response = self.client.post(
            "/api/v1/iam/session/refresh",
            {"refreshToken": refresh_token},
            format="json",
        )
        self.assertEqual(old_refresh_response.status_code, 401)
        self.assertEqual(old_refresh_response.json()["code"], "A0401")

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_data['accessToken']}")
        logout_response = self.client.post("/api/v1/iam/session/logout", {}, format="json")
        self.assertEqual(logout_response.status_code, 200)
        self.assertIsNone(logout_response.json()["data"])

        profile_response = self.client.get("/api/v1/iam/me/profile")
        self.assertEqual(profile_response.status_code, 401)
        self.assertEqual(profile_response.json()["code"], "A0401")

    def test_register_by_phone_should_use_mock_sms_and_report_phone_duplicate(self):
        invalid_sms_response = self.client.post(
            "/api/v1/iam/session/register-by-phone",
            {"phone": "13900139000", "smsCode": "000000", "password": "pass1234"},
            format="json",
        )
        self.assertEqual(invalid_sms_response.status_code, 400)
        self.assertEqual(invalid_sms_response.json()["code"], "B0001")

        first_response = self.client.post(
            "/api/v1/iam/session/register-by-phone",
            {"phone": "13900139000", "smsCode": "123456", "password": "pass1234"},
            format="json",
        )
        self.assertEqual(first_response.status_code, 201)
        self.assertEqual(first_response.json()["data"]["username"], "13900139000")

        duplicate_response = self.client.post(
            "/api/v1/iam/session/register-by-phone",
            {"phone": "13900139000", "smsCode": "123456", "password": "pass1234"},
            format="json",
        )
        self.assertEqual(duplicate_response.status_code, 409)
        self.assertEqual(duplicate_response.json()["code"], "C0102")

    def test_request_contract_should_reject_removed_or_unknown_fields(self):
        legacy_register_response = self.client.post(
            "/api/v1/iam/session/register-by-phone",
            {"phone": "13900139001", "sms_code": "123456", "password": "pass1234"},
            format="json",
        )
        self.assertEqual(legacy_register_response.status_code, 400)
        self.assertEqual(legacy_register_response.json()["code"], "B0001")
        self.assertIn("sms_code", legacy_register_response.json()["data"])

        register_response = self.client.post(
            "/api/v1/iam/session/register",
            {
                "username": "strict_register_user",
                "password": "pass1234",
                "name": "严格注册用户",
                "phone": "13900139002",
                "status": "ACTIVE",
            },
            format="json",
        )
        self.assertEqual(register_response.status_code, 400)
        self.assertEqual(register_response.json()["code"], "B0001")
        self.assertIn("status", register_response.json()["data"])

    def test_register_should_report_duplicate_username_and_phone_conflicts(self):
        duplicate_username_user = User.objects.create_user(username="duplicate_username", password="pass1234", status=UserStatus.ACTIVE)
        ensure_staff_profile(duplicate_username_user, name="重名用户")

        phone_owner = User.objects.create_user(username="phone_owner", password="pass1234", status=UserStatus.ACTIVE)
        staff_profile = ensure_staff_profile(phone_owner, name="手机号占用用户")
        staff_profile.phone = "13900139003"
        staff_profile.save()

        duplicate_username_response = self.client.post(
            "/api/v1/iam/session/register",
            {
                "username": "duplicate_username",
                "password": "pass1234",
                "name": "新用户",
                "phone": "13900139004",
            },
            format="json",
        )
        self.assertEqual(duplicate_username_response.status_code, 409)
        self.assertEqual(duplicate_username_response.json()["code"], "C0101")

        duplicate_phone_response = self.client.post(
            "/api/v1/iam/session/register",
            {
                "username": "fresh_username",
                "password": "pass1234",
                "name": "新用户",
                "phone": "13900139003",
            },
            format="json",
        )
        self.assertEqual(duplicate_phone_response.status_code, 409)
        self.assertEqual(duplicate_phone_response.json()["code"], "C0102")

    def test_logout_should_reject_non_empty_body(self):
        user = User.objects.create_user(username="logout_body_guard", password="pass1234", status=UserStatus.ACTIVE)
        ensure_staff_profile(user, name="登出请求体验证用户")
        self.login(username="logout_body_guard", password="pass1234")

        response = self.client.post(
            "/api/v1/iam/session/logout",
            {"reason": "manual"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "B0001")
        self.assertIn("body", response.json()["data"])

    def test_login_and_refresh_should_reject_invalid_payloads(self):
        login_missing_password_response = self.client.post(
            "/api/v1/iam/session/login",
            {"username": "only_username"},
            format="json",
        )
        self.assertEqual(login_missing_password_response.status_code, 400)
        self.assertEqual(login_missing_password_response.json()["code"], "B0001")
        self.assertIn("password", login_missing_password_response.json()["data"])

        login_unknown_field_response = self.client.post(
            "/api/v1/iam/session/login",
            {"username": "only_username", "password": "pass1234", "tenantCode": "demo"},
            format="json",
        )
        self.assertEqual(login_unknown_field_response.status_code, 400)
        self.assertEqual(login_unknown_field_response.json()["code"], "B0001")
        self.assertIn("tenantCode", login_unknown_field_response.json()["data"])

        refresh_missing_token_response = self.client.post(
            "/api/v1/iam/session/refresh",
            {},
            format="json",
        )
        self.assertEqual(refresh_missing_token_response.status_code, 400)
        self.assertEqual(refresh_missing_token_response.json()["code"], "B0001")
        self.assertIn("refreshToken", refresh_missing_token_response.json()["data"])

        refresh_unknown_field_response = self.client.post(
            "/api/v1/iam/session/refresh",
            {"refreshToken": "fake-token", "accessToken": "unexpected"},
            format="json",
        )
        self.assertEqual(refresh_unknown_field_response.status_code, 400)
        self.assertEqual(refresh_unknown_field_response.json()["code"], "B0001")
        self.assertIn("accessToken", refresh_unknown_field_response.json()["data"])

    def test_superuser_should_not_be_allowed_to_login_formal_iam(self):
        User.objects.create_superuser(username="root_formal_forbidden", password="pass1234")

        response = self.client.post(
            "/api/v1/iam/session/login",
            {"username": "root_formal_forbidden", "password": "pass1234"},
            format="json",
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "A0401")

    def test_disabling_user_should_revoke_all_active_auth_sessions(self):
        user = User.objects.create_user(username="session_revoke_user", password="pass1234", status=UserStatus.ACTIVE)
        ensure_staff_profile(user, name="会话撤销用户")

        client_a = self.new_client()
        client_b = self.new_client()
        response_a = client_a.post(
            "/api/v1/iam/session/login",
            {"username": "session_revoke_user", "password": "pass1234"},
            format="json",
        )
        response_b = client_b.post(
            "/api/v1/iam/session/login",
            {"username": "session_revoke_user", "password": "pass1234"},
            format="json",
        )
        token_a = response_a.json()["data"]["accessToken"]
        self.assertEqual(AuthSession.objects.filter(user=user, revoked_at__isnull=True).count(), 2)
        self.assertEqual(response_b.status_code, 200)

        user.status = UserStatus.DISABLED
        user.save(update_fields=["status", "updated_at"])

        self.assertEqual(AuthSession.objects.filter(user=user, revoked_at__isnull=True).count(), 0)

        client_a.credentials(HTTP_AUTHORIZATION=f"Bearer {token_a}")
        profile_response = client_a.get("/api/v1/iam/me/profile")
        self.assertEqual(profile_response.status_code, 401)
        self.assertEqual(profile_response.json()["code"], "A0401")
