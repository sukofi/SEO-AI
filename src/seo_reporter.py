import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List, Optional

import requests
import google.auth
from google.oauth2 import service_account
from googleapiclient.discovery import build
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from config import Config


@dataclass(frozen=True)
class KeywordEntry:
    keyword: str
    previous_rank: Optional[int]
    row_index: int


@dataclass(frozen=True)
class SerpResult:
    keyword: str
    rank: Optional[int]
    own_url: Optional[str]
    competitors: List[dict]


@dataclass(frozen=True)
class ContentMetrics:
    char_count: int
    headings: List[str]
    image_count: int
    internal_link_count: int


@dataclass(frozen=True)
class KeywordReport:
    keyword: str
    rank: Optional[int]
    previous_rank: Optional[int]
    competitors: List[dict]
    gaps: List[str]
    own_metrics: Optional[ContentMetrics] = None
    competitor_metrics: Optional[ContentMetrics] = None


def setup_logging(config: Config) -> None:
    config.log_path.parent.mkdir(parents=True, exist_ok=True)
    handlers = [logging.StreamHandler(sys.stdout), logging.FileHandler(config.log_path)]
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


import re

def load_keywords(config: Config) -> List[KeywordEntry]:
    service_account_info = config.load_service_account()
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    
    if service_account_info:
        credentials = service_account.Credentials.from_service_account_info(
            service_account_info,
            scopes=scopes,
        )
    else:
        logging.info("Service account not found. Using Application Default Credentials.")
        credentials, project = google.auth.default(scopes=scopes)

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

    # Parse start row from range
    start_row = 1
    match = re.search(r"(\d+):", config.google_sheets_range)
    if match:
        start_row = int(match.group(1))

    start_index = 0
    header = [cell.strip().lower() for cell in values[0]]
    if "keyword" in header:
        start_index = 1
    
    entries = []
    for i, row in enumerate(values[start_index:]):
        if not row:
            continue
        keyword = row[0].strip()
        if not keyword:
            continue
        previous_rank = None
        if len(row) > 1 and row[1].strip().isdigit():
            previous_rank = int(row[1].strip())
        
        # Calculate actual row index (1-based for Sheets API)
        # start_row is the row number of values[0]
        # values[start_index] is the first data row
        # current row is values[start_index + i]
        current_row_index = start_row + start_index + i
        
        entries.append(KeywordEntry(keyword=keyword, previous_rank=previous_rank, row_index=current_row_index))
    return entries


def fetch_serp(config: Config, keyword: str) -> SerpResult:
    headers = {
        "X-API-KEY": config.serp_api_key,
        "Content-Type": "application/json",
    }
    payload = {
        config.serp_api_query_param: keyword,
    }
    if config.serp_api_location_param and config.serp_api_location_value:
        payload[config.serp_api_location_param] = config.serp_api_location_value
    if config.serp_api_language_param and config.serp_api_language_value:
        payload[config.serp_api_language_param] = config.serp_api_language_value

    response = requests.post(config.serp_api_endpoint, headers=headers, json=payload, timeout=30)
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


def analyze_page_content(url: str, own_domain: str = None) -> ContentMetrics:
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            # Set User-Agent to avoid being blocked
            page.set_extra_http_headers({"User-Agent": "Mozilla/5.0 (compatible; SEO-Reporter/1.0)"})
            
            try:
                # Go to URL with 30s timeout
                page.goto(url, timeout=30000, wait_until="domcontentloaded")
                # Wait for network idle to ensure JS loads content (optional but good for SPA)
                # Sometimes networkidle is too strict/slow if there are background polls, so maybe just wait a bit or rely on domcontentloaded + small sleep?
                # "networkidle" is often best for SPAs.
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    logging.warning(f"Timeout waiting for network idle for {url}, proceeding with current content.")
                
                content = page.content()
            finally:
                browser.close()

        soup = BeautifulSoup(content, "html.parser")
        
        # Remove script and style elements
        for script in soup(["script", "style", "noscript", "iframe", "svg", "form"]):
            script.extract()

        # Define noise candidates to remove
        noise_tags = ["header", "footer", "nav", "aside"]
        for tag in noise_tags:
            for t in soup.find_all(tag):
                t.extract()
                
        # Heuristic: Find main content area
        # Priority: <main> -> <article> -> divs with specific IDs/Classes
        main_content = soup.find("main")
        if not main_content:
            main_content = soup.find("article")
        
        if not main_content:
            # Try finding divs with likely content names
            candidate_ids = ["content", "main", "main-content", "post-body", "entry-content", "article-body"]
            for cid in candidate_ids:
                main_content = soup.find("div", id=re.compile(cid, re.I)) or soup.find("div", class_=re.compile(cid, re.I))
                if main_content:
                    break
        
        # Fallback to body if nothing else found, but we already removed noise tags from soup
        if not main_content:
            main_content = soup.find("body") or soup

        # Character count (body text)
        # Use get_text with separator to avoid merging words, and rely on prior cleanup of noise tags
        text_content = main_content.get_text(separator=" ", strip=True)
        char_count = len(text_content.replace(" ", "").replace("\n", ""))
        
        # Headings (search in main_content)
        headings = []
        for h in main_content.find_all(re.compile("^h[1-6]$")):
            text = h.get_text().strip().replace("\n", " ")
            if text:
                headings.append(f"{h.name.upper()}: {text[:50]}")
        
        # Image count (search in main_content)
        images = main_content.find_all("img")
        image_count = len(images)
        
        # Internal link count (search in main_content)
        internal_link_count = 0
        if own_domain:
            for a in main_content.find_all("a", href=True):
                href = a["href"]
                if own_domain in href or href.startswith("/"):
                     internal_link_count += 1
        
        return ContentMetrics(
            char_count=char_count,
            headings=headings,
            image_count=image_count,
            internal_link_count=internal_link_count
        )
    except Exception as e:
        logging.warning(f"Failed to analyze content for {url}: {e}")
        return ContentMetrics(0, [], 0, 0)


def build_gemini_prompt(
    keyword: str, 
    serp_result: SerpResult, 
    own_domain: str,
    own_metrics: Optional[ContentMetrics],
    competitor_metrics: Optional[ContentMetrics]
) -> str:
    competitors = json.dumps(serp_result.competitors, ensure_ascii=False, indent=2)
    own_url = serp_result.own_url or f"https://{own_domain}"
    
    metrics_info = ""
    if own_metrics and competitor_metrics:
        metrics_info = (
            "\n【コンテンツ比較データ】\n"
            f"自社: 文字数={own_metrics.char_count}, 画像数={own_metrics.image_count}, 内部リンク={own_metrics.internal_link_count}\n"
            f"競合: 文字数={competitor_metrics.char_count}, 画像数={competitor_metrics.image_count}, 内部リンク={competitor_metrics.internal_link_count}\n"
            "※自社が劣っている点、または競合の構成（見出し構造など）から学べる点を重点的に分析してください。\n\n"
            "【競合見出し構成】\n" + "\n".join(competitor_metrics.headings[:20]) + "\n"
        )

    return (
        "あなたはSEOアナリストです。以下の情報をもとに競合と自社の差分を分析し、"
        "改善アクションを日本語で箇条書きしてください。\n\n"
        f"キーワード: {keyword}\n"
        f"自社URL: {own_url}\n"
        f"{metrics_info}\n"
        "競合情報(検索結果上位):\n"
        f"{competitors}\n\n"
        "出力は短い箇条書きで、差分と改善アクションを分けてください。"
        "特にコンテンツ量や構成（見出し）の違いに着目してください。"
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
    lines = [
        "# 📊 SEO順位下落レポート",
        f"🕐 **実行日時**: {datetime.utcnow().isoformat()}Z",
        "",
        "---",
        ""
    ]
    
    for idx, report in enumerate(reports, 1):
        lines.append(f"## 🔍 キーワード {idx}: `{report.keyword}`")
        lines.append("")
        
        # Rank info with emoji
        prev = report.previous_rank or "不明"
        curr = report.rank or "未検出"
        lines.append(f"📉 **順位変動**: {prev}位 → **{curr}位**")
        lines.append("")
        
        # Metrics comparison table
        if report.own_metrics and report.competitor_metrics:
            om = report.own_metrics
            cm = report.competitor_metrics
            lines.append("### 📈 コンテンツ比較")
            lines.append("")
            lines.append("```")
            lines.append(f"{'項目':<12} {'自社':>8} {'競合':>8} {'差分':>8}")
            lines.append("-" * 40)
            
            char_diff = om.char_count - cm.char_count
            lines.append(f"{'文字数':<12} {om.char_count:>8,} {cm.char_count:>8,} {char_diff:>+8,}")
            
            heading_diff = len(om.headings) - len(cm.headings)
            lines.append(f"{'見出し数':<12} {len(om.headings):>8} {len(cm.headings):>8} {heading_diff:>+8}")
            
            img_diff = om.image_count - cm.image_count
            lines.append(f"{'画像数':<12} {om.image_count:>8} {cm.image_count:>8} {img_diff:>+8}")
            
            link_diff = om.internal_link_count - cm.internal_link_count
            lines.append(f"{'内部リンク':<12} {om.internal_link_count:>8} {cm.internal_link_count:>8} {link_diff:>+8}")
            lines.append("```")
            lines.append("")
        
        # Top competitors
        if report.competitors:
            lines.append("### 🏆 競合上位サイト")
            lines.append("")
            for i, competitor in enumerate(report.competitors[:3], 1):
                title = competitor.get('title', 'N/A')[:60]
                url = competitor.get('url', '')
                lines.append(f"{i}. **{title}**")
                lines.append(f"   🔗 {url}")
            lines.append("")
        
        # AI analysis
        if report.gaps:
            lines.append("### 🤖 AI分析結果")
            lines.append("")
            for gap in report.gaps:
                lines.append(f"• {gap}")
            lines.append("")
        
        lines.append("---")
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


def update_keyword_ranks(config: Config, entries: List[KeywordEntry], reports: List[SerpResult]) -> None:
    if config.dry_run:
        logging.info("DRY_RUN enabled. Skipping spreadsheet update.")
        return

    # Map keyword to result
    result_map = {r.keyword: r for r in reports if r.rank is not None}
    
    updates = []
    for entry in entries:
        if entry.keyword not in result_map:
            continue
        
        result = result_map[entry.keyword]
        # Previous rank is in Column C (index 2 in 0-indexed terms relative to row start? No, just Col C).
        # We need to construct a batchUpdate for value ranges.
        # But simpler is values().update().
        # Actually batchUpdate is better for multiple disconnected cells, but here we likely have many updates.
        # However, writing cell by cell is slow.
        # If we assume contiguous, we could write one block, but keywords might not be sorted or filtered in memory same as sheet.
        # But KeywordEntry has row_index.
        
        # We will use batchUpdate with ValueInputOption.USER_ENTERED
        # Range string example: "Sheet1!C5"
        
        # Extract sheet name from range config if present
        sheet_name_match = re.match(r"([^!]+)!", config.google_sheets_range)
        sheet_prefix = f"{sheet_name_match.group(1)}!" if sheet_name_match else ""
        
        cell_range = f"{sheet_prefix}C{entry.row_index}"
        updates.append({
            "range": cell_range,
            "values": [[result.rank]]
        })

    if not updates:
        return

    service_account_info = config.load_service_account()
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    
    if service_account_info:
        credentials = service_account.Credentials.from_service_account_info(
            service_account_info,
            scopes=scopes,
        )
    else:
        credentials, _ = google.auth.default(scopes=scopes)

    service = build("sheets", "v4", credentials=credentials)
    
    # Batch update values
    body = {
        "valueInputOption": "USER_ENTERED",
        "data": updates
    }
    
    service.spreadsheets().values().batchUpdate(
        spreadsheetId=config.google_sheets_spreadsheet_id,
        body=body
    ).execute()
    logging.info(f"Updated ranks for {len(updates)} keywords in spreadsheet.")


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
        serp_results: List[SerpResult] = []
        
        for entry in entries:
            serp_result = fetch_serp(config, entry.keyword)
            serp_results.append(serp_result)
            
            if not serp_result.rank or serp_result.rank > 10:
                continue
            if not is_downward(serp_result.rank, entry.previous_rank):
                continue
            
            # Analyze content
            own_metrics = None
            competitor_metrics = None
            
            # Identify competitor one rank above (or just the top one if we are #1? No, logic is "one above")
            target_pos = serp_result.rank - 1
            target_competitor = None
            for comp in serp_result.competitors:
                 p = comp.get("position")
                 if p and int(p) == target_pos:
                     target_competitor = comp
                     break
            
            # Fallback to top competitor if specific one not found or if we are rank 1
            if not target_competitor and serp_result.competitors:
                target_competitor = serp_result.competitors[0]

            if serp_result.own_url:
                logging.info(f"Analyzing own content: {serp_result.own_url}")
                own_metrics = analyze_page_content(serp_result.own_url, config.own_domain)
            
            if target_competitor:
                 comp_url = target_competitor.get("url")
                 if comp_url:
                     logging.info(f"Analyzing competitor content: {comp_url}")
                     competitor_metrics = analyze_page_content(comp_url)

            prompt = build_gemini_prompt(
                entry.keyword, 
                serp_result, 
                config.own_domain,
                own_metrics,
                competitor_metrics
            )
            gaps = request_gemini(config, prompt)
            reports.append(
                KeywordReport(
                    keyword=entry.keyword,
                    rank=serp_result.rank,
                    previous_rank=entry.previous_rank,
                    competitors=serp_result.competitors,
                    gaps=gaps,
                    own_metrics=own_metrics,
                    competitor_metrics=competitor_metrics
                )
            )

        # Update ranks in spreadsheet
        if serp_results:
             update_keyword_ranks(config, entries, serp_results)

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
