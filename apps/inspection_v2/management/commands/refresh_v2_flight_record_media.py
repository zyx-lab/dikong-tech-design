from django.core.management.base import BaseCommand, CommandError

from apps.inspection_v2.models import FlightRecordStatus, InspectionFlightRecord
from apps.inspection_v2.services import sync_media_for_record


class Command(BaseCommand):
    help = "Refresh media associations for completed v2 flight records."

    def add_arguments(self, parser):
        parser.add_argument("--all-completed", action="store_true", help="Refresh all completed records with cloud executions.")
        parser.add_argument("--mission-id", type=int, help="Refresh the flight record for one mission.")
        parser.add_argument("--flight-record-id", type=int, help="Refresh one flight record.")
        parser.add_argument("--dry-run", action="store_true", help="Preview records without syncing media.")

    def handle(self, *args, **options):
        selectors = [
            bool(options["all_completed"]),
            options.get("mission_id") is not None,
            options.get("flight_record_id") is not None,
        ]
        if sum(selectors) != 1:
            raise CommandError("Specify exactly one of --all-completed, --mission-id, or --flight-record-id.")

        dry_run = bool(options["dry_run"])
        processed = 0
        planned = 0
        synced = 0
        photos = 0
        videos = 0
        skipped = 0
        failures = 0

        for record in self._records(options):
            processed += 1
            if dry_run:
                planned += 1
                self.stdout.write(f"would refresh flight_record_id={record.id} mission_id={record.mission_id}")
                continue

            try:
                result = sync_media_for_record(record=record)
            except Exception as exc:  # noqa: BLE001 - batch command should report every failed record.
                failures += 1
                self.stdout.write(self.style.ERROR(f"failed flight_record_id={record.id} mission_id={record.mission_id}: {exc}"))
                continue

            synced += int(result.get("synced") or 0)
            photos += int(result.get("photoCount") or 0)
            videos += int(result.get("videoCount") or 0)
            self.stdout.write(
                f"refreshed flight_record_id={record.id} mission_id={record.mission_id} "
                f"synced={result.get('synced') or 0} photos={result.get('photoCount') or 0} videos={result.get('videoCount') or 0}"
            )

        summary = (
            "v2 flight record media refresh complete: "
            f"processed={processed}, planned={planned}, synced={synced}, photos={photos}, "
            f"videos={videos}, skipped={skipped}, failures={failures}"
        )
        self.stdout.write(self.style.SUCCESS(summary) if failures == 0 else self.style.ERROR(summary))
        if failures:
            raise CommandError(summary)

    def _records(self, options):
        queryset = InspectionFlightRecord.objects.select_related("mission").order_by("id")
        if options["all_completed"]:
            return queryset.filter(
                status=FlightRecordStatus.COMPLETED,
                mission__cloud_execution__isnull=False,
            ).distinct()
        if options.get("mission_id") is not None:
            return queryset.filter(mission_id=options["mission_id"])
        return queryset.filter(id=options["flight_record_id"])
