```python
import os
import re
import requests

print("Starting Telegram bot test...")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# اگر تنظیمات در bot.py باشند، تلاش می‌کنیم آن‌ها را بخوانیم.
if not TOKEN or not CHAT_ID:
    try:
        with open("bot.py", "r", encoding="utf-8") as f:
            code = f.read()

        if not TOKEN:
            patterns = [
                r"""TOKEN\s*=\s*["']([^"']+)["']""",
                r"""BOT_TOKEN\s*=\s*["']([^"']+)["']""",
                r"""TELEGRAM_BOT_TOKEN\s*=\s*["']([^"']+)["']"""
            ]
            for pattern in patterns:
                match = re.search(pattern, code)
                if match:
                    TOKEN = match.group(1)
                    break

        if not CHAT_ID:
            patterns = [
                r"""CHAT_ID\s*=\s*["']([^"']+)["']""",
                r"""TELEGRAM_CHAT_ID\s*=\s*["']([^"']+)["']"""
            ]
            for pattern in patterns:
                match = re.search(pattern, code)
                if match:
                    CHAT_ID = match.group(1)
                    break

    except Exception as e:
        print("Could not read bot.py:", e)

if not TOKEN:
    print("ERROR: Bot token was not found.")
    print("Check where the token is configured in bot.py.")
    raise SystemExit(1)

if not CHAT_ID:
    print("ERROR: Chat ID was not found.")
    print("The bot needs a destination chat ID to send a message.")
    raise SystemExit(1)

print("Token found.")
print("Chat ID found.")
print("Sending test message...")

url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

try:
    response = requests.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": "✅ تست بات تلگرام موفق بود!\nGitHub Actions is working."
        },
        timeout=20
    )

    print("HTTP Status:", response.status_code)
    print("Telegram Response:", response.text)

    response.raise_for_status()

    if response.json().get("ok"):
        print("SUCCESS: Message sent to Telegram.")
    else:
        print("ERROR: Telegram did not confirm the message.")

except requests.RequestException as e:
    print("ERROR sending Telegram message:", e)
```
