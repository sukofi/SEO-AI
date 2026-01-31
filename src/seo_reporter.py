import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List, Optional

import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build

from config import Config


@dataclass(frozen=True)
class KeywordEntry:
    keyword: str
    previous_rank: Optional[int]


@dataclass(frozen=True)
class SerpResult:
    keyword: str
    rank: Optional[int]
    own_url: Optional[str]
    competitors: List[dict]


@dataclass(frozen=True)
class KeywordReport:
    keyword: str
    rank: Optional[int]
    previous_rank: Optional[int]
    competitors: List[dict]
    gaps: List[str]


def setup_logging(config: Config) -> None:
    config.log_path.parent.mkdir(parents=True, exist_ok=True)
    handlers = [logging.StreamHandler(sys.stdout), logging.FileHandler(config.log_path)]
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def load_keywords(config: Config) -> List[KeywordEntry]:
    service_account_info = config.load_service_account()
    if not service_account_info:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON is required for Sheets access")

    credentials = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    service = build("sheets", "v4", credentials=credentials)
    sheet = service.spreadsheets()
    result = (
        sheet.values()
        .get(spreadsheetId=config.google_sheets_spreadsheet_id, range=config.google_sheets_range)
        .execute()
    )
    values = result.get("values", [])
    if not values:
        logging.warning("No data returned from spreadsheet range.")
        return []

    start_index = 0
    header = [cell.strip().lower() for cell in values[0]]
    if "keyword" in header:
        start_index = 1
    entries = []
    for row in values[start_index:]:
        if not row:
            continue
        keyword = row[0].strip()
        if not keyword:
            continue
        previous_rank = None
        if len(row) > 1 and row[1].strip().isdigit():
            previous_rank = int(row[1].strip())
        entries.append(KeywordEntry(keyword=keyword, previous_rank=previous_rank))
    return entries


def fetch_serp(config: Config, keyword: str) -> SerpResult:
    params = {
        config.serp_api_key_param: config.serp_api_key,
        config.serp_api_query_param: keyword,
    }
    if config.serp_api_location_param and config.serp_api_location_value:
        params[config.serp_api_location_param] = config.serp_api_location_value
    if config.serp_api_language_param and config.serp_api_language_value:
        params[config.serp_api_language_param] = config.serp_api_language_value

    response = requests.get(config.serp_api_endpoint, params=params, timeout=30)
    response.raise_for_status()
    payload = response.json()

    organic_results = payload.get("organic_results") or payload.get("organic") or []
    competitors = []
    rank = None
    own_url = None

    for entry in organic_results:
        url = entry.get("link") or entry.get("url") or ""
        title = entry.get("title") or ""
        snippet = entry.get("snippet") or entry.get("description") or ""
        position = entry.get("position") or entry.get("rank")
        competitors.append(
            {
                "position": position,
                "url": url,
                "title": title,
                "snippet": snippet,
            }
        )
        if url and config.own_domain in url and rank is None:
            rank = int(position) if position is not None else None
            own_url = url

    top_competitors = [c for c in competitors if config.own_domain not in (c.get("url") or "")][:10]

    return SerpResult(keyword=keyword, rank=rank, own_url=own_url, competitors=top_competitors)


def is_downward(rank: Optional[int], previous_rank: Optional[int]) -> bool:
    if rank is None or previous_rank is None:
        return False
    return rank > previous_rank


def build_gemini_prompt(keyword: str, serp_result: SerpResult, own_domain: str) -> str:
    competitors = json.dumps(serp_result.competitors, ensure_ascii=False, indent=2)
    own_url = serp_result.own_url or f"https://{own_domain}"
    return (
        "あなたはSEOアナリストです。以下の情報をもとに競合と自社の差分を分析し、"
        "改善アクションを日本語で箇条書きしてください。\n\n"
        f"キーワード: {keyword}\n"
        f"自社URL: {own_url}\n"
        "競合情報(検索結果上位):\n"
        f"{competitors}\n\n"
        "出力は短い箇条書きで、差分と改善アクションを分けてください。"
    )


def request_gemini(config: Config, prompt: str) -> List[str]:
    headers = {"Content-Type": "application/json"}
    params = {"key": config.gemini_api_key}
    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": prompt,
                    }
                ]
            }
        ]
    }
    response = requests.post(
        config.gemini_api_endpoint,
        params=params,
        headers=headers,
        json=payload,
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    candidates = data.get("candidates", [])
    if not candidates:
        return []
    text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
    return [line.strip("-• ") for line in text.splitlines() if line.strip()]


def build_report(reports: Iterable[KeywordReport]) -> str:
    lines = ["SEO差分レポート", f"実行日時: {datetime.utcnow().isoformat()}Z", ""]
    for report in reports:
        lines.append(f"## {report.keyword}")
        lines.append(f"現在順位: {report.rank or '未検出'} (前回: {report.previous_rank or '不明'})")
        if report.competitors:
            lines.append("競合上位:")
            for competitor in report.competitors[:5]:
                lines.append(f"- {competitor.get('title') or 'N/A'}: {competitor.get('url')}")
        if report.gaps:
            lines.append("差分・改善アクション:")
            for gap in report.gaps:
                lines.append(f"- {gap}")
        lines.append("")
    return "\n".join(lines)


def send_discord_report(config: Config, report: str) -> None:
    if config.dry_run:
        logging.info("DRY_RUN enabled. Report not sent to Discord.")
        logging.info("Report preview:\n%s", report)
        return
    response = requests.post(
        config.discord_webhook_url,
        json={"content": report},
        timeout=20,
    )
    response.raise_for_status()


def main() -> int:
    config = Config.from_env()
    setup_logging(config)
    try:
        config.validate()
        entries = load_keywords(config)
        if not entries:
            logging.warning("No keywords found. Exiting.")
            return 0

        reports: List[KeywordReport] = []
        for entry in entries:
            serp_result = fetch_serp(config, entry.keyword)
            if not serp_result.rank or serp_result.rank > 10:
                continue
            if not is_downward(serp_result.rank, entry.previous_rank):
                continue
            prompt = build_gemini_prompt(entry.keyword, serp_result, config.own_domain)
            gaps = request_gemini(config, prompt)
            reports.append(
                KeywordReport(
                    keyword=entry.keyword,
                    rank=serp_result.rank,
                    previous_rank=entry.previous_rank,
                    competitors=serp_result.competitors,
                    gaps=gaps,
                )
            )

        if not reports:
            logging.info("No downward top-10 keywords detected.")
            return 0

        report_text = build_report(reports)
        send_discord_report(config, report_text)
        logging.info("Report sent successfully.")
        return 0
    except Exception:
        logging.exception("SEO reporting job failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
