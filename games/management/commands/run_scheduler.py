"""Run the round sweep: close due rounds, send the day-before reminder, record a heartbeat.

    python manage.py run_scheduler            # loop forever, every 5 minutes
    python manage.py run_scheduler --once     # a single pass (use from cron / a systemd timer)
    python manage.py run_scheduler --interval 60

Run exactly one of these per deployment. It is safe alongside the cron endpoint and the lazy
auto-advance on page loads, because every close takes the same atomic claim.
"""
import time

from django.core.management.base import BaseCommand

from games.scheduler import run_sweep


class Command(BaseCommand):
    help = "Close due rounds, send reminders, and record the scheduler heartbeat."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run one pass and exit.")
        parser.add_argument("--interval", type=int, default=300, help="Seconds between passes.")

    def handle(self, *args, **opts):
        while True:
            result = run_sweep()
            self.stdout.write(
                f"{result['ran_at']}  closed={len(result['closed'])}  "
                f"reminders={result['reminders_sent']}")
            if opts["once"]:
                return
            time.sleep(max(opts["interval"], 5))
