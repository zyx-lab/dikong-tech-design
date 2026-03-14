from django.core.exceptions import PermissionDenied
from django.db.models.signals import m2m_changed
from django.dispatch import receiver

from apps.access.models import User


@receiver(m2m_changed, sender=User.user_permissions.through)
def disable_direct_user_permissions(sender, instance, action, **kwargs):
    """禁止用户直配权限，统一走 tenant_member -> system_role -> group -> permission(scope)。"""
    if action in {"pre_add", "pre_remove", "pre_clear"}:
        raise PermissionDenied("direct user_permissions are disabled; use tenant_member + system_role + group + scope")


@receiver(m2m_changed, sender=User.groups.through)
def disable_direct_user_groups(sender, instance, action, **kwargs):
    """禁止用户直绑 Group，避免出现第二条授权路径。"""
    if action in {"pre_add", "pre_remove", "pre_clear"}:
        raise PermissionDenied("direct user-group bindings are disabled; configure system_role groups instead")
