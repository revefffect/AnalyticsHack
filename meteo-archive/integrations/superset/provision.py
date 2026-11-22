"""Provision the Meteoarchive dashboard in a dedicated Superset instance."""
import json
from os import environ
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import duckdb
from superset.app import create_app


DASHBOARD_TITLE = "Метеоархив · исследование наблюдений"
DASHBOARD_SLUG = "meteo-archive"
PERIODS = "weather_periods"
MONTHS = "weather_comparison_months"
HOURS = "weather_comparison_year_hours"
COLUMN_LABELS = {
    "period_start": "Начало периода", "period_end": "Конец периода", "mean": "Среднее",
    "station_days": "Пригодно по станции", "metric_days": "Пригодно по показателю",
    "period_days": "Дней в периоде", "state": "Статус", "month_start": "Месяц",
    "delta_c": "Разница, °C", "pair_count": "Пар", "hour": "Час по архиву",
    "dataset_version": "Версия набора",
}


def asset_uuid(key):
    return uuid5(NAMESPACE_URL, f"meteoarchive.local/superset/{key}")


def aggregate(column, label):
    return {"expressionType": "SIMPLE", "column": {"column_name": column}, "aggregate": "MAX",
            "label": label, "optionName": f"meteo_{column}"}


def native_filters(periods_table, overview, comparison, station, metric, year):
    base = {"type": "NATIVE_FILTER", "description": "", "cascadeParentIds": [],
            "scope": {"rootPath": ["ROOT_ID"], "excluded": [chart.id for chart in comparison]},
            "chartsInScope": [chart.id for chart in overview], "tabsInScope": []}
    filters = []
    choices = (("station", "Станция", station), ("metric", "Показатель", metric), ("step", "Шаг", "month"))
    for field, label, value in choices:
        filters.append({**base, "id": f"NATIVE_FILTER-meteo-{field}", "name": label, "filterType": "filter_select",
                        "targets": [{"datasetId": periods_table.id, "column": {"name": field}}],
                        "controlValues": {"multiSelect": False, "enableEmptyFilter": False, "inverseSelection": False,
                                          "defaultToFirstItem": False, "searchAllOptions": False},
                        "defaultDataMask": {"extraFormData": {"filters": [{"col": field, "op": "IN", "val": [value]}]},
                                            "filterState": {"value": [value], "label": value}, "ownState": {}}})
    date_range = f"{year}-01-01 : {year + 1}-01-01"
    filters.append({**base, "id": "NATIVE_FILTER-meteo-date", "name": "Период", "filterType": "filter_time",
                    "targets": [{}], "controlValues": {"enableEmptyFilter": False},
                    "defaultDataMask": {"extraFormData": {"time_range": date_range},
                                        "filterState": {"value": date_range}, "ownState": {}}})
    return filters


def dashboard_layout(rows):
    grid = {"id": "GRID_ID", "type": "GRID", "parents": ["ROOT_ID"], "children": []}
    layout = {"DASHBOARD_VERSION_KEY": "v2", "ROOT_ID": {"id": "ROOT_ID", "type": "ROOT", "children": ["GRID_ID"]},
              "GRID_ID": grid, "HEADER_ID": {"id": "HEADER_ID", "type": "HEADER", "meta": {"text": DASHBOARD_TITLE}}}
    for offset, charts in enumerate(rows):
        row_key = f"ROW-meteo-{offset}"
        grid["children"].append(row_key)
        row = {"id": row_key, "type": "ROW", "parents": ["ROOT_ID", "GRID_ID"],
               "children": [], "meta": {"background": "BACKGROUND_TRANSPARENT"}}
        layout[row_key] = row
        for chart in charts:
            chart_key = f"CHART-meteo-{chart.id}"
            row["children"].append(chart_key)
            layout[chart_key] = {"id": chart_key, "type": "CHART", "parents": ["ROOT_ID", "GRID_ID", row_key],
                                 "children": [], "meta": {"chartId": chart.id, "width": 12 // len(charts), "height": 45,
                                                          "sliceName": chart.slice_name, "uuid": str(chart.uuid)}}
    return layout


def make_dashboard(path):
    from superset import db, security_manager
    from superset.connectors.sqla.models import SqlaTable
    from superset.models.core import Database
    from superset.models.dashboard import Dashboard
    from superset.models.slice import Slice

    with duckdb.connect(str(path), read_only=True) as mart:
        station, metric, year = mart.execute("""
            SELECT station, metric, date_part('year', period_start) AS period_year
            FROM weather_periods WHERE step = 'month'
            ORDER BY (metric = 'pressure_hpa' AND period_year = 2024) DESC,
                     (metric = 'pressure_hpa') DESC, station, period_year DESC, metric
            LIMIT 1
        """).fetchone()
        multiple_versions = bool(mart.execute("""
            SELECT 1 FROM weather_periods GROUP BY station_id HAVING count(DISTINCT dataset_version) > 1 LIMIT 1
        """).fetchone())
        table_names = {name for (name,) in mart.execute("SHOW TABLES").fetchall()}
        comparison_identity = None
        if {MONTHS, HOURS} <= table_names:
            comparison_identity = mart.execute(f"SELECT left_station, right_station, year FROM {MONTHS} LIMIT 1").fetchone()

    username = environ.get("METEO_SUPERSET_ADMIN", "admin")
    owner = security_manager.find_user(username=username)
    if owner is None:
        owner = security_manager.add_user(username, "Метео", "Архив", "operator@localhost.example",
                                          security_manager.find_role("Admin"),
                                          password=environ["METEO_SUPERSET_ADMIN_PASSWORD"])
        if not owner:
            raise RuntimeError("Could not create the Superset admin account.")
    database_id = asset_uuid("database")
    database = db.session.query(Database).filter_by(uuid=database_id).one_or_none()
    if database is None:
        database = Database(uuid=database_id, database_name="Метеоархив · витрина DuckDB")
    database.set_sqlalchemy_uri(f"duckdb:///{path}")
    database.extra = json.dumps({"engine_params": {"connect_args": {"read_only": True}}})
    database.expose_in_sqllab = False
    database.allow_dml = False
    db.session.add(database)
    db.session.flush()
    tables = {}
    for table_name in (PERIODS, MONTHS, HOURS) if comparison_identity else (PERIODS,):
        entity_id = asset_uuid(table_name)
        table = db.session.query(SqlaTable).filter_by(uuid=entity_id).one_or_none()
        if table is None:
            table = SqlaTable(uuid=entity_id, table_name=table_name, database=database, owners=[owner])
        table.fetch_metadata()
        for column in table.columns:
            column.verbose_name = COLUMN_LABELS.get(column.column_name)
        db.session.add(table)
        db.session.flush()
        tables[table_name] = table

    def chart(key, name, table_name, viz_type, params):
        table = tables[table_name]
        entity_id = asset_uuid(key)
        item = db.session.query(Slice).filter_by(uuid=entity_id).one_or_none()
        if item is None:
            item = Slice(uuid=entity_id, owners=[owner])
        item.slice_name = name
        item.datasource_id, item.datasource_type, item.datasource_name = table.id, "table", table.table_name
        item.viz_type = viz_type
        item.params = json.dumps({"datasource": f"{table.id}__table", "viz_type": viz_type,
                                  "time_range": "No filter", "adhoc_filters": [], "row_limit": 4000,
                                  "color_scheme": "supersetColors", "emit_filter": False, **params})
        db.session.add(item)
        db.session.flush()
        return item

    groupby = ["dataset_version"] if multiple_versions else []
    trend = chart("trend", "Наблюдения по периодам", PERIODS, "echarts_timeseries_line",
                  {"x_axis": "period_start", "granularity_sqla": "period_start", "metrics": [aggregate("mean", "Среднее")],
                   "groupby": groupby, "x_axis_time_format": "%Y-%m-%d", "y_axis_format": ".2f", "markerEnabled": True})
    quality = chart("quality", "Покрытие и решение по периоду", PERIODS, "table",
                    {"query_mode": "raw", "all_columns": ["period_start", "period_days", "station_days", "metric_days", "mean", "state", *groupby],
                     "server_page_length": 12, "table_timestamp_format": "%Y-%m-%d"})
    rows, comparison = [(trend, quality)], []
    if comparison_identity:
        left, right, comparison_year = comparison_identity
        subtitle = f"{left} − {right}, {comparison_year}"
        monthly = chart("months", f"Разница по месяцам · {subtitle}", MONTHS, "echarts_timeseries_bar",
                        {"x_axis": "month_start", "granularity_sqla": "month_start", "metrics": [aggregate("delta_c", "Разница, °C")],
                         "x_axis_time_format": "%m.%Y", "y_axis_format": ".2f"})
        hourly = chart("hours", f"Средний ход по часам · {subtitle}", HOURS, "echarts_timeseries_bar",
                       {"x_axis": "hour", "metrics": [aggregate("delta_c", "Разница, °C")],
                        "x_axis_title": "Час по времени архива", "x_axis_sort": "val", "x_axis_sort_asc": True, "y_axis_format": ".2f"})
        counts = chart("pairs", "Проверка сравнения · число совпадающих пар", HOURS, "table",
                       {"query_mode": "raw", "all_columns": ["hour", "delta_c", "pair_count"],
                        "server_page_length": 12, "order_by_cols": [json.dumps(["hour", True])]})
        comparison = [monthly, hourly, counts]
        rows.extend([(monthly, hourly), (counts,)])
    dashboard_id = asset_uuid("dashboard")
    dashboard = db.session.query(Dashboard).filter_by(uuid=dashboard_id).one_or_none()
    if dashboard is None:
        dashboard = Dashboard(uuid=dashboard_id, slug=DASHBOARD_SLUG, owners=[owner])
    dashboard.dashboard_title = DASHBOARD_TITLE
    dashboard.published = True
    dashboard.slices = [trend, quality, *comparison]
    dashboard.position_json = json.dumps(dashboard_layout(rows))
    dashboard.json_metadata = json.dumps({"native_filter_configuration": native_filters(
        tables[PERIODS], [trend, quality], comparison, station, metric, year), "chart_configuration": {}, "refresh_frequency": 0})
    db.session.add(dashboard)
    db.session.commit()
    print(dashboard.url)


if __name__ == "__main__":
    mart_path = Path(environ["METEO_DASHBOARD_MART"]).resolve(strict=True)
    with create_app().app_context():
        make_dashboard(mart_path)
