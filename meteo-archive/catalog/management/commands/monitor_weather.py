from time import sleep

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from catalog.grafana import sync_pending
from catalog.streaming import deliver_webhooks, monitor_stale


class Command(BaseCommand):
    help = "Check stream freshness and deliver queued webhooks or provision Grafana dashboards."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--grafana", action="store_true")

    def handle(self, *args, **options):
        while True:
            close_old_connections()
            if options["grafana"]:
                sync_pending()
            else:
                monitor_stale()
                deliver_webhooks()
            if options["once"]:
                break
            sleep(5 if options["grafana"] else 1)
