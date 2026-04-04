import os
import re
import requests
import logging
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)

NOTICE_URL       = "https://www.aiub.edu/category/notices"
LAST_NOTICE_FILE = "last_notice.txt"

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID   = os.getenv('TELEGRAM_CHAT_ID')
SCRAPER_API_KEY    = os.getenv('SCRAPER_API_KEY')

MAX_BODY_LENGTH = 600   # Telegram message limit is 4096 chars; keep body concise


def scrape_via_scraperapi(url: str) -> BeautifulSoup | None:
    """Fetch any AIUB page via ScraperAPI with render fallback."""
    attempts = [
        {'api_key': SCRAPER_API_KEY, 'url': url, 'render': 'true'},
        {'api_key': SCRAPER_API_KEY, 'url': url, 'render': 'false'},
    ]
    for payload in attempts:
        mode = payload['render']
        try:
            log.info("Fetching %s (render=%s)…", url, mode)
            response = requests.get(
                'http://api.scraperapi.com',
                params=payload,
                timeout=90
            )
            response.raise_for_status()
            return BeautifulSoup(response.text, 'html.parser')
        except requests.HTTPError as e:
            log.warning("render=%s → HTTP error: %s", mode, e)
        except requests.RequestException as e:
            log.warning("render=%s → network error: %s", mode, e)
    log.error("All ScraperAPI attempts failed for: %s", url)
    return None


def get_latest_notice():
    soup = scrape_via_scraperapi(NOTICE_URL)
    if not soup:
        return None, None, None, None

    notice_cards = [
        a for a in soup.find_all('a', href=True)
        if a.find('h2')
    ]

    if not notice_cards:
        log.warning("No notice cards found — page structure may have changed.")
        return None, None, None, None

    first = notice_cards[0]
    title = first.find('h2').get_text(strip=True)
    href  = first['href']
    if not href.startswith('http'):
        href = "https://www.aiub.edu" + href

    # Strict date extraction using regex
    # Matches "04 Apr 2026" (abbreviated) OR "04 April 2026" (full month name)
    card_text  = first.get_text(separator=' ', strip=True)
    log.info("DEBUG card_text: %r", card_text[:200])  # <-- add this line
    date_match = re.search(...)

    body = get_notice_body(href)
    return title, href, date, body


def get_notice_body(url: str) -> str:
    """Scrape only the notice body text from an individual notice page."""
    soup = scrape_via_scraperapi(url)
    if not soup:
        return "(Could not load notice body)"

    # --- Remove all noise elements first ---
    for tag in soup.select('nav, header, footer, script, style, .header, .footer, .navbar, .sidebar, .menu'):
        tag.decompose()

    # --- Strategy 1: Find a <div> or <section> that contains the actual notice ---
    # The notice body on AIUB pages sits between the title and the sidebar.
    # It is identifiable as the first large block of <p> tags that appears
    # AFTER the <h1> or <h2> title on the page.
    body_paragraphs = []

    title_tag = soup.find(['h1', 'h2'])
    if title_tag:
        # Walk siblings after the title to collect <p> content only
        for sibling in title_tag.find_all_next():
            tag_name = sibling.name

            # Stop when we hit the sidebar notice list (date + short title pattern)
            text = sibling.get_text(strip=True)
            if tag_name in ['ul', 'ol'] and len(text) < 200:
                break
            # Stop when we hit another notice card (contains a date div pattern)
            if tag_name == 'a' and sibling.find('h2'):
                break

            if tag_name == 'p' and text:
                body_paragraphs.append(text)
            elif tag_name in ['table', 'ul', 'ol'] and text:
                # Include tables/lists as plain text (e.g. seat plans)
                body_paragraphs.append(text)

    body = '\n\n'.join(body_paragraphs)

    # --- Strategy 2: fallback if nothing found ---
    if len(body) < 50:
        # Grab first 5 meaningful <p> tags from the whole page
        all_p = [p.get_text(strip=True) for p in soup.find_all('p') if len(p.get_text(strip=True)) > 30]
        body = '\n\n'.join(all_p[:5])

    # --- Truncate for Telegram ---
    if len(body) > MAX_BODY_LENGTH:
        body = body[:MAX_BODY_LENGTH].rsplit('\n', 1)[0] + "\n\n_(message truncated — see full notice below)_"

    return body or "(No body text found)"

def send_telegram_alert(title: str, link: str, date: str, body: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("Missing Telegram credentials.")
        return False

    message = (
        f"🔔 *New AIUB Notice*\n"
        f"📅 {date}\n\n"
        f"📋 *{title}*\n\n"
        f"{body}\n\n"
        f"🔗 [Read full notice]({link})"
    )

    # Telegram max message length is 4096 chars
    if len(message) > 4096:
        message = message[:4050] + "…\n\n🔗 [Read full notice](" + link + ")"

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={
                "chat_id":                  TELEGRAM_CHAT_ID,
                "text":                     message,
                "parse_mode":               "Markdown",
                "disable_web_page_preview": False,
            },
            timeout=15
        )
        resp.raise_for_status()
        result = resp.json()
        if not result.get("ok"):
            log.error("Telegram rejected message: %s", result)
            return False
        log.info("Telegram alert sent.")
        return True
    except requests.RequestException as e:
        log.error("Telegram request failed: %s", e)
        return False


def load_last_notice() -> str:
    if os.path.exists(LAST_NOTICE_FILE):
        with open(LAST_NOTICE_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    return ""


def save_last_notice(title: str) -> None:
    with open(LAST_NOTICE_FILE, "w", encoding="utf-8") as f:
        f.write(title)


def main():
    log.info("Checking for new AIUB notices…")

    title, link, date, body = get_latest_notice()

    if not title:
        log.warning("Could not retrieve notices. Exiting.")
        return

    if title != load_last_notice():
        log.info("New notice: %s", title)
        if send_telegram_alert(title, link, date, body):
            save_last_notice(title)
    else:
        log.info("No new notices. Standing by.")


if __name__ == "__main__":
    main()
