"""Runtime settings and YAML configuration loading.

Two kinds of configuration live here, and neither is data:

* ``Settings`` — environment variables (secrets, URLs, feature switches). Secrets are
  only ever read from the environment; `.env.example` lists the names.
* ``AppConfig`` — the YAML parameter files in ``config/`` (hazard thresholds, monitored
  districts, plan document sources, replay events).
"""
from __future__ import annotations

import functools
import os
import secrets
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(REPO_ROOT / ".env"), extra="ignore", env_ignore_empty=True)

    # --- core ---
    env: str = "development"
    database_url: str = f"sqlite:///{REPO_ROOT / 'data' / 'tminus.db'}"
    config_dir: Path = REPO_ROOT / "config"
    data_dir: Path = REPO_ROOT / "data"
    frontend_dist: Path = REPO_ROOT / "frontend" / "dist"
    cors_origins: str = "http://localhost:5173"
    secret_key: str = ""

    # --- officer login (display name + role only; no personal data, §9.3) ---
    officer_passcode: str = ""
    admin_passcode: str = ""

    # --- scheduler ---
    scheduler_enabled: bool = True
    scan_interval_min: int = 60          # checks for a new model run; cached by run
    scan_on_startup: bool = True
    auto_plan_min_level: str = "PREPARE"  # agent runs automatically for triggers at/above this level

    # --- LLM (provider-agnostic interface, §9.4) ---
    llm_provider: str = "auto"           # auto | anthropic | gemini
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"
    gemini_backup_model: str = "gemini-flash-lite-latest"
    gemini_timeout_s: float = 180.0
    anthropic_api_key: str = ""
    llm_model: str = "claude-opus-5-5"
    llm_backup_model: str = "claude-sonnet-5-5"
    llm_effort: str = "high"
    llm_timeout_s: float = 180.0

    # --- embeddings ---
    embedding_backend: str = "auto"      # auto | fastembed | tfidf
    fastembed_model: str = "BAAI/bge-small-en-v1.5"

    # --- live sources ---
    open_meteo_ensemble_url: str = "https://ensemble-api.open-meteo.com/v1/ensemble"
    open_meteo_forecast_url: str = "https://api.open-meteo.com/v1/forecast"
    open_meteo_elevation_url: str = "https://api.open-meteo.com/v1/elevation"
    open_meteo_flood_url: str = "https://flood-api.open-meteo.com/v1/flood"
    open_meteo_previous_runs_url: str = "https://previous-runs-api.open-meteo.com/v1/forecast"
    open_meteo_archive_url: str = "https://archive-api.open-meteo.com/v1/archive"
    open_meteo_meta_url: str = "https://ensemble-api.open-meteo.com/data/{model}/static/meta.json"
    open_meteo_api_key: str = ""         # optional commercial key; free tier needs none
    ensemble_models: str = "ecmwf_ifs025,gfs025"
    sachet_feed_url: str = "https://sachet.ndma.gov.in/cap_public_website/rss/rss_india.xml"
    sachet_state_feed_base: str = "https://sachet.ndma.gov.in/cap_public_website/rss/"
    sachet_use_national_feed: bool = False  # national feed only keeps the latest 100 items (all states)
    imd_enabled: bool = False
    imd_base_url: str = ""
    overpass_url: str = "https://overpass-api.de/api/interpreter"
    overpass_mirrors: str = "https://overpass.kumi.systems/api/interpreter,https://overpass.private.coffee/api/interpreter"
    nominatim_url: str = "https://nominatim.openstreetmap.org/search"
    nominatim_reverse_url: str = "https://nominatim.openstreetmap.org/reverse"
    ors_url: str = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    ors_api_key: str = ""
    contact_email: str = ""              # sent in the User-Agent (Nominatim policy)
    http_timeout_s: float = 30.0
    use_os_trust_store: bool = True      # verify HTTPS against the OS trust store (truststore); never disabled

    # --- uploads ---
    max_upload_mb: int = 30          # officer uploads through the UI (spec starting value)
    max_ingest_mb: int = 150         # setup-script downloads from official sources (city plans can be 60+ MB)

    @property
    def is_production(self) -> bool:
        return self.env.lower() == "production"

    @property
    def ensemble_model_list(self) -> list[str]:
        return [m.strip() for m in self.ensemble_models.split(",") if m.strip()]

    @property
    def user_agent(self) -> str:
        contact = f" ({self.contact_email})" if self.contact_email else ""
        return f"T-Minus/1.0 anticipatory-action prototype{contact}"


@functools.lru_cache
def get_settings() -> Settings:
    s = Settings()
    if not s.secret_key:
        # Development convenience only: a per-process random key (sessions reset on
        # restart). Production must set SECRET_KEY — enforced in main.py.
        s.secret_key = secrets.token_urlsafe(32)
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s


# ---------------------------------------------------------------------------
# YAML parameter files
# ---------------------------------------------------------------------------

class RiverPointCfg(BaseModel):
    name: str
    lat: float
    lon: float


class DistrictCfg(BaseModel):
    id: str
    name: str
    state: str
    aliases: list[str] = Field(default_factory=list)
    hq: tuple[float, float]
    search_radius_km: float | None = None
    rivers: list[RiverPointCfg] = Field(default_factory=list)

    def all_names(self) -> list[str]:
        return [self.name, *self.aliases]


class AppConfig(BaseModel):
    hazards: dict[str, Any]
    districts: list[DistrictCfg]
    states: dict[str, str]
    sachet_feeds: dict[str, str] = Field(default_factory=dict)
    district_defaults: dict[str, Any]
    plans: list[dict[str, Any]]
    replay: dict[str, Any]

    def district(self, district_id: str) -> DistrictCfg | None:
        return next((d for d in self.districts if d.id == district_id), None)

    def state_name(self, code: str) -> str:
        return self.states.get(code, code)

    def search_radius_km(self, d: DistrictCfg) -> float:
        return float(d.search_radius_km or self.district_defaults.get("search_radius_km", 25))


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@functools.lru_cache
def get_config() -> AppConfig:
    cfg_dir = Path(os.environ.get("CONFIG_DIR", get_settings().config_dir))
    hazards = _load_yaml(cfg_dir / "hazards.yaml")
    dist = _load_yaml(cfg_dir / "districts.yaml")
    plans = _load_yaml(cfg_dir / "plans.yaml") if (cfg_dir / "plans.yaml").exists() else {}
    replay = _load_yaml(cfg_dir / "replay_events.yaml") if (cfg_dir / "replay_events.yaml").exists() else {}
    districts = [DistrictCfg(**d) for d in dist.get("districts", [])]
    ids = [d.id for d in districts]
    if len(ids) != len(set(ids)):
        raise ValueError("config/districts.yaml: duplicate district ids")
    return AppConfig(
        hazards=hazards,
        districts=districts,
        states=dist.get("states", {}),
        sachet_feeds=dist.get("sachet_feeds", {}),
        district_defaults=dist.get("defaults", {}),
        plans=plans.get("documents", []),
        replay=replay,
    )
