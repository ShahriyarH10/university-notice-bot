import os
import requests
from bs4 import BeautifulSoup

# --- Configuration ---
NOTICE_URL = "https://www.aiub.edu/category/notices" 
LAST_NOTICE_FILE = "last_notice.txt"

# --- Securely fetch Credentials ---
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
SCRAPER_API_KEY = os.getenv('SCRAPER_API_KEY')

def get_latest_notice():
    try:
        # Route our request through ScraperAPI's residential proxy network
        payload = {'api_key': SCRAPER_API_KEY, 'url': NOTICE_URL}
        response = requests.get('http://api.scraperapi.com', params=payload)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        for link in soup.find_all('a'):
            title = link.text.strip()
            href = link.get('href', '')
            
            # Filter for long notice titles
            if len(title) > 35 and href and not href.startswith('#') and "American International University" not in title:
                if not href.startswith('http'):
                    href = "https://www.aiub.edu" + href
                return title, href
                
    except Exception as e:
        print(f"Error fetching the webpage: {e}")
        
    return None, None

def send_telegram_alert(title, link):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Missing Telegram credentials!")
        return

    message = f"🚨 *New University Notice!* 🚨\n\n{title}\n\nRead more: {link}"
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": TELEGRAM_CHAT_ID, 
        "text": message, 
        "parse_mode": "Markdown"
    }
    
    try:
        requests.post(url, data=data)
        print("Telegram alert sent successfully!")
    except Exception as e:
        print(f"Failed to send Telegram message: {e}")

def main():
    latest_title, latest_link = get_latest_notice()
    
    if not latest_title:
        print("Could not find any notices on the page.")
        return

    if os.path.exists(LAST_NOTICE_FILE):
        with open(LAST_NOTICE_FILE, "r", encoding="utf-8") as file:
            last_saved_title = file.read().strip()
    else:
        last_saved_title = ""

    if latest_title != last_saved_title:
        print(f"New notice found: {latest_title}")
        send_telegram_alert(latest_title, latest_link)
        
        with open(LAST_NOTICE_FILE, "w", encoding="utf-8") as file:
            file.write(latest_title)
    else:
        print("No new notices found. Standing by.")

if __name__ == "__main__":
    main()
