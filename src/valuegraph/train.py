"""Train, evaluate against baselines, and save the model + evaluation report.

Splits: a geographic holdout (whole ~5 km grid cells never seen in training) plus random
train / val / calib / test. The dataset has no listing dates, so a time split isn't possible;
add one as soon as dated data is available.
"""

import json
from datetime import date
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.model_selection import GroupShuffleSplit

from .config import Settings
from .engine import grade, grade_limits
from .model import CompIndex, ValuationModel, build_features, locality_counts


def metrics(actual: np.ndarray, pred: np.ndarray, low=None, high=None) -> dict:
    ape = np.abs(pred - actual) / actual
    out = {
        "n": len(actual),
        "mae_inr": float(np.mean(np.abs(pred - actual))),
        "mdape": float(np.median(ape)),
        "within_10pct": float(np.mean(ape <= 0.10)),
        "within_20pct": float(np.mean(ape <= 0.20)),
    }
    if low is not None:
        out["interval_coverage"] = float(np.mean((actual >= low) & (actual <= high)))
        out["median_interval_width"] = float(np.median((high - low) / pred))
    return out


def split(df: pd.DataFrame, seed: int = 0):
    def by_group(d, groups, size):
        a, b = next(GroupShuffleSplit(n_splits=1, test_size=size, random_state=seed).split(d, groups=groups))
        return d.iloc[a], d.iloc[b]

    # Whole localities held out = "areas the model never saw".
    rest, geo = by_group(df, df.city + "|" + df.locality, 0.1)
    # Re-listings of the same flat (dup_group) never straddle train and test.
    train, tmp = by_group(rest, rest.dup_group, 0.3)
    val, tmp = by_group(tmp, tmp.dup_group, 2 / 3)
    calib, test = by_group(tmp, tmp.dup_group, 0.5)
    return train, val, calib, test, geo


BASELINES = ("city_median_rate_x_area", "comparables_only", "linear_hedonic")


def baselines(train: pd.DataFrame, eval_df: pd.DataFrame, comps: CompIndex) -> dict[str, np.ndarray]:
    city_med = train.groupby("city").rate.median()
    out = {"city_median_rate_x_area": eval_df.city.map(city_med).to_numpy() * eval_df.sqft.to_numpy()}
    X_eval = build_features(eval_df, comps, exclude_self=False)
    comp_rate = np.exp(X_eval.comp_log_rate.to_numpy())
    out["comparables_only"] = (
        np.where(np.isnan(comp_rate), out["city_median_rate_x_area"] / eval_df.sqft.to_numpy(), comp_rate)
        * eval_df.sqft.to_numpy()
    )
    num = ["log_sqft", "bhk", "bathrooms", "floor", "view_garden", "view_pool", "view_main_road"]
    cats = ["city", "transaction", "furnishing"]
    Xtr = build_features(train, comps, exclude_self=True)

    def design(X):
        return pd.get_dummies(X[num + cats].fillna(dict.fromkeys(num, 0)), columns=cats, dtype=float)

    dtr = design(Xtr)
    lin = Ridge(alpha=1.0).fit(dtr, np.log(train.rate))
    out["linear_hedonic"] = (
        np.exp(lin.predict(design(X_eval).reindex(columns=dtr.columns, fill_value=0))) * eval_df.sqft.to_numpy()
    )
    return out


def run(
    listings: pd.DataFrame, model_dir: Path, *, coords=None, level=0.8, source="unknown", seed=0, iterations=4000
) -> dict:
    train, val, calib, test, geo = split(listings, seed)
    version = f"cb-{date.today():%Y%m%d}-{source}"
    m = ValuationModel.fit(
        train, val, calib, coords=coords, level=level, version=version, iterations=iterations, seed=seed
    )

    report = {
        "model_version": version,
        "data_source": source,
        "ground_truth": m.ground_truth,
        "ground_truth_note": "Asking prices, not transactions. These are asking-price prediction metrics, "
        "NOT fair-market valuation accuracy.",
        "splits": {
            k: len(v) for k, v in {"train": train, "val": val, "calib": calib, "test": test, "geo_holdout": geo}.items()
        },
        "blend_alpha": m.alpha,
        "twin_beta": m.beta,
        "grade_widths": m.grade_widths,
        "interval_level": level,
        "results": {},
    }
    for name, part in (("test", test), ("geo_holdout", geo)):
        p = m.predict_frame(part)
        actual = part.price_inr.to_numpy()
        res = {"valuegraph": metrics(actual, p.value.to_numpy(), p.low.to_numpy(), p.high.to_numpy())}
        for bname, bpred in baselines(train, part, m.comps).items():
            res[bname] = metrics(actual, bpred)
        seg = part.assign(ape=np.abs(p.value - part.price_inr) / part.price_inr)
        limits = grade_limits(m, Settings())
        seg["grade"] = [
            grade(int(c), w, [], Settings(), limits)
            for c, w in zip(p.comp_count, (p.high - p.low) / p.value, strict=True)
        ]
        seg["inside"] = (part.price_inr >= p.low) & (part.price_inr <= p.high)
        res["by_grade"] = [
            {
                "grade": g,
                "share": round(len(d) / len(seg), 4),
                "n": len(d),
                "mdape": round(float(d.ape.median()), 4),
                "within_10pct": round(float((d.ape <= 0.10).mean()), 4),
                "interval_coverage": round(float(d.inside.mean()), 4),
                "median_interval_width": round(float(((p.high - p.low) / p.value)[d.index].median()), 4),
            }
            for g, d in seg.groupby("grade")
            if len(d)
        ]
        res["beats_all_baselines_mdape"] = all(
            res["valuegraph"]["mdape"] < v["mdape"] for k, v in res.items() if k in BASELINES
        )
        res["by_city"] = seg.groupby("city").ape.agg(n="size", mdape="median").round(4).reset_index().to_dict("records")
        report["results"][name] = res
    m.metrics = report

    # Serve with comparables drawn from every clean listing, not just the training split.
    m.comps = CompIndex(listings, m.comps.k, coords)
    m.localities = locality_counts(listings)
    model_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(m, model_dir / "model.joblib", compress=3)
    (model_dir / "eval_report.json").write_text(json.dumps(report, indent=2))
    return report
