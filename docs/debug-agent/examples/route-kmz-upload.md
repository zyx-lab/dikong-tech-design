# Example: Route KMZ Upload

User report:

```text
POST /api/v2/inspection/routes returns 400 when uploading a KMZ.
```

Expected agent response:

```text
Conclusion:
The most likely issue is request content type or file field shape. Route
creation with KMZ upload should be checked as multipart/form-data, not plain
JSON.

Evidence:
- The frontend guide describes route creation as uploading a KMZ route.
- Route update with KMZ replacement is explicitly described as
  multipart/form-data.
- The current OpenAPI schema and route serializer are the deciding sources.

Most likely causes:
1. The frontend sent JSON instead of FormData.
2. The file field name does not match the schema.
3. The frontend manually set Content-Type and broke the multipart boundary.
4. The KMZ file is missing, empty, invalid, or rejected by serializer validation.

Next verification:
Inspect POST /api/v2/inspection/routes in live schema, then compare requestBody
content type and required fields against the frontend FormData keys.

Frontend action:
Use FormData. Append scalar fields and the KMZ file using schema field names.
Let the browser set multipart Content-Type.

Backend/API issue assessment:
Only likely if the request matches live schema but the serializer still rejects
valid data.

Confidence:
medium
```

