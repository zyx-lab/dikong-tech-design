---
name: debug-upstream
description: Use when debugging backend APIs that proxy upstream services, when the real endpoint must be discovered from live docs, OpenAPI, or schema, when a resource disappears from a list/capacity/index endpoint, or when logs show 404/502, timeout, auth, or state mismatches.
---

# Debug Upstream Interfaces

## Overview
This skill isolates failures between the local app, its gateway/client layer, and any upstream service. The point is to log into upstream first, read the live docs or schema, discover the real interface surface, and then prove which layer broke: local request shaping, upstream auth, upstream business logic, data/state, or transport.

## Use When
- the app proxies an upstream service and the target path is not obvious yet
- a resource disappears from a list, capacity, index, bound, or state endpoint
- logs mention `404`, `502`, timeouts, auth failures, or a state mismatch
- you need to discover the upstream interface from live docs, OpenAPI, schema, or observed traffic
- you need to compare Django's wrapped response with the direct upstream response

Do not use for frontend-only bugs or issues that do not involve an upstream service.

## Debug Flow
1. Start with the exact request, response status, tenant/user/context, and timestamp. Classify the failure before guessing the cause.
2. Log into upstream first. Prove auth works before testing any business API.
3. Open the live docs or schema. If the service exposes OpenAPI, treat it as the first source of truth for paths, payloads, and auth patterns.
4. Map the local code path that owns the request. Inspect routing, handler, serializer/validator, gateway/client, and background task or equivalent layers.
5. Discover the interface instead of assuming it. Search code, docs, schema, and request samples for the real path, payload, and auth flow. If undocumented, infer it from the proxy code and then confirm it directly against upstream.
6. Probe adjacent upstream endpoints, not just the one that failed. Use the docs or schema to find sibling resources, list endpoints, detail endpoints, and state endpoints until the missing state is obvious.
7. Separate the layers explicitly:
   - local request shaping or response translation
   - upstream auth or token expiry
   - upstream business response
   - data/state/scope/permission mismatch
   - transport or availability timeout
8. When something is missing from a list or capacity endpoint, confirm whether it is still present, enabled, assigned, and linked to the expected scope or parent record.
9. End with the failing layer, the evidence that proves it, and the smallest next check.

## Common Clues
- Human-readable upstream error messages usually point to upstream business state, not local transport.
- Timeouts or unreachable errors usually point to network or upstream availability.
- Missing rows from a list, capacity, or index endpoint are often filtered by scope, state, or permission.

## Output Standard
End with:
- the failing layer
- the evidence that proves it
- the smallest next check
