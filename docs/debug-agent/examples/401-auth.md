# Example: 401 Authentication Failure

User report:

```text
GET /api/v2/iam/me/context returns 401.
Response body says token invalid.
```

Expected agent response:

```text
Conclusion:
The request is unauthenticated or using an expired/malformed access token.

Evidence:
- API v2 frontend guide says business requests after login must include
  Authorization: Bearer <accessToken>.
- The current user context endpoint is part of the post-login initialization
  flow.

Most likely causes:
1. Authorization header is missing.
2. Header does not use the exact Bearer format.
3. The frontend sent refreshToken instead of accessToken.
4. The access token expired and refresh flow did not replace it.

Next verification:
Send the request again and capture method, URL, response body, and whether the
Authorization header is present. Do not paste the token value.

Frontend action:
Use Authorization: Bearer <accessToken>. On expiration, call
POST /api/v2/iam/session/refresh, replace the stored access token, and retry the
original request once.

Backend/API issue assessment:
Not enough evidence for a backend issue yet. 401 is expected when the token is
missing, expired, or invalid.

Confidence:
medium
```

