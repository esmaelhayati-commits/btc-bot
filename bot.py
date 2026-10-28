import os
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

XT_URL = "https://sapi.xt.com/v4/public/ticker/price"
LBANK_URL = "https://api.lbank.info/v2/supplement/ticker/price.do"

def get_xt_price():
    response = requests.get(
        XT_URL,
        params={"symbol": "btc_usdt"},
        timeout=15
    )
    response.raise_for_status()
    data = response.json()

    if data.get("rc") != 0 or not data.get("result"):
        raise ValueError(f"XT API error: {data}")

    return float(data["result"][0]["p"])


def get_lbank_price():
    response = requests.get(
        LBANK_URL,
        params={"symbol": "btc_usdt"},
        timeout=15
    )
    response.raise_for_status()
    data = response.json()

    if data.get("result") is not True:
        raise ValueError(f"LBank API error: {data}")

    return float(data["data"][0]["price"])


def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    response = requests.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": message
        },
        timeout=15
    )
    response.raise_for_status()

    result = response.json()
    if not result.get("ok"):
        raise ValueError(f"Telegram API error: {result}")


def main():
    xt_price = get_xt_price()
    lbank_price = get_lbank_price()

    difference = abs(xt_price - lbank_price)
    reference_price = (xt_price + lbank_price) / 2
    percentage = (difference / reference_price) * 100

    now = datetime.now(ZoneInfo("Asia/Tehran"))
    next_update = (now.minute // 10 + 1) * 10

    if next_update == 60:
        next_update = 0
        next_hour = (now.hour + 1) % 24
        next_time = f"{next_hour:02d}:{next_update:02d}"
    else:
        next_time = f"{now.hour:02d}:{next_update:02d}"

    message = (
        "₿ گزارش قیمت بیت‌کوین\n\n"
        f"🏦 XT: {xt_price:,.2f} USDT\n"
        f"🏦 LBank: {lbank_price:,.2f} USDT\n\n"
        f"📊 اختلاف قیمت: {difference:,.2f} USDT\n"
        f"📈 اختلاف درصدی: {percentage:.4f}%\n\n"
        f"🕒 زمان گزارش: {now.strftime('%H:%M:%S')}\n"
        f"🔄 به‌روزرسانی بعدی: حدود {next_time}"
    )

    send_telegram(message)
    print(message)


if __name__ == "__main__":
    main()
