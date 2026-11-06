"""Load and clean listing data.

Source: "India House Price" listings (house_prices.csv — Kaggle, ~187k scraped rows, 81 cities).
Prices are ASKING prices — never treat them as transaction prices. The file has no coordinates,
so location = city + locality parsed from the listing title.
"""

import re
from pathlib import Path

import numpy as np
import pandas as pd

RAW_COLUMNS = [
    "Title",
    "Amount(in rupees)",
    "location",
    "Carpet Area",
    "Super Area",
    "Floor",
    "Transaction",
    "Furnishing",
    "facing",
    "overlooking",
    "Society",
    "Bathroom",
    "Balcony",
    "Car Parking",
    "Ownership",
]
# Area bases, expressed as multipliers to super built-up (the basis comparables and the model use).
# shortcut: one national loading factor; make per-city and valuer-approved before pilot.
LOADING = {"super_built_up": 1.0, "built_up": 1.20, "carpet": 1.35}
UNIT_SQFT = {"sqft": 1.0, "sqyrd": 9.0, "sqm": 10.7639}
MIN_SQFT, MAX_SQFT = 200, 10_000  # super built-up equivalent
MIN_RATE, MAX_RATE = 1_000, 100_000  # ₹/sqft sanity band
CATEGORICAL = ["city", "locality", "area_type", "transaction", "furnishing", "facing", "ownership", "parking_type"]
MISSING = "__missing__"


def read_raw(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path, low_memory=False)
    missing = set(RAW_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"{path}: missing columns {sorted(missing)} — expected the India House Price listings file")
    return df


def parse_price_inr(text) -> float | None:
    """'42 Lac' -> 4_200_000, '1.42 Cr' -> 14_200_000, 'Price on Request' -> None."""
    m = re.fullmatch(r"\s*([\d.,]+)\s*(lac|lakh|l|cr|crore)s?\s*", str(text), re.I)
    if not m:
        return None
    try:
        n = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return n * (1e7 if m.group(2).lower().startswith("c") else 1e5)


def parse_area_sqft(text) -> float | None:
    m = re.fullmatch(r"\s*([\d.,]+)\s*([a-z]+)\s*", str(text), re.I)
    if not m or m.group(2).lower() not in UNIT_SQFT:
        return None
    try:
        return float(m.group(1).replace(",", "")) * UNIT_SQFT[m.group(2).lower()]
    except ValueError:
        return None


def parse_floor(text) -> tuple[float, float]:
    """'10 out of 11' -> (10, 11); 'Ground out of 4' -> (0, 4); basements negative; unknown -> NaN."""
    named = {"ground": 0, "upper basement": -1, "lower basement": -2}
    parts = str(text).lower().split(" out of ")

    def num(s):
        s = s.strip()
        return named.get(s, float(s) if s.replace(".", "", 1).isdigit() else np.nan)

    return (num(parts[0]), num(parts[1]) if len(parts) == 2 else np.nan) if text == text else (np.nan, np.nan)


def locality_from_title(title: str, society) -> str | None:
    """'2 BHK Ready to Occupy Flat for sale in DLF Westend Heights Btm Layout' (society 'DLF Westend Heights')
    -> 'Btm Layout'; '2 BHK Ready to Occupy Flat for sale Nava Vadaj' -> 'Nava Vadaj'."""
    m = re.search(r"for sale (?:in )?(.+)$", str(title))
    if not m:
        return None
    rest = m.group(1).strip()
    if isinstance(society, str) and society.strip() and rest.lower().startswith(society.strip().lower()):
        rest = rest[len(society.strip()) :].strip()
    return rest.title() or None


def resolve_localities(city: pd.Series, raw: pd.Series, trusted: pd.Series) -> pd.Series:
    """Titles without a Society field read '<society> <locality>'. Strip the society by matching the longest
    trailing words against localities seen in trusted rows (society known, or no society in the title)."""
    base = raw.str.split(",").str[0].str.strip()  # 'Kokapet, Outer Ring Road' -> 'Kokapet'
    vocab: dict[str, set[str]] = {}
    for c, loc in zip(city[trusted], base[trusted], strict=True):
        vocab.setdefault(c, set()).add(loc)

    def resolve(c: str, loc: str) -> str:
        words = loc.split()
        for n in range(1, min(len(words), 4) + 1)[::-1]:  # longest suffix first
            tail = " ".join(words[-n:])
            if tail in vocab.get(c, ()):
                return tail
        return loc

    return pd.Series([resolve(c, loc) for c, loc in zip(city, base, strict=True)], index=raw.index)


def _count(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.replace("> 10", "11").str.extract(r"(\d+)")[0], errors="coerce")


def clean(raw: pd.DataFrame, min_city_rows: int = 300) -> tuple[pd.DataFrame, dict]:
    """Return (clean listings, report of rows removed per step)."""
    df = raw[RAW_COLUMNS].copy()
    report: dict[str, int] = {"input": len(df)}

    def step(name: str, mask: pd.Series):
        nonlocal df
        report[name] = int((~mask).sum())
        df = df[mask]

    # The scrape repeats the same listing many times.
    step("duplicate", ~df.duplicated())

    df["price_inr"] = df["Amount(in rupees)"].map(parse_price_inr)
    step("no_price", df.price_inr.notna())

    carpet = df["Carpet Area"].map(parse_area_sqft)
    sbu = df["Super Area"].map(parse_area_sqft)
    df["area_type"] = np.where(carpet.notna(), "carpet", np.where(sbu.notna(), "super_built_up", None))
    df["area_value"] = carpet.fillna(sbu)
    step("no_area", df.area_value.notna())
    df["sqft"] = df.area_value * df.area_type.map(LOADING)
    df["rate"] = df.price_inr / df.sqft

    df["city"] = df["location"].astype(str).str.replace("-", " ").str.title()
    df["locality"] = [locality_from_title(t, s) for t, s in zip(df.Title, df.Society, strict=True)]
    step("no_locality", df.locality.notna())
    df["locality"] = resolve_localities(
        df.city, df.locality, trusted=df.Society.notna() | ~df.Title.str.contains(" for sale in ")
    )
    df["bhk"] = pd.to_numeric(df.Title.astype(str).str.extract(r"^(\d+) BHK")[0], errors="coerce")
    step("no_bhk", df.bhk.between(1, 8))

    floors = df.Floor.map(parse_floor)
    df["floor"] = [f for f, _ in floors]
    df["total_floors"] = [t for _, t in floors]
    df["bathrooms"] = _count(df.Bathroom).clip(upper=8)
    df["balconies"] = _count(df.Balcony).clip(upper=6)
    df["parking"] = _count(df["Car Parking"]).clip(upper=5)  # values like "34 Covered" are society totals
    df["parking_type"] = df["Car Parking"].astype(str).str.extract(r"(Covered|Open)")[0]
    ov = df.overlooking.fillna("")
    for flag, word in (("view_garden", "Garden"), ("view_pool", "Pool"), ("view_main_road", "Main Road")):
        df[flag] = ov.str.contains(word).astype(int)
    df["transaction"] = df.Transaction
    df["furnishing"] = df.Furnishing
    df["facing"] = df.facing
    df["ownership"] = df.Ownership
    df["society"] = df.Society

    step("area_out_of_range", df.sqft.between(MIN_SQFT, MAX_SQFT))
    step("rate_out_of_range", df.rate.between(MIN_RATE, MAX_RATE))
    step("not_a_sale", df.transaction.isin(["Resale", "New Property"]))
    step("floor_above_total", ~(df.floor > df.total_floors))

    # Near-duplicates: the same flat listed by several agents at slightly different prices.
    key = (
        df.city
        + "|"
        + df.locality
        + "|"
        + df.bhk.astype(int).astype(str)
        + "|"
        + df.floor.fillna(-99).astype(int).astype(str)
        + "|"
        + (df.sqft / 25).round().astype(int).astype(str)
    )
    df["dup_group"] = pd.factorize(key)[0]
    step("near_duplicate", ~df.duplicated(["dup_group", "price_inr"]))

    # Per-city robust outliers on log ₹/sqft (median ± 4 MAD).
    lr = np.log(df.rate)
    med = lr.groupby(df.city).transform("median")
    mad = (lr - med).abs().groupby(df.city).transform("median").clip(lower=0.05)
    step("rate_outlier", (lr - med).abs() <= 4 * 1.4826 * mad)
    step("thin_city", df.city.map(df.city.value_counts()) >= min_city_rows)

    df = df.reset_index(drop=True)
    df.insert(0, "listing_id", np.arange(len(df)))
    df["price_source"] = "asking"
    for c in CATEGORICAL:
        df[c] = df[c].fillna(MISSING).astype(str)
    report["output"] = len(df)
    keep = [
        "listing_id",
        "dup_group",
        "city",
        "locality",
        "society",
        "area_type",
        "area_value",
        "sqft",
        "bhk",
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
        "price_inr",
        "rate",
        "price_source",
    ]
    return df[keep], report


# --- Synthetic sample ---------------------------------------------------------------------------
# For tests and offline demos ONLY, in the same raw schema. Never report metrics on this as accuracy.
# Real Indian cities + median ₹/sqft (approximate asking prices) so the demo looks realistic.
_CITIES = {
    "Bangalore": 8000, "Mumbai": 22000, "Chennai": 7500, "Hyderabad": 7000, "Pune": 8500,
    "New Delhi": 11000, "Kolkata": 5500, "Gurgaon": 9500, "Noida": 6000, "Greater Noida": 4500,
    "Ghaziabad": 4000, "Faridabad": 4500, "Navi Mumbai": 9000, "Thane": 8500, "Lucknow": 4000,
    "Jaipur": 4500, "Ahmedabad": 5500, "Chandigarh": 7000, "Mohali": 5500, "Kochi": 6500,
    "Coimbatore": 5000, "Goa": 9000, "Dehradun": 5500, "Mangalore": 5500, "Patna": 3500,
    "Raipur": 3500, "Nagpur": 4500, "Vizag": 4500, "Indore": 4000, "Bhopal": 3800,
    "Guwahati": 4000, "Kanpur": 3500, "Jamshedpur": 4000, "Kalyan": 6500, "Palghar": 4500,
}

# Real locality names per city (representative neighbourhoods from the Indian housing market).
_LOCALITIES: dict[str, list[str]] = {
    "Bangalore": [
        "Whitefield", "Sarjapur Road", "Hebbal", "Kanakapura Road", "Yelahanka",
        "Electronic City", "Marathahalli", "Bannerghatta Road", "Hennur", "Devanahalli",
        "Thanisandra", "Kr Puram", "Jp Nagar", "Hsr Layout", "Bellandur",
        "Rajaji Nagar", "Koramangala", "Btm Layout", "Jayanagar", "Banashankari",
    ],
    "Mumbai": [
        "Andheri West", "Borivali East", "Goregaon West", "Kandivali East", "Malad West",
        "Thane West", "Mulund West", "Bandra West", "Powai", "Dahisar East",
        "Vikhroli East", "Chembur", "Jogeshwari West", "Ghatkopar East", "Worli",
        "Lower Parel", "Juhu", "Versova", "Santacruz West", "Vile Parle East",
    ],
    "Chennai": [
        "Velachery", "Porur", "Tambaram", "Pallavaram", "Chromepet",
        "Sholinganallur", "Perambur", "Medavakkam", "Guduvanchery", "Kelambakkam",
        "Anna Nagar", "T Nagar", "Adyar", "Mylapore", "Thiruvanmiyur",
        "Kodambakkam", "Perungudi", "Madipakkam", "Pallikaranai", "Nanganallur",
    ],
    "Hyderabad": [
        "Gachibowli", "Kondapur", "Kukatpally", "Miyapur", "Bachupally",
        "Kompally", "Kokapet", "Manikonda", "Narsingi", "Tellapur",
        "Uppal", "Lb Nagar", "Dilsukhnagar", "Begumpet", "Secunderabad",
        "Madhapur", "Hitech City", "Banjara Hills", "Jubilee Hills", "Ameerpet",
    ],
    "Pune": [
        "Hinjewadi", "Wakad", "Baner", "Kharadi", "Wagholi",
        "Hadapsar", "Undri", "Punawale", "Tathawade", "Ravet",
        "Bavdhan", "Kothrud", "Aundh", "Viman Nagar", "Koregaon Park",
        "Pimpri", "Chinchwad", "Dhanori", "Nibm Road", "Kondhwa",
    ],
    "New Delhi": [
        "Dwarka", "Rohini", "Janakpuri", "Saket", "Vasant Kunj",
        "Greater Kailash", "Pitampura", "Paschim Vihar", "Lajpat Nagar", "Chattarpur",
        "Nehru Place", "Hauz Khas", "Malviya Nagar", "Defence Colony", "Karol Bagh",
        "Rajouri Garden", "Mayur Vihar", "Preet Vihar", "Vikaspuri", "Uttam Nagar",
    ],
    "Kolkata": [
        "Rajarhat", "Garia", "Tollygunge", "Behala", "Dum Dum",
        "Salt Lake", "New Town", "Jadavpur", "Lake Town", "Ballygunge",
        "Howrah", "Baranagar", "Baguiati", "Kaikhali", "Narendrapur",
        "Em Bypass", "Gariahat", "Kasba", "Phool Bagan", "Bansdroni",
    ],
    "Gurgaon": [
        "Sector 49", "Sector 56", "Sector 82", "Sector 37", "Sohna Road",
        "Golf Course Road", "Sector 84", "Sector 67", "Sector 68", "Dwarka Expressway",
        "Sector 102", "Sector 92", "Sector 48", "Sector 62", "Sector 70",
        "Mg Road", "Sector 54", "Sector 43", "Udyog Vihar", "Dlf Phase",
    ],
    "Noida": [
        "Sector 150", "Sector 137", "Sector 75", "Sector 78", "Sector 44",
        "Sector 62", "Sector 50", "Sector 76", "Sector 128", "Sector 143",
        "Sector 70", "Sector 100", "Sector 93", "Sector 110", "Sector 121",
        "Sector 37", "Sector 15", "Sector 22", "Sector 18", "Sector 63",
    ],
    "Greater Noida": [
        "Sector Alpha 1", "Sector Beta 1", "Sector Omega 1", "Pari Chowk", "Knowledge Park",
        "Sector Zeta 1", "Sector Mu", "Sector Chi", "Sector Phi", "Sector Eta",
        "Yamuna Expressway", "Tech Zone", "Greater Noida West", "Sector 1", "Sector 2",
        "Noida Extension", "Gaur City", "Sector 4", "Sector 12", "Sector 16",
    ],
    "Ghaziabad": [
        "Indirapuram", "Vaishali", "Raj Nagar Extension", "Crossings Republik", "Vasundhara",
        "Sahibabad", "Loni", "Siddharth Vihar", "Mohan Nagar", "Kaushambi",
        "Niti Khand", "Ahinsa Khand", "Pratap Vihar", "Govindpuram", "Ramprastha",
        "Dundahera", "Wave City", "Nhpc Colony", "Bhopura", "Nehru Nagar",
    ],
    "Faridabad": [
        "Sector 86", "Sector 88", "Sector 75", "Sector 37", "Neharpar",
        "Sector 21", "Ballabgarh", "Sector 81", "Sector 77", "Surajkund",
        "Sector 15", "Sector 16", "Nit", "Sector 46", "Old Faridabad",
        "Badkhal", "Sector 62", "Greenfields", "Sector 84", "Sector 79",
    ],
    "Navi Mumbai": [
        "Kharghar", "Panvel", "Vashi", "Airoli", "Ghansoli",
        "Nerul", "Ulwe", "Belapur", "Kopar Khairane", "Taloja",
        "Sanpada", "Seawoods", "Kamothe", "Turbhe", "Juinagar",
        "Palm Beach Road", "Dronagiri", "Kalamboli", "New Panvel", "Khandeshwar",
    ],
    "Thane": [
        "Ghodbunder Road", "Pokhran Road", "Kolshet Road", "Majiwada", "Wagle Estate",
        "Manpada", "Balkum", "Vartak Nagar", "Naupada", "Panchpakhadi",
        "Brahmand", "Hiranandani Estate", "Vasant Vihar", "Kopri", "Teen Hath Naka",
        "Diva", "Bhiwandi", "Mumbra", "Kalyan West", "Dombivli East",
    ],
    "Lucknow": [
        "Gomti Nagar", "Aliganj", "Indira Nagar", "Hazratganj", "Charbagh",
        "Jankipuram", "Ashiyana", "Chinhat", "Sushant Golf City", "Shaheed Path",
        "Vikas Nagar", "Mahanagar", "Alambagh", "Rajajipuram", "Faizabad Road",
        "Kursi Road", "Amar Shaheed Path", "Vrindavan Colony", "Kanpur Road", "Sultanpur Road",
    ],
    "Jaipur": [
        "Mansarovar", "Jagatpura", "Vaishali Nagar", "Malviya Nagar", "Pratap Nagar",
        "Ajmer Road", "Tonk Road", "Sirsi Road", "Sodala", "C Scheme",
        "Raja Park", "Jhotwara", "Sanganer", "Vidhyadhar Nagar", "Lal Kothi",
        "Tilak Nagar", "Bani Park", "Durgapura", "Bapu Nagar", "Shyam Nagar",
    ],
    "Ahmedabad": [
        "Bopal", "South Bopal", "Gota", "Prahlad Nagar", "Satellite",
        "Sg Highway", "Thaltej", "Vastrapur", "Ambawadi", "Navrangpura",
        "Chandkheda", "Motera", "Maninagar", "Naranpura", "Bodakdev",
        "Sarkhej", "Gurukul", "Paldi", "Ellis Bridge", "Shilaj",
    ],
    "Chandigarh": [
        "Sector 17", "Sector 22", "Sector 35", "Sector 44", "Sector 8",
        "Sector 15", "Sector 21", "Sector 34", "Sector 38", "Sector 43",
        "Sector 48", "Sector 52", "Sector 63", "Manimajra", "Zirakpur",
        "Sector 20", "Sector 32", "Sector 40", "Sector 47", "Industrial Area",
    ],
    "Mohali": [
        "Sector 66", "Sector 70", "Sector 71", "Sector 105", "Sector 117",
        "Sector 68", "Sector 77", "Sector 82", "Sector 88", "Sector 91",
        "Kharar", "Aerocity", "Sector 74", "Sector 79", "Sector 85",
        "Sector 115", "Sector 125", "Sector 127", "It City", "Sector 63",
    ],
    "Kochi": [
        "Kakkanad", "Edappally", "Aluva", "Thrippunithura", "Vytilla",
        "Maradu", "Kaloor", "Kadavanthra", "Palarivattom", "Vyttila",
        "Fort Kochi", "Ernakulam", "Panangad", "Tripunithura", "Kalamassery",
        "Manjeri", "Cheranalloor", "Thevara", "Thammanam", "Ravipuram",
    ],
    "Coimbatore": [
        "Saravanampatti", "Singanallur", "Ganapathy", "Peelamedu", "Vadavalli",
        "Thudiyalur", "Kuniyamuthur", "Rs Puram", "Kovaipudur", "Kalapatti",
        "Sulur", "Sundarapuram", "Tatabad", "Gandhipuram", "Ondipudur",
        "Vilankurichi", "Perur", "Podanur", "Ukkadam", "Hopes College",
    ],
    "Goa": [
        "Panaji", "Margao", "Mapusa", "Vasco Da Gama", "Ponda",
        "Calangute", "Candolim", "Anjuna", "Vagator", "Baga",
        "Porvorim", "Dona Paula", "Bambolim", "Alto Porvorim", "Taleigao",
        "Siolim", "Assagao", "Aldona", "Bicholim", "Sanquelim",
    ],
    "Dehradun": [
        "Rajpur Road", "Sahastradhara Road", "Mussoorie Road", "Prem Nagar", "Clement Town",
        "Jakhan", "Balliwala", "Dalanwala", "Aabadi Road", "Nehru Colony",
        "Race Course", "Turner Road", "Karanpur", "Kedarpur", "Kanwali",
        "Doon Vihar", "Indira Nagar", "Hathibarkala", "Raipur", "Doiwala",
    ],
    "Mangalore": [
        "Bejai", "Kadri", "Falnir", "Derebail", "Kottara",
        "Kankanady", "Urwa", "Bendoor", "Mannagudda", "Balmatta",
        "Jeppu", "Pumpwell", "Kavoor", "Kulshekar", "Padil",
        "Bikarnakatte", "Mallikatte", "Hampankatta", "Attavar", "Pandeshwar",
    ],
    "Patna": [
        "Boring Road", "Kankarbagh", "Patliputra Colony", "Rajendra Nagar", "Bailey Road",
        "Danapur", "Phulwari Sharif", "Anisabad", "Jakkanpur", "Saguna More",
        "Ashiana Nagar", "Khajpura", "Naubatpur", "Mahendru", "Gardanibagh",
        "Kumhrar", "Shastri Nagar", "Budh Marg", "Rukanpura", "Sampatchak",
    ],
    "Raipur": [
        "Shankar Nagar", "Devendra Nagar", "Telibandha", "Pandri", "Tatibandh",
        "Amanaka", "Vidhan Sabha Road", "Mowa", "Bhatagaon", "Khamtarai",
        "Sunder Nagar", "Gudhiyari", "Hirapur", "Daldal Seoni", "Amlidih",
        "Boriyakhurd", "Birgaon", "Ring Road", "Vip Road", "Saddu",
    ],
    "Nagpur": [
        "Dharampeth", "Sitabuldi", "Wardha Road", "Hingna", "Khamla",
        "Manish Nagar", "Trimurti Nagar", "Pratap Nagar", "Manewada", "Besa",
        "Ramdaspeth", "Laxmi Nagar", "Seminary Hills", "Civil Lines", "Sadar",
        "Amravati Road", "Godhni", "Nandanvan", "Wadi", "Koradi Road",
    ],
    "Vizag": [
        "Madhurawada", "Gajuwaka", "Seethammadhara", "Pendurthi", "Rushikonda",
        "Mvp Colony", "Nad Junction", "Yendada", "Pm Palem", "Kommadi",
        "Beach Road", "Dwaraka Nagar", "Lawsons Bay Colony", "Siripuram", "Akkayyapalem",
        "Madhavadhara", "Asilmetta", "Gopalapatnam", "Chinagadili", "Anandapuram",
    ],
    "Indore": [
        "Vijay Nagar", "Nipania", "Super Corridor", "Ab Road", "Bhawarkua",
        "Rau", "Bypass Road", "Mahalaxmi Nagar", "Palasia", "New Palasia",
        "Sapna Sangeeta Road", "Rajendra Nagar", "Sudama Nagar", "Bicholi Mardana", "Scheme No 78",
        "Scheme No 54", "Aerodrome Road", "Ring Road", "Mhow", "Khandwa Road",
    ],
    "Bhopal": [
        "Mp Nagar", "Kolar Road", "Hoshangabad Road", "Arera Colony", "Shahpura",
        "Ayodhya Nagar", "Ashoka Garden", "Bag Mugalia", "Sehore Road", "Misrod",
        "Raisen Road", "Mandideep", "Piplani", "Tt Nagar", "Habibganj",
        "Govindpura", "Bawadia Kalan", "Bairagarh", "Berasia Road", "Saket Nagar",
    ],
    "Guwahati": [
        "Zoo Road", "Ganeshguri", "Dispur", "Beltola", "Hatigaon",
        "Kahilipara", "Six Mile", "Lokhra", "Basistha", "Panbazar",
        "Garchuk", "Jalukbari", "Maligaon", "Chandmari", "Noonmati",
        "Paltan Bazar", "Bhangagarh", "Ulubari", "Fancy Bazar", "Rehabari",
    ],
    "Kanpur": [
        "Swaroop Nagar", "Kakadeo", "Kidwai Nagar", "Kalyanpur", "Panki",
        "Shyam Nagar", "Harsh Nagar", "Govind Nagar", "Arya Nagar", "Civil Lines",
        "Jajmau", "Rawatpur", "Barra", "Kakadev", "Lal Bangla",
        "Tilak Nagar", "Transport Nagar", "Avas Vikas", "Naubasta", "Fazalganj",
    ],
    "Jamshedpur": [
        "Bistupur", "Sakchi", "Sonari", "Kadma", "Telco",
        "Sidhgora", "Golmuri", "Baridih", "Mango", "Adityapur",
        "Agrico", "Bhalubasa", "Birsanagar", "Dimna", "Marine Drive",
        "Pardih", "Sitaramdera", "Jugsalai", "Northern Town", "Circuit House Area",
    ],
    "Kalyan": [
        "Kalyan West", "Kalyan East", "Dombivli West", "Dombivli East", "Ambernath",
        "Badlapur West", "Badlapur East", "Titwala", "Ulhasnagar", "Shahad",
        "Mohone", "Katai", "Vitthalwadi", "Govindwadi", "Bail Bazar",
        "Birla College Road", "Gandhari", "Khadakpada", "Kolsewadi", "Netivali",
    ],
    "Palghar": [
        "Virar West", "Virar East", "Vasai West", "Vasai East", "Nalasopara West",
        "Nalasopara East", "Boisar", "Manor", "Palghar Town", "Kelwa Road",
        "Naigaon West", "Naigaon East", "Navghar", "Achole", "Sativali",
        "Evershine City", "Global City", "Nallasopara", "Tungareshwar", "Arnala",
    ],
}

# Fallback localities for cities not explicitly listed above.
_DEFAULT_LOCALITIES = [
    "Civil Lines", "Mg Road", "Station Road", "Model Town", "Nehru Nagar",
    "Gandhi Nagar", "Shastri Nagar", "Rajendra Nagar", "Vikas Nagar", "Indira Nagar",
    "Sadar Bazar", "Lal Bagh", "Cantonment", "University Road", "Ring Road",
    "Bypass Road", "Industrial Area", "Old City", "New Colony", "Adarsh Nagar",
]


def make_synthetic(n: int = 6000, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    cities = list(_CITIES.keys())
    city = rng.choice(cities, size=n)
    # Pick a real locality for each city
    loc_names = []
    loc_indices = []
    for c in city:
        locs = _LOCALITIES.get(c, _DEFAULT_LOCALITIES)
        idx = rng.integers(0, len(locs))
        loc_names.append(locs[idx])
        loc_indices.append(idx)
    loc_idx = np.array(loc_indices)
    loc_effect = np.sin(loc_idx * 1.7) * 0.35  # each locality has its own price level
    bhk = rng.choice([1, 2, 3, 4, 5], size=n, p=[0.15, 0.4, 0.3, 0.1, 0.05])
    sbu = np.round(np.exp(rng.normal(np.log(450 + 400 * bhk), 0.18)))
    is_carpet = rng.random(n) < 0.5
    total = rng.integers(2, 30, n)
    floor = np.minimum(rng.integers(0, 30, n), total)
    furn = rng.choice(["Furnished", "Semi-Furnished", "Unfurnished"], n, p=[0.1, 0.5, 0.4])
    new = rng.random(n) < 0.25
    log_rate = (
        np.log([_CITIES[c] for c in city])
        + loc_effect
        + 0.004 * floor
        + 0.05 * (furn == "Furnished")
        + 0.04 * new
        + rng.normal(0, 0.12, n)
    )
    price = np.exp(log_rate) * sbu
    amount = [f"{p / 1e7:.2f} Cr" if p >= 1e7 else f"{p / 1e5:.1f} Lac" for p in price]
    # Use city names with hyphens for the location column (matches raw data format)
    location_col = [c.lower().replace(" ", "-") for c in city]
    return pd.DataFrame(
        {
            "Title": [
                f"{b} BHK Ready to Occupy Flat for sale in Demo Residency {lc}"
                for b, lc in zip(bhk, loc_names, strict=False)
            ],
            "Amount(in rupees)": amount,
            "location": location_col,
            "Carpet Area": np.where(is_carpet, [f"{a / 1.35:.0f} sqft" for a in sbu], None),
            "Super Area": np.where(~is_carpet, [f"{a:.0f} sqft" for a in sbu], None),
            "Floor": [("Ground" if f == 0 else str(f)) + f" out of {t}" for f, t in zip(floor, total, strict=False)],
            "Transaction": np.where(new, "New Property", "Resale"),
            "Furnishing": furn,
            "facing": rng.choice(["East", "North", "West", "South", None], n),
            "overlooking": rng.choice(["Main Road", "Garden/Park", "Garden/Park, Pool", None], n),
            "Society": "Demo Residency",
            "Bathroom": np.minimum(bhk + rng.integers(0, 2, n), 6).astype(str),
            "Balcony": rng.integers(0, 4, n).astype(str),
            "Car Parking": rng.choice(["1 Covered", "2 Covered", "1 Open", None], n),
            "Ownership": rng.choice(["Freehold", "Leasehold", None], n, p=[0.6, 0.1, 0.3]),
        }
    )


# --- Locality coordinates -----------------------------------------------------------------------
# Geocoded once from OpenStreetMap Nominatim (© OpenStreetMap contributors, ODbL) — see docs/13-integrations.md.
GENERIC_TYPES = {"city", "town", "state", "state_district", "county", "country"}
MAX_KM_FROM_CITY = 25


def _haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


def clean_geocodes(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """raw: city, locality, lat, lon, osm_type. Drops the typical Nominatim misses and returns (coords, report)."""
    df = raw.dropna(subset=["lat", "lon"]).copy()
    report = {"geocoded": len(df)}
    # A city/town result means Nominatim fell back to the whole city — not a locality position.
    df = df[~df.osm_type.isin(GENERIC_TYPES) | (df.locality.str.lower() == df.city.str.lower())]
    report["dropped_city_fallback"] = report["geocoded"] - len(df)
    # Several different localities on (almost) the same point = a shared fallback (e.g. a sector name matched
    # in the neighbouring city). Keep none of them.
    cell = (df.lat / 0.005).round().astype(int).astype(str) + "," + (df.lon / 0.005).round().astype(int).astype(str)
    shared = df.groupby(["city", cell]).locality.transform("nunique") >= 3
    report["dropped_shared_point"] = int(shared.sum())
    df = df[~shared]
    centre = df.groupby("city")[["lat", "lon"]].transform("median")
    far = _haversine_km(df.lat, df.lon, centre.lat, centre.lon) > MAX_KM_FROM_CITY
    report["dropped_far_from_city"] = int(far.sum())
    df = df[~far]
    report["kept"] = len(df)
    return df[["city", "locality", "lat", "lon"]].reset_index(drop=True), report


def load_coords(path: str | Path) -> pd.DataFrame | None:
    p = Path(path)
    return pd.read_csv(p) if p.exists() else None
