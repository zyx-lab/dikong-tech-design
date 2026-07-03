from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand

from apps.inspection_v2.models import WaypointRoute


class Command(BaseCommand):
    help = "Upload existing local v2 route cover files to the configured default storage."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Preview files without uploading.")
        parser.add_argument("--noinput", action="store_true", help="Accepted for non-interactive deploy scripts.")

    def handle(self, *args, **options):
        dry_run = bool(options["dry_run"])
        media_root = Path(settings.MEDIA_ROOT)
        uploaded = 0
        skipped_existing = 0
        missing = 0
        planned = 0

        queryset = WaypointRoute.objects.exclude(cover_image="").order_by("id")
        for route in queryset.iterator():
            cover_name = str(route.cover_image_name or "").strip()
            if not cover_name:
                continue

            if default_storage.exists(cover_name):
                skipped_existing += 1
                continue

            local_path = media_root / cover_name
            if not local_path.is_file():
                missing += 1
                self.stdout.write(f"missing local cover route_id={route.id} path={local_path}")
                continue

            if dry_run:
                planned += 1
                self.stdout.write(f"would upload route_id={route.id} key={cover_name}")
                continue

            with local_path.open("rb") as source:
                default_storage.save(cover_name, File(source))
            uploaded += 1
            self.stdout.write(f"uploaded route_id={route.id} key={cover_name}")

        self.stdout.write(
            self.style.SUCCESS(
                "route cover migration complete: "
                f"uploaded={uploaded}, planned={planned}, skipped_existing={skipped_existing}, missing={missing}"
            )
        )
