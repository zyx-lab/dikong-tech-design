from django.test import SimpleTestCase


class ApiV2DocsBearerAuthTests(SimpleTestCase):
    def test_docs_page_should_include_login_form_for_bearer_token(self):
        response = self.client.get("/api/v2/docs/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="api-v2-docs-login"')
        self.assertContains(response, 'name="username"')
        self.assertContains(response, 'name="password"')
        self.assertContains(response, "ui.authActions.authorize")
        self.assertContains(response, "/api/v2/iam/session/login")

    def test_schema_should_expose_bearer_authorize_input(self):
        response = self.client.get("/api/v2/docs/schema/")

        self.assertEqual(response.status_code, 200)
        schema = response.json()
        security_schemes = schema["components"]["securitySchemes"]
        self.assertEqual(
            security_schemes["BearerAuth"],
            {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "Opaque access token",
                "description": "Paste accessToken returned by POST /api/v2/iam/session/login.",
            },
        )

    def test_authenticated_operations_should_reference_bearer_auth(self):
        response = self.client.get("/api/v2/docs/schema/")

        self.assertEqual(response.status_code, 200)
        schema = response.json()
        operation = schema["paths"]["/api/v2/inspection/routes"]["get"]
        self.assertIn({"BearerAuth": []}, operation["security"])
