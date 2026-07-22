import pandas as pd
import numpy as np

def backtest_intraday_strategy(
    price_history: dict,
    initial_capital: float = 100000.0,
    transaction_cost: float = 0.001,
    stop_loss_pct: float = 0.05,
    risk_reward_ratio: float = None,  # e.g. 2.0 means take-profit at 2x the stop distance
):
    results = {}

    for symbol, df_raw in price_history.items():
        if df_raw is None or len(df_raw) < 100:
            print(f"Skipping {symbol}: Not enough data")
            continue

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

        if len(df) == 0:
            print(f"Skipping {symbol}: empty after dropna")
            continue

        position = 0
        equity = [initial_capital]
        trades = 0
        wins = 0
        losses = 0
        stop_loss_hits = 0
        take_profit_hits = 0
        entry_price = 0.0
        stop_price = 0.0
        target_price = 0.0

        for i in range(1, len(df)):
            curr = df.iloc[i]

            if position == 0 and curr['EMA9'] > curr['EMA21'] and curr['RSI'] > 50:
                position = 1
                entry_price = curr['close']
                stop_price = entry_price * (1 - stop_loss_pct)
                if risk_reward_ratio is not None:
                    risk_distance = entry_price - stop_price
                    target_price = entry_price + (risk_distance * risk_reward_ratio)
                else:
                    target_price = None
                trades += 1
                equity.append(equity[-1] * (1 - transaction_cost))

            elif position == 1 and curr['close'] <= stop_price:
                ret = (stop_price / entry_price) - 1
                equity.append(equity[-1] * (1 + ret) * (1 - transaction_cost))
                position = 0
                stop_loss_hits += 1
                losses += 1

            elif position == 1 and target_price is not None and curr['close'] >= target_price:
                ret = (target_price / entry_price) - 1
                equity.append(equity[-1] * (1 + ret) * (1 - transaction_cost))
                position = 0
                take_profit_hits += 1
                wins += 1

            elif position == 1 and (curr['EMA9'] < curr['EMA21'] or curr['RSI'] < 40):
                ret = (curr['close'] / entry_price) - 1
                equity.append(equity[-1] * (1 + ret) * (1 - transaction_cost))
                position = 0
                if ret > 0:
                    wins += 1
                else:
                    losses += 1

            elif position == 1:
                prev_close = df.iloc[i - 1]['close']
                bar_return = (curr['close'] / prev_close) - 1
                equity.append(equity[-1] * (1 + bar_return))

            else:
                equity.append(equity[-1])

        if position == 1:
            final_ret = (df.iloc[-1]['close'] / entry_price) - 1
            equity.append(equity[-1] * (1 + final_ret) * (1 - transaction_cost))
            if final_ret > 0:
                wins += 1
            else:
                losses += 1

        total_return = (equity[-1] / initial_capital) - 1
        buy_hold_return = (df.iloc[-1]['close'] / df.iloc[0]['close']) - 1

        returns = pd.Series(equity).pct_change().dropna()
        sharpe = (returns.mean() / returns.std() * np.sqrt(252 * 6.5)) if len(returns) > 1 and returns.std() > 0 else 0.0

        win_rate = (wins / trades * 100) if trades > 0 else 0.0

        results[symbol] = {
            'total_return_strategy': round(total_return, 6),
            'total_return_buy_hold': round(buy_hold_return, 6),
            'sharpe_ratio': round(sharpe, 6),
            'max_drawdown': round((pd.Series(equity) / pd.Series(equity).cummax() - 1).min(), 5),
            'num_trades': trades,
            'win_rate_pct': round(win_rate, 2),
            'stop_loss_hits': stop_loss_hits,
            'take_profit_hits': take_profit_hits,
            'final_equity_strategy': round(equity[-1], 2),
            'final_equity_buy_hold': round(initial_capital * (1 + buy_hold_return), 2)
        }

    return pd.DataFrame(results).T