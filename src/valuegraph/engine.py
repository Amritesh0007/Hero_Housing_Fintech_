"""Turn a model prediction into a lender-ready valuation: interval, factors, grade, route, LTV."""

import numpy as np
import pandas as pd

from .config import Settings
from .model import ValuationModel

# Optional property attributes passed straight to the model; anything missing is treated as unknown.
ATTRIBUTES = [
    "area_type",
    "bathrooms",
    "balconies",
    "floor",
    "total_floors",
    "parking",
    "parking_type",
    "view_garden",
    "view_pool",
    "view_main_road",
    "transaction",
    "furnishing",
    "facing",
    "ownership",
    "society",
]


def ltv_cap(loan_inr: int, tiers) -> float:
    return next(cap for limit, cap in tiers if loan_inr <= limit)


def grade_limits(m: ValuationModel, s: Settings) -> tuple[float, float]:
    return (
        s.high_max_width if s.high_max_width is not None else m.grade_widths.get("high", 0.0),
        s.medium_max_width if s.medium_max_width is not None else m.grade_widths.get("medium", 0.0),
    )


def grade(comp_count: int, width: float | None, reasons: list[str], s: Settings, limits: tuple[float, float]) -> str:
    if width is None or any(r in reasons for r in ("UNSUPPORTED_CITY", "UNKNOWN_LOCALITY", "OOD_INPUT", "NO_COMPS")):
        return "insufficient_evidence"
    if comp_count >= s.high_min_comps and width <= limits[0] and "GUIDELINE_DEVIATION" not in reasons:
        return "high"
    if comp_count >= s.medium_min_comps and width <= limits[1]:
        return "medium"
    return "low"


def route(reliability: str, loan_inr: int | None, s: Settings) -> str:
    if reliability == "high" and (loan_inr is None or loan_inr <= s.auto_max_loan_inr):
        return "auto"
    if reliability in ("high", "medium"):
        return "desktop_review"
    return "physical_inspection"


def value_property(m: ValuationModel, req: dict, s: Settings) -> dict:
    """req keys: city, locality, sqft (super built-up basis), bhk, any of ATTRIBUTES,
    optional guideline_rate_sqft (same basis), loan_amount_inr."""
    reasons: list[str] = []
    city = req["city"].strip().title()
    locality = req["locality"].strip().title()
    result = {
        "value": None,
        "low": None,
        "high": None,
        "rate_per_sqft": None,
        "factors": [],
        "base_value": None,
        "comp_count": 0,
        "comparables": [],
    }

    if city not in m.localities:
        reasons.append("UNSUPPORTED_CITY")
    elif locality not in m.localities[city]:
        reasons.append("UNKNOWN_LOCALITY")
    if not (m.sqft_range[0] <= req["sqft"] <= m.sqft_range[1]) or not (m.bhk_range[0] <= req["bhk"] <= m.bhk_range[1]):
        reasons.append("OOD_INPUT")

    if not reasons:
        row = pd.DataFrame(
            [
                {
                    "listing_id": -1,
                    "dup_group": -1,
                    "city": city,
                    "locality": locality,
                    "sqft": req["sqft"],
                    "bhk": req["bhk"],
                    **{a: req.get(a) for a in ATTRIBUTES},
                }
            ]
        )
        p = m.predict_frame(row).iloc[0]
        result["comp_count"] = int(p.comp_count)
        if p.comp_count == 0:
            reasons.append("NO_COMPS")
        else:
            base, factors = m.explain(row)
            comps = m.comps.comparables(city, locality, req["sqft"], req["bhk"])
            result.update(
                value=round(p.value),
                low=round(p.low),
                high=round(p.high),
                rate_per_sqft=round(p.rate),
                base_value=round(base),
                factors=factors,
                comparables=[
                    {
                        "listing_id": int(r.listing_id),
                        "locality": r.locality,
                        "society": r.society if isinstance(r.society, str) else None,
                        "sqft": round(r.sqft),
                        "bhk": int(r.bhk),
                        "floor": None if pd.isna(r.floor) else int(r.floor),
                        "furnishing": r.furnishing,
                        "price_inr": round(r.price_inr),
                        "rate_per_sqft": round(r.rate),
                        "weight": round(float(r.weight), 4),
                        "price_source": r.price_source,
                    }
                    for r in comps.itertuples()
                ],
            )
            if p.comp_count < s.medium_min_comps:
                reasons.append("FEW_COMPS")

    width = (result["high"] - result["low"]) / result["value"] if result["value"] else None
    limits = grade_limits(m, s)
    if width is not None and width > limits[1]:
        reasons.append("WIDE_INTERVAL")

    guideline = None
    if req.get("guideline_rate_sqft") and result["value"]:
        gval = round(req["guideline_rate_sqft"] * req["sqft"])
        dev = result["value"] / gval - 1
        guideline = {"rate_per_sqft": req["guideline_rate_sqft"], "value": gval, "deviation_pct": round(100 * dev, 1)}
        if abs(dev) > s.guideline_max_deviation:
            reasons.append("GUIDELINE_DEVIATION")

    reliability = grade(result["comp_count"], width, reasons, s, limits)
    loan = req.get("loan_amount_inr")
    lending = None
    if loan and result["low"]:
        cap = ltv_cap(loan, s.ltv_tiers)
        ltv = loan / result["low"]  # LTV on the LOW bound: conservative by design
        lending = {
            "ltv_basis": result["low"],
            "ltv_pct": round(100 * ltv, 1),
            "ltv_cap_pct": round(100 * cap, 1),
            "within_policy": ltv <= cap,
        }
        if ltv > cap:
            reasons.append("LTV_ABOVE_CAP")

    return {
        **result,
        "currency": "INR",
        "interval_level": m.level,
        "guideline": guideline,
        "reliability": reliability,
        # A loan above the LTV cap is a policy breach: never auto-approve it.
        "route": "desktop_review"
        if route(reliability, loan, s) == "auto" and "LTV_ABOVE_CAP" in reasons
        else route(reliability, loan, s),
        "reason_codes": reasons,
        "lending": lending,
        "basis": m.ground_truth,
        "model_version": m.version,
        **location_context(m, city, locality),
    }


def location_context(m: ValuationModel, city: str, locality: str) -> dict:
    """Approximate locality position and nearby localities' asking levels, for the map. Empty if not geocoded."""
    pos = m.comps.coords.get((city, locality))
    if pos is None:
        return {"location": None, "neighbourhood": []}
    nb = m.comps.neighbours(city, locality).head(15)
    return {
        "location": {"lat": pos[0], "lon": pos[1], "precision": "locality centroid (OpenStreetMap)"},
        "neighbourhood": [
            {
                "locality": r.locality,
                "lat": r.lat,
                "lon": r.lon,
                "distance_km": round(r.distance_km, 2),
                "median_rate_per_sqft": round(float(np.exp(r.median_lr))),
                "listings": int(r.n),
            }
            for r in nb.itertuples()
        ],
    }
