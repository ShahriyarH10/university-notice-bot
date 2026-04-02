import os
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


def get_latest_notice():
    # Try render=true first, fall back to render=false if it fails
    attempts = [
        {'api_key': SCRAPER_API_KEY, 'url': NOTICE_URL, 'render': 'true'},
        {'api_key': SCRAPER_API_KEY, 'url': NOTICE_URL, 'render': 'false'},
    ]

    for payload in attempts:
        mode = payload['render']
        try:
            log.info("Trying ScraperAPI with render=%s…", mode)
            response = requests.get(
                'http://api.scraperapi.com',
                params=payload,
                timeout=90          # increased from 60 — render mode needs more time
            )
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')

            notice_cards = [
                a for a in soup.find_all('a', href=True)
                if a.find('h2')
            ]

            if not notice_cards:
                log.warning("render=%s: page loaded but no notice cards found.", mode)
                continue            # try next attempt

            first = notice_cards[0]
            title = first.find('h2').get_text(strip=True)
            href  = first['href']

            if not href.startswith('http'):
                href = "https://www.aiub.edu" + href

            log.info("Success with render=%s", mode)
            return title, href

        except requests.HTTPError as e:
            log.warning("render=%s failed with HTTP error: %s — trying next option.", mode, e)
        except requests.RequestException as e:
            log.warning("render=%s failed with network error: %s — trying next option.", mode, e)
        except Exception as e:
            log.error("Unexpected error with render=%s: %s", mode, e)

    log.error("All ScraperAPI attempts exhausted.")
    return None, None

def send_telegram_alert(title: str, link: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("Missing Telegram credentials.")
        return False

    message = (
        "🔔 *New AIUB Notice*\n\n"
        f"📋 {title}\n\n"
        f"🔗 [Read more]({link})"
    )
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

    title, link = get_latest_notice()

    if not title:
        log.warning("Could not retrieve notices. Exiting.")
        return

    if title != load_last_notice():
        log.info("New notice: %s", title)
        if send_telegram_alert(title, link):
            save_last_notice(title)
    else:
        log.info("No new notices. Standing by.")


if __name__ == "__main__":
    main()
