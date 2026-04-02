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
    # Added a more complex User-Agent so AIUB's server doesn't mistake us for a spam bot
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    
    try:
        response = requests.get(NOTICE_URL, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Smart Parser: Look at every single link on the page
        for link in soup.find_all('a'):
            title = link.text.strip()
            href = link.get('href', '')
            
            # Notices have long, descriptive titles. Menus/Buttons have short titles.
            # We filter for links with more than 35 characters in the text, 
            # and ignore footer links that just spell out the university name.
            if len(title) > 35 and href and not href.startswith('#') and "American International University" not in title:
                
                # Format the link correctly if it's a relative path
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
