from django.test import SimpleTestCase
from django.urls import reverse


class OnboardingPageTests(SimpleTestCase):
    def test_page_explains_the_postgresql_deployment_sequence(self):
        response = self.client.get(reverse("onboarding"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "接手低空平台")
        self.assertContains(response, "部署前检查")
        self.assertContains(response, "生产配置")
        self.assertContains(response, "按顺序启动")
        self.assertContains(response, "上线验收")
        self.assertContains(response, "DB_ENGINE=postgres")
        self.assertContains(response, "DJANGO_DEBUG=false")
        self.assertContains(response, "v2-dji-worker")
        self.assertNotContains(response, "SQLite")
