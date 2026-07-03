from django.conf import settings
from django.db import models


class V2AuditLog(models.Model):
    action = models.CharField(max_length=128)
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="v2_audit_logs",
    )
    actor_department = models.ForeignKey(
        "iam_v2.Department",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="actor_v2_audit_logs",
    )
    resource_owner_department = models.ForeignKey(
        "iam_v2.Department",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="resource_owner_v2_audit_logs",
    )
    resource_type = models.CharField(max_length=16, blank=True)
    resource_object_id = models.CharField(max_length=64, blank=True)
    target_type = models.CharField(max_length=128)
    target_id = models.CharField(max_length=64, blank=True)
    before_data = models.JSONField(null=True, blank=True)
    after_data = models.JSONField(null=True, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    request_id = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "v2_audit_logs"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["actor_department", "created_at"], name="idx_v2_audit_actor_dept"),
            models.Index(fields=["resource_type", "resource_object_id"], name="idx_v2_audit_resource"),
        ]
