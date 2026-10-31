import os
import sys
import requests

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
MANUAL_RUN = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"

# ---------- تنظیمات ----------
TRADE_USDT = 1000        # حجم معامله فرضی
FEE_XT = 0.002           # کارمزد معامله XT (حدس: ۰.۲٪)
FEE_LBANK = 0.001        # کارمزد معامله LBank (حدس: ۰.۱٪)
PREFILTER_PCT = 0.5      # حداقل اختلاف با آخرین قیمت معامله برای بررسی دقیق
PREFILTER_MAX_PCT = 30   # اختلاف بیشتر از این معمولاً آشغاله و رد می‌شه
MAX_LBANK_BOOKS = 400    # حداکثر تعداد کوین برای بررسی دقیق در LBank
MIN_GROSS_PCT = 1.0      # حداقل اختلاف ناخالص برای بررسی بیشتر
MIN_NET_USDT = 5         # حداقل سود خالص (دلار) برای ارسال پیام
MIN_NET_PCT = 0.5        # حداقل سود خالص (درصد)
MIN_DEPTH_USDT = 300     # حداقل حجم موجود در بالای دفتر سفارش‌ها
SUSPECT_GROSS_PCT = 15   # اختلاف بالاتر از این مشکوکه
MAX_CANDIDATES = 15
TOP_N = 5

# در اجرای دستی (Run workflow) آستانه‌ها شل می‌شن تا محاسبه‌ها تست بشن.
# اجرای زمان‌بندی‌شده همیشه با مقدارهای بالا کار می‌کنه.
if MANUAL_RUN:
    MIN_GROSS_PCT = 0.1
    MIN_NET_USDT = -1000
    MIN_NET_PCT = -100
    MIN_DEPTH_USDT = 50


def num(x, allow_zero=False):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v < 0 or (v == 0 and not allow_zero):
        return None
    return v


def get(url, **params):
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def books_xt():
    data = get("https://sapi.xt.com/v4/public/ticker/book")["result"]
    out = {}
    for it in data:
        sym = str(it.get("s", "")).lower()
        if not sym.endswith("_usdt"):
            continue
        bid, bq = num(it.get("bp")), num(it.get("bq"))
        ask, aq = num(it.get("ap")), num(it.get("aq"))
        if bid and bq and ask and aq:
            out[sym] = (bid, bq, ask, aq)
    return out


def lbank_last_prices():
    j = get("https://api.lbkex.com/v2/ticker/24hr.do", symbol="all")
    data = j.get("data") if isinstance(j, dict) else None
    if not isinstance(data, list):
        raise RuntimeError("LBank ticker/24hr جواب غیرمنتظره داد: " + str(j)[:400])
    out = {}
    for it in data:
        sym = str(it.get("symbol", "")).lower()
        if not sym.endswith("_usdt"):
            continue
        p = num((it.get("ticker") or {}).get("latest"))
        if p:
            out[sym] = p
    return out


def lbank_book(sym):
    j = get(
        "https://api.lbkex.com/v2/supplement/ticker/bookTicker.do", symbol=sym
    )
    data = j.get("data") if isinstance(j, dict) else None
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict):
        raise RuntimeError(f"LBank bookTicker {sym} جواب غیرمنتظره داد: " + str(j)[:400])
    bid, bq = num(data.get("bidPrice")), num(data.get("bidQty"))
    ask, aq = num(data.get("askPrice")), num(data.get("askQty"))
    if bid and bq and ask and aq:
        return (bid, bq, ask, aq)
    return None


def chains_xt():
    data = get("https://sapi.xt.com/v4/public/currencies")["result"]
    if isinstance(data, dict):
        data = data.get("currencies") or data.get("list") or []
    out = {}
    for c in data:
        name = str(c.get("currency", "")).lower()
        out[name] = [
            {
                "wd": bool(ch.get("withdrawEnabled")),
                "dep": bool(ch.get("depositEnabled")),
                "fee": num(ch.get("withdrawFeeAmount"), True),
            }
            for ch in (c.get("supportChains") or [])
        ]
    return out


def chains_lbank(coin):
    j = get("https://api.lbkex.com/v2/withdrawConfigs.do", assetCode=coin)
    data = j.get("data") if isinstance(j, dict) else None
    if not isinstance(data, list):
        raise RuntimeError(f"LBank withdrawConfigs {coin}: " + str(j)[:300])
    return [
        {
            "wd": str(d.get("canWithDraw")).lower() == "true",
            "dep": None,
            "fee": num(d.get("fee"), True),
        }
        for d in data
    ]


def cheapest_withdraw(chains):
    ok = [c["fee"] for c in chains if c["wd"] and c["fee"] is not None]
    return min(ok) if ok else None


def fmt(p):
    return f"{p:.6g}"


def send(text):
    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text},
        timeout=15,
    )
    r.raise_for_status()


def main():
    xt = books_xt()
    lb_last = lbank_last_prices()
    common = sorted(set(xt) & set(lb_last))

    # مرحله ۱: پیش‌فیلتر با آخرین قیمت معامله
    pre = []
    for sym in common:
        xbid, xbq, xask, xaq = xt[sym]
        mid = (xbid + xask) / 2
        gap = abs(lb_last[sym] - mid) / mid * 100
        if PREFILTER_PCT <= gap <= PREFILTER_MAX_PCT:
            pre.append((gap, sym))
    pre.sort(reverse=True)

    # مرحله ۲: قیمت دقیق خرید/فروش LBank فقط برای کوین‌های پیش‌فیلتر شده
    lb = {}
    for gap, sym in pre[:MAX_LBANK_BOOKS]:
        try:
            b = lbank_book(sym)
        except Exception as e:
            print(e, file=sys.stderr)
            continue
        if b:
            lb[sym] = b

    gaps = []
    for sym in sorted(lb):
        xbid, xbq, xask, xaq = xt[sym]
        lbid, lbq, lask, laq = lb[sym]
        for buy, sell, ask, aq, bid, bq in (
            ("XT", "LBank", xask, xaq, lbid, lbq),
            ("LBank", "XT", lask, laq, xbid, xbq),
        ):
            gross = (bid - ask) / ask * 100
            depth = min(aq * ask, bq * bid)
            if gross > 0:
                gaps.append(
                    dict(sym=sym, buy=buy, sell=sell, ask=ask, bid=bid,
                         gross=gross, depth=depth)
                )
    gaps.sort(key=lambda g: g["gross"], reverse=True)
    cands = [
        g for g in gaps
        if g["gross"] >= MIN_GROSS_PCT and g["depth"] >= MIN_DEPTH_USDT
    ][:MAX_CANDIDATES]

    try:
        xt_chains = chains_xt()
    except Exception as e:
        print("خطا در دریافت اطلاعات کوین‌های XT:", e, file=sys.stderr)
        xt_chains = {}

    results = []
    for g in cands:
        coin = g["sym"][:-5]
        try:
            src = xt_chains.get(coin, []) if g["buy"] == "XT" else chains_lbank(coin)
        except Exception as e:
            print(f"خطا برای {coin}:", e, file=sys.stderr)
            continue
        wfee = cheapest_withdraw(src)
        if wfee is None:
            continue  # برداشت بسته یا نامشخص

        warn = []
        if g["sell"] == "XT":
            dst = xt_chains.get(coin, [])
            if dst and not any(c["dep"] for c in dst):
                continue  # واریز در XT بسته است
        else:
            warn.append("باز بودن واریز در LBank رو دستی چک کن")
        if g["gross"] >= SUSPECT_GROSS_PCT:
            warn.append("اختلاف خیلی زیاده؛ احتمالاً توکن متفاوت با اسم مشابه")

        usable = min(TRADE_USDT, g["depth"])
        fee_buy = FEE_XT if g["buy"] == "XT" else FEE_LBANK
        fee_sell = FEE_LBANK if g["buy"] == "XT" else FEE_XT
        qty = usable / g["ask"] * (1 - fee_buy) - wfee
        if qty <= 0:
            continue
        net = qty * g["bid"] * (1 - fee_sell) - usable
        net_pct = net / usable * 100
        if net >= MIN_NET_USDT and net_pct >= MIN_NET_PCT:
            results.append({**g, "wfee_usd": wfee * g["ask"], "usable": usable,
                            "net": net, "net_pct": net_pct, "warn": warn})
    results.sort(key=lambda r: r["net"], reverse=True)

    lines = []
    if results:
        title = "🔔 فرصت آربیتراژ XT ↔ LBank"
        if MANUAL_RUN:
            title = "🧪 حالت تست (آستانه‌ها شل شده)\n" + title
        lines.append(f"{title} (معامله فرضی {TRADE_USDT:,} $)")
        lines.append(f"کوین‌های مشترک: {len(common)} | بررسی دقیق: {len(lb)}")
        lines.append("")
        for r in results[:TOP_N]:
            coin = r["sym"][:-5].upper()
            lines.append(
                f"{coin}: بخر از {r['buy']} ({fmt(r['ask'])}) ← "
                f"بفروش در {r['sell']} ({fmt(r['bid'])})"
            )
            lines.append(
                f"اختلاف ناخالص: {r['gross']:.2f}% | "
                f"کارمزد برداشت: {r['wfee_usd']:.2f}$"
            )
            lines.append(
                f"سود خالص برای {r['usable']:,.0f}$: "
                f"{r['net']:.2f}$ ({r['net_pct']:.2f}%)"
            )
            for w in r["warn"]:
                lines.append(f"⚠️ {w}")
            lines.append("")
    elif MANUAL_RUN:
        lines.append("✅ اجرای تستی موفق بود")
        lines.append(f"تعداد کوین‌های مشترک (جفت USDT): {len(common)}")
        lines.append(f"بررسی دقیق شده: {len(lb)} کوین")
        lines.append("هیچ کوینی از فیلترها رد نشد.")
        real = [g for g in gaps if g["depth"] >= MIN_DEPTH_USDT]
        if real:
            lines.append("")
            lines.append(f"بیشترین اختلاف‌ها با حجم حداقل {MIN_DEPTH_USDT}$ (قبل از کارمزد):")
            for g in real[:TOP_N]:
                lines.append(
                    f"{g['sym'][:-5].upper()}: {g['gross']:.2f}% "
                    f"(بخر {g['buy']} ← بفروش {g['sell']}، حجم {g['depth']:,.0f}$)"
                )
    else:
        print("فرصتی پیدا نشد")
        return

    send("\n".join(lines))


if __name__ == "__main__":
    main()
