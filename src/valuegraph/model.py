"""Comparables + CatBoost + conformal intervals + rupee explanations.

Training and serving both go through `build_features` — one code path, no train/serve skew.
Target is log(₹/sqft) on the super built-up basis; value = area × exp(prediction).
Comparables are similar listings in the same city + locality. Where a locality has been geocoded, its position and
the price level of neighbouring localities (within NEIGHBOUR_KM, own locality excluded) are features too.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor, Pool
from sklearn.neighbors import BallTree

from .data import MISSING

NUMERIC = [
    "log_sqft",
    "bhk",
    "bathrooms",
    "balconies",
    "floor",
    "total_floors",
    "parking",
    "view_garden",
    "view_pool",
    "view_main_road",
    "locality_listings",
    "comp_log_rate",
    "comp_count",
    "comp_spread",
    "lat",
    "lon",
    "area_log_rate",
    "area_listings",
    "soc_log_rate",
    "soc_count",
    "twin_log_rate",
    "twin_count",
]
CAT_FEATURES = ["city", "locality", "area_type", "transaction", "furnishing", "facing", "ownership", "parking_type"]
FEATURES = CAT_FEATURES + NUMERIC
FACTOR_GROUPS = {
    "Locality": ["city", "locality", "locality_listings", "lat", "lon"],
    "Neighbourhood prices": ["area_log_rate", "area_listings"],
    "Nearby listing prices": ["comp_log_rate", "comp_count", "comp_spread"],
    "Same building / project": ["soc_log_rate", "soc_count", "twin_log_rate", "twin_count"],
    "Size": ["log_sqft", "area_type"],
    "Bedrooms & bathrooms": ["bhk", "bathrooms", "balconies"],
    "Floor": ["floor", "total_floors"],
    "Furnishing": ["furnishing"],
    "New vs resale": ["transaction"],
    "Parking": ["parking", "parking_type"],
    "Views": ["view_garden", "view_pool", "view_main_road"],
    "Facing": ["facing"],
    "Ownership": ["ownership"],
}
# Optional request fields; when blank, their SHAP effect is reported as "Details not given".
OPTIONAL_INPUTS = [
    "furnishing",
    "facing",
    "ownership",
    "parking_type",
    "bathrooms",
    "balconies",
    "floor",
    "total_floors",
    "parking",
]
SIMILAR = 0.25  # weight above which a listing counts as "similar" (≈ size within ±40% and BHK within 1)
NEIGHBOUR_KM = 3.0
EARTH_KM = 6371.0


class CompIndex:
    """Similar listings in the same locality, weighted by size and BHK similarity.
    shortcut: in-memory per process; move to the database when listings must update without a restart."""

    def __init__(self, listings: pd.DataFrame, k: int = 30, coords: pd.DataFrame | None = None):
        self.df = listings.reset_index(drop=True)
        self.k = k
        self.groups = {key: idx.to_numpy() for key, idx in self.df.groupby(["city", "locality"]).groups.items()}
        self.log_rate = np.log(self.df["rate"].to_numpy())
        self.sqft = self.df["sqft"].to_numpy(float)
        self.bhk = self.df["bhk"].to_numpy(float)
        self.dup = self.df["dup_group"].to_numpy()
        self.txn = self.df["transaction"].astype(str).to_numpy()
        # Same society (building / project) is the strongest evidence a listing can have; key = city + normalised name.
        soc = society_key(self.df)
        self.soc_groups = {k: idx.to_numpy() for k, idx in self.df.groupby(soc).groups.items() if k}
        # Locality positions (all geocoded localities, so unseen ones still get a position) and, for those with
        # listings here, their median asking level — the neighbourhood map.
        self.coords = (
            {}
            if coords is None
            else {
                (c, loc): (la, lo)
                for c, loc, la, lo in coords[["city", "locality", "lat", "lon"]].itertuples(index=False)
            }
        )
        stats = self.df.assign(lr=self.log_rate).groupby(["city", "locality"]).lr.agg(["median", "size"])
        self.loc = pd.DataFrame(
            [
                (c, loc, *self.coords[(c, loc)], r["median"], int(r["size"]))
                for (c, loc), r in stats.iterrows()
                if (c, loc) in self.coords
            ],
            columns=["city", "locality", "lat", "lon", "median_lr", "n"],
        )
        self.city_lr = self.df.assign(lr=self.log_rate).groupby("city").lr.median().to_dict()
        self.loc_tree = (
            BallTree(np.radians(self.loc[["lat", "lon"]].to_numpy()), metric="haversine") if len(self.loc) else None
        )

    def neighbours(self, city: str, locality: str, radius_km: float = NEIGHBOUR_KM) -> pd.DataFrame:
        """Other geocoded localities within radius_km (same city), nearest first, with their median ₹/sqft."""
        pos = self.coords.get((city, locality))
        if pos is None or self.loc_tree is None:
            return self.loc.iloc[[]].assign(distance_km=[])
        idx, dist = self.loc_tree.query_radius(np.radians([pos]), r=radius_km / EARTH_KM, return_distance=True)
        out = self.loc.iloc[idx[0]].assign(distance_km=dist[0] * EARTH_KM)
        out = out[(out.city == city) & (out.locality != locality)]
        return out.sort_values("distance_km")

    def area_features(self, df: pd.DataFrame) -> pd.DataFrame:
        out = pd.DataFrame(
            {"lat": np.nan, "lon": np.nan, "area_log_rate": np.nan, "area_listings": 0.0}, index=df.index
        )
        for (c, loc), q in df.groupby(["city", "locality"], sort=False):
            pos = self.coords.get((c, loc))
            if pos is None:
                continue
            out.loc[q.index, ["lat", "lon"]] = pos
            nb = self.neighbours(c, loc)
            if len(nb):
                w = np.exp(-nb.distance_km.to_numpy() / 1.5) * np.log1p(nb.n.to_numpy())
                out.loc[q.index, "area_log_rate"] = float((w * nb.median_lr).sum() / w.sum())
                out.loc[q.index, "area_listings"] = float(np.log1p(nb.n.sum()))
        return out

    def size(self, city: str, locality: str) -> int:
        return len(self.groups.get((city, locality), ()))

    def _weights(self, q: pd.DataFrame, cand: np.ndarray, exclude_dups: bool) -> np.ndarray:
        w = np.exp(-np.abs(np.log(q.sqft.to_numpy(float)[:, None] / self.sqft[cand])) / 0.35) * np.exp(
            -np.abs(q.bhk.to_numpy(float)[:, None] - self.bhk[cand]) / 1.5
        )
        if exclude_dups:  # a training row must not see itself or its re-listings
            w[q.dup_group.to_numpy()[:, None] == self.dup[cand]] = 0.0
        return w

    def features(self, df: pd.DataFrame, exclude_self: bool) -> pd.DataFrame:
        out = pd.DataFrame({"comp_log_rate": np.nan, "comp_count": 0, "comp_spread": np.nan}, index=df.index)
        for key, q in df.groupby(["city", "locality"], sort=False):
            cand = self.groups.get(key)
            if cand is None:
                continue
            for start in range(0, len(q), 500):  # chunk to bound memory in big localities
                qq = q.iloc[start : start + 500]
                w = self._weights(qq, cand, exclude_self)
                if w.shape[1] > self.k:
                    keep = np.argpartition(-w, self.k - 1, axis=1)[:, : self.k]
                    w = np.take_along_axis(w, keep, axis=1)
                    lr = self.log_rate[cand][keep]
                else:
                    lr = np.broadcast_to(self.log_rate[cand], w.shape)
                wsum = w.sum(1)
                with np.errstate(invalid="ignore", divide="ignore"):
                    mean = np.where(wsum > 0, (w * lr).sum(1) / wsum, np.nan)
                    out.loc[qq.index, "comp_log_rate"] = mean
                    # How much the similar listings disagree with each other (weighted std of log ₹/sqft).
                    out.loc[qq.index, "comp_spread"] = np.sqrt(
                        np.where(wsum > 0, (w * (lr - mean[:, None]) ** 2).sum(1) / wsum, np.nan)
                    )
                out.loc[qq.index, "comp_count"] = (w >= SIMILAR).sum(1)
        return out

    def society_features(self, df: pd.DataFrame, exclude_self: bool) -> pd.DataFrame:
        """Size-weighted mean log ₹/sqft of other listings in the same society (own re-listings excluded)."""
        out = pd.DataFrame(
            {"soc_log_rate": np.nan, "soc_count": 0, "twin_log_rate": np.nan, "twin_count": 0}, index=df.index
        )
        for key, q in df.groupby(society_key(df), sort=False):
            cand = self.soc_groups.get(key) if key else None
            if cand is None:
                continue
            w = self._weights(q, cand, False) + 1e-3  # any same-building listing counts, similar ones more
            if exclude_self:
                w[q.dup_group.to_numpy()[:, None] == self.dup[cand]] = 0.0
            n = (w > 0).sum(1)
            with np.errstate(invalid="ignore", divide="ignore"):
                out.loc[q.index, "soc_log_rate"] = np.where(n > 0, (w * self.log_rate[cand]).sum(1) / w.sum(1), np.nan)
            out.loc[q.index, "soc_count"] = n
            # Near-twins: same BHK, same sale type, size within a few percent — the tightest evidence there is.
            tw = np.exp(-np.abs(np.log(q.sqft.to_numpy(float)[:, None] / self.sqft[cand])) / 0.03)
            tw *= (q.bhk.to_numpy(float)[:, None] == self.bhk[cand]) & (
                q.transaction.astype(str).to_numpy()[:, None] == self.txn[cand]
            )
            tw[w == 0] = 0.0
            tw[tw < 0.3] = 0.0  # beyond ~±3.5% in size it is not a twin
            ts = tw.sum(1)
            with np.errstate(invalid="ignore", divide="ignore"):
                out.loc[q.index, "twin_log_rate"] = np.where(ts > 0, (tw * self.log_rate[cand]).sum(1) / ts, np.nan)
            out.loc[q.index, "twin_count"] = (tw > 0).sum(1)
        return out

    def comparables(self, city: str, locality: str, sqft: float, bhk: int, top: int = 10) -> pd.DataFrame:
        cand = self.groups.get((city, locality))
        if cand is None:
            return self.df.iloc[[]].assign(weight=[])
        q = pd.DataFrame({"sqft": [sqft], "bhk": [bhk]})
        out = self.df.iloc[cand].copy()
        out["weight"] = self._weights(q, cand, exclude_dups=False)[0]
        return out[out.weight >= SIMILAR].sort_values("weight", ascending=False).head(top)


def society_key(df: pd.DataFrame) -> pd.Series:
    if "society" not in df:
        return pd.Series("", index=df.index)
    name = df.society.fillna("").astype(str).str.lower().str.replace(r"[^a-z0-9]+", " ", regex=True).str.strip()
    return (df.city.astype(str) + "|" + name).where(name.str.len() > 2, "")


def build_features(df: pd.DataFrame, comps: CompIndex, exclude_self: bool) -> pd.DataFrame:
    X = pd.DataFrame(index=df.index)
    for c in CAT_FEATURES:
        X[c] = df[c].fillna(MISSING).astype(str) if c in df else MISSING
    X["log_sqft"] = np.log(df.sqft.to_numpy(float))
    for c in [
        "bhk",
        "bathrooms",
        "balconies",
        "floor",
        "total_floors",
        "parking",
        "view_garden",
        "view_pool",
        "view_main_road",
    ]:
        X[c] = pd.to_numeric(df[c], errors="coerce") if c in df else np.nan
    X["locality_listings"] = np.log1p([comps.size(c, loc) for c, loc in zip(df.city, df.locality, strict=False)])
    X = X.join(comps.features(df, exclude_self)).join(comps.area_features(df))
    X = X.join(comps.society_features(df, exclude_self))
    # No similar listings -> fall back to the best local level we have (neighbourhood, then city). Left blank,
    # CatBoost sends missing values down its lowest branch and undervalues unseen localities badly.
    X["comp_log_rate"] = X.comp_log_rate.fillna(X.area_log_rate).fillna(df.city.map(comps.city_lr))
    unseen = X.locality_listings == 0
    X.loc[unseen, "locality"] = MISSING  # same representation the model learned via locality dropout
    return X[FEATURES]


def hide_locality(X: pd.DataFrame, mask: np.ndarray, comps: "CompIndex", city: pd.Series) -> pd.DataFrame:
    """Make rows look like a locality the model has never seen (training-time locality dropout)."""
    X = X.copy()
    X.loc[mask, "locality"] = MISSING
    X.loc[mask, ["locality_listings", "comp_count", "soc_count", "twin_count"]] = 0
    X.loc[mask, ["comp_spread", "soc_log_rate", "twin_log_rate"]] = np.nan
    X.loc[mask, "comp_log_rate"] = X.loc[mask, "area_log_rate"].fillna(city[mask].map(comps.city_lr))
    return X[FEATURES]


def conformal_q(abs_resid: np.ndarray, level: float) -> float:
    """Split-conformal quantile with finite-sample correction."""
    n = len(abs_resid)
    rank = min(int(np.ceil((n + 1) * level)), n)
    return float(np.sort(abs_resid)[rank - 1])


def widest_meeting_target(width: np.ndarray, ape: np.ndarray, target: float, min_share: float = 0.02) -> float:
    """Largest width w such that ALL cases with width <= w have median error <= target (0.0 if none qualify)."""
    order = np.argsort(width)
    w, e = width[order], ape[order]
    best = 0.0
    for thr in np.unique(np.quantile(w, np.linspace(0, 1, 201), method="lower")):
        n = int(np.searchsorted(w, thr, side="right"))  # every case at or below the threshold, ties included
        if n >= max(30, min_share * len(w)) and np.median(e[:n]) <= target:
            best = float(thr)
    return best


def locality_counts(df: pd.DataFrame) -> dict[str, dict[str, int]]:
    return {c: g.locality.value_counts().to_dict() for c, g in df.groupby("city")}


@dataclass
class ValuationModel:
    cb: CatBoostRegressor
    alpha: float  # weight on model vs comparable estimate
    sigma: CatBoostRegressor | None  # predicts typical |log error| for a property (normalised conformal)
    q_norm: float  # conformal quantile of |error| / sigma on the calibration split
    grade_widths: dict[str, float]  # widest (high-low)/value that still meets each grade's accuracy target
    level: float
    localities: dict[str, dict[str, int]]  # city -> locality -> listing count
    sqft_range: tuple[float, float]
    bhk_range: tuple[int, int]
    version: str
    ground_truth: str = "asking_price_proxy"
    metrics: dict = field(default_factory=dict)
    comps: CompIndex | None = None
    beta: float = 1.0  # weight on the blend vs same-building near-twin listings, where a twin exists

    # ---- training -----------------------------------------------------------------------------
    @classmethod
    def fit(
        cls,
        train,
        val,
        calib,
        *,
        coords=None,
        level=0.8,
        version="dev",
        k=30,
        iterations=4000,
        seed=0,
        grade_targets=(("high", 0.10), ("medium", 0.15)),
    ):
        comps = CompIndex(train, k, coords)
        Xtr = build_features(train, comps, exclude_self=True)
        # 10% of rows trained as if their locality were unknown, so unseen localities aren't extrapolation.
        Xtr = hide_locality(Xtr, np.random.default_rng(seed).random(len(Xtr)) < 0.10, comps, train.city)
        Xva = build_features(val, comps, exclude_self=False)
        cb = CatBoostRegressor(
            iterations=iterations,
            learning_rate=0.05,
            depth=8,  # tuned on validation: 6 -> 8 gained ~0.2 pt; 10 and robust losses gained nothing more
            loss_function="RMSE",
            random_seed=seed,
            verbose=False,
            allow_writing_files=False,
        )
        cb.fit(
            Pool(Xtr, np.log(train.rate), cat_features=CAT_FEATURES),
            eval_set=Pool(Xva, np.log(val.rate), cat_features=CAT_FEATURES),
            early_stopping_rounds=100,
        )

        m = cls(
            cb=cb,
            alpha=1.0,
            sigma=None,
            q_norm=1.0,
            grade_widths={},
            level=level,
            localities=locality_counts(train),
            sqft_range=(float(train.sqft.quantile(0.01)), float(train.sqft.quantile(0.99))),
            bhk_range=(int(train.bhk.min()), int(train.bhk.max())),
            version=version,
            comps=comps,
        )

        # Blend weight chosen on validation (never on test).
        pm, pc = m._raw(Xva)
        y = np.log(val.rate.to_numpy())
        has = ~np.isnan(pc)
        grid = np.linspace(0, 1, 21)
        errs = [np.median(np.abs(y[has] - (a * pm[has] + (1 - a) * pc[has]))) for a in grid] if has.any() else [0]
        m.alpha = float(grid[int(np.argmin(errs))]) if has.any() else 1.0
        # Same for near-twins in the same building: the model alone shrinks this strongest evidence too far.
        pb, pt = m._blend_comps(Xva), Xva.twin_log_rate.to_numpy(float)
        tw = ~np.isnan(pt)
        if tw.any():
            errs = [np.median(np.abs(y[tw] - (b * pb[tw] + (1 - b) * pt[tw]))) for b in grid]
            m.beta = float(grid[int(np.argmin(errs))])

        # Uncertainty model: learn how wrong the blend tends to be for each kind of property (on validation),
        # so well-evidenced flats get tight ranges and unusual ones wide ranges.
        abs_err_va = np.abs(y - m._blend(Xva))
        m.sigma = CatBoostRegressor(
            iterations=max(100, iterations // 5),
            learning_rate=0.05,
            depth=4,
            loss_function="Quantile:alpha=0.5",
            random_seed=seed,
            verbose=False,
            allow_writing_files=False,
        )
        m.sigma.fit(Pool(Xva, abs_err_va, cat_features=CAT_FEATURES))

        # Normalised split-conformal on a separate calibration split: coverage holds whatever sigma learned.
        Xca = build_features(calib, comps, exclude_self=False)
        resid = np.log(calib.rate.to_numpy()) - m._blend(Xca)
        sig = m._sigma(Xca)
        m.q_norm = conformal_q(np.abs(resid) / sig, level)

        # Grade cut-offs: widest range at which the calibration cases still meet each accuracy target.
        width = 2 * np.sinh(m.q_norm * sig)  # (high - low) / value for a symmetric log interval
        ape = np.abs(np.exp(-resid) - 1)  # |estimate - actual| / actual
        m.grade_widths = {g: widest_meeting_target(width, ape, target) for g, target in grade_targets}
        return m

        # ---- inference ----------------------------------------------------------------------------

    def _raw(self, X):
        pm = self.cb.predict(Pool(X, cat_features=CAT_FEATURES))
        # Blend toward comparables only when there were real ones (comp_log_rate may be a fallback level).
        return pm, np.where(X.comp_count.to_numpy() > 0, X["comp_log_rate"].to_numpy(float), np.nan)

    def _blend_comps(self, X):
        pm, pc = self._raw(X)
        return np.where(np.isnan(pc), pm, self.alpha * pm + (1 - self.alpha) * pc)

    def _blend(self, X):
        pb, pt = self._blend_comps(X), X.twin_log_rate.to_numpy(float)
        return np.where(np.isnan(pt), pb, self.beta * pb + (1 - self.beta) * pt)

    def _sigma(self, X):
        return np.maximum(self.sigma.predict(Pool(X, cat_features=CAT_FEATURES)), 0.02)

    def predict_frame(self, df: pd.DataFrame, exclude_self=False) -> pd.DataFrame:
        X = build_features(df, self.comps, exclude_self)
        log_rate = self._blend(X)
        q = self.q_norm * self._sigma(X)
        sqft = df.sqft.to_numpy(float)
        return pd.DataFrame(
            {
                "value": sqft * np.exp(log_rate),
                "low": sqft * np.exp(log_rate - q),
                "high": sqft * np.exp(log_rate + q),
                "rate": np.exp(log_rate),
                "comp_count": X.comp_count.to_numpy(),
            },
            index=df.index,
        )

    def explain(self, df_one: pd.DataFrame) -> tuple[float, list[dict]]:
        """Return (base value, factors in ₹) for one row. Factors sum to value − base exactly."""
        X = build_features(df_one, self.comps, exclude_self=False)
        shap = self.cb.get_feature_importance(Pool(X, cat_features=CAT_FEATURES), type="ShapValues")[0]
        expected, contrib = shap[-1], dict(zip(FEATURES, shap[:-1], strict=False))
        # Inputs the user left blank: show their effect as one honest line instead of crediting e.g. "Furnishing".
        row = X.iloc[0]
        blank = {f for f in OPTIONAL_INPUTS if row[f] == MISSING or (not isinstance(row[f], str) and pd.isna(row[f]))}
        log_parts = {g: sum(contrib[f] for f in fs if f not in blank) for g, fs in FACTOR_GROUPS.items()}
        log_parts["Details not given"] = sum(contrib[f] for f in blank)
        pm, pc = self._raw(X)
        if not np.isnan(pc[0]):
            log_parts["Comparable listings adjustment"] = (1 - self.alpha) * (pc[0] - pm[0])
        pt = X.twin_log_rate.iloc[0]
        if not np.isnan(pt):
            log_parts["Near-identical flats in the building"] = (1 - self.beta) * (pt - self._blend_comps(X)[0])
        sqft = float(df_one.sqft.iloc[0])
        base = sqft * np.exp(expected)
        value = sqft * np.exp(expected + sum(log_parts.values()))
        total_log = sum(log_parts.values())
        # Allocate the rupee change in proportion to each factor's share of the log change.
        rupees = (
            {k: (value - base) * v / total_log for k, v in log_parts.items()}
            if abs(total_log) > 1e-9
            else {k: base * v for k, v in log_parts.items()}
        )
        factors = [{"label": k, "contribution_inr": round(v)} for k, v in rupees.items()]
        factors = [f for f in factors if f["contribution_inr"] != 0]
        factors.sort(key=lambda f: -abs(f["contribution_inr"]))
        return float(base), factors
