import numpy as np
import pandas as pd

from valuegraph.config import Settings
from valuegraph.data import clean, locality_from_title, make_synthetic, parse_area_sqft, parse_floor, parse_price_inr
from valuegraph.engine import grade, ltv_cap, value_property
from valuegraph.model import conformal_q

HYD = {
    "city": "Hyderabad",
    "locality": "Sector 3",
    "sqft": 1300,
    "bhk": 2,
    "area_type": "super_built_up",
    "transaction": "Resale",
    "furnishing": "Semi-Furnished",
    "floor": 4,
    "total_floors": 10,
}


def test_parsers():
    assert parse_price_inr("42 Lac ") == 4_200_000 and parse_price_inr("1.42 Cr") == 14_200_000
    assert parse_price_inr("Price on Request") is None
    assert parse_area_sqft("100 sqyrd") == 900 and parse_area_sqft("3 marla") is None
    assert parse_floor("Ground out of 4") == (0, 4) and parse_floor("10 out of 11") == (10, 11)
    assert (
        locality_from_title(
            "2 BHK Ready to Occupy Flat for sale in DLF Westend Heights Btm Layout", "DLF Westend Heights"
        )
        == "Btm Layout"
    )
    assert locality_from_title("2 BHK Ready to Occupy Flat for sale Nava Vadaj", None) == "Nava Vadaj"


def test_clean_dedups_and_normalises_area():
    raw = make_synthetic(800, seed=3)
    raw = pd.concat([raw, raw.iloc[:50]])  # exact repeats, like the real scrape
    raw.loc[raw.index[5], "Amount(in rupees)"] = "Price on Request"
    df, rep = clean(raw, min_city_rows=10)
    assert rep["duplicate"] == 50 and rep["no_price"] >= 1
    carpet = df[df.area_type == "carpet"].iloc[0]
    assert abs(carpet.sqft - carpet.area_value * 1.35) < 1e-6  # carpet scaled to super built-up
    assert df.price_source.eq("asking").all()


def test_conformal_quantile_is_finite_sample_correct():
    r = np.arange(1, 11, dtype=float)  # n=10, level .8 -> rank ceil(11*.8)=9
    assert conformal_q(r, 0.8) == 9


def test_report_is_honest_and_calibrated(trained):
    rep = trained[2]
    assert rep["ground_truth"] == "asking_price_proxy" and "NOT fair-market" in rep["ground_truth_note"]
    cov = rep["results"]["test"]["valuegraph"]["interval_coverage"]
    assert abs(cov - 0.8) < 0.07, cov
    assert rep["results"]["test"]["valuegraph"]["mdape"] < rep["results"]["test"]["city_median_rate_x_area"]["mdape"]


def test_training_comps_exclude_own_relistings(model, trained):
    df = trained[1].head(200)
    with_self = model.comps.features(df.assign(dup_group=-1), exclude_self=False)
    without = model.comps.features(df, exclude_self=True)
    assert (without.comp_count.to_numpy() <= with_self.comp_count.to_numpy()).all()
    assert (without.comp_count.to_numpy() < with_self.comp_count.to_numpy()).any()


def test_bigger_flat_is_worth_more(model):
    s = Settings()
    small = value_property(model, HYD, s)["value"]
    big = value_property(model, {**HYD, "sqft": 1430}, s)["value"]
    assert big > small


def test_factors_add_up_to_value(model):
    r = value_property(model, HYD, Settings())
    assert abs(r["base_value"] + sum(f["contribution_inr"] for f in r["factors"]) - r["value"]) <= len(r["factors"])
    assert r["low"] < r["value"] < r["high"]
    assert r["comparables"] and all(c["price_source"] == "asking" for c in r["comparables"])


def test_refuses_out_of_distribution_and_unknown_city(model):
    s = Settings()
    assert value_property(model, {**HYD, "sqft": 60_000}, s)["reliability"] == "insufficient_evidence"
    r = value_property(model, {**HYD, "city": "Patna"}, s)
    assert r["value"] is None and "UNSUPPORTED_CITY" in r["reason_codes"] and r["route"] == "physical_inspection"
    nowhere = value_property(model, {**HYD, "locality": "Atlantis"}, s)
    assert nowhere["reliability"] == "insufficient_evidence" and "UNKNOWN_LOCALITY" in nowhere["reason_codes"]


def test_policy_rules():
    s, LIM = Settings(), (0.3, 0.45)
    assert grade(10, 0.4, [], s, LIM) == "medium" and grade(10, 0.5, [], s, LIM) == "low"
    assert grade(10, 0.2, [], s, LIM) == "high"
    assert grade(5, 0.2, [], s, LIM) == "medium"
    assert grade(2, 0.2, [], s, LIM) == "low"
    assert grade(10, 0.2, ["GUIDELINE_DEVIATION"], s, LIM) == "medium"
    assert grade(0, None, ["NO_COMPS"], s, LIM) == "insufficient_evidence"
    assert ltv_cap(2_500_000, s.ltv_tiers) == 0.90 and ltv_cap(5_000_000, s.ltv_tiers) == 0.80
    assert ltv_cap(9_000_000, s.ltv_tiers) == 0.75


def test_ltv_uses_low_bound(model):
    r = value_property(model, {**HYD, "loan_amount_inr": 3_000_000}, Settings())
    assert r["lending"]["ltv_basis"] == r["low"]


def test_ltv_breach_is_never_auto(model):
    lenient = Settings(high_min_comps=1, high_max_width=10, medium_max_width=10, auto_max_loan_inr=10**12)
    ok = value_property(model, {**HYD, "loan_amount_inr": 1_000_000}, lenient)
    assert ok["reliability"] == "high" and ok["route"] == "auto"
    breach = value_property(model, {**HYD, "loan_amount_inr": ok["low"]}, lenient)  # 100% LTV
    assert not breach["lending"]["within_policy"] and breach["route"] == "desktop_review"


def test_society_prefix_is_stripped_from_locality():
    from valuegraph.data import resolve_localities

    city = pd.Series(["Hyderabad"] * 3)
    raw = pd.Series(["Gachibowli", "Prestige High Fields Gachibowli", "Kokapet, Outer Ring Road"])
    out = resolve_localities(city, raw, trusted=pd.Series([True, False, False]))
    assert out.tolist() == ["Gachibowli", "Gachibowli", "Kokapet"]


def test_grades_are_calibrated_and_hold_out_of_sample(model, trained):
    from valuegraph.model import widest_meeting_target

    w = np.array([0.1, 0.2, 0.3, 0.4] * 25)
    e = np.array([0.05, 0.08, 0.2, 0.3] * 25)
    assert abs(widest_meeting_target(w, e, 0.10, min_share=0.0) - 0.3) < 1e-9  # 75% of cases, median 8%
    assert model.grade_widths["high"] <= model.grade_widths["medium"]
    by_grade = trained[2]["results"]["test"]["by_grade"]
    assert by_grade, "evaluation must report accuracy per grade"


def test_geocode_cleaning_drops_fallbacks():
    from valuegraph.data import clean_geocodes

    raw = pd.DataFrame(
        [
            ["Noida", "Sector 50", 28.571, 77.367, "residential"],
            ["Noida", "Sector 62", 28.621, 77.364, "suburb"],
            ["Noida", "Sector 61", 28.596, 77.368, "residential"],
            ["Noida", "Sector 74", 28.4712, 77.5313, "yes"],  # three sectors on one Greater Noida point
            ["Noida", "Sector 75", 28.4716, 77.5313, "yes"],
            ["Noida", "Sector 78", 28.4712, 77.5308, "yes"],
            ["Noida", "Sector 2", 28.535, 77.391, "city"],  # whole-city fallback
            ["Noida", "Far Road", 29.0, 77.9, "primary"],  # >25 km out
        ],
        columns=["city", "locality", "lat", "lon", "osm_type"],
    )
    out, rep = clean_geocodes(raw)
    assert sorted(out.locality) == ["Sector 50", "Sector 61", "Sector 62"]
    assert rep["dropped_city_fallback"] == 1 and rep["dropped_shared_point"] == 3 and rep["dropped_far_from_city"] == 1


def test_neighbourhood_excludes_own_locality(model):
    nb = model.comps.neighbours("Hyderabad", "Sector 3")
    assert len(nb) and "Sector 3" not in set(nb.locality) and (nb.distance_km <= 3.0).all()
    assert nb.distance_km.is_monotonic_increasing
    f = model.comps.area_features(pd.DataFrame({"city": ["Hyderabad"], "locality": ["Sector 3"]}))
    assert f.lat.notna().all() and f.area_log_rate.notna().all()
    nowhere = model.comps.area_features(pd.DataFrame({"city": ["Hyderabad"], "locality": ["Atlantis"]}))
    assert nowhere.lat.isna().all()


def test_unseen_localities_not_worse_than_city_average(trained):
    # Regression: blank comparable features once sent unseen localities down CatBoost's lowest branch.
    geo = trained[2]["results"]["geo_holdout"]
    assert geo["valuegraph"]["mdape"] <= geo["city_median_rate_x_area"]["mdape"] * 1.05


def test_same_society_listings_are_used_but_never_the_flat_itself():
    from valuegraph.model import CompIndex

    df = pd.DataFrame(
        {
            "city": ["Pune"] * 4,
            "locality": ["Baner"] * 4,
            "society": ["Green Heights", "green  heights!", "Green Heights", None],
            "sqft": [1000.0, 1050.0, 1000.0, 1000.0],
            "bhk": [2, 2, 2, 2],
            "rate": [10_000.0, 12_000.0, 99_000.0, 5_000.0],
            "dup_group": [1, 2, 3, 4],
            "transaction": ["Resale"] * 4,
        }
    )
    f = CompIndex(df).society_features(df, exclude_self=True)
    assert f.twin_count.tolist() == [1, 0, 1, 0]  # 1000 sqft rows 0 and 2 are twins; 1050 sqft is 5% off
    assert f.soc_count.tolist() == [2, 2, 2, 0]  # names normalised; own row excluded; no society -> no match
    assert np.isnan(f.soc_log_rate.iloc[3])
    assert np.exp(f.soc_log_rate.iloc[2]) < 12_000  # row 2's 99k typo-price only sees rows 0 and 1


def test_blank_inputs_are_reported_as_details_not_given(model):
    sparse = {k: v for k, v in HYD.items() if k not in ("furnishing", "floor", "total_floors")}
    r = value_property(model, sparse, Settings())
    labels = {f["label"] for f in r["factors"]}
    assert "Furnishing" not in labels and "Floor" not in labels and "Details not given" in labels
    assert abs(r["base_value"] + sum(f["contribution_inr"] for f in r["factors"]) - r["value"]) <= len(r["factors"])
