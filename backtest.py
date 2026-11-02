import pandas as pd
import numpy as np


def btc_signal(df):
    """
    Input:
        df: DataFrame with columns:
            open, high, low, close, volume

        Data must be sorted oldest to newest.
        Last row must be a CLOSED 5-minute candle.

    Output:
        Dictionary containing signal, entry, stop_loss,
        take_profit, or None if no new signal.
    """

    if len(df) < 200:
        return None

    df = df.copy()

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    close = df["close"]
    high = df["high"]
    low = df["low"]

    # Moving averages
    df["ema9"] = close.ewm(span=9, adjust=False).mean()
    df["ema21"] = close.ewm(span=21, adjust=False).mean()
    df["ema50"] = close.ewm(span=50, adjust=False).mean()
    df["ema200"] = close.ewm(span=200, adjust=False).mean()

    # RSI
    delta = close.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / 14, adjust=False, min_periods=14
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / 14, adjust=False, min_periods=14
    ).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))

    # ATR
    prev_close = close.shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    df["atr"] = tr.ewm(
        alpha=1 / 14, adjust=False, min_periods=14
    ).mean()

    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()

    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(
        span=9, adjust=False
    ).mean()

    # ADX
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = up_move.where(
        (up_move > down_move) & (up_move > 0), 0.0
    )

    minus_dm = down_move.where(
        (down_move > up_move) & (down_move > 0), 0.0
    )

    atr = df["atr"].replace(0, np.nan)

    plus_di = 100 * plus_dm.ewm(
        alpha=1 / 14, adjust=False
    ).mean() / atr

    minus_di = 100 * minus_dm.ewm(
        alpha=1 / 14, adjust=False
    ).mean() / atr

    dx = (
        100 * (plus_di - minus_di).abs()
        / (plus_di + minus_di).replace(0, np.nan)
    )

    df["adx"] = dx.ewm(
        alpha=1 / 14, adjust=False
    ).mean()

    # Latest CLOSED candle
    last = df.iloc[-1]
    prev = df.iloc[-2]

    values = [
        last["close"],
        last["ema21"],
        last["ema50"],
        last["ema200"],
        last["rsi"],
        last["atr"],
        last["macd"],
        last["macd_signal"],
        last["adx"],
    ]

    if not np.isfinite(values).all():
        return None

    price = float(last["close"])
    atr_value = float(last["atr"])
    rsi = float(last["rsi"])

    if price <= 0 or atr_value <= 0:
        return None

    # Avoid extremely low volatility and weak trends
    atr_pct = atr_value / price * 100

    if atr_pct < 0.03 or last["adx"] < 18:
        return None

    # LONG conditions
    long_signal = (
        last["ema21"] > last["ema50"]
        and last["ema50"] > last["ema200"]
        and last["close"] > last["ema21"]
        and 52 <= rsi <= 68
        and last["macd"] > last["macd_signal"]
        and prev["macd"] <= prev["macd_signal"]
        and last["adx"] >= 18
    )

    # SHORT conditions
    short_signal = (
        last["ema21"] < last["ema50"]
        and last["ema50"] < last["ema200"]
        and last["close"] < last["ema21"]
        and 32 <= rsi <= 48
        and last["macd"] < last["macd_signal"]
        and prev["macd"] >= prev["macd_signal"]
        and last["adx"] >= 18
    )

    if not long_signal and not short_signal:
        return None

    # Risk management
    risk_distance = 1.5 * atr_value
    reward_distance = 2.0 * risk_distance

    if long_signal:
        signal = "LONG"
        stop_loss = price - risk_distance
        take_profit = price + reward_distance
    else:
        signal = "SHORT"
        stop_loss = price + risk_distance
        take_profit = price - reward_distance

    return {
        "symbol": "BTCUSDT",
        "timeframe": "5m",
        "signal": signal,
        "entry": round(price, 2),
        "stop_loss": round(stop_loss, 2),
        "take_profit": round(take_profit, 2),
        "risk_reward": 2.0,
        "rsi": round(rsi, 2),
        "adx": round(float(last["adx"]), 2),
        "atr_percent": round(atr_pct, 4),
    }
