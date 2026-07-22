import pandas as pd
import numpy as np


def compute_rsi(price_history, window=14):
    delta = price_history.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    avg_gain = gain.rolling(window=window).mean()
    avg_loss = loss.rolling(window=window).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def compute_macd(price_history, fast=12, slow=26, signal=9):
    ema_fast = price_history.ewm(span=fast, adjust=False).mean()
    ema_slow = price_history.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def compute_sma_signals(price_history, short_window=20, long_window=200):
    """
    price_history: DataFrame with dates as index, symbols as columns

    Combines SMA20/SMA200 crossover with RSI and MACD confirmation.

    Base signal (SMA crossover):
        BUY  if SMA20 > SMA200
        SELL if SMA20 < SMA200
        HOLD if insufficient data

    Confirmation layer:
        - RSI > 70 downgrades a BUY to HOLD (overbought, don't chase)
        - RSI < 30 downgrades a SELL to HOLD (oversold, don't chase down)
        - MACD histogram must agree in direction (positive for BUY, negative for SELL),
          otherwise downgrade to HOLD
    """
    sma_short = price_history.rolling(window=short_window).mean()
    sma_long = price_history.rolling(window=long_window).mean()
    rsi = compute_rsi(price_history)
    macd_line, signal_line, histogram = compute_macd(price_history)

    latest_sma_short = sma_short.iloc[-1]
    latest_sma_long = sma_long.iloc[-1]
    latest_rsi = rsi.iloc[-1]
    latest_macd_hist = histogram.iloc[-1]

    results = {}
    for symbol in price_history.columns:
        s = latest_sma_short.get(symbol)
        l = latest_sma_long.get(symbol)
        r = latest_rsi.get(symbol)
        m = latest_macd_hist.get(symbol)

        if pd.isna(s) or pd.isna(l):
            base_signal = "HOLD"
        elif s > l:
            base_signal = "BUY"
        elif s < l:
            base_signal = "SELL"
        else:
            base_signal = "HOLD"

        final_signal = base_signal

        if base_signal == "BUY":
            if pd.notna(r) and r > 70:
                final_signal = "HOLD"  # overbought, don't chase
            elif pd.notna(m) and m < 0:
                final_signal = "HOLD"  # MACD disagrees with uptrend

        elif base_signal == "SELL":
            if pd.notna(r) and r < 30:
                final_signal = "HOLD"  # oversold, don't chase down
            elif pd.notna(m) and m > 0:
                final_signal = "HOLD"  # MACD disagrees with downtrend

        results[symbol] = {
            "sma_20": s,
            "sma_200": l,
            "rsi": r,
            "macd_hist": m,
            "base_signal": base_signal,
            "signal": final_signal,
        }

    return pd.DataFrame(results).T