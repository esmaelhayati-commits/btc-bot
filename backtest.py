import os
import requests
import sys

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

print("Starting Telegram test...")

if not TOKEN or not CHAT_ID:
    print("ERROR: Telegram secrets are missing!")
    print("Check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
    sys.exit(1)

url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

try:
    response = requests.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": "✅ تست بات بیت‌کوین موفق بود!\nGitHub Actions is working."
        },
        timeout=20
    )

    print("HTTP Status:", response.status_code)
    print("Telegram Response:", response.text)

    response.raise_for_status()

    result = response.json()

    if result.get("ok"):
        print("SUCCESS: Telegram message sent!")
    else:
        print("ERROR: Telegram rejected the message.")
        sys.exit(1)

except Exception as e:
    print("TELEGRAM ERROR:", str(e))
    sys.exit(1)
