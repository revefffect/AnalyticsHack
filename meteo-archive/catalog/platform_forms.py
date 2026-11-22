from pathlib import PurePath

from django import forms
from django.conf import settings

from eda.core import METRICS

from .importing import UNIT_OPTIONS, WHOLE_FILE, guesses
from .reporting import COLUMNS


class OriginalFileForm(forms.Form):
    data_file = forms.FileField(label="Файл наблюдений", widget=forms.ClearableFileInput(attrs={"accept": ".csv,.xlsx,.json,.zip"}),
                                help_text="CSV, XLSX, JSON или ZIP из задания. До 10 МиБ.")

    def clean_data_file(self):
        upload = self.cleaned_data["data_file"]
        if PurePath(upload.name).suffix.lower() not in (".csv", ".xlsx", ".json", ".zip"):
            raise forms.ValidationError("Выбери CSV, XLSX, JSON или ZIP.")
        if upload.size > settings.CATALOG_CSV_MAX_BYTES:
            raise forms.ValidationError("Максимальный размер файла 10 МиБ.")
        return upload


class MappingForm(forms.Form):
    time_zone = forms.CharField(label="Часовой пояс IANA", max_length=80, required=False, help_text="Оставь пустым, если неизвестен.")
    preset = forms.CharField(label="Название шаблона", max_length=80, required=False, help_text="Заполни, чтобы сохранить сопоставление.")
    terms = forms.CharField(label="Условия использования", max_length=240, required=False)

    def __init__(self, tables, *args, initial_plan=None, **kwargs):
        super().__init__(*args, **kwargs)
        columns, candidates = guesses(tables)
        initial_plan = initial_plan or {}
        member_choices = [(table.name, table.name) for table in tables]
        same_archive = all(table.station_code for table in tables)
        if len(tables) == 1 or same_archive:
            member_choices.insert(0, (WHOLE_FILE, "Все таблицы файла"))
        self.fields["member"] = forms.ChoiceField(label="Лист или файл", choices=member_choices,
                                                   initial=initial_plan.get("member", member_choices[0][0]))
        self.fields["time_zone"].initial = initial_plan.get("time_zone", "")
        labels = {"datetime": "Дата и время", "qc_flag": "Исходный флаг качества", **{k: v[0] for k, v in METRICS.items()}}
        for field, label in labels.items():
            proposed = candidates.get(field, [])
            saved = initial_plan.get("columns", {}).get(field)
            picked = saved if saved in columns else proposed[0] if len(proposed) == 1 else ""
            self.fields["map_" + field] = forms.ChoiceField(
                label=label, choices=[("", "Колонка не выбрана"), *((name, name) for name in columns)],
                required=field == "datetime", initial=picked,
                help_text="Уточни сопоставление: подходит несколько колонок." if len(proposed) > 1 else "",
            )
            if field in UNIT_OPTIONS and len(UNIT_OPTIONS[field]) > 1:
                unit = initial_plan.get("units", {}).get(field, UNIT_OPTIONS[field][0])
                self.fields["unit_" + field] = forms.ChoiceField(
                    label=f"Единица · {label}", choices=[(value, value) for value in UNIT_OPTIONS[field]], initial=unit, required=False,
                )
        self.order_fields(["member", "time_zone", "terms"])

    def mapping_plan(self):
        return {"member": self.cleaned_data["member"], "time_zone": self.cleaned_data["time_zone"],
                "columns": {k[4:]: v for k, v in self.cleaned_data.items() if k.startswith("map_") and v},
                "units": {k[5:]: v for k, v in self.cleaned_data.items() if k.startswith("unit_") and v and self.cleaned_data.get("map_" + k[5:])},
                "meta": {"terms": self.cleaned_data["terms"]}}


class ReportForm(forms.Form):
    metric = forms.ChoiceField(label="Показатель")
    start = forms.DateField(label="С", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    end = forms.DateField(label="По", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    step = forms.ChoiceField(label="Шаг", choices=(("day", "Сутки"), ("decade", "Декада"), ("month", "Месяц")))
    template = forms.ChoiceField(label="Шаблон", choices=(("scientific", "Научная сводка"), ("operations", "Оперативный отчёт")))
    detail = forms.DateField(label="День для разбора", required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    historical = forms.BooleanField(label="Историческое сравнение", required=False)
    column_order = forms.CharField(label="Порядок колонок", required=False, max_length=160,
                                    help_text="Коды колонок через запятую. Пустое поле показывает все.")
    column_labels = forms.CharField(label="Подписи колонок", required=False, max_length=320,
                                     help_text="Например, mean=Температура; metric_days=Дней. Подписи сохраняются в отчёте.")

    def __init__(self, dataset, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dataset = dataset
        self.fields["metric"].choices = [(metric, METRICS[metric][0]) for metric in dataset.metrics if metric in METRICS]

    def clean(self):
        data = super().clean()
        start, end = data.get("start"), data.get("end")
        if start and end and (end < start or (end - start).days > 36600 or start.year < 1900 or end.year > 2200):
            raise forms.ValidationError("Укажи корректный диапазон между 1900 и 2200 годами длиной до 100 лет.")
        order = [key.strip() for key in data.get("column_order", "").split(",") if key.strip()]
        if len(order) != len(set(order)) or any(key not in COLUMNS for key in order):
            raise forms.ValidationError("Проверь порядок. Доступны " + ", ".join(COLUMNS) + ".")
        labels = {}
        for item in filter(None, data.get("column_labels", "").split(";")):
            key, separator, label = item.strip().partition("=")
            if not separator or key.strip() not in COLUMNS or not label.strip():
                raise forms.ValidationError("Подписи указываются в формате код=Подпись.")
            labels[key.strip()] = label.strip()[:70]
        data["order"] = order or list(COLUMNS)
        data["labels"] = labels
        return data

    def options(self):
        data = self.cleaned_data.copy()
        for field in ("start", "end", "detail"):
            data[field] = data[field].isoformat() if data.get(field) else ""
        return data
