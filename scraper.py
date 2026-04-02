import os
import requests
import logging
from datetime import datetime
from bs4 import BeautifulSoup

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)

# --- Configuration ---
NOTICE_URL = "https://www.aiub.edu/category/notices"
LAST_NOTICE_FILE = "last_notice.txt"

# --- Securely fetch Credentials ---
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
SCRAPER_API_KEY = os.getenv('SCRAPER_API_KEY')


def get_latest_notice():
    """Scrape the AIUB notices page and return the latest (title, url)."""
    try:
        payload = {
            'api_key': SCRAPER_API_KEY,
            'url': NOTICE_URL,
            'render': 'true',
        }
        response = requests.get(
            'http://api.scraperapi.com',
            params=payload,
            timeout=60
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')

        # --- More targeted selector ---
        # AIUB notice pages typically wrap each post in an <article> or a
        # dedicated list; adjust the selector if the structure ever changes.
        # Priority 1: look for a <ul>/<ol> with class containing "notice"
        notice_links = soup.select("ul.notice-list a, ol.notice-list a")

        # Priority 2: fall back to article headings
        if not notice_links:
            notice_links = soup.select("article h2 a, article h3 a")

        # Priority 3: last-resort heuristic (your original logic, refined)
        if not notice_links:
            notice_links = [
                a for a in soup.find_all('a')
                if len(a.get_text(strip=True)) > 35
                and a.get('href', '')
                and not a['href'].startswith('#')
                and "American International University" not in a.get_text()
            ]

        if not notice_links:
            log.warning("No notice links found — page structure may have changed.")
            return None, None

        # Take the first (most recent) result
        first = notice_links[0]
        title = first.get_text(strip=True)
        href = first['href']
        if not href.startswith('http'):
            href = "https://www.aiub.edu" + href

        return title, href

    except requests.RequestException as e:
        log.error("Network error while fetching notices: %s", e)
    except Exception as e:
        log.error("Unexpected error in get_latest_notice: %s", e)

    return None, None


def send_telegram_alert(title: str, link: str) -> bool:
    """Send a Telegram message. Returns True on success."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("Missing Telegram credentials — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")
        return False

    message = (
        "🚨 *New AIUB Notice!* 🚨\n\n"
        f"📋 {title}\n\n"
        f"🔗 [Read more]({link})"
    )
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }

    try:
        resp = requests.post(url, data=payload, timeout=15)
        resp.raise_for_status()
        result = resp.json()
        if not result.get("ok"):
            log.error("Telegram API rejected the message: %s", result)
            return False
        log.info("Telegram alert sent successfully.")
        return True
    except requests.RequestException as e:
        log.error("Failed to send Telegram message: %s", e)
        return False


def load_last_notice() -> str:
    """Read the previously saved notice title from disk."""
    if os.path.exists(LAST_NOTICE_FILE):
        with open(LAST_NOTICE_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    return ""


def save_last_notice(title: str) -> None:
    """Persist the latest notice title to disk."""
    with open(LAST_NOTICE_FILE, "w", encoding="utf-8") as f:
        f.write(title)


def main():
    log.info("Checking for new AIUB notices…")

    latest_title, latest_link = get_latest_notice()

    if not latest_title:
        log.warning("Could not retrieve any notices. Exiting.")
        return

    last_saved = load_last_notice()

    if latest_title != last_saved:
        log.info("New notice detected: %s", latest_title)
        sent = send_telegram_alert(latest_title, latest_link)
        if sent:
            save_last_notice(latest_title)  # only persist after confirmed delivery
    else:
        log.info("No new notices. Standing by.")


if __name__ == "__main__":
    main()
