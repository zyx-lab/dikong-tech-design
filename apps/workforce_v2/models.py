from django.core.exceptions import ValidationError
from django.db import models

from apps.access.models import DirectoryStatus, TimeStampedModel
from apps.iam_v2.models import FixedRole, V2AccountProfile


class PilotProfile(TimeStampedModel):
    account_profile = models.OneToOneField(V2AccountProfile, on_delete=models.CASCADE, related_name="pilot_profile")
    display_name = models.CharField(max_length=128)
    level = models.CharField(max_length=64, blank=True, default="")
    status = models.PositiveSmallIntegerField(choices=DirectoryStatus.choices, default=DirectoryStatus.ACTIVE)
    remark = models.TextField(blank=True, default="")

    class Meta:
        db_table = "v2_pilot_profiles"
        ordering = ["id"]

    def clean(self):
        role_codes = set(self.account_profile.role_assignments.values_list("role_code", flat=True))
        if FixedRole.PILOT not in role_codes:
            raise ValidationError({"account_profile": "飞手账号必须拥有 pilot 角色"})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def department(self):
        return self.account_profile.department

    def __str__(self):
        return f"{self.account_profile_id}:{self.display_name}"
