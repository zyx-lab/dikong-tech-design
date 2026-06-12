# Example: 400 Request Schema Failure

User report:

```text
PUT /api/v2/iam/accounts/5 returns 400.
The response says a field is invalid.
```

Expected agent response:

```text
Conclusion:
This is most likely a request body validation error. The exact fix depends on
the field named in the response body.

Evidence:
- API v2 standard error responses put the actionable message in msg.
- Account update contracts should be checked against the current OpenAPI schema
  and serializer implementation.

Most likely causes:
1. Required field omitted.
2. Field type does not match schema.
3. Enum or profile type value is invalid.
4. The frontend sent extra shape from an old v1 contract.

Next verification:
Provide method, URL, full response body, and request body with secrets removed.
Then inspect the operation in /api/v2/docs/schema/ and the account serializer.

Frontend action:
Stop using any v1 account payload shape. Build the request from current schema
and preserve server-returned ids instead of guessing ids.

Backend/API issue assessment:
Unknown until the concrete invalid field and schema are checked.

Confidence:
low
```

