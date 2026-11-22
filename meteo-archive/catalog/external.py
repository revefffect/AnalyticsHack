import json
from datetime import date, timedelta
from os import environ
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from django import forms
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .importing import ImportProblem, WHOLE_FILE, confirm_import, create_draft
from .models import Dataset, Station


FIELDS = {"temperature_2m": "t_air", "relative_humidity_2m": "rh", "surface_pressure": "pressure_hpa",
          "wind_speed_10m": "wind_speed", "wind_direction_10m": "wind_dir"}
ATTRIBUTION = "Open-Meteo / Copernicus Climate Change Service (ERA5) · CC BY 4.0"


class OpenMeteoForm(forms.Form):
    station = forms.ModelChoiceField(Station.objects.all(), label="Привязать к объекту каталога")
    latitude = forms.DecimalField(min_value=-90, max_value=90, decimal_places=5, label="Широта")
    longitude = forms.DecimalField(min_value=-180, max_value=180, decimal_places=5, label="Долгота")
    start = forms.DateField(label="С", widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(label="По", widget=forms.DateInput(attrs={"type": "date"}))
    research = forms.BooleanField(label="Подтверждаю, что использую бесплатный API для некоммерческого исследования")

    def clean(self):
        values = super().clean()
        start, end = values.get("start"), values.get("end")
        if start and end and (start > end or end - start > timedelta(days=365) or start < date(1940, 1, 1)):
            raise forms.ValidationError("Укажи период от 1940 года длиной до 366 дней.")
        return values


def fetch_archive(values, key=""):
    host = "customer-archive-api.open-meteo.com" if key else "archive-api.open-meteo.com"
    params = {"latitude": values["latitude"], "longitude": values["longitude"], "start_date": values["start"],
              "end_date": values["end"], "hourly": ",".join(FIELDS), "models": "era5", "timezone": "GMT",
              "wind_speed_unit": "ms"}
    if key:
        params["apikey"] = key
    try:
        with urlopen(f"https://{host}/v1/archive?{urlencode(params)}", timeout=8) as response:
            data = json.loads(response.read(3_000_000))
        hourly = data["hourly"]
        records = [{"datetime": instant, **{column: hourly[column][index] for column in FIELDS}}
                   for index, instant in enumerate(hourly["time"])]
        if not records or not any(row["temperature_2m"] is not None for row in records):
            raise ValueError("empty archive")
    except (URLError, TimeoutError, KeyError, IndexError, ValueError, TypeError) as error:
        raise ImportProblem("Open-Meteo не вернул доступные данные за этот период. Проверь даты и доступ к API.") from error
    metadata = {"provider": "Open-Meteo", "attribution": ATTRIBUTION, "model": "ERA5", "license": "CC BY 4.0",
                "latitude": str(values["latitude"]), "longitude": str(values["longitude"]),
                "grid_latitude": data.get("latitude"), "grid_longitude": data.get("longitude"),
                "start": str(values["start"]), "end": str(values["end"]), "cadence": "1 час"}
    return json.dumps(records, ensure_ascii=False).encode(), metadata


@login_required
@permission_required("catalog.add_dataset", raise_exception=True)
@require_http_methods(["GET", "POST"])
def connect(request):
    key = environ.get("METEO_OPEN_METEO_KEY", "")
    form = OpenMeteoForm(request.POST or None)
    if key:
        del form.fields["research"]
    if request.method == "POST" and form.is_valid():
        try:
            payload, metadata = fetch_archive(form.cleaned_data, key)
            values = form.cleaned_data
            draft = create_draft(request.user, values["station"], f"open-meteo-era5-{values['start']}-{values['end']}.json", payload)
            versions = confirm_import(draft, {"member": WHOLE_FILE, "time_zone": "UTC", "units": {},
                                      "columns": {"datetime": "datetime", **{value: field for field, value in FIELDS.items()}},
                                      "source_kind": Dataset.Source.REANALYSIS, "meta": metadata})
        except ImportProblem as error:
            form.add_error(None, str(error))
        else:
            return redirect("dataset-report", version=versions[0].version)
    return render(request, "catalog/form.html", {"form": form, "title": "Подключить реанализ Open-Meteo",
                  "action": "Загрузить ERA5", "description": "Координаты и даты будут отправлены в Open-Meteo. "
                  "Коммерческий доступ требует настроенного ключа. Сохранённая версия будет приватной."})
