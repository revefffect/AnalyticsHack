import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Location(models.Model):
    class Kind(models.TextChoices):
        CITY = "city", "Город"
        TERRITORY = "territory", "Территория"

    name = models.CharField("Название", max_length=140)
    region = models.CharField("Регион", max_length=100, blank=True)
    kind = models.CharField("Тип места", max_length=12, choices=Kind, default=Kind.CITY)

    class Meta:
        ordering = ("region", "name", "kind")
        constraints = [models.UniqueConstraint(fields=("name", "region", "kind"), name="unique_catalog_location")]
        verbose_name = "место"
        verbose_name_plural = "места"

    def __str__(self):
        return f"{self.name} · {self.region}" if self.region else self.name


class Station(models.Model):
    class Profile(models.IntegerChoices):
        TERMS = 72, "АМЕ-60 · 72 срока за сутки"
        HOURS = 18, "Сокол М1 · 18 разных часов за сутки"

    name = models.CharField("Название станции", max_length=140)
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="stations", verbose_name="Место")
    profile = models.PositiveSmallIntegerField("Профиль полноты", choices=Profile, default=Profile.HOURS)
    latitude = models.DecimalField("Широта", max_digits=9, decimal_places=6, null=True, blank=True,
                                   validators=[MinValueValidator(-90), MaxValueValidator(90)])
    longitude = models.DecimalField("Долгота", max_digits=9, decimal_places=6, null=True, blank=True,
                                    validators=[MinValueValidator(-180), MaxValueValidator(180)])

    class Meta:
        ordering = ("location__region", "location__name", "name")
        constraints = [
            models.UniqueConstraint(fields=("location", "name"), name="unique_station_at_location"),
            models.CheckConstraint(
                condition=(models.Q(latitude__isnull=True, longitude__isnull=True)
                           | models.Q(latitude__isnull=False, longitude__isnull=False,
                                      latitude__gte=-90, latitude__lte=90, longitude__gte=-180, longitude__lte=180)),
                name="station_complete_valid_coordinates",
                violation_error_message="Укажите широту и долготу вместе или оставьте оба поля пустыми.",
            ),
        ]
        verbose_name = "станция"
        verbose_name_plural = "станции"

    def __str__(self):
        return self.name


class Dataset(models.Model):
    class Source(models.TextChoices):
        OBSERVED = "observed", "Наблюдения станции"
        REPLAY = "replay", "Воспроизведение архива"
        REANALYSIS = "reanalysis", "Реанализ"
        FORECAST = "forecast", "Прогноз"

    station = models.ForeignKey(Station, on_delete=models.PROTECT, related_name="datasets", verbose_name="Станция")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name="Загрузил")
    uploaded_at = models.DateTimeField("Время загрузки", auto_now_add=True)
    original_name = models.CharField("Имя файла", max_length=255)
    sha256 = models.CharField(max_length=64, editable=False)
    contents = models.BinaryField(editable=False)
    row_count = models.PositiveIntegerField("Число наблюдений")
    first_observation = models.DateField("Первый день в файле")
    last_observation = models.DateField("Последний день в файле")
    metrics = models.JSONField("Показатели", default=list)
    version = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    mapping_hash = models.CharField(max_length=64, blank=True, default="", editable=False)
    normalization = models.BinaryField(null=True, editable=False)
    profile = models.PositiveSmallIntegerField(choices=Station.Profile, null=True, editable=False)
    source_kind = models.CharField(max_length=16, choices=Source, default=Source.OBSERVED)
    source_meta = models.JSONField(default=dict, blank=True)
    published = models.BooleanField(default=False)
    readers = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="shared_weather_versions", blank=True)

    class Meta:
        ordering = ("-uploaded_at", "-pk")
        constraints = [models.UniqueConstraint(fields=("station", "uploaded_by", "sha256", "mapping_hash"), name="unique_dataset_mapping_per_station")]
        verbose_name = "выгрузка"
        verbose_name_plural = "выгрузки"

    def __str__(self):
        return self.original_name


class ImportDraft(models.Model):
    key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    station = models.ForeignKey(Station, on_delete=models.CASCADE)
    researcher = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    filename = models.CharField(max_length=255)
    contents = models.BinaryField(editable=False)
    created_at = models.DateTimeField(auto_now_add=True)


class SavedMapping(models.Model):
    researcher = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=80)
    mapping = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("researcher", "name"), name="unique_researcher_mapping")]


class SavedReport(models.Model):
    key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.PROTECT)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    options = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class Stream(models.Model):
    station = models.ForeignKey(Station, on_delete=models.PROTECT)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    token_hash = models.CharField(max_length=64, editable=False)
    name = models.CharField(max_length=100)
    mode = models.CharField(max_length=16, choices=Dataset.Source, default=Dataset.Source.REPLAY)
    latest_observation = models.DateTimeField(null=True)
    latest_receipt = models.DateTimeField(null=True)


class StreamMessage(models.Model):
    stream = models.ForeignKey(Stream, on_delete=models.CASCADE, related_name="messages")
    message_id = models.CharField(max_length=100)
    observation = models.DateTimeField()
    received = models.DateTimeField(auto_now_add=True)
    source = models.JSONField(default=dict)
    values = models.JSONField(default=dict)
    rejected = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("stream", "message_id"), name="unique_station_message")]
        indexes = [models.Index(fields=("stream", "observation"), name="stream_time_idx")]


class AlertRule(models.Model):
    stream = models.ForeignKey(Stream, on_delete=models.CASCADE, related_name="rules")
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    name = models.CharField(max_length=100)
    metric = models.CharField(max_length=32)
    threshold = models.FloatField()
    duration_minutes = models.PositiveIntegerField(default=30)
    interval_minutes = models.PositiveIntegerField(default=15)
    minimum_coverage = models.FloatField(default=0.75)
    stale_minutes = models.PositiveIntegerField(default=60)
    version = models.PositiveIntegerField(default=1)
    webhook = models.BooleanField(default=False)
    state = models.JSONField(default=dict, blank=True)


class AlertEvent(models.Model):
    rule = models.ForeignKey(AlertRule, on_delete=models.PROTECT, related_name="events")
    event_key = models.CharField(max_length=64, unique=True)
    category = models.CharField(max_length=12)
    state = models.CharField(max_length=16)
    observation = models.DateTimeField()
    created = models.DateTimeField(auto_now_add=True)
    version = models.PositiveIntegerField()
    details = models.JSONField(default=dict)
    delivery = models.CharField(max_length=12, default="in_app")
    sent_at = models.DateTimeField(null=True)


class DashboardBinding(models.Model):
    dataset = models.OneToOneField(Dataset, on_delete=models.CASCADE, related_name="dashboard")
    uid = models.CharField(max_length=40, unique=True)
    org_id = models.PositiveIntegerField(null=True)
    bucket_id = models.CharField(max_length=40, blank=True)
    state = models.CharField(max_length=16, default="pending")
    error = models.CharField(max_length=200, blank=True)
    synced_at = models.DateTimeField(null=True)


class StreamDashboard(models.Model):
    stream = models.OneToOneField(Stream, on_delete=models.CASCADE, related_name="dashboard")
    uid = models.CharField(max_length=40, unique=True)
    org_id = models.PositiveIntegerField(null=True)
    bucket_id = models.CharField(max_length=40, blank=True)
    cursor = models.PositiveBigIntegerField(default=0)
    state = models.CharField(max_length=16, default="pending")
    error = models.CharField(max_length=200, blank=True)
    synced_at = models.DateTimeField(null=True)
