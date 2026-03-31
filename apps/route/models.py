from django.db import models


class Route(models.Model):
    """航线台账（V1）。"""

    tenant = models.ForeignKey(
        "access.Tenant",
        on_delete=models.CASCADE,
        related_name="routes",
        verbose_name="租户",
    )
    name = models.CharField("航线名称", max_length=100)
    xml_file = models.FileField("航线 XML 文件", upload_to="routes/xml", blank=True, default="")
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
