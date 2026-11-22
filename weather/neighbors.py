"""Read-only estimates of missing daily air temperature in the reserve archive."""
from datetime import date
from statistics import median

from . import reports
from .imports import RESERVE_SHA

METHOD = "sokol-daily-seasonal-median-v1"
SEASONS = ((12, 1, 2), (3, 4, 5), (6, 7, 8), (9, 10, 11))


def features(manifest):
    if manifest["sha256"] != RESERVE_SHA:
        return []
    files = {}
    for path, choice in manifest["mappings"].items():
        name = path.rsplit("/", 1)[-1]
        station = name.split("_", 1)[0]
        if choice["station"] != station:
            # Mixed or reassigned stations cannot reproduce these archive examples.
            return []
        mapping = choice["mapping"]
        if all(mapping.get(k) == k for k in ("datetime", "t_air", "qc_flag")):
            files[name] = mapping
    available = []
    if files.get("kr1_2024.csv", {}).get("pressure_hpa") == "pressure_hpa":
        available.append("pressure")
    if "ame60_2010.csv" in files:
        available.append("january")
    if all(f"{station}_{year}.csv" in files for station in ("kr1", "kr2") for year in (2024, 2025)):
        available.append("neighbors")
    return available


def layer(con, role, ds, station, parameter, start, end, version=None, revision=None, *, include_observed=False):
    dataset, version, manifest, _ = reports.visible(con, role, ds, version)
    revision = dataset["revision"] if revision is None else min(revision, dataset["revision"])
    if end < start:
        raise ValueError("Проверьте границы периода.")
    result = {"version": version, "revision": revision, "method": METHOD, "points": []}
    if include_observed:
        result["observed"] = {}
    start, end = max(start, date(2025, 1, 1)), min(end, date(2025, 12, 31))
    if (start > end or station not in ("kr1", "kr2") or parameter != "t_air"
            or "neighbors" not in features(manifest)):
        return result
    donor = "kr1" if station == "kr2" else "kr2"

    def days(site, year, month, cap):
        report = reports.report(con, role, ds, site, parameter, f"{year}-{month:02}", "day", version, cap)
        return report["rows"]

    calibration = {}
    for month in range(start.month, end.month + 1):
        selected = [r for r in days(station, 2025, month, revision)
                    if start.isoformat() <= r["period"] <= end.isoformat()]
        if include_observed:
            result["observed"].update((r["period"], r["value"]) for r in selected)
        target = [r for r in selected if r["value"] is None]
        if not target:
            continue
        reference = {r["period"]: r["value"] for r in days(donor, 2025, month, revision)}
        season = SEASONS[month % 12 // 3]
        for row in target:
            ref = reference[row["period"]]
            if ref is None:
                continue
            if season not in calibration:
                pairs = []
                for m in season:
                    for left, right in zip(days(station, 2024, m, 0), days(donor, 2024, m, 0), strict=True):
                        if left["value"] is not None and right["value"] is not None:
                            pairs.append(left["value"] - right["value"])
                calibration[season] = (median(pairs) if len(pairs) >= 30 else None, len(pairs))
            offset, support = calibration[season]
            if offset is not None:
                result["points"].append({"date": row["period"], "station": station, "donor": donor,
                                         "reference": ref, "offset": offset, "pairs": support,
                                         "estimate": ref + offset, "valid_hours": row["valid"]})
    return result
