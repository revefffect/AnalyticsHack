import json
import math
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import compare_digest
from os import environ
from secrets import token_urlsafe
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import pandas as pd
from django.db import transaction
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from eda.core import METRICS
from .importing import QC_VERSION, SCHEMA, Table, normalized_table
from .models import AlertEvent, AlertRule, Dataset, Stream, StreamDashboard, StreamMessage


def hash_token(value):
    return sha256(value.encode()).hexdigest()


@transaction.atomic
def register_stream(station, user, name, mode):
    token = token_urlsafe(32)
    stream = Stream.objects.create(station=station, owner=user, name=name, mode=mode, token_hash=hash_token(token))
    StreamDashboard.objects.create(stream=stream, uid=f"live-{stream.key.hex}")
    return stream, token


def parse_payload(stream, raw):
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError
    message_id = data["id"]
    source_clock = data["datetime"]
    values = data["values"]
    if (not isinstance(message_id, str) or not 1 <= len(message_id) <= 100 or not isinstance(source_clock, str)
            or not isinstance(values, dict) or not values or set(values) - set(METRICS)
            or not all(value is None or type(value) in (int, float) and math.isfinite(value) for value in values.values())):
        raise ValueError
    moment = datetime.fromisoformat(source_clock)
    if not 1700 <= moment.year <= 2200:
        raise ValueError
    assumed = data.get("assumed_timezone")
    if moment.utcoffset() is None:
        if stream.mode != Dataset.Source.REPLAY or assumed != "UTC":
            raise ValueError
        moment = moment.replace(tzinfo=UTC)
    elif assumed is not None:
        raise ValueError
    supplied_flags = data.get("qc_flag", "")
    if not isinstance(supplied_flags, str) or len(supplied_flags) > 1024:
        raise ValueError
    frame = normalized_table(Table("stream", pd.DataFrame([{"datetime": source_clock, "qc_flag": supplied_flags, **values}])),
                             {"datetime": "datetime", "qc_flag": "qc_flag", **{metric: metric for metric in values}}, {}, "")
    flags = frame.iloc[0].qc_flag
    tokens = [token.strip() for token in flags.split(";")]
    rejected = {metric: [token for token in tokens if token == "dup_conflict" or token.startswith(metric + ":")]
                for metric in values}
    source = {"mode": stream.mode, "source_time": source_clock, "source_timezone": "неизвестен" if assumed else
              str(moment.utcoffset()), "assumed_replay_timezone": assumed, "schema": SCHEMA, "qc": QC_VERSION,
              "archive": data.get("archive", "") if stream.mode == Dataset.Source.REPLAY else "",
              "qc_flag": flags}
    if not isinstance(source["archive"], str) or len(source["archive"]) > 255:
        raise ValueError
    return message_id, moment, values, rejected, source


def recorded_event(rule, category, state, moment, details):
    key = f"{rule.pk}|{rule.version}|{category}|{state}|{moment.isoformat()}"
    AlertEvent.objects.get_or_create(event_key=hash_token(key), defaults={
        "rule": rule, "category": category, "state": state, "observation": moment, "version": rule.version,
        "details": {**details, "threshold": rule.threshold, "rule_name": rule.name, "metric": rule.metric,
                    "mode": rule.stream.mode, "interval_minutes": rule.interval_minutes,
                    "duration_minutes": rule.duration_minutes, "minimum_coverage": rule.minimum_coverage},
        "delivery": "pending" if rule.webhook else "in_app",
    })


def evaluate_threshold(rule, moment):
    start = moment - timedelta(minutes=rule.duration_minutes)
    valid = {}
    conflicts = set()
    # A conflicting timestamp contributes no sample to a threshold window.
    points = rule.stream.messages.filter(observation__range=(start, moment)).order_by("observation", "pk")
    for point in points:
        key = point.observation
        value = point.values.get(rule.metric)
        sample = value if value is not None and not point.rejected.get(rule.metric) else None
        if key in valid and valid[key] != sample:
            conflicts.add(key)
        else:
            valid[key] = sample
    for key in conflicts:
        valid[key] = None
    bins = {}
    for clock, value in valid.items():
        if value is not None:
            slot = int((clock - start).total_seconds() // (rule.interval_minutes * 60))
            bins.setdefault(slot, []).append(value)
    coverage = len(bins) / (rule.duration_minutes // rule.interval_minutes + 1)
    values = [value for group in bins.values() for value in group]
    high = bool(values) and min(values) >= rule.threshold
    current = valid.get(moment)
    if coverage < rule.minimum_coverage or current is None:
        status = "insufficient"
    elif rule.state.get("active") and current < rule.threshold:
        status = "ok"
        recorded_event(rule, "threshold", "recovered", moment, {"value": current, "coverage": coverage})
        rule.state["active"] = False
    elif high and (moment - min(clock for clock, value in valid.items() if value is not None)).total_seconds() >= rule.duration_minutes * 60:
        status = "active"
        if not rule.state.get("active"):
            recorded_event(rule, "threshold", "triggered", moment, {"value": current, "coverage": coverage})
        rule.state["active"] = True
    else:
        status = "active" if rule.state.get("active") else "ok"
    rule.state.update(status=status, coverage=coverage, latest_value=current)
    if rule.state.pop("stale_since", None):
        recorded_event(rule, "stale", "recovered", moment, {})
    rule.save(update_fields=("state",))


@csrf_exempt
@require_POST
def ingest(request, key):
    auth = request.headers.get("Authorization", "")
    stream = Stream.objects.filter(key=key).first()
    if not stream or not auth.startswith("Bearer ") or not compare_digest(stream.token_hash, hash_token(auth[7:])):
        return JsonResponse({"error": "unauthorized"}, status=401)
    if len(request.body) > 8192:
        return JsonResponse({"error": "message_too_large"}, status=413)
    try:
        message_id, moment, values, rejected, source = parse_payload(stream, request.body)
    except (ValueError, KeyError, TypeError, OverflowError):
        return JsonResponse({"error": "invalid_observation"}, status=400)
    with transaction.atomic():
        stream = Stream.objects.select_for_update().get(pk=stream.pk)
        point, created = StreamMessage.objects.get_or_create(stream=stream, message_id=message_id, defaults={
            "observation": moment, "values": values, "rejected": rejected, "source": source,
        })
        if not created:
            if (point.observation, point.values, point.source) != (moment, values, source):
                return JsonResponse({"error": "message_id_conflict"}, status=409)
            return JsonResponse({"status": "duplicate", "id": point.pk})
        late = stream.latest_observation is not None and moment < stream.latest_observation
        stream.latest_receipt = point.received
        if not late:
            stream.latest_observation = moment
        stream.save(update_fields=("latest_observation", "latest_receipt"))
        for rule in stream.rules.select_for_update():
            if not late:
                evaluate_threshold(rule, moment)
            elif rule.state.pop("stale_since", None):
                recorded_event(rule, "stale", "recovered", point.received, {"late": True})
                rule.save(update_fields=("state",))
    return JsonResponse({"status": "late" if late else "accepted", "id": point.pk, "received": point.received.isoformat()}, status=201)


def monitor_stale(now=None):
    now = now or timezone.now()
    for pk in AlertRule.objects.values_list("pk", flat=True):
        with transaction.atomic():
            rule = AlertRule.objects.select_for_update().select_related("stream").get(pk=pk)
            receipt = rule.stream.latest_receipt
            if receipt is None or now - receipt < timedelta(minutes=rule.stale_minutes) or rule.state.get("stale_since"):
                continue
            due = receipt + timedelta(minutes=rule.stale_minutes)
            recorded_event(rule, "stale", "triggered", due, {"last_received": receipt.isoformat()})
            rule.state["stale_since"] = due.isoformat()
            rule.save(update_fields=("state",))


def deliver_webhooks():
    endpoint = environ.get("METEO_WEBHOOK_URL", "")
    base_url = environ.get("METEO_PUBLIC_URL", "").rstrip("/")
    target = urlparse(endpoint)
    if target.scheme != "https" and not (target.scheme == "http" and target.hostname in ("127.0.0.1", "localhost")):
        return
    for event in AlertEvent.objects.filter(delivery="pending").select_related("rule__stream").order_by("pk")[:50]:
        payload = {"id": event.event_key, "state": event.state, "category": event.category, "version": event.version,
                   "time": event.observation.isoformat(), "details": event.details,
                   "url": base_url + reverse("stream-detail", args=(event.rule.stream.key,))}
        request = Request(endpoint, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json",
                          "Idempotency-Key": event.event_key}, method="POST")
        try:
            with urlopen(request, timeout=4) as response:
                delivered = 200 <= response.status < 300
        except (OSError, URLError):
            delivered = False
        if delivered:
            AlertEvent.objects.filter(pk=event.pk, delivery="pending").update(delivery="sent", sent_at=timezone.now())
