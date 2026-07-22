import json
import os
import threading
import time
import requests
from datetime import datetime

from wrapper import IBWrapper
from client import IBClient
from contract import future
from order import market, BUY, SELL
from position_sizing import calculate_position_size
from telegram_config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
import position_ownership as owner

STRATEGY = "nq_30min"
SYMBOL = "NQ"

LOG_FILE = "nq_30min_trade_log.txt"
STATE_FILE = "nq_30min_position_state.json"

RSI_ENTRY_MIN = 50
RSI_ENTRY_MAX = 70
RSI_EXIT_THRESHOLD = 40

POSITION_SIZE_PCT = 0.01
NQ_MULTIPLIER = 20  # VERIFY in TWS
MAX_QTY = 5


class IBApp(IBWrapper, IBClient):
    def __init__(self, ip, port, client_id, account):
        IBWrapper.__init__(self)
        IBClient.__init__(self, wrapper=self)
        self.account = account
        self.connect(ip, port, client_id)
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()
        time.sleep(3)


def load_state():
    if os.path.exists(STATE_FILE):
        f = open(STATE_FILE, "r")
        data = json.load(f)
        f.close()
        if "quantity" not in data:
            data["quantity"] = 0
        if "manual" not in data:
            data["manual"] = False
        return data
    return {"position": 0, "entry_price": 0.0, "stop_price": 0.0, "quantity": 0, "manual": False}


def save_state(position, entry_price, stop_price, quantity, manual=False):
    state = {
        "position": position,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "quantity": quantity,
        "manual": manual,
    }
    f = open(STATE_FILE, "w")
    json.dump(state, f)
    f.close()


def log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = "[" + timestamp + "] " + message
    print(line)
    f = open(LOG_FILE, "a")
    f.write(line + "\n")
    f.close()


def send_telegram(message_text):
    url = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message_text,
        "parse_mode": "Markdown",
    }
    try:
        response = requests.post(url, data=payload, timeout=10)
        if response.status_code == 200:
            print("Telegram sent.")
        else:
            print("Telegram send failed: " + str(response.status_code) + " " + response.text)
    except Exception as e:
        print("Failed to send Telegram: " + str(e))


def compute_signal(prices, rsi_window=14):
    ema9 = prices.ewm(span=9, adjust=False).mean()
    ema21 = prices.ewm(span=21, adjust=False).mean()

    delta = prices.diff()
    gain = delta.where(delta > 0, 0).rolling(window=rsi_window).mean()
    loss = -delta.where(delta < 0, 0).rolling(window=rsi_window).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))

    return ema9.iloc[-1], ema21.iloc[-1], rsi.iloc[-1]


def reconcile_state(real_positions):
    state = load_state()
    local_position = state["position"]

    real_qty = 0
    real_avg_cost = 0.0
    if "NQ" in real_positions:
        real_qty = real_positions["NQ"].get("position", 0)
        real_avg_cost = real_positions["NQ"].get("average_cost", 0.0)

    broker_has_position = real_qty != 0

    if local_position == 1 and not broker_has_position:
        log("STATE MISMATCH - local file says position open, broker shows none. Resetting to flat.")
        save_state(0, 0.0, 0.0, 0, False)
        return load_state()

    if local_position == 0 and broker_has_position:
        log("position (" + str(real_qty) + ") not tracked locally — treating as MANUAL, bot will not auto-sell this.")
        save_state(1, real_avg_cost, 0.0, int(abs(real_qty)), True)
        return load_state()

    return state


def has_pending_order(open_orders):
    if "NQ" not in open_orders:
        return False
    return len(open_orders["NQ"]) > 0


if __name__ == "__main__":
    app = IBApp("127.0.0.1", 7497, 164, "DUQ153118")
    app.reqMarketDataType(3)

    log("=== NQ 30-MIN signal check started ===")

    real_positions = app.get_positions()
    open_orders = app.get_open_orders()

    cleared = owner.sync_with_broker(real_positions)
    if cleared:
        log("Cleared stale ownership claims: " + str(cleared))

    log("Broker-reported NQ position: " + str(real_positions.get("NQ", {})))
    log("Pending open orders: " + str(list(open_orders.keys())))

    nq_contract = future("NQ", "CME", "202609", currency="USD")

    state = reconcile_state(real_positions)
    is_manual = state.get("manual", False)

    if has_pending_order(open_orders):
        log("Pending order already exists. Skipping to avoid duplicate.")
        app.disconnect()
        exit()

    history = app.get_historical_data(
        request_id=122000, contract=nq_contract,
        duration="5 D", bar_size="30 mins", what_to_show="TRADES"
    )

    if history.empty or len(history) < 25:
        log("ERROR insufficient data (" + str(len(history)) + " rows). Skipping.")
        app.disconnect()
        exit()

    ema9, ema21, rsi = compute_signal(history["close"])
    current_price = history["close"].iloc[-1]

    price_str = str(round(current_price, 2))
    log("NQ: price=" + price_str + " EMA9=" + str(round(ema9, 2)) + " EMA21=" + str(round(ema21, 2)) + " RSI=" + str(round(rsi, 2)))

    position = state["position"]
    entry_price = state["entry_price"]
    stop_price = state["stop_price"]
    held_quantity = state.get("quantity", 0)

    entry_signal = ema9 > ema21 and rsi > RSI_ENTRY_MIN and rsi < RSI_ENTRY_MAX
    exit_signal = ema9 < ema21 or rsi < RSI_EXIT_THRESHOLD
    stop_hit = position == 1 and current_price <= stop_price

    action_taken = None

    if position == 0 and entry_signal and not owner.can_trade(SYMBOL, STRATEGY):
        holder = owner.owner_of(SYMBOL)
        log("ENTRY SIGNAL but " + SYMBOL + " is owned by '" + str(holder) +
            "'. Skipping to avoid two strategies fighting over one contract.")

    elif position == 0 and entry_signal:
        account_values = app.get_account_values()
        net_liq = account_values.get("NetLiquidation", (0.0, "USD"))[0]
        dynamic_qty = calculate_position_size(
            net_liq, current_price, POSITION_SIZE_PCT,
            multiplier=NQ_MULTIPLIER, max_qty=MAX_QTY
        )
        log("ENTRY SIGNAL. Net Liq=$" + str(round(net_liq, 2)) + " -> sizing to " + str(dynamic_qty) + " contract(s)")
        order = market(BUY, dynamic_qty)
        order_id = app.send_order(nq_contract, order)
        log("order sent (id=" + str(order_id) + ")")
        time.sleep(5)
        new_stop = current_price * 0.95
        save_state(1, current_price, new_stop, dynamic_qty, False)
        owner.claim(SYMBOL, STRATEGY, dynamic_qty, float(current_price))
        action_taken = "BUY x" + str(dynamic_qty) + " at " + price_str

    elif position == 1 and is_manual:
        entry_str = str(round(entry_price, 2)) if entry_price else "unknown"
        log("MANUAL position (x" + str(held_quantity) + ", entry=" + entry_str + ") — bot will not auto-sell.")

    elif position == 1 and stop_hit:
        log("STOP-LOSS HIT. Placing SELL x" + str(held_quantity))
        order = market(SELL, held_quantity)
        order_id = app.send_order(nq_contract, order)
        log("close order sent (id=" + str(order_id) + ")")
        time.sleep(5)
        save_state(0, 0.0, 0.0, 0, False)
        owner.release(SYMBOL, STRATEGY)
        action_taken = "SELL (stop-loss) x" + str(held_quantity) + " at " + price_str

    elif position == 1 and exit_signal:
        log("EXIT SIGNAL. Placing SELL x" + str(held_quantity))
        order = market(SELL, held_quantity)
        order_id = app.send_order(nq_contract, order)
        log("close order sent (id=" + str(order_id) + ")")
        time.sleep(5)
        save_state(0, 0.0, 0.0, 0, False)
        owner.release(SYMBOL, STRATEGY)
        action_taken = "SELL (trend exit) x" + str(held_quantity) + " at " + price_str

    elif position == 1:
        entry_str = str(round(entry_price, 2))
        stop_str = str(round(stop_price, 2))
        log("holding x" + str(held_quantity) + ". entry=" + entry_str +
            " current=" + price_str + " stop=" + stop_str)

    else:
        log("No action. position=" + str(position))

    log("=== NQ 30-MIN check complete ===")
    app.disconnect()

    if action_taken:
        send_telegram("\u23f1 *NQ 30-min*\n" + action_taken)