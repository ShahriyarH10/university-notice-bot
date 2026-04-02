import requests 
from bs4 import BeautifulSoup
import os

# --- Configuration ---
NOTICE_URL = "https://www.aiub.edu/category/notices" 
LAST_NOTICE_FILE = "last_notice.txt"

# --- Securely fetch Telegram Credentials ---
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

def get_latest_notice():
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    try:
        response = requests.get(NOTICE_URL, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        notice_container = soup.find('ul', class_='info-list') or soup.find('div', class_='col-md-8')
        
        if notice_container:
            first_notice = notice_container.find('a')
            if first_notice:
                title = first_notice.text.strip()
                link = first_notice['href']
                if not link.startswith('http'):
                    link = "https://www.aiub.edu" + link
                return title, link
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
        response = requests.post(url, data=data)
        response.raise_for_status()
        print("Telegram alert sent successfully!")
    except Exception as e:
        print(f"Failed to send Telegram message: {e}")

def main():
    latest_title, latest_link = get_latest_notice()
    
    if not latest_title:
        print("Could not find any notices on the page.")
        return

    # Check against the last saved notice
    if os.path.exists(LAST_NOTICE_FILE):
        with open(LAST_NOTICE_FILE, "r", encoding="utf-8") as file:
            last_saved_title = file.read().strip()
    else:
        last_saved_title = ""

    # If new notice, alert and save
    if latest_title != last_saved_title:
        print(f"New notice found: {latest_title}")
        send_telegram_alert(latest_title, latest_link)
        
        with open(LAST_NOTICE_FILE, "w", encoding="utf-8") as file:
            file.write(latest_title)
    else:
        print("No new notices found. Standing by.")

if __name__ == "__main__":
    main()