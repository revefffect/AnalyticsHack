"""Station identity and navigation for the currently accessible dataset version."""
from urllib.parse import urlencode

from . import imports, reports


NAMES = {"ame60": "АМЕ-60", "kr1": "Кр1 · Поляна", "kr2": "Кр2 · Поле"}
EXAMPLES = {"ame60": ("january", "2010-01"), "kr1": ("pressure", "2024-04"), "kr2": ("neighbors", "2025-05")}
REFERENCE_SITES = (
    ("RU-Fyo", "Фёдоровское", "Башня наблюдений в старовозрастном ельнике.", 56.4615, 32.9221,
     "FLUXNET2015 · 1998–2014", "https://doi.org/10.18140/FLX/1440183"),
    ("RU-Fy2", "Фёдоровское · сухой ельник", "Площадка мониторинга потоков метана.", 56.4476, 32.9019,
     "FLUXNET-CH4 · 2015–2018", "https://doi.org/10.18140/FLX/1669657"),
)


def references():
    return [{"id": "reference:" + code, "code": code, "name": name, "location_kind": "reference",
             "latitude": lat, "longitude": lon, "description": description, "publication": publication,
             "source_url": url} for code, name, description, lat, lon, publication, url in REFERENCE_SITES]


def station_label(manifest, code):
    if code in NAMES and imports.reserve_mapping(manifest["sha256"], manifest["mappings"], code):
        return NAMES[code]
    return code.upper()


def stations(role, dataset, version, manifest, features):
    if manifest["source"].get("data_type") == "reanalysis":
        return []
    cards = []
    for code, count in sorted(manifest["counts"].items()):
        months = sorted(key.split("/")[1] for key in manifest["partitions"] if key.startswith(code + "/"))
        params = manifest["station_parameters"][code]
        if not months or not params:
            continue
        ds = dataset["id"]
        name = station_label(manifest, code)
        example, example_month = EXAMPLES.get(code, (None, None))
        question = example if example in features else None
        month = example_month if question and example_month in months else months[-1]
        parameter = "t_air" if "t_air" in params else params[0]
        selection = dict(dataset=ds, version=version, revision=dataset["revision"], station=code,
                         parameter=parameter, month=month, step="day")
        files = sorted(path for path, choice in manifest["mappings"].items() if choice["station"] == code)
        cards.append({
            "id": f"{ds}:{version}:{code}", "code": code, "name": name,
            "location_kind": "area" if name == NAMES.get(code) else "unknown",
            "title": dataset["title"], "input": manifest["input"], "months": [months[0], months[-1]],
            "files": files, "records": count, "parameters": params, "question": question, "selection": selection,
            "report_url": "/portal?" + urlencode(selection),
            "grafana_url": reports.graph_url(role, ds, version, code, parameter, month, "day", dataset["revision"]),
        })
    return cards
