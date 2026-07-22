"""
metrics.py

Single source of truth for all performance analytics.

Import these from BOTH dashboard_export.py and portfolio_backtest.py so the
live dashboard and the backtest can never disagree on a number. Duplicating
this logic in JavaScript is how you end up with two different Sharpe ratios
and no idea which one is wrong.

Pure functions, no IBKR dependency, so this is unit-testable offline.
"""

import math
from datetime import datetime, date

import numpy as np
import pandas as pd

TRADING_DAYS = 252


# ----------------------------------------------------------------------
# return series
# ----------------------------------------------------------------------

def to_returns(equity):
    """Equity series (pd.Series indexed by date) -> simple daily returns."""
    return equity.pct_change().dropna()


def cagr(equity):
    """Compound annual growth rate from an equity curve."""
    if len(equity) < 2:
        return 0.0
    start_val = float(equity.iloc[0])
    end_val = float(equity.iloc[-1])
    if start_val <= 0:
        return 0.0
    days = (equity.index[-1] - equity.index[0]).days
    if days <= 0:
        return 0.0
    years = days / 365.25
    return (end_val / start_val) ** (1.0 / years) - 1.0


def sharpe(equity, risk_free=0.0):
    """
    Annualised Sharpe from an equity curve.

    risk_free is an annual rate (e.g. 0.02). Deducted per-period, not
    subtracted from the final number, which is the usual sloppy shortcut.
    """
    r = to_returns(equity)
    if len(r) < 2:
        return 0.0
    rf_daily = (1.0 + risk_free) ** (1.0 / TRADING_DAYS) - 1.0
    excess = r - rf_daily
    sd = excess.std(ddof=1)
    if sd == 0 or math.isnan(sd):
        return 0.0
    return float(excess.mean() / sd * math.sqrt(TRADING_DAYS))


def sortino(equity, risk_free=0.0):
    """Like Sharpe but only penalises downside deviation."""
    r = to_returns(equity)
    if len(r) < 2:
        return 0.0
    rf_daily = (1.0 + risk_free) ** (1.0 / TRADING_DAYS) - 1.0
    excess = r - rf_daily
    downside = excess[excess < 0]
    if len(downside) < 2:
        return 0.0
    dd = downside.std(ddof=1)
    if dd == 0 or math.isnan(dd):
        return 0.0
    return float(excess.mean() / dd * math.sqrt(TRADING_DAYS))


def volatility(equity):
    """Annualised standard deviation of returns."""
    r = to_returns(equity)
    if len(r) < 2:
        return 0.0
    return float(r.std(ddof=1) * math.sqrt(TRADING_DAYS))


# ----------------------------------------------------------------------
# drawdown
# ----------------------------------------------------------------------

def drawdown_series(equity):
    """
    Underwater curve: fractional distance below the running peak.
    Always <= 0. This is what feeds the underwater strip on the dashboard.
    """
    peak = equity.cummax()
    return (equity / peak) - 1.0


def max_drawdown(equity):
    dd = drawdown_series(equity)
    if len(dd) == 0:
        return 0.0
    return float(dd.min())


def drawdown_detail(equity):
    """
    Full anatomy of the worst drawdown: how deep, when it started, when it
    bottomed, when (or whether) it recovered, and how long each phase took.

    A single max-DD number hides whether you spent two days or four months
    underwater. This is the part that actually matters.
    """
    dd = drawdown_series(equity)
    if len(dd) == 0:
        return None

    trough_date = dd.idxmin()
    depth = float(dd.loc[trough_date])

    # peak preceding the trough
    before = equity.loc[:trough_date]
    peak_date = before.idxmax()
    peak_value = float(before.max())

    # first date after the trough that reclaims the old peak
    after = equity.loc[trough_date:]
    recovered = after[after >= peak_value]
    if len(recovered) > 0:
        recovery_date = recovered.index[0]
        recovery_days = (recovery_date - trough_date).days
        is_recovered = True
    else:
        recovery_date = None
        recovery_days = (equity.index[-1] - trough_date).days
        is_recovered = False

    # longest continuous underwater stretch anywhere in the series
    underwater = dd < -1e-9
    longest = 0
    current = 0
    for flag in underwater:
        if flag:
            current += 1
            longest = max(longest, current)
        else:
            current = 0

    return {
        "depth_pct": round(depth * 100, 2),
        "peak_date": peak_date.strftime("%Y-%m-%d"),
        "trough_date": trough_date.strftime("%Y-%m-%d"),
        "recovery_date": recovery_date.strftime("%Y-%m-%d") if recovery_date else None,
        "is_recovered": is_recovered,
        "decline_days": (trough_date - peak_date).days,
        "recovery_days": recovery_days,
        "longest_underwater_days": int(longest),
        "currently_underwater_pct": round(float(dd.iloc[-1]) * 100, 2),
    }


# ----------------------------------------------------------------------
# rolling metrics
# ----------------------------------------------------------------------

def rolling_sharpe(equity, window=60, risk_free=0.0):
    """
    Rolling annualised Sharpe.

    Inception-to-date Sharpe on a five-month paper run is noise. Rolling
    shows whether edge is decaying, which is the question you actually have.
    """
    r = to_returns(equity)
    if len(r) < window:
        return pd.Series(dtype=float)
    rf_daily = (1.0 + risk_free) ** (1.0 / TRADING_DAYS) - 1.0
    excess = r - rf_daily
    mean = excess.rolling(window).mean()
    sd = excess.rolling(window).std(ddof=1)
    out = (mean / sd) * math.sqrt(TRADING_DAYS)
    return out.replace([np.inf, -np.inf], np.nan).dropna()


def rolling_win_rate(trades, window=20):
    """
    Win rate over the last `window` closed trades, computed trade-by-trade
    rather than by calendar date.
    """
    if len(trades) < window:
        return []
    wins = [1 if t["pnl"] > 0 else 0 for t in trades]
    out = []
    for i in range(window - 1, len(wins)):
        chunk = wins[i - window + 1:i + 1]
        out.append({
            "trade_index": i + 1,
            "date": trades[i]["exit_date"],
            "win_rate": round(sum(chunk) / window * 100, 1),
        })
    return out


# ----------------------------------------------------------------------
# trade statistics
# ----------------------------------------------------------------------

def trade_stats(trades):
    """
    Aggregate statistics over a list of closed trades.
    Each trade dict needs at least: pnl, pnl_pct, holding_days.
    """
    if not trades:
        return {
            "count": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "avg_win": 0.0, "avg_loss": 0.0, "profit_factor": 0.0,
            "expectancy": 0.0, "avg_holding_days": 0.0,
            "best": 0.0, "worst": 0.0, "payoff_ratio": 0.0,
        }

    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    gross_win = sum(wins)
    gross_loss = abs(sum(losses))

    avg_win = (gross_win / len(wins)) if wins else 0.0
    avg_loss = (gross_loss / len(losses)) if losses else 0.0
    win_rate = len(wins) / len(pnls)

    if gross_loss > 0:
        profit_factor = gross_win / gross_loss
    else:
        profit_factor = float("inf") if gross_win > 0 else 0.0

    return {
        "count": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate * 100, 1),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else None,
        "payoff_ratio": round(avg_win / avg_loss, 2) if avg_loss > 0 else None,
        "expectancy": round(win_rate * avg_win - (1 - win_rate) * avg_loss, 2),
        "avg_holding_days": round(sum(t["holding_days"] for t in trades) / len(trades), 1),
        "best": round(max(pnls), 2),
        "worst": round(min(pnls), 2),
    }


def duration_histogram(trades, buckets=None):
    """
    Holding-period distribution split by outcome.

    Trend-following dies when winners get cut short. If the winning bucket
    isn't clearly to the right of the losing bucket, the exit rule is
    truncating the trades that were supposed to pay for everything else.
    """
    if buckets is None:
        buckets = [(0, 5), (6, 10), (11, 20), (21, 40), (41, 80), (81, 10**6)]

    out = []
    for lo, hi in buckets:
        label = f"{lo}-{hi}d" if hi < 10**6 else f"{lo}d+"
        in_bucket = [t for t in trades if lo <= t["holding_days"] <= hi]
        w = [t for t in in_bucket if t["pnl"] > 0]
        l = [t for t in in_bucket if t["pnl"] <= 0]
        out.append({
            "bucket": label,
            "wins": len(w),
            "losses": len(l),
            "total": len(in_bucket),
            "avg_pnl": round(sum(t["pnl"] for t in in_bucket) / len(in_bucket), 2) if in_bucket else 0.0,
        })
    return out


# ----------------------------------------------------------------------
# correlation
# ----------------------------------------------------------------------

def correlation_matrix(price_frame, window=None):
    """
    Correlation of daily returns between instruments.

    price_frame: DataFrame, one column per instrument, indexed by date.

    This is the test of the diversification claim. If NQ, SOXX, NVDA, TSM
    and MSFT all sit above 0.8 you do not hold 13 positions, you hold about
    four bets wearing thirteen hats.
    """
    if window:
        price_frame = price_frame.tail(window)
    rets = price_frame.pct_change().dropna(how="all")
    corr = rets.corr()
    corr = corr.replace([np.inf, -np.inf], np.nan).fillna(0.0)

    labels = list(corr.columns)
    matrix = [[round(float(corr.iloc[i, j]), 3) for j in range(len(labels))]
              for i in range(len(labels))]
    return {"labels": labels, "matrix": matrix}


def effective_bets(price_frame, window=None):
    """
    How many independent bets you actually hold.

    Computed from the eigenvalues of the correlation matrix
    (exp of the entropy of the normalised spectrum). 13 perfectly
    uncorrelated instruments gives 13. 13 identical ones gives 1.
    """
    if window:
        price_frame = price_frame.tail(window)
    rets = price_frame.pct_change().dropna(how="all")
    if rets.shape[1] < 2 or len(rets) < 5:
        return float(rets.shape[1])

    corr = rets.corr().fillna(0.0).values
    eig = np.linalg.eigvalsh(corr)
    eig = eig[eig > 1e-10]
    if len(eig) == 0:
        return 1.0
    p = eig / eig.sum()
    entropy = -np.sum(p * np.log(p))
    return round(float(np.exp(entropy)), 2)


def avg_pairwise_correlation(price_frame, window=None):
    """Mean of the off-diagonal correlations. One number for the KPI strip."""
    if window:
        price_frame = price_frame.tail(window)
    rets = price_frame.pct_change().dropna(how="all")
    if rets.shape[1] < 2:
        return 0.0
    corr = rets.corr().fillna(0.0).values
    n = corr.shape[0]
    mask = ~np.eye(n, dtype=bool)
    return round(float(corr[mask].mean()), 3)


# ----------------------------------------------------------------------
# expiry
# ----------------------------------------------------------------------

def days_to_expiry(expiry_str, today=None):
    """
    expiry_str: 'YYYYMMDD' or 'YYYYMM' (IBKR contract month).
    For 'YYYYMM' the last calendar day of that month is assumed, which is
    conservative: the real last trading day is earlier, so this understates
    urgency rather than overstating it.
    """
    if not expiry_str:
        return None
    today = today or date.today()
    s = str(expiry_str)

    if len(s) == 8:
        exp = datetime.strptime(s, "%Y%m%d").date()
    elif len(s) == 6:
        year = int(s[:4])
        month = int(s[4:6])
        if month == 12:
            nxt = date(year + 1, 1, 1)
        else:
            nxt = date(year, month + 1, 1)
        exp = nxt - pd.Timedelta(days=1)
        exp = exp.date() if hasattr(exp, "date") else exp
    else:
        return None

    return (exp - today).days


def expiry_status(days):
    """Amber under 10 days, red under 4. Matches the dashboard colour ramp."""
    if days is None:
        return "none"
    if days < 0:
        return "expired"
    if days <= 3:
        return "critical"
    if days <= 10:
        return "warning"
    return "ok"
