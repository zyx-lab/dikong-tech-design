from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.access.models import DirectoryStatus, TimeStampedModel
from apps.iam_v2.models import FixedRole, V2AccountProfile


class PilotProfile(TimeStampedModel):
    account_profile = models.OneToOneField(V2AccountProfile, on_delete=models.CASCADE, related_name="pilot_profile")
    display_name = models.CharField(max_length=128)
    phone = models.CharField(max_length=32, blank=True, default="")
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


class PilotQualification(TimeStampedModel):
    pilot = models.ForeignKey(PilotProfile, on_delete=models.CASCADE, related_name="qualifications")
    qualification_type = models.CharField(max_length=128)
    certificate_no = models.CharField(max_length=128, blank=True, default="")
    issued_at = models.DateField(null=True, blank=True)
    expires_at = models.DateField(null=True, blank=True)
    status = models.PositiveSmallIntegerField(choices=DirectoryStatus.choices, default=DirectoryStatus.ACTIVE)
    remark = models.TextField(blank=True, default="")

    class Meta:
        db_table = "v2_pilot_qualifications"
        ordering = ["-expires_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["pilot", "qualification_type", "certificate_no"],
                name="uniq_v2_pilot_qualification",
            ),
        ]

    def is_effective(self, at=None) -> bool:
        today = (at or timezone.now()).date()
        if self.status != DirectoryStatus.ACTIVE:
            return False
        if self.issued_at and self.issued_at > today:
            return False
        if self.expires_at and self.expires_at < today:
            return False
        return True

    def __str__(self):
        return f"{self.pilot_id}:{self.qualification_type}:{self.certificate_no}"
