from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from catalog.importing import ImportProblem, STATION_NAMES, WHOLE_FILE, confirm_import, create_draft, guesses, load_tables
from catalog.models import Station


class Command(BaseCommand):
    help = "Import the organizer's ZIP through the same normalization used by the browser."

    def add_arguments(self, parser):
        parser.add_argument("archive", type=Path)
        parser.add_argument("--user", required=True)

    def handle(self, *args, **options):
        try:
            user = get_user_model().objects.get(username=options["user"])
            source = options["archive"]
            if not user.has_perm("catalog.add_dataset"):
                raise CommandError("Учётной записи требуется право добавлять наборы.")
            contents = source.read_bytes()
            tables = load_tables(source.name, contents)
            if not tables or not all(table.station_code for table in tables):
                raise CommandError("Выбери полный ZIP организаторов, включающий стандартные имена файлов.")
            _, candidates = guesses(tables)
            plan = {"member": WHOLE_FILE, "time_zone": "", "units": {},
                    "columns": {metric: names[0] for metric, names in candidates.items() if len(names) == 1},
                    "meta": {"provider": "Организаторы хакатона · архив урочища «Красное»",
                             "terms": "Условия определяет поставщик архива"}}
            del tables
            station = Station.objects.get(name=STATION_NAMES["kr1"], location__name='Урочище «Красное»')
            draft = create_draft(user, station, source.name, contents)
            for dataset in confirm_import(draft, plan):
                self.stdout.write(f"{dataset.station}: {dataset.row_count} · {dataset.version}")
            draft.delete()
        except (ImportProblem, OSError, get_user_model().DoesNotExist, Station.DoesNotExist) as error:
            raise CommandError(str(error)) from error
