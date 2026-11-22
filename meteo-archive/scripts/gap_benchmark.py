import argparse
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eda.core import MeteoArchive, observation_rows


VERSION = "hourly-mask-v1"
LENGTHS = [1, 3, 6, 12, 24]
PARTNER = {"kr1": "kr2", "kr2": "kr1"}


def season(index):
    return (index.month % 12 // 3).map({0: "winter", 1: "spring", 2: "summer", 3: "autumn"})


def hourly_series(archive, station):
    source = archive.observations[station]
    rows = observation_rows(archive, station, "t_air", source.datetime.min(), source.datetime.max())
    rows = rows.loc[rows.valid]
    return rows.groupby(rows.datetime.dt.floor("h")).value.mean().sort_index()


def seasonal_lookup(training):
    groups = training.groupby([season(training.index), training.index.hour])
    center = groups.mean().where(groups.size().ge(20))
    aligned = center.reindex(pd.MultiIndex.from_arrays([season(training.index), training.index.hour]))
    residuals = pd.Series(np.abs(aligned.to_numpy() - training.to_numpy()), index=training.index)
    error = residuals.groupby([season(training.index), training.index.hour]).quantile(.90)
    return center, error


def lookup(values, index):
    return values.reindex(pd.MultiIndex.from_arrays([season(index), index.hour])).to_numpy()


def station_experiment(series, partner, code):
    final_year = int(series.index.max().year)
    start = pd.Timestamp(final_year, 1, 1)
    training = series.loc[series.index < start]
    holdout = series.loc[series.index >= start].asfreq("h")
    profile, uncertainty = seasonal_lookup(training)
    bias = error = None
    if partner is not None:
        overlap = training - partner.loc[partner.index < start].reindex(training.index)
        overlap = overlap.dropna()
        if len(overlap) >= 20:
            bias, error = seasonal_lookup(overlap)
    cold, hot = training.quantile([.05, .95])
    valid = holdout.notna()
    results = []
    for size in LENGTHS:
        candidates = valid.rolling(size + 2).sum().eq(size + 2)
        for label in ("winter", "spring", "summer", "autumn"):
            available = np.flatnonzero(candidates & (season(holdout.index) == label))
            if not len(available):
                continue
            generator = np.random.default_rng(20261122 + size)
            chosen = []
            for stop in generator.permutation(available):
                if all(abs(stop - old) >= size + 2 for old in chosen):
                    chosen.append(stop)
                if len(chosen) == 30:
                    break
            for stop in sorted(chosen):
                days = holdout.index[stop - size:stop]
                truth = holdout.iloc[stop - size:stop].to_numpy()
                left, right = holdout.iloc[[stop - size - 1, stop]]
                linear = np.linspace(left, right, size + 2)[1:-1] if size <= 6 else np.full(size, np.nan)
                methods = {"linear": (linear, np.full(size, np.nan)),
                           "season_hour": (lookup(profile, days), lookup(uncertainty, days))}
                if bias is not None:
                    methods["neighbor_adjusted"] = (partner.reindex(days).to_numpy() + lookup(bias, days), lookup(error, days))
                for name, (predicted, proxy) in methods.items():
                    results.extend({"station": code, "test_year": final_year, "season": label, "hours": size,
                                    "datetime": clock, "benchmark_truth": actual, "estimate": value,
                                    "training_residual_p90": typical_error, "extreme": actual <= cold or actual >= hot,
                                    "method": name, "version": VERSION, "layer": "experimental_masked_gap"}
                                   for clock, actual, value, typical_error in zip(days, truth, predicted, proxy))
    return results


def main():
    parser = argparse.ArgumentParser(description="Compare temperature gap methods using held-out real station observations.")
    parser.add_argument("archive", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    archive = MeteoArchive.read(args.archive)
    series = {station: hourly_series(archive, station) for station in archive.observations}
    runs = [record for station, values in series.items()
            for record in station_experiment(values, series.get(PARTNER.get(station)), station)]
    results = pd.DataFrame(runs)
    results["error"] = results.estimate - results.benchmark_truth
    results["absolute_error"] = results.error.abs()
    results["extreme_error"] = results.absolute_error.where(results.extreme)
    stats = {"points": ("estimate", "size"), "coverage": ("estimate", lambda values: values.notna().mean()),
             "mae_c": ("absolute_error", "mean"), "bias_c": ("error", "mean"),
             "extremes": ("extreme_error", "count"), "extreme_mae_c": ("extreme_error", "mean")}
    summary = results.groupby(["station", "test_year", "season", "hours", "method"]).agg(**stats).reset_index()
    args.out.mkdir(parents=True, exist_ok=True)
    results.to_csv(args.out / "estimates.csv", index=False)
    summary.to_csv(args.out / "metrics.csv", index=False)
    print(results.groupby(["hours", "method"]).agg(**stats).round(3).to_string())


if __name__ == "__main__":
    main()
