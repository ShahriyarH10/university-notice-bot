import os
import re
import logging
import cloudscraper
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

MAX_BODY_LENGTH = 600

# One shared scraper session for all requests
scraper = cloudscraper.create_scraper(
    browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False}
)


def fetch_page(url: str) -> BeautifulSoup | None:
    """Fetch any AIUB page using cloudscraper (bypasses Cloudflare for free)."""
    try:
        log.info("Fetching %s…", url)
        response = scraper.get(url, timeout=30)
        response.raise_for_status()
        return BeautifulSoup(response.text, 'html.parser')
    except Exception as e:
        log.error("Failed to fetch %s: %s", url, e)
        return None


def get_latest_notice():
    """Return (title, link, date, body) of the most recent notice."""
    soup = fetch_page(NOTICE_URL)
    if not soup:
        return None, None, None, None

    # Each notice card is an <a href="..."> that directly contains an <h2>
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

    # Strict date extraction — handles both "Apr" and "April" format
    card_text  = first.get_text(separator=' ', strip=True)
    log.info("DEBUG card_text: %r", card_text[:300])
    date_match = re.search(
        r'(\d{1,2})\s+(January|February|March|April|May|June|July|August'
        r'|September|October|November|December|Jan|Feb|Mar|Apr|May|Jun'
        r'|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})',
        card_text
    )
    if date_match:
        date = f"{date_match.group(1)} {date_match.group(2)} {date_match.group(3)}"
    else:
        date = 'Date unavailable'

    body = get_notice_body(href)
    return title, href, date, body


def get_notice_body(url: str) -> str:
    """Scrape only the notice body text from an individual notice page."""
    soup = fetch_page(url)
    if not soup:
        return "(Could not load notice body)"

    # Remove all noise elements
    for tag in soup.select('nav, header, footer, script, style, .header, .footer, .navbar, .sidebar, .menu'):
        tag.decompose()

    # Walk siblings after the <h1>/<h2> title, stop before sidebar content
    body_paragraphs = []
    title_tag = soup.find(['h1', 'h2'])
    if title_tag:
        for sibling in title_tag.find_all_next():
            tag_name = sibling.name
            text     = sibling.get_text(strip=True)

            # Stop when we hit another notice card (sidebar)
            if tag_name == 'a' and sibling.find('h2'):
                break
            if tag_name in ['ul', 'ol'] and len(text) < 200:
                break

            if tag_name == 'p' and text:
                body_paragraphs.append(text)
            elif tag_name in ['table', 'ul', 'ol'] and text:
                body_paragraphs.append(text)

    body = '\n\n'.join(body_paragraphs)

    # Fallback — grab first 5 meaningful <p> tags
    if len(body) < 50:
        all_p = [p.get_text(strip=True) for p in soup.find_all('p') if len(p.get_text(strip=True)) > 30]
        body  = '\n\n'.join(all_p[:5])

    # Truncate for Telegram
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
        import requests
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
    except Exception as e:
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
