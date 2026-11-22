import math

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_safe

from eda.core import METRICS
from .models import AlertEvent, AlertRule, Dataset, Station, Stream
from .platform_views import report_figure
from .streaming import register_stream


class StreamForm(forms.Form):
    name = forms.CharField(max_length=100, label="Название")
    mode = forms.ChoiceField(choices=[(Dataset.Source.OBSERVED, "Живая станция"), (Dataset.Source.REPLAY, "Воспроизведение архива")],
                             label="Тип источника")


class RuleForm(forms.Form):
    name = forms.CharField(max_length=100, label="Название")
    metric = forms.ChoiceField(choices=[(key, value[0]) for key, value in METRICS.items()], label="Показатель")
    threshold = forms.FloatField(label="Порог «выше или равно» в единицах показателя")
    duration_minutes = forms.IntegerField(min_value=1, max_value=1440, initial=30, label="Продолжительность, мин")
    interval_minutes = forms.IntegerField(min_value=1, max_value=1440, initial=15, label="Ожидаемый интервал, мин")
    minimum_coverage = forms.FloatField(min_value=0.01, max_value=1, initial=0.75, label="Минимальное покрытие, доля 0–1")
    stale_minutes = forms.IntegerField(min_value=1, max_value=1440, initial=60, label="Считать устаревшим после, мин")
    recipient = forms.CharField(max_length=150, label="Логин получателя")
    webhook = forms.BooleanField(required=False, label="Отправлять через вебхук, настроенный администратором")

    def clean(self):
        values = super().clean()
        login = values.get("recipient", "")
        user = get_user_model().objects.filter(username=login, is_active=True).first()
        if not user or not user.has_perm("catalog.view_dataset"):
            self.add_error("recipient", "Укажи действующего пользователя с правом чтения.")
        else:
            values["recipient"] = user
        duration, interval = values.get("duration_minutes"), values.get("interval_minutes")
        if duration and interval and duration < interval:
            self.add_error("duration_minutes", "Продолжительность должна быть не меньше интервала.")
        if "threshold" in values and not math.isfinite(values["threshold"]):
            self.add_error("threshold", "Укажи конечное число.")
        return values


@login_required
@require_safe
def inbox(request):
    streams = Stream.objects.filter(Q(owner=request.user) | Q(rules__recipient=request.user)).distinct().select_related("station")
    events = AlertEvent.objects.filter(rule__recipient=request.user).select_related("rule__stream").order_by("-created")[:100]
    return render(request, "catalog/monitor.html", {"streams": streams, "events": events})


@login_required
@permission_required("catalog.add_dataset", raise_exception=True)
@require_http_methods(["GET", "POST"])
def add_stream(request, pk):
    station = get_object_or_404(Station, pk=pk)
    form = StreamForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        stream, token = register_stream(station, request.user, **form.cleaned_data)
        response = render(request, "catalog/stream.html", {"stream": stream, "token": token,
                          "endpoint": request.build_absolute_uri(reverse("stream-ingest", args=(stream.key,)))})
        response["Cache-Control"] = "private, no-store"
        return response
    return render(request, "catalog/form.html", {"form": form, "title": "Добавить поток", "station": station, "action": "Создать вход"})


@login_required
@require_http_methods(["GET", "POST"])
def stream_detail(request, key):
    stream = get_object_or_404(Stream.objects.filter(Q(owner=request.user) | Q(rules__recipient=request.user)).distinct(), key=key)
    owner = stream.owner_id == request.user.pk
    form = RuleForm(request.POST or None) if owner else None
    if request.method == "POST":
        if not owner:
            return redirect("alerts")
        if form.is_valid():
            AlertRule.objects.create(stream=stream, **form.cleaned_data)
            return redirect("stream-detail", key=key)
    rules = stream.rules.all() if owner else stream.rules.filter(recipient=request.user)
    metric = request.GET.get("metric", "t_air")
    if metric not in METRICS or not owner and metric not in set(rules.values_list("metric", flat=True)):
        metric = rules.values_list("metric", flat=True).first() or "t_air"
    points = list(stream.messages.order_by("-observation", "-pk")[:500])
    records = [{"start": point.observation.isoformat(), "mean": None if point.rejected.get(metric) else point.values.get(metric)}
               for point in reversed(points)]
    response = render(request, "catalog/stream.html", {"stream": stream, "rules": rules, "form": form,
                      "points": points[:25], "metric": metric, "chart": report_figure(records, METRICS[metric][1])})
    response["Cache-Control"] = "private, no-store"
    return response
