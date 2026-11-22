from django.contrib.auth.decorators import login_required, permission_required
from django.conf import settings
from django.db import IntegrityError
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_http_methods, require_safe

from eda.core import METRICS

from .forms import LocationFilter, LocationForm, StationForm
from .models import Station, Stream
from .reporting import allowed_datasets


@require_safe
def health(request):
    return HttpResponse("ok", content_type="text/plain")


@login_required
@permission_required("catalog.view_station", raise_exception=True)
@require_safe
def catalog(request):
    filters = LocationFilter(request.GET or None)
    stations = Station.objects.select_related("location").annotate(dataset_count=Count(
        "datasets", filter=Q(datasets__in=allowed_datasets(request.user)),
    ))
    if filters.is_bound:
        if filters.is_valid():
            place = filters.cleaned_data["place"]
            if place is not None:
                stations = stations.filter(location=place)
        else:
            stations = stations.none()
    return render(request, "catalog/catalog.html", {"filters": filters, "stations": stations,
                  "map_style_url": settings.METEO_MAP_STYLE_URL})


@login_required
@permission_required("catalog.view_station", raise_exception=True)
@require_safe
def station_geojson(request):
    filters = LocationFilter(request.GET)
    if not filters.is_valid():
        return JsonResponse({"errors": filters.errors.get_json_data()}, status=400)
    stations = Station.objects.select_related("location").filter(latitude__isnull=False, longitude__isnull=False)
    place = filters.cleaned_data["place"]
    if place:
        stations = stations.filter(location=place)
    features = [
        {"type": "Feature", "id": station.pk,
         "geometry": {"type": "Point", "coordinates": [float(station.longitude), float(station.latitude)]},
         "properties": {"name": station.name, "location": str(station.location),
                        "station_url": reverse("station", args=(station.pk,))}}
        for station in stations
    ]
    response = JsonResponse({"type": "FeatureCollection", "features": features}, content_type="application/geo+json")
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@permission_required("catalog.view_station", raise_exception=True)
@require_safe
def station(request, pk):
    selected = get_object_or_404(Station.objects.select_related("location"), pk=pk)
    datasets = allowed_datasets(request.user).filter(station=selected).select_related("uploaded_by").defer("contents", "normalization")
    for dataset in datasets:
        dataset.metric_labels = [METRICS.get(metric, (metric,))[0] for metric in dataset.metrics]
    streams = Stream.objects.filter(station=selected).filter(Q(owner=request.user) | Q(rules__recipient=request.user)).distinct()
    return render(request, "catalog/station.html", {"station": selected, "datasets": datasets, "streams": streams,
                  "superset_url": settings.METEO_SUPERSET_URL if request.user.has_perm("catalog.view_dataset") else ""})


@login_required
@permission_required("catalog.add_location", raise_exception=True)
@require_http_methods(["GET", "POST"])
def add_location(request):
    form = LocationForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            form.save()
        except IntegrityError:
            form.add_error("name", "Это место уже появилось в каталоге.")
        else:
            return redirect("catalog")
    return render(request, "catalog/form.html", {"form": form, "title": "Добавить место", "action": "Сохранить место"})


@login_required
@permission_required("catalog.add_station", raise_exception=True)
@require_http_methods(["GET", "POST"])
def add_station(request):
    form = StationForm(request.POST if request.method == "POST" else None, initial={"location": request.GET.get("place")})
    if request.method == "POST" and form.is_valid():
        try:
            selected = form.save()
        except IntegrityError:
            form.add_error("name", "Станция с таким названием уже появилась в этом месте.")
        else:
            return redirect("station", pk=selected.pk)
    return render(request, "catalog/form.html", {"form": form, "title": "Добавить станцию", "action": "Сохранить станцию"})


@login_required
@permission_required("catalog.view_dataset", raise_exception=True)
@require_safe
def download(request, pk):
    dataset = get_object_or_404(allowed_datasets(request.user).defer("normalization"), pk=pk)
    response = HttpResponse(dataset.contents, content_type="application/octet-stream")
    response["Content-Disposition"] = content_disposition_header(True, dataset.original_name)
    response["Cache-Control"] = "private, no-store"
    return response
