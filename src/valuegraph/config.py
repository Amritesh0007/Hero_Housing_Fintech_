from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"  # local | test | prod
    database_url: str = "sqlite:///./valuegraph.db"
    jwt_secret: str = "dev-only-change-me"
    jwt_ttl_min: int = 60
    model_dir: Path = Path("models")
    cors_origins: str = "http://localhost:8000"
    rate_limit_per_min: int = 120
    # Google Maps JavaScript API browser key. Public by design — restrict it by HTTP referrer in
    # Google Cloud Console. Empty disables the maps and the UI says so.
    maps_api_key: str = ""
    # Google Sign-In OAuth client ID ("...apps.googleusercontent.com"). Public by design. Empty hides the button.
    google_client_id: str = ""
    # Role given to someone the first time they sign in with Google.
    google_default_role: Literal["underwriter", "reviewer", "risk"] = "underwriter"
    # Website translations. SECRET: server-side only, never sent to browsers. Empty = English only.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-flash-latest"
    i18n_cache_dir: Path = Path("data/i18n")
    gemini_max_calls_per_hour: int = 20  # caps spend even if someone hammers the language picker

    # Valuation policy (see docs/PRD.md §7). Thresholds are config, not code.
    interval_level: float = 0.8
    comp_k: int = 30
    high_min_comps: int = 8
    medium_min_comps: int = 4
    # Grades are calibrated at training: High = range width at which calibration cases had median error <= 10%,
    # Medium <= 15%. Set these to override with lender-agreed absolute widths ((high-low)/value).
    high_max_width: float | None = None
    medium_max_width: float | None = None
    guideline_max_deviation: float = 0.40
    auto_max_loan_inr: int = 7_500_000
    # RBI tiered LTV caps for housing loans — verify current directions before pilot.
    ltv_tiers: list[tuple[int, float]] = [(3_000_000, 0.90), (7_500_000, 0.80), (10**15, 0.75)]

    @model_validator(mode="after")
    def _no_default_secret_in_prod(self):
        if self.app_env == "prod" and (self.jwt_secret == "dev-only-change-me" or len(self.jwt_secret) < 32):
            raise ValueError("Set a strong JWT_SECRET (>=32 chars) in prod")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
