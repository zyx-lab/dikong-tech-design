import uuid

from django.db import models


def route_kmz_upload_to(instance, filename):
    return f"routes/{instance.tenant_id or 'unknown'}/{uuid.uuid4().hex}.kmz"


class Route(models.Model):
    """航线台账（V1）。"""

    tenant = models.ForeignKey(
        "access.Tenant",
        on_delete=models.CASCADE,
        related_name="routes",
        verbose_name="租户",
    )
    dji_platform = models.ForeignKey(
        "dji_bff.DjiCloudPlatform",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="routes",
        verbose_name="DJI 平台",
    )
    name = models.CharField("航线名称", max_length=100)
    kmz_file = models.FileField("航线 KMZ 文件", upload_to=route_kmz_upload_to, blank=True, default="")
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        db_table = "routes"
        ordering = ["-id"]
        default_permissions = ()
        permissions = [
            ("view_route", "可查看航线"),
            ("manage_route", "可新增与编辑航线"),
        ]

    def __str__(self):
        return f"{self.id}-{self.name}"

    @property
    def dji_index(self):
        cached = getattr(self, "_legacy_dji_index_cache", None)
        if cached is not None:
            return cached

        prefetched = getattr(self, "_prefetched_objects_cache", {}).get("dji_indexes")
        if prefetched is not None:
            for route_index in prefetched:
                if route_index.dji_platform_id is None:
                    self._legacy_dji_index_cache = route_index
                    return route_index
            return None

        if self.pk is None:
            return None
        route_index = self.dji_indexes.filter(dji_platform__isnull=True).first()
        if route_index is not None:
            self._legacy_dji_index_cache = route_index
        return route_index

    @dji_index.setter
    def dji_index(self, value):
        if value is None or getattr(value, "dji_platform_id", None) is None:
            self._legacy_dji_index_cache = value
