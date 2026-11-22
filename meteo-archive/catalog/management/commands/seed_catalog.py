from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db import transaction

from catalog.models import Location, Station


class Command(BaseCommand):
    help = "Create the reserve's station catalog and employee groups."

    @transaction.atomic
    def handle(self, *args, **options):
        location, _ = Location.objects.get_or_create(
            name="Урочище «Красное»", region="Тверская область", kind=Location.Kind.TERRITORY,
        )
        for name, profile in (
            ("АМЕ-60", Station.Profile.TERMS),
            ("Сокол М1 Кр1 · Поляна", Station.Profile.HOURS),
            ("Сокол М1 Кр2 · Поле", Station.Profile.HOURS),
        ):
            Station.objects.get_or_create(location=location, name=name, defaults={"profile": profile})

        view = ("view_location", "view_station", "view_dataset")
        manage = ("add_location", "add_station", "add_dataset")
        for name, codenames in (("Читатели", view), ("Исследователи", view + manage)):
            group, _ = Group.objects.get_or_create(name=name)
            group.permissions.add(*Permission.objects.filter(content_type__app_label="catalog", codename__in=codenames))
        self.stdout.write(self.style.SUCCESS("Каталог станций и группы сотрудников готовы."))
