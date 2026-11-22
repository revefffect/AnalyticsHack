"""Fill the nature404 station calendar using its prepared donor and ERA5-Land exports."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor


GRID_FIELDS = ["soil_temperature_0_to_7cm_mean", "soil_temperature_7_to_28cm_mean",
               "temperature_2m_mean"]
KEY = ["station", "parameter", "date"]


def weather_features(path):
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if any(payload["daily_units"][field] != "°C" for field in GRID_FIELDS):
        raise ValueError("ERA5-Land temperatures must be in °C")
    weather = pd.DataFrame(payload["daily"]).set_index("time")[GRID_FIELDS]
    weather.index = pd.to_datetime(weather.index)
    weather = weather.sort_index().asfreq("D").replace([np.inf, -np.inf], np.nan)
    features = pd.concat([weather.shift(lag).add_suffix(f"_lag{lag}") for lag in range(8)], axis=1)
    phase = 2 * np.pi * features.index.dayofyear / 365.25
    features["season_sin"], features["season_cos"] = np.sin(phase), np.cos(phase)
    return features.dropna()


def fill_daily(analysis):
    fields = KEY + ["statistic", "unit", "value", "value_origin", "source_id", "method"]
    daily = pd.read_csv(analysis / "service/daily.csv", usecols=fields, parse_dates=["date"])
    daily = daily.set_index(KEY, verify_integrity=True).sort_index()
    sources = pd.read_csv(analysis / "gapfill/daily_sources.csv", parse_dates=["date"])
    selected = sources.loc[sources.selected.eq(1)].set_index(KEY, verify_integrity=True)
    # Reapply the selected donors to the measured calendar, preserving other service estimates.
    eligible = daily.value_origin.isin(["missing", "restored"]) & daily.statistic.eq("mean")
    keys = daily.index[eligible].intersection(selected.index)
    daily.loc[keys, ["value", "source_id", "method"]] = selected.loc[
        keys, ["estimate", "source_id", "method"]].to_numpy()
    daily.loc[keys, "value_origin"] = "restored"

    features = weather_features(analysis / "external/era5_land_soil_1992_2021.json")
    for depth in ("soil_05", "soil_10", "soil_20"):
        soil = daily.loc[("AME60", depth), :].sort_index().asfreq("D")
        truth = soil.value.where(soil.value_origin.eq("measured"))
        missing = soil.index[soil.value_origin.eq("missing")]
        trend = (truth.shift(1) + (truth.shift(1) - truth.shift(4)) / 3).where(
            truth.shift(1).rolling(4).count().eq(4))
        first = missing.intersection(trend.dropna().index)
        remaining = missing.difference(first).intersection(features.index)
        training = truth.dropna().index.intersection(features.index)
        estimates = [(first, trend.reindex(first), "AME60", "soil_trend_1d")]
        if len(remaining) and len(training):
            forest = RandomForestRegressor(n_estimators=100, min_samples_leaf=5, random_state=42)
            forest.fit(features.loc[training], truth.loc[training])
            estimates.append((remaining, forest.predict(features.loc[remaining]),
                              "ERA5-Land", "era5_land_random_forest"))
        for dates, values, source, method in estimates:
            if len(dates):
                rows = pd.MultiIndex.from_product([["AME60"], [depth], dates], names=KEY)
                daily.loc[rows, "value"] = np.asarray(values)
                daily.loc[rows, ["value_origin", "source_id", "method"]] = ["restored", source, method]
    return daily


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nature404", type=Path, required=True, help="Root of the prepared nature404 project")
    parser.add_argument("--output", type=Path, required=True, help="New output directory")
    args = parser.parse_args()
    project = args.nature404.resolve()
    dest = args.output.resolve()
    if dest.exists():
        parser.error("Choose a new output directory")
    daily = fill_daily(project / "analysis")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=dest.parent) as tmp:
        batch = Path(tmp) / "results"
        batch.mkdir()
        daily.to_csv(batch / "daily.csv", encoding="utf-8-sig")
        # Reuse nature404's QC, anchor checks, and validated 12-slot interpolation limit.
        subprocess.run([sys.executable, "-c",
                        "import sys; from build_subdaily_gap_dataset import write_subdaily; "
                        "print(write_subdaily(sys.argv[1]))", str(batch / "air_15min_fills.csv")],
                       cwd=project / "scripts", check=True)
        os.rename(batch, dest)


if __name__ == "__main__":
    main()
