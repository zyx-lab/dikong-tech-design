import tempfile
from pathlib import Path

from django.test import SimpleTestCase, override_settings
from django.urls import reverse


class OnboardingPageTests(SimpleTestCase):
    def test_page_explains_the_postgresql_deployment_sequence(self):
        response = self.client.get(reverse("onboarding"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "接手低空平台")
        self.assertContains(response, "安装与拉取代码")
        self.assertContains(response, "生产配置")
        self.assertContains(response, "按顺序启动")
        self.assertContains(response, "上线验收")
        self.assertContains(response, "DB_ENGINE=postgres")
        self.assertContains(response, "DJANGO_DEBUG=false")
        self.assertContains(response, "v2-dji-worker")
        self.assertContains(response, "HTTPS 反向代理")
        self.assertContains(response, "创建平台管理员")
        self.assertContains(response, "createsuperuser")
        self.assertContains(response, "创建 DJI 连接")
        self.assertContains(response, "备份与恢复")
        self.assertContains(response, "更新与回滚")
        self.assertContains(response, "日志与停服")
        self.assertContains(response, "保存生产配置")
        self.assertContains(response, 'name="DB_PASSWORD"')
        self.assertContains(response, "可选媒体 Webhook Token")
        self.assertContains(response, "DJI 上云地址、账号和密码由平台用户配置")
        self.assertNotContains(response, "SQLite")

    @override_settings(ONBOARDING_CONFIG_WRITABLE=False)
    def test_regular_web_service_cannot_write_deployment_config(self):
        response = self.client.post(reverse("onboarding"))

        self.assertEqual(response.status_code, 403)

    def test_local_setup_writes_compose_env_file(self):
        values = {
            "DB_NAME": "dikong",
            "DB_USER": "dikong_app",
            "DB_PASSWORD": "database-secret-123",
            "DJANGO_SECRET_KEY": "a" * 48,
            "DJANGO_ALLOWED_HOSTS": "api.example.com",
            "MINIO_ROOT_USER": "dikong-storage",
            "MINIO_ROOT_PASSWORD": "storage-secret-123",
            "OBJECT_STORAGE_BUCKET_NAME": "dikong-route-covers",
            "OBJECT_STORAGE_ENDPOINT_URL": "https://files.example.com",
            "DJI_INTERNAL_API_TOKEN": "b" * 48,
        }
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("CUSTOM_DEPLOYMENT_FLAG=keep-me\n")
            with override_settings(ONBOARDING_ENV_PATH=env_path, ONBOARDING_CONFIG_WRITABLE=True):
                response = self.client.post(reverse("onboarding"), values)

            self.assertEqual(response.status_code, 200)
            config = env_path.read_text()
            self.assertIn("DB_ENGINE=postgres", config)
            self.assertIn("DB_USER=dikong_app", config)
            self.assertIn("DJANGO_DEBUG=false", config)
            self.assertIn("OBJECT_STORAGE_ENDPOINT_URL=https://files.example.com", config)
            self.assertIn("CUSTOM_DEPLOYMENT_FLAG=keep-me", config)

    def test_dji_callback_token_is_optional(self):
        values = {
            "DB_NAME": "dikong",
            "DB_USER": "dikong_app",
            "DB_PASSWORD": "database-secret-123",
            "DJANGO_SECRET_KEY": "a" * 48,
            "DJANGO_ALLOWED_HOSTS": "api.example.com",
            "MINIO_ROOT_USER": "dikong-storage",
            "MINIO_ROOT_PASSWORD": "storage-secret-123",
            "OBJECT_STORAGE_BUCKET_NAME": "dikong-route-covers",
            "OBJECT_STORAGE_ENDPOINT_URL": "https://files.example.com",
            "DJI_INTERNAL_API_TOKEN": "",
        }
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            with override_settings(ONBOARDING_ENV_PATH=env_path, ONBOARDING_CONFIG_WRITABLE=True):
                response = self.client.post(reverse("onboarding"), values)

            self.assertEqual(response.status_code, 200)
            self.assertIn("DJI_INTERNAL_API_TOKEN=", env_path.read_text())
