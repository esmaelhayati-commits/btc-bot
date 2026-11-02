import os
import sys
import time
import numpy as np
import pandas as pd
import requests

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# ---------- تنظیمات ----------
YEARS = 10
FEE = 0.001           # کارمزد هر طرف (۰.۱٪)
SLIP = 0.0002         # اسلیپیج هر طرف (۰.۰۲٪)
COST = FEE + SLIP
ASSETS = [("BTC-USD", "BTC"), ("ETH-USD", "ETH")]
MAIN_TF, MAIN_F, MAIN_S = "4h", 50, 200
VARIANTS = {
    "4h": [(20, 50), (30, 100), (50, 200), (75, 250), (100, 300)],
    "1D": [(10, 30), (20, 50), (50, 200)],
}
BPY = {"4h": 2190, "1D": 365}


# ---------- دریافت داده (Coinbase، کندل ساعتی) ----------
def fetch_coinbase(product, years):
    end = pd.Timestamp.now(tz="UTC").floor("h")
    cur = end - pd.Timedelta(days=365 * years)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    rows = []
    while cur < end:
        nxt = min(cur + pd.Timedelta(hours=299), end)
        data = None
        for attempt in range(6):
            try:
                r = requests.get(
                    f"https://api.exchange.coinbase.com/products/{product}/candles",
                    params=dict(granularity=3600, start=cur.strftime(fmt),
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
                print(f"خطا ({product}، تلاش {attempt + 1}):", e, file=sys.stderr)
                time.sleep(1.5)
        if data is None:
            raise RuntimeError(f"دریافت داده‌ی {product} ممکن نشد")
        rows += data
        cur = nxt + pd.Timedelta(hours=1)
        time.sleep(0.12)
    if not rows:
        raise RuntimeError(f"داده‌ای برای {product} پیدا نشد")
    df = pd.DataFrame(rows, columns=["t", "low", "high", "open", "close", "volume"])
    df = df.drop_duplicates("t").sort_values("t")
    df.index = pd.to_datetime(df["t"].astype("int64"), unit="s")
    df = df[["open", "high", "low", "close"]].astype(float)
    return df.iloc[:-1]  # کندل آخر ممکنه ناقص باشه


def resample(df, rule):
    return df.resample(rule).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()


# ---------- استراتژی: EMA cross، فقط خرید ----------
def ema_long(close, f, s):
    ef = close.ewm(span=f, adjust=False).mean()
    es = close.ewm(span=s, adjust=False).mean()
    pos = (ef > es).astype(float)
    pos.iloc[:s] = 0.0   # دوره‌ی گرم‌شدن
    return pos


def run(close, pos):
    ret = close.pct_change().fillna(0)
    p = pos.shift(1).fillna(0)          # سیگنال کندل t روی بازده کندل t+1 اثر می‌ذاره
    turn = p.diff().abs().fillna(p.abs())
    strat = p * ret - turn * COST
    return strat, p, ret


def metrics(strat, p, ret, bpy):
    eq = (1 + strat).cumprod()
    last = float(eq.iloc[-1])
    yrs = len(strat) / bpy
    sd = strat.std()
    sharpe = float(strat.mean() / sd * np.sqrt(bpy)) if sd > 0 else 0.0
    dd = float((eq / eq.cummax() - 1).min())
    chg = (p != p.shift()).cumsum()
    gross = (1 + p * ret).groupby(chg).prod() - 1
    side = p.groupby(chg).first()
    tr = ((1 + gross) * (1 - COST) ** 2 - 1)[side != 0]   # با هزینه‌ی ورود و خروج
    wins, loss = tr[tr > 0], tr[tr <= 0]
    pf = float(wins.sum() / -loss.sum()) if len(loss) and loss.sum() < 0 else float("inf")
    return dict(
        tot=last - 1,
        cagr=(last ** (1 / yrs) - 1) if last > 0 and yrs > 0 else -1.0,
        dd=dd, sharpe=sharpe, trades=int(len(tr)),
        win=float((tr > 0).mean()) if len(tr) else 0.0, pf=pf,
    )


def yearly(r):
    return r.groupby(r.index.year).apply(lambda x: float((1 + x).prod() - 1))


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


def pct(x):
    return f"{x * 100:+.0f}%"


def main():
    lines = [
        "🧪 تست پایداری EMA cross (فقط خرید)",
        f"کارمزد+اسلیپیج: {COST * 100:.2f}% هر طرف | داده‌ی ساعتی Coinbase",
        "",
    ]
    all_checks = []

    for product, label in ASSETS:
        base = fetch_coinbase(product, YEARS)
        lines.append(f"━━ {label} ({base.index[0]:%Y-%m-%d} تا {base.index[-1]:%Y-%m-%d}) ━━")

        df_main = resample(base, MAIN_TF)
        close = df_main.close
        strat, p, ret = run(close, ema_long(close, MAIN_F, MAIN_S))
        m = metrics(strat, p, ret, BPY[MAIN_TF])
        one = pd.Series(1.0, index=close.index)
        bh = metrics(ret, one, ret, BPY[MAIN_TF])

        lines.append(
            f"خرید و نگهداری: {pct(bh['tot'])} | سالانه {pct(bh['cagr'])} | "
            f"افت {pct(bh['dd'])} | Sharpe {bh['sharpe']:.2f}"
        )
        pf = "∞" if m["pf"] == float("inf") else f"{m['pf']:.2f}"
        lines.append(
            f"{MAIN_TF} EMA {MAIN_F}/{MAIN_S}: {pct(m['tot'])} | سالانه {pct(m['cagr'])} | "
            f"افت {pct(m['dd'])} | Sharpe {m['sharpe']:.2f}"
        )
        lines.append(
            f"  {m['trades']} معامله | برد {m['win'] * 100:.0f}% | PF {pf}"
        )
        lines.append("")
        lines.append("سال به سال (استراتژی | خرید و نگهداری):")
        ys, yb = yearly(strat), yearly(ret)
        years = list(ys.index)
        better = 0
        for y in years:
            mark = "*" if y in (years[0], years[-1]) else ""
            lines.append(f"  {y}{mark}: {pct(ys[y])} | {pct(yb[y])}")
            if ys[y] > yb[y]:
                better += 1
        lines.append(f"  در {better} از {len(years)} سال استراتژی بهتر از خرید و نگهداری بود")
        lines.append("")

        lines.append("تنظیم‌های مختلف (کل بازه):")
        n_profit = n_beat = n_tot = 0
        for tf, grid in VARIANTS.items():
            dfx = df_main if tf == MAIN_TF else resample(base, tf)
            cx = dfx.close
            sret = cx.pct_change().fillna(0)
            bh_x = metrics(sret, pd.Series(1.0, index=cx.index), sret, BPY[tf])
            for f, s in grid:
                st, px, rx = run(cx, ema_long(cx, f, s))
                mx = metrics(st, px, rx, BPY[tf])
                n_tot += 1
                n_profit += mx["tot"] > 0
                n_beat += mx["sharpe"] > bh_x["sharpe"]
                lines.append(
                    f"  {tf} EMA {f}/{s}: {pct(mx['tot'])} | افت {pct(mx['dd'])} | "
                    f"Sharpe {mx['sharpe']:.2f} | {mx['trades']} معامله"
                )
        lines.append(
            f"  {n_profit} از {n_tot} تنظیم سودده، {n_beat} تا Sharpe بهتر از خرید و نگهداری"
        )
        lines.append("")
        all_checks.append((label, m, bh))

    lines.append("نکته: سال اول و آخر ناقص‌ان (ستاره). Sharpe بهتر یعنی ریسک‌تعدیل‌شده بهتر. "
                 "اگه برتری فقط از دوران ریزش اومده (بیرون موندن)، همون ارزش اصلی روشه، نه شکار صعودها.")

    text = "\n".join(lines)
    print(text)
    send(text)


if __name__ == "__main__":
    main()
