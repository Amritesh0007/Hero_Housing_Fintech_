import argparse
import json
import sys
from pathlib import Path

from .config import get_settings


def main(argv=None):
    p = argparse.ArgumentParser("valuegraph")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("synthetic", help="write a synthetic sample in the Kaggle schema")
    s.add_argument("--rows", type=int, default=8000)
    t = sub.add_parser("train")
    t.add_argument("--csv", default="data/raw/house_prices.csv")
    t.add_argument("--iterations", type=int, default=4000)
    t.add_argument("--coords", default="data/geo/locality_coords.csv", help="geocoded localities (optional)")
    g = sub.add_parser("geocode-import", help="clean raw geocoder output into data/geo/locality_coords.csv")
    g.add_argument("json", help='JSON list of ["City|Locality", lat, lon, osm_type]')
    u = sub.add_parser("create-user")
    u.add_argument("email")
    u.add_argument("role", choices=["admin", "underwriter", "reviewer", "risk"])
    k = sub.add_parser("create-api-key")
    k.add_argument("email")
    a = p.parse_args(argv)

    if a.cmd == "synthetic":
        from .data import make_synthetic

        out = Path("data/sample/synthetic_train.csv")
        out.parent.mkdir(parents=True, exist_ok=True)
        make_synthetic(a.rows).to_csv(out, index=False)
        print(f"wrote {out} (SYNTHETIC — not for accuracy claims)")
    elif a.cmd == "train":
        from .data import clean, load_coords, read_raw
        from .train import run

        listings, report = clean(read_raw(a.csv))
        print("cleaning:", json.dumps(report))
        source = "synthetic" if "synthetic" in a.csv else "india_house_prices"
        out = Path("data/processed")
        out.mkdir(parents=True, exist_ok=True)
        listings.to_csv(out / "listings.csv", index=False)
        rep = run(
            listings,
            get_settings().model_dir,
            coords=None if source == "synthetic" else load_coords(a.coords),
            level=get_settings().interval_level,
            source=source,
            iterations=a.iterations,
        )
        print(json.dumps(rep["results"]["test"], indent=2, default=str))
    elif a.cmd == "geocode-import":
        import pandas as pd

        from .data import clean_geocodes

        rows = [(*k.split("|", 1), la, lo, t) for k, la, lo, t in json.loads(Path(a.json).read_text())]
        coords, report = clean_geocodes(pd.DataFrame(rows, columns=["city", "locality", "lat", "lon", "osm_type"]))
        out = Path("data/geo/locality_coords.csv")
        out.parent.mkdir(parents=True, exist_ok=True)
        coords.round(6).to_csv(out, index=False)
        print(json.dumps(report), "->", out)
    elif a.cmd == "create-user":
        import getpass

        from .api.auth import hash_password
        from .api.db import User, init_db, session_scope

        pw = getpass.getpass("password (min 12 chars): ")
        if len(pw) < 12:
            sys.exit("password too short")
        init_db()
        with session_scope() as db:
            db.add(User(email=a.email.lower(), password_hash=hash_password(pw), role=a.role))
        print("created", a.email)
    elif a.cmd == "create-api-key":
        from .api.auth import new_api_key
        from .api.db import ApiKey, User, init_db, session_scope

        init_db()
        with session_scope() as db:
            user = db.query(User).filter_by(email=a.email.lower()).one()
            raw, prefix, digest = new_api_key()
            db.add(ApiKey(user_id=user.id, prefix=prefix, key_hash=digest))
        print("API key (shown once):", raw)


if __name__ == "__main__":
    main()
