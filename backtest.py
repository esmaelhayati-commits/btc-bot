import os
import sys
import time
import numpy as np
import pandas as pd
import requests

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# ---------- تنظیمات ----------
YEARS = 2
FEE = 0.001            # کارمزد هر طرف (۰.۱٪)
SLIP = 0.0002          # اسلیپیج هر طرف (۰.۰۲٪)
COST = FEE + SLIP
SPLIT = 0.6            # ۶۰٪ اول IS، ۴۰٪ آخر OOS
BPY = 365 * 288        # تعداد کندل ۵ دقیقه‌ای در سال
TOP_N = 8


# ---------- دریافت داده ----------
def fetch_binance(years):
    hosts = ["https://data-api.binance.vision", "https://api.binance.com"]
    end_ms = int(time.time() * 1000)
    cur = end_ms - years * 365 * 24 * 3600 * 1000
    rows, host = [], None
    while cur < end_ms:
        data = None
        for h in ([host] if host else hosts):
            try:
                r = requests.get(
                    f"{h}/api/v3/klines",
                    params=dict(symbol="BTCUSDT", interval="5m",
                                startTime=cur, limit=1000),
                    timeout=20,
                )
                r.raise_for_status()
                data = r.json()
                host = h
                break
            except Exception as e:
                print("خطا در", h, e, file=sys.stderr)
        if data is None:
            raise RuntimeError("دریافت داده از بایننس ممکن نشد")
        if not data:
            break
        rows += data
        cur = data[-1][0] + 5 * 60 * 1000
        if len(data) < 1000:
            break
        time.sleep(0.12)
    df = pd.DataFrame(rows).iloc[:, :6]
    df.columns = ["t", "open", "high", "low", "close", "volume"]
    df.index = pd.to_datetime(df["t"].astype("int64"), unit="ms")
    df = df[["open", "high", "low", "close"]].astype(float)
    return df.iloc[:-1]


def fetch_coinbase(years):
    end = pd.Timestamp.now(tz="UTC").floor("5min")
    cur = end - pd.Timedelta(days=365 * years)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    rows = []
    while cur < end:
        nxt = min(cur + pd.Timedelta(minutes=5 * 299), end)
        data = None
        for attempt in range(6):
            try:
                r = requests.get(
                    "https://api.exchange.coinbase.com/products/BTC-USD/candles",
                    params=dict(granularity=300, start=cur.strftime(fmt),
                                end=nxt.strftime(fmt)),
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=20,
                )
                if r.status_code == 429:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                r.raise_for_status()
                data = r.json()
                break
            except Exception as e:
                print("خطا Coinbase:", e, file=sys.stderr)
                time.sleep(1.5)
        if data is None:
            raise RuntimeError("دریافت داده از Coinbase ممکن نشد")
        rows += data
        cur = nxt + pd.Timedelta(minutes=5)
        time.sleep(0.12)
    df = pd.DataFrame(rows, columns=["t", "low", "high", "open", "close", "volume"])
    df = df.drop_duplicates("t").sort_values("t")
    df.index = pd.to_datetime(df["t"].astype("int64"), unit="s")
    return df[["open", "high", "low", "close"]].astype(float).iloc[:-1]


def load():
    try:
        return fetch_binance(YEARS), "Binance BTCUSDT"
    except Exception as e:
        print("بایننس جواب نداد:", e, file=sys.stderr)
    return fetch_coinbase(YEARS), "Coinbase BTC-USD"


# ---------- قانون‌ها (خروجی: موقعیت -1/0/+1 بعد از بسته شدن کندل) ----------
def ema_pos(df, f, s):
    ef = df.close.ewm(span=f, adjust=False).mean()
    es = df.close.ewm(span=s, adjust=False).mean()
    pos = np.sign(ef - es).fillna(0)
    pos.iloc[:s] = 0
    return pos


def donchian_pos(df, n):
    hh = df.high.rolling(n).max().shift(1)
    ll = df.low.rolling(n).min().shift(1)
    sig = pd.Series(np.nan, index=df.index)
    sig[df.close > hh] = 1.0
    sig[df.close < ll] = -1.0
    return sig.ffill().fillna(0)


def supertrend_pos(df, length, mult):
    h, l, c = df.high.values, df.low.values, df.close.values
    prev_c = np.roll(c, 1)
    prev_c[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))
    atr = pd.Series(tr).rolling(length).mean().values
    hl2 = (h + l) / 2
    ub, lb = hl2 + mult * atr, hl2 - mult * atr
    n = len(c)
    fub, flb = ub.copy(), lb.copy()
    out = np.zeros(n)
    d = 1
    for i in range(n):
        if np.isnan(atr[i]):
            continue
        if i == 0 or np.isnan(atr[i - 1]):
            d = 1 if c[i] > hl2[i] else -1
        else:
            fub[i] = ub[i] if (ub[i] < fub[i - 1] or c[i - 1] > fub[i - 1]) else fub[i - 1]
            flb[i] = lb[i] if (lb[i] > flb[i - 1] or c[i - 1] < flb[i - 1]) else flb[i - 1]
            if d == 1 and c[i] < flb[i]:
                d = -1
            elif d == -1 and c[i] > fub[i]:
                d = 1
        out[i] = d
    return pd.Series(out, index=df.index)


def rsi_rev_pos(df, n, lo):
    hi = 100 - lo
    delta = df.close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rsi = (100 - 100 / (1 + up / dn)).replace([np.inf, -np.inf], np.nan).fillna(50).values
    pos = np.zeros(len(rsi))
    cur = 0
    for i in range(len(rsi)):
        if cur == 0:
            if rsi[i] < lo:
                cur = 1
            elif rsi[i] > hi:
                cur = -1
        elif cur == 1 and rsi[i] > 50:
            cur = 0
        elif cur == -1 and rsi[i] < 50:
            cur = 0
        pos[i] = cur
    return pd.Series(pos, index=df.index)


def boll_rev_pos(df, n, k):
    m = df.close.rolling(n).mean()
    sd = df.close.rolling(n).std()
    up, dn = (m + k * sd).values, (m - k * sd).values
    mid, c = m.values, df.close.values
    pos = np.zeros(len(c))
    cur = 0
    for i in range(len(c)):
        if np.isnan(mid[i]):
            pos[i] = 0
            continue
        if cur == 0:
            if c[i] < dn[i]:
                cur = 1
            elif c[i] > up[i]:
                cur = -1
        elif cur == 1 and c[i] > mid[i]:
            cur = 0
        elif cur == -1 and c[i] < mid[i]:
            cur = 0
        pos[i] = cur
    return pd.Series(pos, index=df.index)


STRATS = [
    ("EMA cross", ema_pos, [dict(f=9, s=21), dict(f=20, s=50), dict(f=50, s=200)]),
    ("Donchian", donchian_pos, [dict(n=20), dict(n=50), dict(n=100)]),
    ("Supertrend", supertrend_pos, [dict(length=10, mult=3.0), dict(length=10, mult=4.0)]),
    ("RSI reversion", rsi_rev_pos, [dict(n=2, lo=10), dict(n=7, lo=20), dict(n=14, lo=25)]),
    ("Bollinger reversion", boll_rev_pos, [dict(n=20, k=2.0), dict(n=20, k=2.5), dict(n=50, k=2.5)]),
]


# ---------- بک‌تست ----------
def run(close, pos):
    ret = close.pct_change().fillna(0)
    p = pos.shift(1).fillna(0)
    turn = p.diff().abs().fillna(p.abs())
    strat = p * ret - turn * COST
    return strat, p, ret


def metrics(strat, p, ret, bpy):
    if len(strat) < 100:
        return None
    eq = (1 + strat).cumprod()
    last = float(eq.iloc[-1])
    sd = strat.std()
    sharpe = float(strat.mean() / sd * np.sqrt(bpy)) if sd > 0 else 0.0
    dd = float((eq / eq.cummax() - 1).min())
    chg = (p != p.shift()).cumsum()
    gross = (1 + p * ret).groupby(chg).prod() - 1
    side = p.groupby(chg).first()
    tr = ((1 + gross) * (1 - COST) ** 2 - 1)[side != 0]   # با هزینه‌ی ورود و خروج
    return dict(
        tot=last - 1, dd=dd, sharpe=sharpe, trades=int(len(tr)),
        win=float((tr > 0).mean()) if len(tr) else 0.0,
        avg=float(tr.mean()) if len(tr) else 0.0,
    )


def pstr(params):
    return ", ".join(f"{k}={v}" for k, v in params.items())


def pct(x, d=0):
    return f"{x * 100:+.{d}f}%"


def send(text):
    if not (BOT_TOKEN and CHAT_ID):
        return
    for i in range(0, len(text), 3800):
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text[i:i + 3800]},
            timeout=15,
        )
        r.raise_for_status()


def main():
    df, source = load()
    close = df.close
    a = int(len(df) * SPLIT)
    split_date = df.index[a]
    ret = close.pct_change().fillna(0)
    bh = metrics(ret.iloc[a:], pd.Series(1.0, index=close.index).iloc[a:], ret.iloc[a:], BPY)

    results = []
    for name, fn, grid in STRATS:
        best = {"L/S": None, "Long": None}
        oos_pos = {"L/S": 0, "Long": 0}
        for params in grid:
            raw = fn(df, **params)
            for mode in ("L/S", "Long"):
                pos = raw if mode == "L/S" else raw.clip(lower=0)
                strat, p, r = run(close, pos)
                mi = metrics(strat.iloc[:a], p.iloc[:a], r.iloc[:a], BPY)
                mo = metrics(strat.iloc[a:], p.iloc[a:], r.iloc[a:], BPY)
                if mi is None or mo is None:
                    continue
                if mo["tot"] > 0:
                    oos_pos[mode] += 1
                if best[mode] is None or mi["sharpe"] > best[mode][0]["sharpe"]:
                    best[mode] = (mi, mo, params)
        for mode in ("L/S", "Long"):
            if best[mode] is None:
                continue
            mi, mo, params = best[mode]
            results.append(dict(name=name, mode=mode, params=params, mi=mi, mo=mo,
                                robust=f"{oos_pos[mode]}/{len(grid)}"))

    results.sort(key=lambda r: r["mo"]["sharpe"], reverse=True)

    def line(r):
        mi, mo = r["mi"], r["mo"]
        gross_avg = mo["avg"] + 2 * COST
        return (
            f"{r['name']} ({pstr(r['params'])}) | {r['mode']}\n"
            f"  IS: {pct(mi['tot'])} | {mi['trades']} معامله | میانگین/معامله {pct(mi['avg'], 3)}\n"
            f"  OOS: {pct(mo['tot'])} | افت {pct(mo['dd'])} | {mo['trades']} معامله | "
            f"برد {mo['win'] * 100:.0f}%\n"
            f"  OOS میانگین هر معامله: خالص {pct(mo['avg'], 3)} (ناخالص {pct(gross_avg, 3)})\n"
            f"  تنظیم‌های سودده در OOS: {r['robust']}"
        )

    print("========== همه نتایج ==========")
    for r in results:
        print(line(r))
        print()

    good = [r for r in results if r["mo"]["tot"] > 0]
    head = [
        f"🧪 بک‌تست ۵ دقیقه‌ای BTC ({source})",
        f"{df.index[0]:%Y-%m-%d} تا {df.index[-1]:%Y-%m-%d} ({len(df):,} کندل)",
        f"آموزش (IS): تا {split_date:%Y-%m-%d} | آزمون (OOS): بعد از اون",
        f"هزینه‌ی هر معامله‌ی رفت‌وبرگشت: {2 * COST * 100:.2f}%",
        f"خرید و نگهداری در OOS: {pct(bh['tot'])} | افت {pct(bh['dd'])}",
        "",
        f"از {len(results)} ترکیب (بعد از کارمزد)، {len(good)} تا در OOS سودده بودن.",
        "",
        f"{TOP_N} ترکیب برتر بر اساس Sharpe در OOS:",
        "",
    ]
    body = "\n\n".join(line(r) for r in results[:TOP_N])
    foot = (
        "\n\nنکته: فقط OOS معیاره. اگه میانگین ناخالص هر معامله از "
        f"{2 * COST * 100:.2f}% کمتر باشه، بعد از کارمزد ضرر قطعیه. "
        "قانون‌های برگشت به میانگین درصد برد بالا دارن ولی ممکنه یه ضرر بزرگ "
        "کل سودشون رو بخوره."
    )
    text = "\n".join(head) + body + foot
    print(text)
    send(text)


if __name__ == "__main__":
    main()
