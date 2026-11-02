import os
import sys
import time
import numpy as np
import pandas as pd
import requests

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# ---------- تنظیمات ----------
YEARS = 6
FEE = 0.001            # کارمزد هر طرف (۰.۱٪)
SLIP = 0.0002          # اسلیپیج هر طرف (۰.۰۲٪)
COST = FEE + SLIP
SPLIT = 0.6            # ۶۰٪ اول آموزش (IS)، ۴۰٪ آخر آزمون (OOS)
TOP_N = 10
TIMEFRAMES = [("1D", "1D", 365), ("4h", "4h", 2190), ("1h", "1h", 8760)]


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
                    params=dict(symbol="BTCUSDT", interval="1h",
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
        cur = data[-1][0] + 3600 * 1000
        if len(data) < 1000:
            break
        time.sleep(0.15)
    df = pd.DataFrame(rows).iloc[:, :6]
    df.columns = ["t", "open", "high", "low", "close", "volume"]
    df.index = pd.to_datetime(df["t"].astype("int64"), unit="ms")
    df = df[["open", "high", "low", "close"]].astype(float)
    return df.iloc[:-1]  # کندل آخر ممکنه ناقص باشه


def fetch_coinbase(years):
    end = pd.Timestamp.now(tz="UTC").floor("h")
    cur = end - pd.Timedelta(days=365 * years)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    rows = []
    while cur < end:
        nxt = min(cur + pd.Timedelta(hours=299), end)
        r = requests.get(
            "https://api.exchange.coinbase.com/products/BTC-USD/candles",
            params=dict(granularity=3600, start=cur.strftime(fmt),
                        end=nxt.strftime(fmt)),
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20,
        )
        r.raise_for_status()
        rows += r.json()
        cur = nxt + pd.Timedelta(hours=1)
        time.sleep(0.15)
    df = pd.DataFrame(rows, columns=["t", "low", "high", "open", "close", "volume"])
    df = df.drop_duplicates("t").sort_values("t")
    df.index = pd.to_datetime(df["t"].astype("int64"), unit="s")
    return df[["open", "high", "low", "close"]].astype(float)


def load():
    try:
        return fetch_binance(YEARS), "Binance BTCUSDT"
    except Exception as e:
        print("بایننس جواب نداد:", e, file=sys.stderr)
    return fetch_coinbase(YEARS), "Coinbase BTC-USD"


def resample(df, rule):
    return df.resample(rule).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()


# ---------- استراتژی‌ها (خروجی: موقعیت -1/0/+1 بعد از بسته شدن کندل) ----------
def ema_pos(df, f, s):
    ef = df.close.ewm(span=f, adjust=False).mean()
    es = df.close.ewm(span=s, adjust=False).mean()
    return np.sign(ef - es).fillna(0)


def donchian_pos(df, n):
    hh = df.high.rolling(n).max().shift(1)
    ll = df.low.rolling(n).min().shift(1)
    sig = pd.Series(np.nan, index=df.index)
    sig[df.close > hh] = 1.0
    sig[df.close < ll] = -1.0
    return sig.ffill().fillna(0)


def mom_pos(df, n):
    return np.sign(df.close / df.close.shift(n) - 1).fillna(0)


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


def ichi_pos(df):
    h, l, c = df.high, df.low, df.close

    def don(n):
        return (h.rolling(n).max() + l.rolling(n).min()) / 2

    tk, kj = don(9), don(26)
    a = ((tk + kj) / 2).shift(26)
    b = don(52).shift(26)
    both = pd.concat([a, b], axis=1)
    top = both.max(axis=1, skipna=False)
    bot = both.min(axis=1, skipna=False)
    sig = pd.Series(np.nan, index=df.index)
    sig[(c > top) & (tk > kj)] = 1.0
    sig[(c < bot) & (tk < kj)] = -1.0
    return sig.ffill().fillna(0)


STRATS = [
    ("EMA cross", ema_pos,
     [dict(f=10, s=30), dict(f=20, s=50), dict(f=20, s=100),
      dict(f=30, s=90), dict(f=50, s=200)]),
    ("Donchian", donchian_pos,
     [dict(n=10), dict(n=20), dict(n=40), dict(n=55), dict(n=100)]),
    ("Supertrend", supertrend_pos,
     [dict(length=l, mult=m) for l in (10, 14) for m in (2.0, 3.0, 4.0)]),
    ("Momentum", mom_pos,
     [dict(n=10), dict(n=20), dict(n=50), dict(n=100), dict(n=200)]),
    ("Ichimoku", ichi_pos, [dict()]),
]


# ---------- بک‌تست ----------
def run(close, pos):
    ret = close.pct_change().fillna(0)
    p = pos.shift(1).fillna(0)          # سیگنال کندل t روی بازده کندل t+1 اثر می‌ذاره
    turn = p.diff().abs().fillna(p.abs())
    strat = p * ret - turn * COST
    return strat, p


def metrics(strat, p, bpy):
    if len(strat) < 30:
        return None
    eq = (1 + strat).cumprod()
    last = float(eq.iloc[-1])
    yrs = len(strat) / bpy
    sd = strat.std()
    sharpe = float(strat.mean() / sd * np.sqrt(bpy)) if sd > 0 else 0.0
    dd = float((eq / eq.cummax() - 1).min())
    chg = (p != p.shift()).cumsum()
    seg = strat.groupby(chg).sum()
    sp = p.groupby(chg).first()
    tr = seg[sp != 0]
    wins, loss = tr[tr > 0], tr[tr < 0]
    pf = float(wins.sum() / -loss.sum()) if len(loss) and loss.sum() < 0 else float("inf")
    return dict(
        tot=last - 1,
        cagr=(last ** (1 / yrs) - 1) if last > 0 and yrs > 0 else -1.0,
        dd=dd, sharpe=sharpe, trades=int(len(tr)),
        win=float((tr > 0).mean()) if len(tr) else 0.0,
        pf=pf, expo=float((p != 0).mean()),
    )


def bh_metrics(close, a, bpy):
    ret = close.pct_change().fillna(0)
    one = pd.Series(1.0, index=close.index)
    return (metrics(ret.iloc[:a], one.iloc[:a], bpy),
            metrics(ret.iloc[a:], one.iloc[a:], bpy))


def pstr(params):
    return ", ".join(f"{k}={v}" for k, v in params.items()) or "default"


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
    base, source = load()
    results, bh_info = [], []
    split_date = None

    for tf, rule, bpy in TIMEFRAMES:
        df = base if rule == "1h" else resample(base, rule)
        close = df.close
        a = int(len(df) * SPLIT)
        if tf == "1D":
            split_date = df.index[a]
        bh_i, bh_o = bh_metrics(close, a, bpy)
        bh_info.append((tf, bh_o))

        for name, fn, grid in STRATS:
            best = {"L/S": None, "Long": None}
            oos_pos = {"L/S": 0, "Long": 0}
            for params in grid:
                raw = fn(df, **params)
                for mode in ("L/S", "Long"):
                    pos = raw if mode == "L/S" else raw.clip(lower=0)
                    strat, p = run(close, pos)
                    mi = metrics(strat.iloc[:a], p.iloc[:a], bpy)
                    mo = metrics(strat.iloc[a:], p.iloc[a:], bpy)
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
                results.append(dict(
                    tf=tf, name=name, mode=mode, params=params, mi=mi, mo=mo,
                    robust=f"{oos_pos[mode]}/{len(grid)}",
                    bh_sharpe=bh_o["sharpe"] if bh_o else 0.0,
                ))

    # ---------- گزارش ----------
    results.sort(key=lambda r: r["mo"]["sharpe"], reverse=True)

    def line(r):
        mi, mo = r["mi"], r["mo"]
        pf = "∞" if mo["pf"] == float("inf") else f"{mo['pf']:.2f}"
        return (
            f"{r['tf']} | {r['name']} ({pstr(r['params'])}) | {r['mode']}\n"
            f"  IS: {mi['tot'] * 100:+.0f}% Sharpe {mi['sharpe']:.2f}\n"
            f"  OOS: {mo['tot'] * 100:+.0f}% | افت {mo['dd'] * 100:.0f}% | "
            f"Sharpe {mo['sharpe']:.2f} | {mo['trades']} معامله | "
            f"برد {mo['win'] * 100:.0f}% | PF {pf}\n"
            f"  تنظیم‌های سودده در OOS: {r['robust']}"
        )

    print("========== همه نتایج ==========")
    for r in results:
        print(line(r))
        print()

    passed = [r for r in results
              if r["mo"]["tot"] > 0 and r["mo"]["sharpe"] > r["bh_sharpe"]]

    head = [
        f"🧪 بک‌تست BTC ({source})",
        f"{base.index[0]:%Y-%m-%d} تا {base.index[-1]:%Y-%m-%d}",
        f"آموزش (IS): تا {split_date:%Y-%m-%d} | آزمون (OOS): بعد از اون",
        f"کارمزد+اسلیپیج: {COST * 100:.2f}% هر طرف",
        "",
        "خرید و نگهداری در بازه‌ی OOS:",
    ]
    for tf, bo in bh_info:
        if bo:
            head.append(
                f"  {tf}: {bo['tot'] * 100:+.0f}% | افت {bo['dd'] * 100:.0f}% | "
                f"Sharpe {bo['sharpe']:.2f}"
            )
    head += [
        "",
        f"از {len(results)} ترکیب، {len(passed)} تا در OOS هم سودده بودن "
        f"و هم Sharpe بهتر از خرید و نگهداری داشتن.",
        "",
        f"{TOP_N} ترکیب برتر بر اساس Sharpe در OOS:",
        "",
    ]
    body = "\n\n".join(line(r) for r in results[:TOP_N])
    foot = (
        "\n\nنکته: IS = بخشی که تنظیم ازش انتخاب شده، OOS = بخش دیده‌نشده. "
        "فقط OOS معیاره. چون چند ده ترکیب امتحان شده، بعضی‌ها شانسی خوب "
        "از آب درمیان. اگه فقط یکی دو تنظیم از چند تا سودده بودن، شاید شانسی باشه. "
        "Sharpe بالاتر از خرید و نگهداری یعنی ریسک‌تعدیل‌شده بهتر، نه لزوماً سود بیشتر."
    )
    text = "\n".join(head) + body + foot
    print(text)
    send(text)


if __name__ == "__main__":
    main()
