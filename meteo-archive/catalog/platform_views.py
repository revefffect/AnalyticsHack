import csv
from datetime import timedelta
from io import StringIO

import plotly.graph_objects as go
import pandas as pd
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import Q
from django.http import HttpResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST, require_safe
from plotly.offline import get_plotlyjs

from eda.core import METRICS

from .importing import ImportProblem, confirm_import, create_draft, load_tables
from .models import AlertEvent, Dataset, ImportDraft, SavedMapping, SavedReport, Station
from .platform_forms import MappingForm, OriginalFileForm, ReportForm
from .reporting import (COLUMNS, allowed_datasets, csv_safe, direction_distribution, export_result, history, metadata,
                        period_records, profile_summary, source_records, version_context)


@login_required
@permission_required("catalog.add_dataset", raise_exception=True)
@require_http_methods(["GET", "POST"])
def new_import(request, pk):
    station = get_object_or_404(Station, pk=pk)
    form = OriginalFileForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        uploaded = form.cleaned_data["data_file"]
        contents = uploaded.read()
        try:
            load_tables(uploaded.name, contents)
        except ImportProblem as error:
            form.add_error("data_file", str(error))
        else:
            draft = create_draft(request.user, station, uploaded.name, contents)
            return redirect("preview-import", key=draft.key)
    return render(request, "catalog/form.html", {"form": form, "title": "Загрузить наблюдения",
                  "action": "Открыть предпросмотр", "station": station})


@login_required
@permission_required("catalog.add_dataset", raise_exception=True)
@require_http_methods(["GET", "POST"])
def preview_import(request, key):
    draft = get_object_or_404(ImportDraft, key=key, researcher=request.user,
                              created_at__gte=timezone.now() - timedelta(hours=24))
    tables = load_tables(draft.filename, draft.contents)
    saved = SavedMapping.objects.filter(researcher=request.user).order_by("name")
    selected = saved.filter(name=request.GET.get("mapping", "")).first()
    form = MappingForm(tables, request.POST or None, initial_plan=selected.mapping if selected else None)
    if request.method == "POST" and form.is_valid():
        try:
            versions = confirm_import(draft, form.mapping_plan(), form.cleaned_data["preset"])
        except ImportProblem as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, f"Сохранено версий · {len(versions)}. Повтор того же импорта использует существующие версии.")
            return redirect("dataset-report", version=versions[0].version) if len(versions) == 1 else redirect("datasets")
    member = form["member"].value()
    sample = next((table for table in tables if table.name == member), tables[0])
    preview = sample.frame.head(7).fillna("").astype(str)
    kinds = []
    for column in preview:
        present = preview[column].str.strip().loc[lambda values: values.ne("")]
        if present.empty:
            kind = "пусто в выборке"
        elif pd.to_numeric(present.str.replace(",", ".", regex=False), errors="coerce").notna().all():
            kind = "число"
        elif present.str.contains(r"\d{4}[-/]\d{2}[-/]\d{2}").all() and pd.to_datetime(present, format="mixed", errors="coerce").notna().all():
            kind = "дата и время"
        else:
            kind = "текст"
        kinds.append((str(column), kind))
    return render(request, "catalog/import_preview.html", {
        "draft": draft, "form": form, "presets": saved, "sample": sample, "columns": list(preview.columns),
        "preview": preview.to_numpy().tolist(), "table_count": len(tables), "kinds": kinds,
    })


def dataset_catalog(request, business=False):
    query = request.GET.get("q", "").strip()[:100]
    kind = request.GET.get("kind", "")
    place = request.GET.get("place", "")
    accessible = allowed_datasets(request.user)
    listed = accessible.select_related("station__location", "uploaded_by").defer("contents", "normalization")
    if query:
        listed = listed.filter(Q(original_name__icontains=query) | Q(station__name__icontains=query)
                               | Q(station__location__name__icontains=query) | Q(uploaded_by__username__icontains=query))
    if kind in Dataset.Source.values:
        listed = listed.filter(source_kind=kind)
    if place.isdecimal():
        listed = listed.filter(station__location_id=place)
    from .models import Location

    return render(request, "catalog/datasets.html", {
        "datasets": listed[:150], "title": "Кабинет бизнеса" if business else "Каталог наборов",
        "query": query, "kind": kind, "place": place, "kinds": Dataset.Source.choices,
        "locations": Location.objects.filter(stations__datasets__in=accessible).distinct(),
        "saved": SavedReport.objects.filter(owner=request.user, dataset__in=accessible).select_related("dataset")[:20] if business else (),
        "sources": [
            ("Open-Meteo · исторические данные", "Реанализ · подключение в кабинете источников", reverse("external-source")),
            ("Росгидромет · ЕИП", "Архивы наблюдений · выбери набор на портале и добавь разрешённую выгрузку", "https://eip.meteo.ru/services"),
            ("ВНИИГМИ-МЦД", "Каталог массивов · наличие и формат зависят от выбранного архива", "https://meteo.ru/data"),
        ],
    })


@login_required
@permission_required("catalog.view_dataset", raise_exception=True)
@require_safe
def datasets(request):
    return dataset_catalog(request)


@login_required
@permission_required("catalog.view_dataset", raise_exception=True)
@require_safe
def business(request):
    return dataset_catalog(request, business=True)


def load_authorized(user, version):
    return get_object_or_404(allowed_datasets(user).select_related("station__location", "uploaded_by")
                             .defer("contents", "normalization"), version=version)


def _report_form(dataset, query):
    metric = "t_air" if "t_air" in dataset.metrics else dataset.metrics[0]
    first = dataset.first_observation
    last = min(dataset.last_observation, first + timedelta(days=365))
    initial = {"metric": metric, "start": first.isoformat(), "end": last.isoformat(), "step": "month", "template": "scientific"}
    return ReportForm(dataset, {**initial, **query}), initial


def report_figure(records, unit, directions=None):
    if directions:
        figure = go.Figure(go.Barpolar(theta=[row[0] for row in directions], r=[row[1] for row in directions],
                                       marker_color="#117666", hovertemplate="%{theta}° · %{r:.1f}%<extra></extra>"))
        figure.update_layout(polar={"angularaxis": {"direction": "clockwise", "rotation": 90}})
    else:
        figure = go.Figure(go.Scatter(x=[record["start"] for record in records], y=[record["mean"] for record in records],
                                      mode="lines+markers", connectgaps=False, line={"color": "#117666"}, marker={"size": 6},
                                      hovertemplate="%{x|%d.%m.%Y}<br>%{y:.2f} " + unit + "<extra></extra>"))
        figure.update_layout(yaxis_title=unit)
    figure.update_layout(template="simple_white", height=350, margin={"l": 56, "r": 21, "t": 20, "b": 48})
    return figure.to_html(full_html=False, include_plotlyjs=False, config={"displaylogo": False, "responsive": True})


def period_link(report_path, query, options, period):
    filters = query.copy()
    filters["metric"] = options["metric"]
    if options["step"] == "day":
        filters["step"] = "day"
        filters["start"], filters["end"] = options["start"], options["end"]
        filters["detail"] = period["start"]
        return report_path + "?" + filters.urlencode() + "#qc"
    filters.pop("detail", None)
    filters["start"] = max(period["start"], options["start"])
    filters["end"] = min(period["end"], options["end"])
    filters["step"] = "day"
    return report_path + "?" + filters.urlencode() + "#results"


def render_report(request, dataset, values, saved=None):
    form, initial = _report_form(dataset, values)
    query = QueryDict(mutable=True)
    query.update({name: values[name] for name in form.fields if name in values})
    context = {"dataset": dataset, "form": form, "saved_report": saved, "query": query.urlencode(),
               "owner": dataset.uploaded_by_id == request.user.pk or request.user.is_superuser}
    if not form.is_valid():
        return render(request, "catalog/report.html", context, status=400)
    options = form.options()
    metric, start, end = (options[key] for key in ("metric", "start", "end"))
    rows, daily, monthly, ten_day = version_context(dataset, metric, start, end)
    records = period_records(daily, monthly, ten_day, options["step"])
    info = metadata(dataset, options)
    profile = profile_summary(rows, daily, metric, dataset.profile or dataset.station.profile)
    info["source_timezone"] = dataset.source_meta.get("mapping", {}).get("time_zone") or "указан в записи или неизвестен"
    info["coverage"] = f'{profile["metric_days"]}/{profile["days"]}'
    info["flags"] = "; ".join(f"{name}={count}" for name, count in profile["flags"])

    action = request.GET.get("export")
    if action in ("csv", "json"):
        return export_result(dataset, options, records, action, info=info)
    if action == "source":
        buf = StringIO()
        selected = rows.loc[rows.day.eq(pd.Timestamp(options["detail"]))] if options["detail"] else rows
        fields = ["datetime", "original_datetime", "source_timezone", "value", "status", "qc_flag", "source", "csv_line"]
        writer = csv.writer(buf)
        writer.writerow(["dataset_version", "metric", "unit", *fields])
        for row in selected.reindex(columns=fields).itertuples(index=False, name=None):
            writer.writerow([info["dataset_version"], metric, info["unit"], *(csv_safe(value) if pd.notna(value) else "" for value in row)])
        response = HttpResponse("\ufeff" + buf.getvalue(), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="weather-source-{dataset.version}.csv"'
        response["Cache-Control"] = "private, no-store"
        return response

    events = AlertEvent.objects.filter(rule__recipient=request.user, rule__stream__station=dataset.station)
    events = events.filter(pk__in=saved.options.get("event_ids", [])) if saved else events.filter(observation__date__range=(start, end))
    historical = None
    if options["historical"] and metric != "wind_dir":
        _, reference, years, slope = history(dataset, metric, int(start[5:7]))
        period_mean = records[0]["mean"] if len(records) == 1 and options["step"] == "month" else None
        historical = {"reference": reference if years else None, "years": years, "slope": slope,
                      "first_year": dataset.first_observation.year, "last_year": dataset.last_observation.year,
                      "difference": period_mean - reference if period_mean is not None and years else None}
    keys = options["order"]
    report_path = reverse("dataset-report", args=(dataset.version,))
    display = [{"values": [record[key] for key in keys], "detail_url": period_link(report_path, query, options, record)}
               for record in records[:200]]
    chart_data = {"periods": records, "unit": info["unit"], "metric": metric, "step": options["step"],
                  "start": start, "end": end, "report": report_path, "query": query.urlencode(),
                  "directions": direction_distribution(rows) if metric == "wind_dir" else []}
    context.update({
        "info": info, "metrics_label": METRICS[metric][0], "options": options, "profile": profile,
        "headers": [options["labels"].get(key, COLUMNS[key]) for key in keys],
        "records": display, "record_count": len(records),
        "source_rows": source_records(rows, metric, options["detail"], flags_only=True), "events": events.order_by("-created")[:25],
        "historical": historical, "chart_data": chart_data,
        "dashboard": getattr(dataset, "dashboard", None),
    })
    final = render(request, "catalog/report.html", context)
    final["Cache-Control"] = "private, no-store"
    return final


@login_required
@require_safe
def report(request, version):
    dataset = load_authorized(request.user, version)
    return render_report(request, dataset, request.GET.dict())


@login_required
@require_POST
def save_report(request, version):
    dataset = load_authorized(request.user, version)
    form, _ = _report_form(dataset, request.POST.dict())
    if not form.is_valid():
        return render(request, "catalog/report.html", {"dataset": dataset, "form": form}, status=400)
    options = form.options()
    options["event_ids"] = list(AlertEvent.objects.filter(rule__recipient=request.user, rule__stream__station=dataset.station,
                                        observation__date__range=(options["start"], options["end"])).values_list("pk", flat=True))
    snapshot = SavedReport.objects.create(dataset=dataset, owner=request.user, options=options)
    return redirect("saved-report", key=snapshot.key)


@login_required
@require_safe
def saved_report(request, key):
    snapshot = get_object_or_404(SavedReport.objects.select_related("dataset"), key=key, owner=request.user)
    dataset = load_authorized(request.user, snapshot.dataset.version)
    return render_report(request, dataset, snapshot.options, saved=snapshot)


@login_required
@require_POST
def share(request, version):
    queryset = allowed_datasets(request.user)
    if not request.user.is_superuser:
        queryset = queryset.filter(uploaded_by=request.user)
    dataset = get_object_or_404(queryset.defer("contents", "normalization"), version=version)
    action = request.POST.get("action", "")
    if action in ("publish", "unpublish"):
        dataset.published = action == "publish"
        dataset.save(update_fields=("published",))
        messages.success(request, "Доступ через каталог обновлён.")
    elif action in ("grant", "revoke"):
        account = get_user_model().objects.filter(username=request.POST.get("username", ""), is_active=True).first()
        if account is None or not account.has_perm("catalog.view_dataset"):
            messages.error(request, "Укажи действующего пользователя с правом чтения.")
        else:
            (dataset.readers.add if action == "grant" else dataset.readers.remove)(account)
            messages.success(request, "Доступ к версии обновлён.")
    return redirect("dataset-report", version=version)


@require_safe
def plotly_js(request):
    response = HttpResponse(get_plotlyjs(), content_type="application/javascript")
    response["Cache-Control"] = "public, max-age=86400"
    return response
