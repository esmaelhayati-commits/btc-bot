import os
import sys
import time
import pandas as pd
import requests

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
MANUAL = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"

FAST, SLOW = 50, 200
WINDOW_HOURS = 3.5   # سیگنال فقط وقتی فرستاده می‌شه که کندل تازه بسته شده باشه
HOSTS = ["https://data-api.binance.vision", "https://api.binance.com"]


def get_candles():
    last_err = None
    for h in HOSTS:
        try:
            r = requests.get(
                f"{h}/api/v3/klines",
                params=dict(symbol="BTCUSDT", interval="4h", limit=1000),
                timeout=20,
            )
            r.raise_for_status()
            df = pd.DataFrame(r.json()).iloc[:, [0, 4, 6]]
            df.columns = ["open_time", "close", "close_time"]
            df["close"] = df["close"].astype(float)
            df["close_time"] = df["close_time"].astype("int64")
            now_ms = int(time.time() * 1000)
            return df[df["close_time"] < now_ms].reset_index(drop=True)
        except Exception as e:
            last_err = e
            print("خطا در", h, e, file=sys.stderr)
    raise RuntimeError(f"دریافت قیمت ممکن نشد: {last_err}")


def send(text):
    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text},
        timeout=15,
    )
    r.raise_for_status()


def ts(ms):
    return pd.to_datetime(ms, unit="ms").strftime("%Y-%m-%d %H:%M UTC")


def main():
    df = get_candles()
    close = df["close"]
    ef = close.ewm(span=FAST, adjust=False).mean()
    es = close.ewm(span=SLOW, adjust=False).mean()
    bull = ef > es
    price = close.iloc[-1]

    cross_up = bool(bull.iloc[-1] and not bull.iloc[-2])
    cross_dn = bool((not bull.iloc[-1]) and bull.iloc[-2])
    age_h = (time.time() * 1000 - df["close_time"].iloc[-1]) / 3_600_000
    fresh = age_h <= WINDOW_HOURS

    if cross_up and fresh:
        send(
            "🟢 سیگنال خرید BTC (تایم‌فریم ۴ ساعته)\n"
            "EMA50 از EMA200 بالا زد.\n"
            f"قیمت بسته‌شدن کندل: {price:,.0f}$\n\n"
            "قانون: تا وقتی EMA50 بالای EMA200 هست نگه دار. "
            "سیگنال فروش وقتی میاد که پایین بیاد."
        )
    elif cross_dn and fresh:
        send(
            "🔴 سیگنال فروش BTC (تایم‌فریم ۴ ساعته)\n"
            "EMA50 از EMA200 پایین اومد.\n"
            f"قیمت بسته‌شدن کندل: {price:,.0f}$\n\n"
            "قانون: اگه با سیگنال خرید خریدی، بفروش و تا سیگنال خرید بعدی "
            "کنار بمون."
        )
    elif MANUAL:
        changed = bull != bull.shift()
        changed.iloc[:SLOW] = False   # میانگین‌ها اوایل داده هنوز جا نیفتادن
        idxs = changed[changed].index
        if len(idxs):
            i = idxs[-1]
            kind = "خرید" if bull.iloc[i] else "فروش"
            last = (
                f"آخرین سیگنال: {kind} در {ts(df['close_time'].iloc[i])} "
                f"با قیمت {close.iloc[i]:,.0f}$"
            )
        else:
            last = "در داده‌ی موجود سیگنالی نبود."
        state = "خرید (قانون می‌گه نگه دار)" if bull.iloc[-1] else "کنار (قانون می‌گه خارج بمون)"
        send(
            "🧪 پیام تستی\n"
            f"وضعیت فعلی: {state}\n"
            f"قیمت: {price:,.0f}$\n"
            f"EMA50: {ef.iloc[-1]:,.0f} | EMA200: {es.iloc[-1]:,.0f}\n"
            f"{last}\n\n"
            "در اجرای خودکار فقط وقتی سیگنال باشه پیام میاد."
        )
    else:
        print("سیگنال جدیدی نیست")


if __name__ == "__main__":
    main()
