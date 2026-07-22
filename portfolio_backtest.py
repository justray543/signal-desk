import pandas as pd
import numpy as np


def backtest_portfolio(
    price_history: dict,
    initial_capital: float = 100000.0,
    transaction_cost: float = 0.001,
    stop_loss_pct: float = 0.05,
    allocation_pct: float = None,  # defaults to equal-weight across symbols
):
    symbols = list(price_history.keys())
    n = len(symbols)
    if allocation_pct is None:
        allocation_pct = 1.0 / n  # equal weight

    # prepare per-symbol signals first
    signals = {}
    for symbol, df_raw in price_history.items():
        df = df_raw.copy()
        if 'close' not in df.columns:
            df = df.rename(columns={df.columns[0]: 'close'})
        df['close'] = df['close'].ffill()
        df = df.dropna(subset=['close'])

        df['EMA9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['EMA21'] = df['close'].ewm(span=21, adjust=False).mean()

        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0).rolling(window=14).mean()
        loss = -delta.where(delta < 0, 0).rolling(window=14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))

        df = df.dropna().reset_index(drop=True)
        signals[symbol] = df

    # align all symbols on a common date index
    min_len = min(len(df) for df in signals.values())
    for symbol in signals:
        signals[symbol] = signals[symbol].iloc[-min_len:].reset_index(drop=True)

    # per-symbol capital allocation, tracked independently but summed for portfolio equity
    sub_equity = {symbol: [initial_capital * allocation_pct] for symbol in symbols}
    position = {symbol: 0 for symbol in symbols}
    entry_price = {symbol: 0.0 for symbol in symbols}
    stop_price = {symbol: 0.0 for symbol in symbols}
    trades = {symbol: 0 for symbol in symbols}

    for i in range(1, min_len):
        for symbol in symbols:
            df = signals[symbol]
            curr = df.iloc[i]
            eq = sub_equity[symbol]

            if position[symbol] == 0 and curr['EMA9'] > curr['EMA21'] and curr['RSI'] > 50:
                position[symbol] = 1
                entry_price[symbol] = curr['close']
                stop_price[symbol] = entry_price[symbol] * (1 - stop_loss_pct)
                trades[symbol] += 1
                eq.append(eq[-1] * (1 - transaction_cost))

            elif position[symbol] == 1 and curr['close'] <= stop_price[symbol]:
                ret = (stop_price[symbol] / entry_price[symbol]) - 1
                eq.append(eq[-1] * (1 + ret) * (1 - transaction_cost))
                position[symbol] = 0

            elif position[symbol] == 1 and (curr['EMA9'] < curr['EMA21'] or curr['RSI'] < 40):
                ret = (curr['close'] / entry_price[symbol]) - 1
                eq.append(eq[-1] * (1 + ret) * (1 - transaction_cost))
                position[symbol] = 0

            elif position[symbol] == 1:
                prev_close = df.iloc[i - 1]['close']
                bar_return = (curr['close'] / prev_close) - 1
                eq.append(eq[-1] * (1 + bar_return))

            else:
                eq.append(eq[-1])

    # combine into one portfolio equity curve
    portfolio_equity = pd.DataFrame(sub_equity).sum(axis=1)

    total_return = (portfolio_equity.iloc[-1] / initial_capital) - 1
    running_max = portfolio_equity.cummax()
    drawdown = (portfolio_equity - running_max) / running_max
    max_drawdown = drawdown.min()

    daily_returns = portfolio_equity.pct_change().dropna()
    sharpe = (
        daily_returns.mean() / daily_returns.std() * np.sqrt(252)
        if daily_returns.std() > 0 else float("nan")
    )

    return {
        "total_return": round(total_return, 4),
        "final_equity": round(portfolio_equity.iloc[-1], 2),
        "max_drawdown": round(max_drawdown, 4),
        "sharpe_ratio": round(sharpe, 4),
        "trades_per_symbol": trades,
    }, portfolio_equity