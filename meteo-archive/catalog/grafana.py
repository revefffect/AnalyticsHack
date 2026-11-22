import base64
import json
from datetime import UTC, date, datetime, time, timedelta
from os import environ
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from django.contrib.auth.decorators import login_required
from django.db.models import F
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_safe

from eda.core import METRICS
from .models import DashboardBinding, Stream, StreamDashboard
from .platform_views import _report_form, load_authorized
from .reporting import allowed_datasets, period_records, version_context


class ApiFailure(RuntimeError):
    pass


def enabled():
    return all(environ.get(name) for name in ("METEO_GRAFANA_URL", "METEO_GRAFANA_PASSWORD", "METEO_INFLUX_URL",
                                               "METEO_INFLUX_TOKEN", "METEO_GRAFANA_PROXY_KEY"))


def http(base, auth, path, method="GET", payload=None, *, org=None, missing=False):
    headers = {"Authorization": auth, "Accept": "application/json"}
    if org:
        headers["X-Grafana-Org-Id"] = str(org)
    if isinstance(payload, bytes):
        headers["Content-Type"] = "text/plain; charset=utf-8"
    elif payload is not None:
        headers["Content-Type"] = "application/json"
        payload = json.dumps(payload).encode()
    try:
        with urlopen(Request(base.rstrip("/") + path, payload, headers=headers, method=method), timeout=12) as result:
            data = result.read()
            return json.loads(data) if data else {}
    except HTTPError as error:
        if missing and error.code == 404:
            return None
        raise ApiFailure(f"{method} {path.split('?')[0]} · HTTP {error.code}") from error
    except (OSError, ValueError) as error:
        raise ApiFailure(f"{method} {path.split('?')[0]} · сервис недоступен") from error


def influx(path, *args, **kwargs):
    return http(environ["METEO_INFLUX_URL"], "Token " + environ["METEO_INFLUX_TOKEN"], path, *args, **kwargs)


def grafana(path, *args, **kwargs):
    encoded = base64.b64encode(("admin:" + environ["METEO_GRAFANA_PASSWORD"]).encode()).decode()
    return http(environ["METEO_GRAFANA_URL"], "Basic " + encoded, path, *args, **kwargs)


def influx_bucket(name):
    org_name = environ.get("METEO_INFLUX_ORG", "meteo")
    org = influx("/api/v2/orgs?" + urlencode({"org": org_name}))["orgs"][0]
    found = influx("/api/v2/buckets?" + urlencode({"orgID": org["id"], "name": name}), missing=True)
    buckets = found["buckets"] if found is not None else []
    if buckets:
        return buckets[0]
    return influx("/api/v2/buckets", "POST", {"orgID": org["id"], "name": name, "retentionRules": []})


def epoch(iso_date):
    return int(datetime.combine(date.fromisoformat(iso_date), time(), UTC).timestamp())


def aggregate_points(dataset):
    lines = []
    for metric in dataset.metrics:
        _, daily, months, decades = version_context(dataset, metric, str(dataset.first_observation), str(dataset.last_observation))
        for step in ("day", "decade", "month"):
            for row in period_records(daily, months, decades, step):
                fields = [f'{key}={row[key]}i' for key in ("station_days", "metric_days", "period_days")]
                if row["mean"] is not None:
                    fields.append(f'mean={row["mean"]}')
                exclusive_end = epoch(row["end"]) + 86400
                lines.append(f'weather,metric={metric},step={step},ends_at={exclusive_end} {",".join(fields)} {epoch(row["start"])}')
    return "\n".join(lines)


def dashboard(binding):
    dataset = binding.dataset
    metrics = [{"text": METRICS[metric][0], "value": metric, "selected": index == 0}
               for index, metric in enumerate(dataset.metrics)]
    steps = [{"text": label, "value": step, "selected": index == 0} for index, (step, label) in
             enumerate([("day", "Сутки"), ("decade", "Декада"), ("month", "Месяц")])]
    template = [{"name": name, "label": label, "type": "custom", "query": ",".join(f"{item['text']} : {item['value']}" for item in options),
                 "options": options, "current": options[0]} for name, label, options in
                [("metric", "Показатель", metrics), ("step", "Шаг", steps)]]
    source = {"type": "influxdb", "uid": binding.uid}
    flux = ('from(bucket: "' + binding.uid + '") |> range(start: v.timeRangeStart, stop: v.timeRangeStop) '
            '|> filter(fn: (r) => r._measurement == "weather" and r.metric == ${metric:json} and r.step == ${step:json}) '
            '|> filter(fn: (r) => int(v: r.ends_at) * 1000000000 <= int(v: v.timeRangeStop)) '
            '|> group(columns: ["_field"])')
    panels = []
    for index, (title, select, panel_type) in enumerate([
        ("Среднее по пригодным наблюдениям", 'r._field == "mean"', "timeseries"),
        ("Покрытие · пригодные дни и дни периода", 'r._field != "mean"', "barchart"),
    ]):
        panels.append({"id": index + 1, "title": title, "type": panel_type,
                       "gridPos": {"h": 8, "w": 24, "x": 0, "y": index * 8}, "datasource": source,
                       "targets": [{"refId": "A", "datasource": source, "query": flux + f" |> filter(fn: (r) => {select})"}],
                       "fieldConfig": {"defaults": {"custom": {"spanNulls": False}}, "overrides": []}})
    return {"uid": binding.uid, "title": f"{dataset.station.name} · {dataset.get_source_kind_display()}",
            "schemaVersion": 39, "timezone": "utc", "refresh": "", "panels": panels,
            "templating": {"list": template}, "links": [{"title": "Разобрать покрытие в каталоге", "type": "link",
            "url": reverse("dataset-report", args=(dataset.version,)) + "?metric=${metric}&step=${step}"}],
            "time": {"from": str(dataset.first_observation) + "T00:00:00Z",
                     "to": str(dataset.last_observation + timedelta(days=1)) + "T00:00:00Z"}}


def provision(binding):
    bucket, org_id = setup_workspace(binding.uid)
    parameters = urlencode({"orgID": bucket["orgID"], "bucket": bucket["id"], "precision": "s"})
    points = aggregate_points(binding.dataset).splitlines()
    for index in range(0, len(points), 5000):
        influx("/api/v2/write?" + parameters, "POST", "\n".join(points[index:index + 5000]).encode())
    grafana("/api/dashboards/db", "POST", {"dashboard": dashboard(binding), "overwrite": True}, org=org_id)
    binding.org_id, binding.bucket_id = org_id, bucket["id"]
    binding.state, binding.error, binding.synced_at = "ready", "", timezone.now()
    binding.save(update_fields=("org_id", "bucket_id", "state", "error", "synced_at"))


def setup_workspace(uid):
    bucket = influx_bucket(uid)
    org = grafana("/api/orgs/name/" + quote(uid), missing=True) or grafana("/api/orgs", "POST", {"name": uid})
    org_id = org.get("orgId", org.get("id"))
    members = grafana(f"/api/orgs/{org_id}/users")
    if not any(member["login"] == "admin" for member in members):
        grafana(f"/api/orgs/{org_id}/users", "POST", {"loginOrEmail": "admin", "role": "Admin"})
    path = f"/api/datasources/uid/{uid}"
    if grafana(path, org=org_id, missing=True) is None:
        authorization = influx("/api/v2/authorizations", "POST", {"orgID": bucket["orgID"], "description": uid,
                               "permissions": [{"action": "read", "resource": {"type": "buckets", "id": bucket["id"],
                                                                                 "orgID": bucket["orgID"]}}]})
        try:
            grafana("/api/datasources", "POST", {"name": uid, "uid": uid, "type": "influxdb",
                    "access": "proxy", "url": environ["METEO_INFLUX_URL"],
                    "jsonData": {"version": "Flux", "organization": environ.get("METEO_INFLUX_ORG", "meteo"), "defaultBucket": uid},
                    "secureJsonData": {"token": authorization["token"]}}, org=org_id)
        except ApiFailure:
            influx("/api/v2/authorizations/" + authorization["id"], "DELETE")
            raise
    return bucket, org_id


def stream_definition(binding):
    source = {"type": "influxdb", "uid": binding.uid}
    flux = ('from(bucket: "' + binding.uid + '") |> range(start: v.timeRangeStart, stop: v.timeRangeStop) '
            '|> filter(fn: (r) => r._measurement == "observation" and r.metric == ${metric:json}) '
            '|> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value") '
            '|> filter(fn: (r) => r.valid == 1) |> keep(columns: ["_time", "value"])')
    options = [{"text": METRICS[metric][0], "value": metric, "selected": index == 0}
               for index, metric in enumerate(METRICS)]
    metric = {"name": "metric", "label": "Показатель", "type": "custom", "current": options[0], "options": options,
              "query": ",".join(f"{item['text']} : {item['value']}" for item in options)}
    return {"uid": binding.uid, "title": f"{binding.stream.name} · {binding.stream.get_mode_display()}",
            "timezone": "utc", "schemaVersion": 39, "refresh": "5s",
            "time": {"from": "now-6h", "to": "now"}, "templating": {"list": [metric]},
            "links": [{"title": "Измерения и время приёма", "type": "link",
                       "url": reverse("stream-detail", args=(binding.stream.key,))}],
            "panels": [{"id": 1, "title": "Пригодные наблюдения", "type": "timeseries", "datasource": source,
                       "gridPos": {"h": 14, "w": 24, "x": 0, "y": 0},
                       "targets": [{"refId": "A", "datasource": source, "query": flux}]}]}


def sync_stream(binding):
    stream = binding.stream
    points = list(stream.messages.filter(pk__gt=binding.cursor).order_by("pk")[:250])
    if not points:
        return
    if not binding.org_id:
        bucket, org_id = setup_workspace(binding.uid)
        grafana("/api/dashboards/db", "POST", {"dashboard": stream_definition(binding), "overwrite": True}, org=org_id)
        binding.org_id, binding.bucket_id = org_id, bucket["id"]
    instants = {point.observation for point in points}
    grouped = {}
    for point in stream.messages.filter(observation__in=instants):
        for metric, value in point.values.items():
            sample = None if point.rejected.get(metric) or value is None else float(value)
            grouped.setdefault((point.observation, metric), set()).add(sample)
    lines = []
    for (clock, metric), values in grouped.items():
        usable = len(values) == 1 and None not in values
        fields = f"value={next(iter(values)) if usable else 0.0},valid={int(usable)}i"
        instant = int(clock.timestamp()) * 10**9 + clock.microsecond * 1000
        lines.append(f"observation,metric={metric} {fields} {instant}")
    endpoint = "/api/v2/write?" + urlencode({"bucket": binding.bucket_id, "precision": "ns"})
    influx(endpoint, "POST", "\n".join(lines).encode())
    binding.cursor = points[-1].pk
    binding.state, binding.error, binding.synced_at = "ready", "", timezone.now()
    binding.save(update_fields=("cursor", "org_id", "bucket_id", "state", "error", "synced_at"))


def sync_pending():
    if not enabled():
        return
    streams = StreamDashboard.objects.filter(stream__messages__pk__gt=F("cursor")).distinct().select_related("stream").order_by(
        F("synced_at").asc(nulls_first=True))[:10]
    for binding in streams:
        try:
            sync_stream(binding)
        except (ApiFailure, KeyError, IndexError) as error:
            binding.error, binding.synced_at = str(error)[:200], timezone.now()
            binding.save(update_fields=("error", "synced_at"))
    waiting = DashboardBinding.objects.exclude(state="ready").select_related("dataset__station", "dataset__uploaded_by").order_by(
        F("synced_at").asc(nulls_first=True))[:1]
    for binding in waiting:
        try:
            provision(binding)
        except (ApiFailure, KeyError, IndexError) as error:
            binding.state, binding.error, binding.synced_at = "retry", str(error)[:200], timezone.now()
            binding.save(update_fields=("state", "error", "synced_at"))


def authorized_orgs(user):
    datasets = DashboardBinding.objects.filter(dataset__in=allowed_datasets(user), state="ready")
    streams = StreamDashboard.objects.filter(stream__owner=user, state="ready")
    return set(datasets.values_list("org_id", flat=True)) | set(streams.values_list("org_id", flat=True))


def reconcile_user(user, orgs, target):
    login = f"meteo-{user.pk}"
    account = grafana("/api/users/lookup?" + urlencode({"loginOrEmail": login}), missing=True)
    if account is None:
        from secrets import token_urlsafe

        account = grafana("/api/admin/users", "POST", {"login": login, "email": f"{login}@meteo.invalid",
                          "name": login, "password": token_urlsafe(40), "OrgId": target})
    user_id = account["id"]
    memberships = {org["orgId"]: org["role"] for org in grafana(f"/api/users/{user_id}/orgs")}
    for org_id in memberships.keys() - orgs:
        grafana(f"/api/orgs/{org_id}/users/{user_id}", "DELETE")
    if target not in memberships:
        grafana(f"/api/orgs/{target}/users", "POST", {"loginOrEmail": login, "role": "Viewer"})
    elif memberships[target] != "Viewer":
        grafana(f"/api/orgs/{target}/users/{user_id}", "PATCH", {"role": "Viewer"})
    grafana(f"/api/users/{user_id}/using/{target}", "POST")
    return login


@login_required
@require_safe
def open_dashboard(request, version):
    dataset = load_authorized(request.user, version)
    binding = get_object_or_404(DashboardBinding, dataset=dataset, state="ready")
    form, _ = _report_form(dataset, request.GET.dict())
    options = form.options() if form.is_valid() else {}
    params = {"orgId": binding.org_id}
    if options:
        end = (date.fromisoformat(options["end"]) + timedelta(days=1)).isoformat()
        params.update({"from": epoch(options["start"]) * 1000, "to": epoch(end) * 1000,
                       "var-metric": options["metric"], "var-step": options["step"]})
    return open_binding(request, binding, params)


@login_required
@require_safe
def open_stream(request, key):
    stream = get_object_or_404(Stream, owner=request.user, key=key)
    binding = get_object_or_404(StreamDashboard, stream=stream, state="ready")
    params = {"orgId": binding.org_id}
    if stream.mode == "replay" and stream.latest_observation:
        params.update({"to": int((stream.latest_observation + timedelta(minutes=5)).timestamp()) * 1000,
                       "from": int((stream.latest_observation - timedelta(hours=6)).timestamp()) * 1000})
    return open_binding(request, binding, params)


def open_binding(request, binding, params):
    if not enabled():
        return HttpResponse("Grafana ожидает настройки.", status=503)
    orgs = authorized_orgs(request.user)
    if binding.org_id not in orgs:
        return HttpResponse(status=403)
    try:
        login = reconcile_user(request.user, orgs, binding.org_id)
    except (ApiFailure, KeyError):
        return HttpResponse("Не удалось открыть Grafana. Повтори попытку после запуска сервисов.", status=503)
    request.session["grafana_scope"] = sorted(orgs)
    request.session["grafana_login"] = login
    return redirect(f"/grafana/d/{binding.uid}/station?{urlencode(params)}")


@require_safe
def proxy_identity(request):
    from hmac import compare_digest

    key = environ.get("METEO_GRAFANA_PROXY_KEY", "")
    if not key or not compare_digest(key, request.headers.get("X-Meteo-Proxy-Key", "")) or not request.user.is_authenticated:
        return HttpResponse(status=403)
    login = request.session.get("grafana_login")
    if login != f"meteo-{request.user.pk}" or set(request.session.get("grafana_scope", [])) != authorized_orgs(request.user):
        return HttpResponse(status=403)
    return HttpResponse(status=204, headers={"X-WEBAUTH-USER": login})
