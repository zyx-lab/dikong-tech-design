---
name: regression-debug
description: Use when the user asks whether a behavior is covered by tests, whether a bug is a regression, which validation command to run, or whether docs/schema/code are still in sync.
---

# Regression Debug

Determine whether an observed behavior is covered by tests and which narrow
validation proves the conclusion.

## Trigger Examples

- "是不是回归"
- "有没有测试覆盖"
- "跑哪个测试"
- "schema 和 docs 对不上"
- "这个接口以前能用"
- "帮我验证一下"

## Workflow

1. Identify the endpoint, workflow, or module.
2. Search tests under relevant apps:
   - `apps/api_v2`
   - `apps/iam_v2`
   - `apps/resource_v2`
   - `apps/inspection_v2`
   - `apps/system_v2`
   - `apps/access`
3. Search related evidence under `evidence/*`.
4. Choose the smallest validation command:
   - a specific test module when possible
   - `python manage.py check` for Django config health
   - `python manage.py makemigrations --check --dry-run` for migration drift
   - `scripts/test_v2_regression.sh fast` for broader API v2 regression
5. Report whether existing tests prove the behavior.

## Answer Shape

```text
Conclusion:

Coverage found:

Validation command:

Expected result:

Gap:

Recommended next test:

Confidence:
```

## Rules

- Do not claim a test passes unless it was freshly run and exited 0.
- Prefer narrow tests over full regression.
- If there is no coverage, say so plainly.
- Do not treat experiments as mandatory daily-loop regression evidence.

