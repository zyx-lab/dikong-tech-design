import time

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.dji_bff.tasks import sync_device_indexes, sync_media_indexes


class Command(BaseCommand):
    help = "执行 DJI 资源同步调度。默认循环执行；可用 --once 只跑一轮。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="只执行一轮设备/媒体同步，然后退出。",
        )
        parser.add_argument(
            "--interval-seconds",
            type=int,
            default=60,
            help="循环模式下每轮间隔秒数，默认 60。",
        )
        parser.add_argument(
            "--max-cycles",
            type=int,
            default=0,
            help="仅用于测试或受控运行。大于 0 时在指定轮次后退出。",
        )

    def handle(self, *args, **options):
        once = options["once"]
        interval_seconds = options["interval_seconds"]
        max_cycles = options["max_cycles"]

        if interval_seconds < 0:
            raise CommandError("--interval-seconds 不能小于 0")
        if max_cycles < 0:
            raise CommandError("--max-cycles 不能小于 0")

        cycle = 0
        while True:
            cycle += 1
            started_at = timezone.now()
            self.stdout.write(self.style.NOTICE(f"[cycle {cycle}] start at {started_at.isoformat()}"))
            try:
                summary = self.run_sync_cycle()
            except Exception as exc:
                if once:
                    raise CommandError(f"DJI 同步失败: {exc}") from exc
                self.stderr.write(self.style.ERROR(f"[cycle {cycle}] failed: {exc}"))
                if max_cycles > 0 and cycle >= max_cycles:
                    return
                time.sleep(interval_seconds)
                continue

            finished_at = timezone.now()
            self.stdout.write(
                self.style.SUCCESS(
                    f"[cycle {cycle}] done at {finished_at.isoformat()} "
                    f"devices={summary['devices']} media={summary['media']}"
                )
            )

            if once or (max_cycles > 0 and cycle >= max_cycles):
                return

            time.sleep(interval_seconds)

    @staticmethod
    def run_sync_cycle():
        return {
            "devices": sync_device_indexes(),
            "media": sync_media_indexes(),
        }
