from django.test import TestCase
from rest_framework.test import APIClient


class RouteCoverBase64SchemaTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def schema(self) -> dict:
        response = self.client.get("/api/v2/docs/schema/")
        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        return response.json()

    def schema_ref(self, schema: dict, schema_value: dict) -> dict:
        while "$ref" in schema_value:
            schema_name = schema_value["$ref"].split("/")[-1]
            schema_value = schema["components"]["schemas"][schema_name]
        return schema_value

    def request_body_properties(self, schema: dict, *, path: str, method: str, content_type: str) -> dict:
        request_schema = schema["paths"][path][method]["requestBody"]["content"][content_type]["schema"]
        return self.schema_ref(schema, request_schema).get("properties", {})

    def test_route_save_schema_should_document_json_base64_and_multipart_binary_for_create_and_update(self):
        schema = self.schema()

        for method, path in (
            ("post", "/api/v2/inspection/routes"),
            ("put", "/api/v2/inspection/routes/{id}"),
        ):
            with self.subTest(method=method, path=path):
                content = schema["paths"][path][method]["requestBody"]["content"]
                self.assertIn("application/json", content)
                self.assertIn("multipart/form-data", content)

                json_properties = self.request_body_properties(
                    schema,
                    path=path,
                    method=method,
                    content_type="application/json",
                )
                self.assertIn("coverImage", json_properties)
                self.assertIn("waypoints", json_properties)
                self.assertEqual(json_properties["coverImage"]["type"], "string")
                self.assertNotEqual(json_properties["coverImage"].get("format"), "binary")
                self.assertIn("base64", json_properties["coverImage"].get("description", ""))
                self.assertIn("data:image", json_properties["coverImage"].get("description", ""))
                self.assertEqual(json_properties["waypoints"]["type"], "array")

                multipart_properties = self.request_body_properties(
                    schema,
                    path=path,
                    method=method,
                    content_type="multipart/form-data",
                )
                self.assertEqual(multipart_properties["coverImage"]["type"], "string")
                self.assertEqual(multipart_properties["coverImage"]["format"], "binary")
                self.assertEqual(multipart_properties["waypoints"]["type"], "string")
