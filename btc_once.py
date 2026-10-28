import os
import sys
import requests

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]


def price_xt():
    r = requests.get(
        "https://sapi.xt.com/v4/public/ticker/price",
        params={"symbol": "btc_usdt"},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()["result"]
    item = data[0] if isinstance(data, list) else data
    return float(item["p"])


def price_lbank():
    r = requests.get(
        "https://api.lbkex.com/v2/supplement/ticker/price.do",
        params={"symbol": "btc_usdt"},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()["data"]
    item = data[0] if isinstance(data, list) else data
    return float(item["price"])


def safe(fn):
    try:
        return fn()
    except Exception as e:
        print(f"خطا در {fn.__name__}: {e}", file=sys.stderr)
        return None


def main():
    xt = safe(price_xt)
    lb = safe(price_lbank)

    if xt is None and lb is None:
        print("هیچ‌کدوم از صرافی‌ها جواب ندادن", file=sys.stderr)
        sys.exit(1)

    lines = ["💰 قیمت بیت‌کوین (BTC/USDT)", ""]
    lines.append(f"XT: {xt:,.2f} $" if xt else "XT: خطا در دریافت قیمت")
    lines.append(f"LBank: {lb:,.2f} $" if lb else "LBank: خطا در دریافت قیمت")

    if xt and lb:
        diff = xt - lb
        pct = diff / lb * 100
        lines += ["", f"اختلاف: {diff:+,.2f} $ ({pct:+.3f}%)"]

    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": "\n".join(lines)},
        timeout=10,
    )
    r.raise_for_status()


if __name__ == "__main__":
    main()
