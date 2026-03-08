from django.test import TestCase
from rest_framework.test import APIClient


class BusinessApiResponseContractTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_health_should_include_business_code(self):
        response = self.client.get("/api/v1/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.get("business_code"), "SUCCESS")
        self.assertEqual(response.data.get("business_detail_code"), "OK")

    def test_root_should_include_business_code(self):
        response = self.client.get("/api/v1/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.get("business_code"), "SUCCESS")
        self.assertEqual(response.data.get("business_detail_code"), "OK")

    def test_business_endpoint_permission_error_should_include_business_code(self):
        response = self.client.get("/api/v1/drones")
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data.get("business_code"), "PERMISSION_DENIED")
        self.assertEqual(response.data.get("business_detail_code"), "NOT_AUTHENTICATED")
