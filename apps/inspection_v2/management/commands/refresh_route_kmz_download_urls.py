from django.core.management.base import BaseCommand, CommandError

from apps.dji_cloud.gateway import DjiGatewayError
from apps.inspection_v2.models import WaypointRouteCloudFile
from apps.inspection_v2.views import (
    _absolute_dji_url,
    _download_url_needs_refresh,
    _is_absolute_http_url,
    _parse_download_url_expires_at,
)
from apps.resource_v2.gateway import DjiConnectionGateway


class Command(BaseCommand):
    help = "Refresh cached DJI absolute download URLs for v2 route KMZ files."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Preview records without updating them.")
        parser.add_argument("--force", action="store_true", help="Refresh every cloud file, including currently valid URLs.")

    def handle(self, *args, **options):
        dry_run = bool(options["dry_run"])
        force = bool(options["force"])
        refreshed = 0
        planned = 0
        skipped_current = 0
        failures = 0

        queryset = WaypointRouteCloudFile.objects.select_related("dji_connection", "route").order_by("id")
        for cloud_file in queryset.iterator():
            if not cloud_file.dji_file_id:
                skipped_current += 1
                continue
            if not force and not _download_url_needs_refresh(cloud_file):
                skipped_current += 1
                continue

            if dry_run:
                planned += 1
                self.stdout.write(f"would refresh route_id={cloud_file.route_id} dji_file_id={cloud_file.dji_file_id}")
                continue

            try:
                gateway = DjiConnectionGateway(cloud_file.dji_connection)
                download_url = _absolute_dji_url(
                    cloud_file.dji_connection,
                    str(gateway.get_route_download_url(cloud_file.dji_file_id) or ""),
                )
                if not download_url or not _is_absolute_http_url(download_url):
                    raise DjiGatewayError(
                        "未获取到可直接访问的航线下载地址",
                        status_code=502,
                        data={"dji_file_id": cloud_file.dji_file_id, "download_url": download_url},
                    )
                cloud_file.download_url = download_url
                cloud_file.download_url_expires_at = _parse_download_url_expires_at(download_url)
                cloud_file.save(update_fields=["download_url", "download_url_expires_at", "updated_at"])
            except Exception as exc:  # noqa: BLE001 - batch command should report all failed records.
                failures += 1
                self.stdout.write(
                    self.style.ERROR(
                        f"failed route_id={cloud_file.route_id} dji_file_id={cloud_file.dji_file_id}: {exc}"
                    )
                )
                continue

            refreshed += 1
            self.stdout.write(f"refreshed route_id={cloud_file.route_id} dji_file_id={cloud_file.dji_file_id}")

        summary = (
            "route KMZ download URL refresh complete: "
            f"refreshed={refreshed}, planned={planned}, skipped_current={skipped_current}, failures={failures}"
        )
        if failures:
            raise CommandError(summary)
        self.stdout.write(self.style.SUCCESS(summary))
