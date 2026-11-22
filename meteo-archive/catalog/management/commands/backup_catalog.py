import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import NamedTemporaryFile

from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = "Create a consistent copy of the SQLite catalog, including uploaded files."

    def add_arguments(self, parser):
        parser.add_argument("destination", type=Path)

    def handle(self, *args, destination, **options):
        source = Path(connection.settings_dict["NAME"]).resolve()
        target = destination.resolve()
        if target == source or target.exists():
            raise CommandError("Укажи новый путь для резервной копии.")
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with NamedTemporaryFile(dir=target.parent, prefix=".catalog-backup-") as temporary:
            try:
                with closing(sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)) as current:
                    with closing(sqlite3.connect(temporary.name)) as backup:
                        current.backup(backup)
                target.hardlink_to(temporary.name)
            except (OSError, sqlite3.Error) as error:
                raise CommandError(f"Не удалось создать копию: {error}") from error
        self.stdout.write(str(target))
