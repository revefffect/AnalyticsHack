"""Detect tabular inputs, confirm mappings and build immutable monthly partitions."""
import calendar
import csv
import gzip
import io
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import PurePosixPath
from zipfile import BadZipFile, ZipFile

from . import store

LIMIT = 64 * 1024 * 1024
EXPANDED = 256 * 1024 * 1024
STATIONS = {"ame60", "kr1", "kr2"}
RESERVE_SHA = "8c574c518617aea0f301997c62dfe75dc09b059abca1cdbd268645a3cd6cc33d"
UNITS = {"t_air": "°C", "pressure_hpa": "гПа", "rh": "%", "wind_speed": "м/с", "wind_gust": "м/с",
         "wind_dir": "°", **{k: "°C" for k in ("soil_05", "soil_10", "soil_20")}}
BOUNDS = {"t_air": (-50, 45), "pressure_hpa": (940, 1050), "rh": (0, 100), "wind_speed": (0, 60),
          "wind_gust": (0, 75), "wind_dir": (0, 360), **{k: (-40, 45) for k in ("soil_05", "soil_10", "soil_20")}}
ALIASES = {"datetime": {"datetime","timestamp","дата и время","время наблюдения"}, "t_air":{"t_air","температура","температура воздуха"},
           "pressure_hpa":{"pressure_hpa","давление","давление гпа"}, "qc_flag":{"qc_flag","флаги"}}
for name in UNITS:
    ALIASES.setdefault(name, {name})


def station_from(name):
    match = re.search(r"(ame60|kr1|kr2)(?:_|\.|$)", PurePosixPath(name.lower()).name)
    return match.group(1) if match else None


def reserve_mapping(sha, mappings, station):
    files = [name for name, choice in mappings.items() if choice["station"] == station]
    return sha == RESERVE_SHA and bool(files) and all(
        PurePosixPath(name).name.split("_", 1)[0] == station for name in files)


def zip_check(z):
    entries = z.infolist()
    if len(entries) > 500 or sum(e.file_size for e in entries) > EXPANDED:
        raise ValueError("Превышен допустимый размер распакованных файлов.")


def decode(data):
    for encoding in ("utf-8-sig","cp1251"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError("Не удалось определить кодировку текста.")


def tables(filename, data):
    suffix = PurePosixPath(filename.lower()).suffix
    if suffix == ".zip":
        with ZipFile(io.BytesIO(data)) as archive:
            zip_check(archive)
            seen = set()
            for name in archive.namelist():
                if name.lower().endswith(".csv") and PurePosixPath(name).name != "coverage_by_month.csv":
                    if name in seen:
                        raise ValueError("Повторяющееся имя файла в архиве.")
                    seen.add(name)
                    yield from tables(name, archive.read(name))
    elif suffix == ".csv":
        value, encoding = decode(data)
        try:
            sep = csv.Sniffer().sniff(value[:8192], delimiters=",;\t|").delimiter
        except csv.Error:
            sep = max(",;\t|", key=value.split("\n", 1)[0].count)
        reader = csv.DictReader(io.StringIO(value, newline=""), delimiter=sep)
        yield filename, reader.fieldnames or [], ((reader.line_num, row) for row in reader), {"encoding":encoding, "delimiter":sep}
    elif suffix == ".xlsx":
        from openpyxl import load_workbook
        with ZipFile(io.BytesIO(data)) as z:
            zip_check(z)
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        try:
            for sheet in book:
                records = sheet.values
                cols = [str(c or "").strip() for c in next(records, ())]
                yield sheet.title, cols, ((i,dict(zip(cols,row))) for i,row in enumerate(records,2)), {"sheet":sheet.title}
        finally:
            book.close()
    elif suffix in {".json",".jsonl"}:
        value, encoding = decode(data)
        if suffix == ".jsonl":
            records = [json.loads(s) for s in value.splitlines() if s.strip()]
        else:
            records = json.loads(value)
            if isinstance(records, dict):
                records = records.get("observations", records.get("rows"))
        if not isinstance(records,list) or not records or not all(isinstance(r,dict) for r in records):
            raise ValueError("Ожидается массив записей или объект с массивом observations.")
        cols = list(dict.fromkeys(k for row in records for k in row))
        yield filename, cols, enumerate(records,1), {"encoding":encoding}
    else:
        raise ValueError("Поддерживаются ZIP с CSV, CSV, XLSX, JSON и JSONL.")


def display(value):
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return value


def inspect(filename, data):
    from itertools import islice
    result = []
    for name, cols, rows, detected in tables(filename, data):
        if len(cols)>128 or len(set(cols))!=len(cols) or any(not x for x in cols):
            raise ValueError(f"{name}: заголовки должны быть уникальны и заполнены.")
        mapping = {}
        for key,aliases in ALIASES.items():
            matches = [col for col in cols if col.strip().lower() in aliases]
            if len(matches)==1:
                mapping[key] = matches[0]
        sample = [{k:display(v) for k,v in r.items() if k in cols} for _,r in islice(rows,3)]
        types={col:next((key for key,value in mapping.items() if value==col),None) for col in cols}
        types={col:("datetime" if kind=="datetime" else "number" if kind in UNITS else "text") for col,kind in types.items()}
        result.append({"name":name,"columns":cols,"sample":sample,"types":types,"station":station_from(name) or station_from(filename),
                       "mapping":mapping,"units":{k:UNITS[k] for k in mapping if k in UNITS},**detected})
    if not result:
        raise ValueError("Не найдено таблиц наблюдений.")
    return result


def prepare(owner, filename, data, metadata=None):
    if not data or len(data)>LIMIT:
        raise ValueError("Допустимый размер файла от 1 байта до 64 МиБ.")
    files = inspect(filename,data)
    sha = store.put(data)
    info = {"files":files,"source":metadata or {"data_type":"station","timezone":"unknown"}}
    key = store.digest(store.packed([owner,filename,sha,info["source"]]).encode())[:32]
    with store.db().begin() as con:
        store.execute(con,"INSERT INTO previews VALUES (:k,:owner,:sha,:f,:meta) ON CONFLICT DO NOTHING",
                      k=key,owner=owner,sha=sha,f=filename,meta=store.packed(info))
        templates = store.many(con,"SELECT name,mappings FROM templates WHERE owner=:owner",owner=owner)
    return {"id":key,"files":files,"filename":filename,"source_sha256":sha,"reserve_archive":sha==RESERVE_SHA,
            "templates":{t["name"]:json.loads(t["mappings"]) for t in templates}}


def normalized(row, mapping):
    stamp = str(display(row.get(mapping.get("datetime"))) or "").strip()
    dt = datetime.fromisoformat(stamp)
    if dt.tzinfo is not None:
        raise ValueError("Передайте местное время без смещения и зону в метаданных.")
    flags = set(filter(None,str(row.get(mapping.get("qc_flag")) or "").split(";")))
    values = {}
    for key,col in mapping.items():
        if key not in UNITS:
            continue
        src = row.get(col)
        if src is None or str(src).strip()=="":
            values[key] = None
            continue
        val = float(str(src).strip().replace(",","."))
        if not math.isfinite(val):
            raise ValueError("Нечисловое измерение.")
        values[key] = val
        low,high = BOUNDS[key]
        if not low<=val<=high:
            flags.add(key+":range")
    return dt.isoformat(sep=" ",timespec="seconds"), values, sorted(flags)


def confirm(owner, key, title, selected, template=None, dataset=None, combine_stations=()):
    with store.db().connect() as con:
        draft = store.one(con,"SELECT * FROM previews WHERE id=:id AND owner=:o",id=key,o=owner)
    if not draft:
        raise LookupError("Предпросмотр недоступен.")
    info = json.loads(draft["metadata"])
    config = {x["name"]:x for x in selected}
    if not config or len(config)!=len(selected):
        raise ValueError("Выберите таблицы и подтвердите сопоставление.")
    files = {f["name"]:f for f in info["files"]}
    external = info["source"].get("data_type")=="reanalysis"
    for name,choice in config.items():
        mapping = choice["mapping"]
        columns = files.get(name,{}).get("columns",[])
        allowed = {"era5"} if external else STATIONS
        if choice["station"] not in allowed or "datetime" not in mapping or not (UNITS.keys() & mapping.keys()):
            raise ValueError(f"{name}: выберите станцию, время и хотя бы один показатель.")
        if not set(mapping)<=set(ALIASES) or not set(mapping.values())<=set(columns) or len(set(mapping.values()))!=len(mapping):
            raise ValueError(f"{name}: проверьте соответствие колонок.")
    source_identity=info["source"].get("series_sha256") if external else info["source"]
    fingerprint = store.digest(store.packed([draft["raw_hash"],config,source_identity,"normalize-v1"]).encode())
    # A subset of a user archive is a separate import. Two selections may each contain a KR1.
    implicit_id = store.digest(store.packed([owner,key]).encode())[:20]
    with store.db().connect() as con:
        if dataset is None and set(config) != set(files):
            legacy_version = store.digest((implicit_id+fingerprint).encode())[:32]
            if not store.one(con,"SELECT id FROM versions WHERE dataset=:d AND id=:v",d=implicit_id,v=legacy_version):
                implicit_id = store.digest(store.packed([owner,key,sorted(config)]).encode())[:20]
        dataset = dataset or implicit_id
        version = store.digest((dataset+fingerprint).encode())[:32]
        existing = store.one(con,"SELECT * FROM datasets WHERE id=:id",id=dataset)
        if existing and existing["owner"]!=owner:
            raise LookupError("Набор недоступен.")
        if store.one(con,"SELECT id FROM versions WHERE id=:v",v=version):
            return {"id":dataset,"version":version,"title":existing["title"],"repeated":True}
    for code, total in Counter(choice["station"] for choice in selected).items():
        if total > 1 and code not in combine_stations and not reserve_mapping(draft["raw_hash"], config, code):
            raise ValueError(f"{code.upper()}: несколько таблиц с одним кодом. Подтвердите общую станцию или импортируйте таблицы отдельными наборами.")
    partitions = defaultdict(list)
    stats = Counter()
    available = defaultdict(set)
    for name,_,rows,_ in tables(draft["filename"],store.get(draft["raw_hash"])):
        if name not in config:
            continue
        station, mapping = config[name]["station"], config[name]["mapping"]
        available[station].update(set(mapping)&UNITS.keys())
        buckets = defaultdict(io.BytesIO)
        # Partitions are independently readable and bound peak memory to one source table.
        for number,row in rows:
            try:
                stamp, values, flags = normalized(row,mapping)
            except (ValueError,TypeError,OverflowError) as exc:
                raise ValueError(f"{name}, запись {number}: {exc}") from exc
            record = [stamp,number,values,flags]
            buckets[stamp[:7]].write((store.packed(record)+"\n").encode())
            stats[station]+=1
        for month,b in buckets.items():
            partitions[station+"/"+month].append({"blob":store.put(gzip.compress(b.getvalue(),compresslevel=1,mtime=0)),"file":name})
    if not stats:
        raise ValueError("Нет строк наблюдений.")
    manifest = {"source":info["source"],"input":draft["filename"],"sha256":draft["raw_hash"],"method":"reserve-v1",
                "schema":"normalize-v1","mappings":config,"partitions":dict(partitions),"counts":dict(stats),
                "station_parameters":{s:sorted(p) for s,p in available.items()}}
    with store.db().begin() as con:
        store.execute(con,"INSERT INTO datasets (id,owner,title,version) VALUES (:d,:o,:t,:v) ON CONFLICT DO NOTHING",d=dataset,o=owner,t=title,v=version)
        store.execute(con,"INSERT INTO versions VALUES (:v,:d,:f,:now,:m) ON CONFLICT DO NOTHING",v=version,d=dataset,f=fingerprint,now=store.utcnow(),m=store.packed(manifest))
        store.execute(con,"UPDATE datasets SET title=:t,version=:v WHERE id=:d AND owner=:o",t=title,v=version,d=dataset,o=owner)
        store.execute(con,"INSERT INTO chart_sync VALUES (:d,1) ON CONFLICT(dataset) DO UPDATE SET due=1",d=dataset)
        if template:
            store.execute(con,"INSERT INTO templates VALUES (:o,:n,:m) ON CONFLICT(owner,name) DO UPDATE SET mappings=:m",o=owner,n=template,m=store.packed(config))
    return {"id":dataset,"version":version,"title":title,"count":sum(stats.values()),"repeated":False}
