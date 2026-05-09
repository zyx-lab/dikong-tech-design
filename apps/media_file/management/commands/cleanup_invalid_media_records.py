"""
清理本地数据库中 DJI 服务器不存在的无效媒体记录

使用方式:
    # 在本地 Django 服务器上运行
    python manage.py cleanup_invalid_media_records --dry-run

    # 指定参数
    python manage.py cleanup_invalid_media_records --workspace-id fc4c751a-42ad-11f1-93b9-6c92bf9e870c --tenant-code frontend_lab --dry-run

    # 实际删除
    python manage.py cleanup_invalid_media_records

参数:
    --dry-run      仅显示要删除的记录，不实际删除（默认）
    --no-dry-run   实际执行删除操作
    --workspace-id 指定 DJI workspace ID（可选，默认使用本地配置的 workspace）
    --tenant-code  指定租户编码（可选，默认使用 frontend_lab）
"""
import os
from dataclasses import dataclass

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.access.models import Tenant
from apps.dji_bff.gateway import DjiGateway
from apps.dji_bff.models import TenantMediaIndex
from apps.media_file.models import MediaFile


@dataclass
class MediaFileInfo:
    file_id: str
    file_name: str
    drone: str
    create_time: str


class Command(BaseCommand):
    help = "清理本地数据库中 DJI 服务器已删除的媒体记录"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="仅预览不实际删除")
        parser.add_argument("--workspace-id", type=str, help="指定 DJI workspace ID")
        parser.add_argument("--tenant-code", default="frontend_lab", help="租户编码，默认 frontend_lab")

    def _fetch_remote_media_files(self, gateway: DjiGateway) -> list[MediaFileInfo]:
        """从 DJI 服务器获取所有媒体文件列表"""
        self.stdout.write("正在从 DJI 服务器获取媒体文件列表...")
        raw_items = gateway.list_media_files()

        media_files = []
        for item in raw_items:
            media_files.append(MediaFileInfo(
                file_id=item.get("file_id", ""),
                file_name=item.get("file_name", ""),
                drone=item.get("drone", ""),
                create_time=item.get("create_time", ""),
            ))

        self.stdout.write(f"  DJI 服务器共有 {len(media_files)} 个媒体文件")
        return media_files

    def _get_local_media_indexes(self, tenant: Tenant) -> set[str]:
        """获取本地租户的所有 DJI file_id 集合"""
        indexes = TenantMediaIndex.objects.filter(
            tenant=tenant,
            sync_status="SYNCED",
        ).values_list("dji_file_id", flat=True)
        return set(indexes)

    def _get_invalid_media_files(
        self,
        tenant: Tenant,
        remote_file_ids: set[str],
    ) -> list[MediaFile]:
        """找出本地有但 DJI 服务器没有的媒体记录"""
        local_indexes = self._get_local_media_indexes(tenant)

        # 找出 DJI 不存在的 file_id
        invalid_file_ids = local_indexes - remote_file_ids

        if not invalid_file_ids:
            self.stdout.write(self.style.SUCCESS("  未发现无效媒体记录"))
            return []

        # 获取对应的 MediaFile 记录
        invalid_records = MediaFile.objects.filter(
            tenant=tenant,
            is_deleted=False,
            dji_index__dji_file_id__in=invalid_file_ids,
        ).select_related("dji_index")

        return list(invalid_records)

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        workspace_id = options.get("workspace_id")
        tenant_code = options["tenant_code"]

        if dry_run:
            self.stdout.write(self.style.WARNING("=== DRY RUN 模式 - 仅预览不删除 ===\n"))

        # 获取租户
        tenant = Tenant.objects.filter(code=tenant_code).first()
        if not tenant:
            raise CommandError(f"租户不存在: {tenant_code}")

        self.stdout.write(f"目标租户: {tenant.code} ({tenant.name})\n")

        # 初始化 DJI Gateway
        gateway = DjiGateway()
        if workspace_id:
            self.stdout.write(f"使用指定的 workspace: {workspace_id}")
        else:
            config = gateway.get_workspace_config()
            workspace_id = config.workspace_id
            self.stdout.write(f"使用配置的 workspace: {workspace_id}")

        # 获取远程媒体文件列表
        remote_files = self._fetch_remote_media_files(gateway)
        remote_file_ids = {f.file_id for f in remote_files}

        # 打印远程文件详情
        self.stdout.write("\n=== DJI 服务器媒体文件 ===")
        for i, f in enumerate(remote_files, 1):
            self.stdout.write(f"  {i}. {f.file_name} ({f.file_id})")

        # 获取无效记录
        self.stdout.write("\n正在检查本地无效记录...")
        invalid_records = self._get_invalid_media_files(tenant, remote_file_ids)

        if not invalid_records:
            self.stdout.write(self.style.SUCCESS("\n✅ 没有需要清理的无效媒体记录"))
            return

        self.stdout.write(f"\n=== 发现 {len(invalid_records)} 条无效媒体记录 ===\n")

        for i, record in enumerate(invalid_records, 1):
            dji_index = getattr(record, "dji_index", None)
            dji_file_id = dji_index.dji_file_id if dji_index else "N/A"
            self.stdout.write(f"  {i}. ID={record.id}, 文件名={record.file_name}, DJI_ID={dji_file_id}")

        if dry_run:
            self.stdout.write(f"\n⚠️  共 {len(invalid_records)} 条记录将被删除（dry-run 模式）")
            return

        # 执行软删除
        self.stdout.write(f"\n正在软删除 {len(invalid_records)} 条记录...")
        now = timezone.now()

        deleted_count = 0
        for record in invalid_records:
            record.is_deleted = True
            record.deleted_at = now
            try:
                record.save(update_fields=["is_deleted", "deleted_at", "updated_at"])
                deleted_count += 1
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  删除失败 ID={record.id}: {e}"))

        self.stdout.write(self.style.SUCCESS(f"\n✅ 成功删除 {deleted_count}/{len(invalid_records)} 条记录"))