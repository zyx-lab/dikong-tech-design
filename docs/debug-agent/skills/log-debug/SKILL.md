---
name: log-debug
description: Use when a request id, trace id, exception, stack trace, 5xx response, DJI upstream error, object storage error, or log investigation is involved.
---

# Log Debug

Trace a backend request through Django JSON logs.

## Trigger Examples

- "traceId 是 xxx"
- "X-Request-ID 是 xxx"
- "后端 500"
- "DJI 上游报错"
- "MinIO 报错"
- "帮我看日志"
- "request_exception"
- "external_call_failed"

## Workflow

1. Get `request_id` or `traceId`.
2. Identify log directory:
   - `$DJANGO_LOG_DIR` when set
   - otherwise project `logs/`
3. Search:
   - `app.log`
   - `error.log`
4. Prefer JSON-line filtering:

   ```bash
   jq -c 'select(.request_id=="REQ")' logs/app.log
   jq -c 'select(.request_id=="REQ")' logs/error.log
   ```

5. Inspect event types:
   - `request_finished`
   - `request_exception`
   - `external_call_started`
   - `external_call_finished`
   - `external_call_retry`
   - `external_call_failed`
6. Classify the issue:
   - local validation/permission/business error
   - Django exception
   - DJI upstream timeout/auth/business error
   - object storage failure
   - missing logs or wrong environment

## Answer Shape

```text
Conclusion:

Log evidence:

Failure layer:

Next verification:

Frontend action:

Backend/API issue assessment:

Confidence:
```

## Rules

- Redact secrets from headers and bodies.
- If `DJANGO_LOG_REDACT_PAYLOADS=false`, assume log payloads may contain secrets.
- Do not store raw log lines in long-term memory.
- Store only confirmed cause/fix summaries when useful.

