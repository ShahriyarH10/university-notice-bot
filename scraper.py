import os
import re
import json
import logging
import requests
import cloudscraper
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)

NOTICE_URL       = "https://www.aiub.edu/category/notices"
LAST_NOTICE_FILE = "last_notices.json"  # changed from .txt to .json

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID   = os.getenv('TELEGRAM_CHAT_ID')

MAX_BODY_LENGTH = 600

scraper = cloudscraper.create_scraper(
    browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False}
)


def fetch_page(url: str) -> BeautifulSoup | None:
    try:
        log.info("Fetching %s…", url)
        response = scraper.get(url, timeout=30)
        response.raise_for_status()
        return BeautifulSoup(response.text, 'html.parser')
    except Exception as e:
        log.error("Failed to fetch %s: %s", url, e)
        return None


def get_all_notices() -> list:
    """Return a list of (title, href, date) for ALL notices on the page."""
    soup = fetch_page(NOTICE_URL)
    if not soup:
        return []

    notice_cards = [
        a for a in soup.find_all('a', href=True)
        if a.find('h2')
    ]

    if not notice_cards:
        log.warning("No notice cards found — page structure may have changed.")
        return []

    notices = []
    for card in notice_cards:
        title = card.find('h2').get_text(strip=True)
        href  = card['href']
        if not href.startswith('http'):
            href = "https://www.aiub.edu" + href

        card_text  = card.get_text(separator=' ', strip=True)
        date_match = re.search(
            r'(\d{1,2})\s+(January|February|March|April|May|June|July|August'
            r'|September|October|November|December|Jan|Feb|Mar|Apr|May|Jun'
            r'|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})',
            card_text
        )
        date = f"{date_match.group(1)} {date_match.group(2)} {date_match.group(3)}" if date_match else 'Date unavailable'

        notices.append({'title': title, 'href': href, 'date': date})

    log.info("Found %d notices on page.", len(notices))
    return notices


def get_notice_body(url: str) -> str:
    soup = fetch_page(url)
    if not soup:
        return "(Could not load notice body)"

    for tag in soup.select('nav, header, footer, script, style, .header, .footer, .navbar, .sidebar, .menu'):
        tag.decompose()

    body_paragraphs = []
    title_tag = soup.find(['h1', 'h2'])
    if title_tag:
        for sibling in title_tag.find_all_next():
            tag_name = sibling.name
            text     = sibling.get_text(strip=True)

            if tag_name == 'a' and sibling.find('h2'):
                break
            if tag_name in ['ul', 'ol'] and len(text) < 200:
                break

            if tag_name == 'p' and text:
                body_paragraphs.append(text)
            elif tag_name in ['table', 'ul', 'ol'] and text:
                body_paragraphs.append(text)

    body = '\n\n'.join(body_paragraphs)

    if len(body) < 50:
        all_p = [p.get_text(strip=True) for p in soup.find_all('p') if len(p.get_text(strip=True)) > 30]
        body  = '\n\n'.join(all_p[:5])

    if len(body) > MAX_BODY_LENGTH:
        body = body[:MAX_BODY_LENGTH].rsplit('\n', 1)[0] + "\n\n_(truncated — see full notice below)_"

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

    if len(message) > 4096:
        message = message[:4050] + f"…\n\n🔗 [Read full notice]({link})"

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
        log.info("Telegram alert sent for: %s", title)
        return True
    except Exception as e:
        log.error("Telegram request failed: %s", e)
        return False


def load_seen_notices() -> set:
    """Load the set of already-sent notice URLs from disk."""
    if os.path.exists(LAST_NOTICE_FILE):
        with open(LAST_NOTICE_FILE, "r", encoding="utf-8") as f:
            try:
                return set(json.load(f))
            except json.JSONDecodeError:
                return set()
    return set()


def save_seen_notices(seen: set) -> None:
    """Persist the set of sent notice URLs to disk."""
    with open(LAST_NOTICE_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen), f, indent=2)


def main():
    log.info("Checking for new AIUB notices…")

    all_notices = get_all_notices()
    if not all_notices:
        log.warning("Could not retrieve notices. Exiting.")
        return

    seen = load_seen_notices()
    new_notices = [n for n in all_notices if n['href'] not in seen]

    if not new_notices:
        log.info("No new notices. Standing by.")
        return

    # Send oldest first so they appear in chronological order in Telegram
    for notice in reversed(new_notices):
        log.info("New notice found: %s", notice['title'])
        body = get_notice_body(notice['href'])
        sent = send_telegram_alert(notice['title'], notice['href'], notice['date'], body)
        if sent:
            seen.add(notice['href'])
            save_seen_notices(seen)  # save after each successful send

    log.info("Done. %d new notice(s) sent.", len(new_notices))


if __name__ == "__main__":
    main()
