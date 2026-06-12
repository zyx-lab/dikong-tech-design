# Example: 403 Permission Failure

User report:

```text
POST /api/v2/inspection/missions/12/start returns 403.
traceId: req-123
```

Expected agent response:

```text
Conclusion:
The request is authenticated but the current account likely lacks the required
permission, data scope, or resource visibility for starting this mission.

Evidence:
- API v2 uses database-backed roles, permissions, menus, and data scope.
- Permissions are not embedded in the token; frontend should refresh
  me/context and menus/current after role/menu changes.
- A trace id is available, so logs can verify the exact backend decision.

Most likely causes:
1. The role does not include the mission start permission.
2. The mission is outside the user's department/data scope.
3. The frontend has stale context/menus after an admin changed permissions.
4. The mission id exists but is not visible to the current user.

Next verification:
Search app.log and error.log for request_id=req-123, then inspect status_code,
context.user_id, context.username, and response.body.

Frontend action:
Refresh GET /api/v2/iam/me/context and GET /api/v2/system/menus/current after
permission changes. Confirm the current account can see the mission before
calling start.

Backend/API issue assessment:
Possible only if logs show the user has the expected permission and scope but
the permission check still denies access.

Confidence:
medium
```

