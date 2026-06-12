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

    def test_route_save_schema_should_document_multipart_create_and_json_metadata_update(self):
        schema = self.schema()

        post_content = schema["paths"]["/api/v2/inspection/routes"]["post"]["requestBody"]["content"]
        self.assertNotIn("application/json", post_content)
        self.assertIn("multipart/form-data", post_content)
        post_multipart = self.request_body_properties(
            schema,
            path="/api/v2/inspection/routes",
            method="post",
            content_type="multipart/form-data",
        )
        self.assertEqual(post_multipart["coverImage"]["type"], "string")
        self.assertEqual(post_multipart["coverImage"]["format"], "binary")
        self.assertEqual(post_multipart["kmzFile"]["format"], "binary")
        self.assertIn("djiConnectionId", post_multipart)
        self.assertNotIn("waypoints", post_multipart)
        self.assertNotIn("waylineType", post_multipart)
        self.assertNotIn("defaultAltitude", post_multipart)
        self.assertNotIn("defaultSpeed", post_multipart)

        put_content = schema["paths"]["/api/v2/inspection/routes/{id}"]["put"]["requestBody"]["content"]
        self.assertIn("application/json", put_content)
        self.assertIn("multipart/form-data", put_content)
        put_json = self.request_body_properties(
            schema,
            path="/api/v2/inspection/routes/{id}",
            method="put",
            content_type="application/json",
        )
        self.assertIn("coverImage", put_json)
        self.assertNotIn("waypoints", put_json)
        self.assertEqual(put_json["coverImage"]["type"], "string")
        self.assertNotEqual(put_json["coverImage"].get("format"), "binary")
        self.assertIn("base64", put_json["coverImage"].get("description", ""))
        self.assertIn("data:image", put_json["coverImage"].get("description", ""))

        put_multipart = self.request_body_properties(
            schema,
            path="/api/v2/inspection/routes/{id}",
            method="put",
            content_type="multipart/form-data",
        )
        self.assertEqual(put_multipart["coverImage"]["type"], "string")
        self.assertEqual(put_multipart["coverImage"]["format"], "binary")
        self.assertIn("djiConnectionId", put_multipart)
        self.assertIn("kmzFile", put_multipart)
        self.assertNotIn("waypoints", put_multipart)
        self.assertNotIn("waylineType", put_multipart)
        self.assertNotIn("defaultAltitude", put_multipart)
        self.assertNotIn("defaultSpeed", put_multipart)
