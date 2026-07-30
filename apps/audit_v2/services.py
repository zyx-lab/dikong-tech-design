from apps.audit_v2.models import V2AuditLog
from apps.common.request import resolve_client_ip


def _request_id_for_audit(request) -> str:
    if request is None:
        return ""
    return str(getattr(request, "request_id", "") or "")[:64]


def log_v2_action(
    *,
    request,
    context,
    action: str,
    target_type: str,
    target_id=None,
    resource_owner_department=None,
    resource_type: str = "",
    resource_object_id=None,
    before_data=None,
    after_data=None,
):
    return V2AuditLog.objects.create(
        action=action,
        actor_user=context.user if context is not None else getattr(request, "user", None),
        actor_department=getattr(context, "department", None),
        resource_owner_department=resource_owner_department,
        resource_type=resource_type or "",
        resource_object_id=str(resource_object_id or ""),
        target_type=target_type,
        target_id=str(target_id or ""),
        before_data=before_data,
        after_data=after_data,
        ip=resolve_client_ip(request),
        request_id=_request_id_for_audit(request),
    )
