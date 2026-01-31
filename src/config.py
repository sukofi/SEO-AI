import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


def _get_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    google_service_account_json: Optional[Path]
    google_sheets_spreadsheet_id: str
    google_sheets_range: str
    serp_api_key: str
    serp_api_endpoint: str
    serp_api_key_param: str
    serp_api_query_param: str
    serp_api_location_param: Optional[str]
    serp_api_location_value: Optional[str]
    serp_api_language_param: Optional[str]
    serp_api_language_value: Optional[str]
    own_domain: str
    gemini_api_key: str
    gemini_api_endpoint: str
    discord_webhook_url: str
    log_path: Path
    log_level: str
    dry_run: bool


    @staticmethod
    def from_env() -> "Config":
        service_account_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
        return Config(
            google_service_account_json=Path(service_account_path) if service_account_path else None,
            google_sheets_spreadsheet_id=os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", "").strip(),
            google_sheets_range=os.getenv("GOOGLE_SHEETS_RANGE", "").strip(),
            serp_api_key=os.getenv("SERP_API_KEY", "").strip(),
            serp_api_endpoint=os.getenv("SERP_API_ENDPOINT", "").strip(),
            serp_api_key_param=os.getenv("SERP_API_KEY_PARAM", "api_key").strip(),
            serp_api_query_param=os.getenv("SERP_API_QUERY_PARAM", "q").strip(),
            serp_api_location_param=os.getenv("SERP_API_LOCATION_PARAM"),
            serp_api_location_value=os.getenv("SERP_API_LOCATION_VALUE"),
            serp_api_language_param=os.getenv("SERP_API_LANGUAGE_PARAM"),
            serp_api_language_value=os.getenv("SERP_API_LANGUAGE_VALUE"),
            own_domain=os.getenv("OWN_DOMAIN", "").strip(),
            gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
            gemini_api_endpoint=os.getenv(
                "GEMINI_API_ENDPOINT",
                "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent",
            ).strip(),
            discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL", "").strip(),
            log_path=Path(os.getenv("LOG_PATH", "logs/seo_reporter.log")),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            dry_run=_get_bool("DRY_RUN", False),
        )

    def validate(self) -> None:
        missing = []
        if not self.google_sheets_spreadsheet_id:
            missing.append("GOOGLE_SHEETS_SPREADSHEET_ID")
        if not self.google_sheets_range:
            missing.append("GOOGLE_SHEETS_RANGE")
        if not self.serp_api_key:
            missing.append("SERP_API_KEY")
        if not self.serp_api_endpoint:
            missing.append("SERP_API_ENDPOINT")
        if not self.own_domain:
            missing.append("OWN_DOMAIN")
        if not self.gemini_api_key:
            missing.append("GEMINI_API_KEY")
        if not self.discord_webhook_url:
            missing.append("DISCORD_WEBHOOK_URL")
        if missing:
            raise ValueError(f"Missing required env vars: {', '.join(missing)}")

    def load_service_account(self) -> Optional[dict]:
        if not self.google_service_account_json:
            return None
        content = self.google_service_account_json.read_text(encoding="utf-8")
        return json.loads(content)
