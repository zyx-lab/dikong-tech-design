# Example: Mission Start Failure

User report:

```text
POST /api/v2/inspection/missions/8/start returns 502.
traceId: req-456
```

Expected agent response:

```text
Conclusion:
The 502 suggests the backend reached an external dependency or upstream DJI
operation that failed. The trace id is required to distinguish timeout,
authentication, and upstream business errors.

Evidence:
- The frontend guide says mission start starts the task, live stream, and DJI
  wayline flight task.
- The logging guide says external DJI calls are logged as external_call_* events.
- 502-level failures usually need app.log and error.log correlation.

Most likely causes:
1. DJI upstream is unreachable or timed out.
2. DJI upstream auth failed and retry did not recover.
3. The selected drone/dock/payload state does not allow mission start.
4. Required route, resource binding, or live capacity data is missing.

Next verification:
Search app.log and error.log for request_id=req-456. Look for
request_finished, request_exception, external_call_failed, and
external_call_retry events.

Frontend action:
Do not retry blindly. Show the backend msg and preserve the mission state. If
logs show upstream state failure, guide the operator to verify device online
state and resource binding.

Backend/API issue assessment:
Likely not a frontend request-shape issue if the same endpoint reached DJI
external calls. The backend or upstream layer must be inspected with logs.

Confidence:
medium
```

