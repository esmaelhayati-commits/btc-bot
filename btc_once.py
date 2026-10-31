import html
import os
import sys
import requests

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
MANUAL_RUN = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"

# ---------- تنظیمات ----------
TRADE_USDT = 100         # حداکثر سرمایه برای هر معامله
FEE_XT = 0.002           # کارمزد معامله XT (حدس: ۰.۲٪)
FEE_LBANK = 0.001        # کارمزد معامله LBank (حدس: ۰.۱٪)
PREFILTER_PCT = 0.5      # حداقل اختلاف با آخرین قیمت معامله برای بررسی دقیق
PREFILTER_MAX_PCT = 30   # اختلاف بیشتر از این معمولاً آشغاله و رد می‌شه
MAX_LBANK_BOOKS = 400    # حداکثر تعداد کوین برای بررسی دقیق در LBank
MIN_GROSS_PCT = 1.0      # حداقل اختلاف ناخالص برای بررسی بیشتر
MIN_NET_USDT = 1         # حداقل سود خالص (دلار) برای ارسال پیام
MIN_NET_PCT = 0.8        # حداقل سود خالص (درصد)
MIN_DEPTH_USDT = 100     # حداقل حجم موجود در بالای دفتر سفارش‌ها
TARGET_NET_USDT = 1      # برای نمایش «حجم لازم برای این مقدار سود»
SUSPECT_GROSS_PCT = 15   # اختلاف بالاتر از این مشکوکه
MAX_CANDIDATES = 15
TOP_N = 5
SEP = "━━━━━━━━━━━━━━"

# در اجرای دستی (Run workflow) آستانه‌ها شل می‌شن تا محاسبه‌ها تست بشن.
# اجرای زمان‌بندی‌شده همیشه با مقدارهای بالا کار می‌کنه.
if MANUAL_RUN:
    MIN_GROSS_PCT = -100
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
                "chain": ch.get("chain"),
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
            "chain": d.get("chain"),
            "wd": str(d.get("canWithDraw")).lower() == "true",
            "dep": None,
            "fee": num(d.get("fee"), True),
        }
        for d in data
    ]


def cheapest_withdraw(chains):
    ok = [
        (c["fee"], str(c.get("chain") or "؟"))
        for c in chains
        if c["wd"] and c["fee"] is not None
    ]
    return min(ok) if ok else None


def fmt(p):
    return f"{p:.6g}"


def send(text):
    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=15,
    )
    r.raise_for_status()


def format_result(r):
    coin = html.escape(r["sym"][:-5].upper())
    icon = "✅" if r["net"] >= 0 else "❌"
    sign = "+" if r["net"] >= 0 else ""
    parts = [
        f"🪙 <b>{coin}</b>",
        f"🟢 خرید از <b>{r['buy']}</b>: <code>{fmt(r['ask'])}</code>",
        f"🔴 فروش در <b>{r['sell']}</b>: <code>{fmt(r['bid'])}</code>",
        f"📊 اختلاف ناخالص: <code>{r['gross']:.2f}%</code>",
        f"💸 کارمزد معامله: <code>{r['trade_fee']:.2f}$</code> + "
        f"برداشت: <code>{r['wfee_usd']:.2f}$</code> = "
        f"<code>{r['trade_fee'] + r['wfee_usd']:.2f}$</code>",
        f"🔗 شبکه برداشت: <code>{html.escape(r['chain'])}</code>",
        f"{icon} سود خالص ({r['usable']:,.0f}$): "
        f"<b>{sign}{r['net']:.2f}$ ({sign}{r['net_pct']:.2f}%)</b>",
    ]
    if r["be"] is None:
        parts.append("📏 با این اختلاف هیچ حجمی سودآور نیست")
    else:
        parts.append(
            f"📏 حجم سر‌به‌سر: <code>{r['be']:,.0f}$</code> | "
            f"برای سود {TARGET_NET_USDT}$: <code>{r['need']:,.0f}$</code> "
            f"(حداکثر قابل خرید: {r['depth']:,.0f}$)"
        )
    for w in r["warn"]:
        parts.append(f"⚠️ {html.escape(w)}")
    return "\n".join(parts)


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
            if gross > 0 or MANUAL_RUN:
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
        best = cheapest_withdraw(src)
        if best is None:
            continue  # برداشت بسته یا نامشخص
        wfee, chain = best

        warn = []
        if g["sell"] == "XT":
            dst = xt_chains.get(coin, [])
            if dst and not any(c["dep"] for c in dst):
                continue  # واریز در XT بسته است
        else:
            warn.append("باز بودن واریز در LBank (برای همین شبکه) رو دستی چک کن")
        if g["gross"] >= SUSPECT_GROSS_PCT:
            warn.append("اختلاف خیلی زیاده؛ احتمالاً توکن متفاوت با اسم مشابه")

        fee_buy = FEE_XT if g["buy"] == "XT" else FEE_LBANK
        fee_sell = FEE_LBANK if g["buy"] == "XT" else FEE_XT
        ask, bid = g["ask"], g["bid"]

        usable = min(TRADE_USDT, g["depth"])
        qty = usable / ask * (1 - fee_buy) - wfee
        if qty <= 0:
            continue
        sell_gross = qty * bid
        net = sell_gross * (1 - fee_sell) - usable
        net_pct = net / usable * 100
        trade_fee = usable * fee_buy + sell_gross * fee_sell

        # حجم سر‌به‌سر: net(S) = S*k - fixed
        k = bid * (1 - fee_buy) * (1 - fee_sell) / ask - 1
        fixed = wfee * bid * (1 - fee_sell)
        if k > 0:
            be = fixed / k
            need = (TARGET_NET_USDT + fixed) / k
        else:
            be = need = None

        if net >= MIN_NET_USDT and net_pct >= MIN_NET_PCT:
            results.append({
                **g, "wfee_usd": wfee * ask, "trade_fee": trade_fee,
                "chain": chain, "usable": usable, "net": net,
                "net_pct": net_pct, "be": be, "need": need, "warn": warn,
            })
    results.sort(key=lambda r: r["net"], reverse=True)

    if results:
        header = "🔔 <b>فرصت آربیتراژ XT ⇄ LBank</b>"
        if MANUAL_RUN:
            header = "🧪 <b>حالت تست</b> (سود منفی عادیه)\n" + header
        info = (
            f"حداکثر سرمایه: {TRADE_USDT:,}$ | "
            f"بررسی‌شده: {len(lb)} از {len(common)} کوین"
        )
        body = ("\n" + SEP + "\n").join(format_result(r) for r in results[:TOP_N])
        text = f"{header}\n{info}\n{SEP}\n{body}\n{SEP}"
    elif MANUAL_RUN:
        text = (
            "✅ <b>اجرای تستی موفق بود</b>\n"
            f"کوین‌های مشترک (جفت USDT): {len(common)}\n"
            f"بررسی دقیق شده: {len(lb)} کوین\n"
            "هیچ کوینی از فیلترها عبور نکرد."
        )
    else:
        print("فرصتی پیدا نشد")
        return

    send(text)


if __name__ == "__main__":
    main()
