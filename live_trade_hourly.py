import json
import os
import threading
import time
import smtplib
import requests
from email.mime.text import MIMEText
from datetime import datetime

from wrapper import IBWrapper
from client import IBClient
from contract import future, cfd, stock
from order import market, BUY, SELL
from position_sizing import calculate_position_size
from email_config import GMAIL_ADDRESS, GMAIL_APP_PASSWORD, RECIPIENT_EMAIL
from telegram_config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
import position_ownership as owner

STRATEGY = "hourly"

LOG_FILE = "hourly_trade_log.txt"

RSI_ENTRY_MIN = 50
RSI_ENTRY_MAX = 70
RSI_EXIT_THRESHOLD = 40

POSITION_SIZE_PCT = 0.01
MAX_QTY_FUTURES = 3
MAX_QTY_CFD = 20

summary_lines = []
action_summary = []


def build_instruments():
    instruments = {}

    dax_contract = future("DAX", "EUREX", "202609", currency="EUR", multiplier="25", trading_class="FDAX")
    instruments["DAX"] = {"contract": dax_contract, "multiplier": 25, "state_file": "dax_hourly_position_state.json"}

    nkd_contract = future("NKD", "CME", "20260910", currency="USD")
    instruments["NKD"] = {"contract": nkd_contract, "multiplier": 5, "state_file": "nkd_hourly_position_state.json"}

    spy_data_contract = stock("SPY", "SMART", "USD", primary_exchange="ARCA")
    spy_trade_contract = cfd("SPY", "SMART", "USD")
    instruments["SPY"] = {
        "contract": spy_trade_contract,
        "data_contract": spy_data_contract,
        "multiplier": 1,
        "state_file": "spy_hourly_position_state.json",
    }

    return instruments


class IBApp(IBWrapper, IBClient):
    def __init__(self, ip, port, client_id, account):
        IBWrapper.__init__(self)
        IBClient.__init__(self, wrapper=self)
        self.account = account
        self.connect(ip, port, client_id)
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()
        time.sleep(3)


def load_state(state_file):
    if os.path.exists(state_file):
        f = open(state_file, "r")
        data = json.load(f)
        f.close()
        if "quantity" not in data:
            data["quantity"] = 0
        if "manual" not in data:
            data["manual"] = False
        return data
    return {"position": 0, "entry_price": 0.0, "stop_price": 0.0, "quantity": 0, "manual": False}


def save_state(state_file, position, entry_price, stop_price, quantity, manual=False):
    state = {
        "position": position,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "quantity": quantity,
        "manual": manual,
    }
    f = open(state_file, "w")
    json.dump(state, f)
    f.close()


def log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = "[" + timestamp + "] " + message
    print(line)
    f = open(LOG_FILE, "a")
    f.write(line + "\n")
    f.close()
    summary_lines.append(message)


def has_notable_action():
    for label, action in action_summary:
        if action.startswith("BUY") or action.startswith("SELL"):
            return True
    return False


def send_summary_email():
    subject = "HOURLY Trading Bot Summary - " + datetime.now().strftime("%Y-%m-%d %H:%M")

    rows_html = ""
    for label, action in action_summary:
        color = "#333333"
        if action.startswith("BUY"):
            color = "#1a7f37"
        elif action.startswith("SELL"):
            color = "#cf222e"
        rows_html += (
            "<tr>"
            "<td style='padding:8px 14px; font-weight:600; border-bottom:1px solid #eee;'>" + label + "</td>"
            "<td style='padding:8px 14px; color:" + color + "; border-bottom:1px solid #eee;'>" + action + "</td>"
            "</tr>"
        )

    html_body = (
        "<html><body style=\"font-family: 'Helvetica Neue', Arial, sans-serif; font-size:14px; color:#222; max-width:600px;\">"
        "<h2 style=\"font-family: Georgia, 'Times New Roman', serif; color:#111;\">Hourly Futures/CFD Summary</h2>"
        "<p style='color:#666; font-size:12px;'>" + datetime.now().strftime("%Y-%m-%d %H:%M") + "</p>"
        "<table style='border-collapse:collapse; width:100%;'>"
        + rows_html +
        "</table>"
        "</body></html>"
    )

    msg = MIMEText(html_body, "html")
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = RECIPIENT_EMAIL

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, RECIPIENT_EMAIL, msg.as_string())
        server.quit()
        print("Summary email sent.")
    except Exception as e:
        print("Failed to send summary email: " + str(e))


def send_telegram_summary():
    buys = [item for item in action_summary if item[1].startswith("BUY")]
    sells = [item for item in action_summary if item[1].startswith("SELL")]

    lines = []
    lines.append("⏱️ *HOURLY Futures/CFD Summary*")
    lines.append("_" + datetime.now().strftime("%A, %b %d — %H:%M") + "_")
    lines.append("")

    if buys:
        lines.append("🟢 *BUYS*")
        for label, action in buys:
            detail = action.replace("BUY (", "").rstrip(")")
            lines.append("   • *" + label + "*  —  " + detail)

    if sells:
        lines.append("🔴 *SELLS*")
        for label, action in sells:
            detail = action.replace("SELL (", "").rstrip(")")
            lines.append("   • *" + label + "*  —  " + detail)

    message_text = "\n".join(lines)

    url = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message_text,
        "parse_mode": "Markdown",
    }

    try:
        response = requests.post(url, data=payload, timeout=10)
        if response.status_code == 200:
            print("Telegram summary sent.")
        else:
            print("Telegram send failed: " + str(response.status_code) + " " + response.text)
    except Exception as e:
        print("Failed to send Telegram summary: " + str(e))


def compute_signal(prices, rsi_window=14):
    ema9 = prices.ewm(span=9, adjust=False).mean()
    ema21 = prices.ewm(span=21, adjust=False).mean()

    delta = prices.diff()
    gain = delta.where(delta > 0, 0).rolling(window=rsi_window).mean()
    loss = -delta.where(delta < 0, 0).rolling(window=rsi_window).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))

    return ema9.iloc[-1], ema21.iloc[-1], rsi.iloc[-1]


def reconcile_state(label, state_file, real_positions):
    state = load_state(state_file)
    local_position = state["position"]

    real_qty = 0
    real_avg_cost = 0.0
    if label in real_positions:
        real_qty = real_positions[label].get("position", 0)
        real_avg_cost = real_positions[label].get("average_cost", 0.0)

    broker_has_position = real_qty != 0

    if local_position == 1 and not broker_has_position:
        log(label + ": STATE MISMATCH - local file says position open, broker shows none. Resetting to flat.")
        save_state(state_file, 0, 0.0, 0.0, 0, False)
        owner.release(label, STRATEGY)
        return load_state(state_file)

    if local_position == 0 and broker_has_position:
        log(label + ": position (" + str(real_qty) + ") not tracked locally — treating as MANUAL, bot will not auto-sell this.")
        save_state(state_file, 1, real_avg_cost, 0.0, int(abs(real_qty)), True)
        return load_state(state_file)

    return state


def has_pending_order(label, open_orders):
    if label not in open_orders:
        return False
    return len(open_orders[label]) > 0


def process_instrument(app, label, config, request_id, real_positions, open_orders):
    contract = config["contract"]
    data_contract = config.get("data_contract", contract)
    multiplier = config["multiplier"]
    state_file = config["state_file"]

    log("--- Checking " + label + " ---")

    state = reconcile_state(label, state_file, real_positions)
    is_manual = state.get("manual", False)

    if has_pending_order(label, open_orders):
        log(label + ": pending order already exists (from a previous run). Skipping to avoid duplicate.")
        action_summary.append((label, "HOLD (pending order already open, skipped)"))
        return

    what_to_show = "TRADES" if data_contract.secType == "FUT" else "MIDPOINT"

    history = app.get_historical_data(request_id, data_contract, "10 D", "1 hour", what_to_show)

    log(label + ": received " + str(len(history)) + " rows of historical data.")

    if history.empty or len(history) < 25:
        log(label + ": ERROR insufficient data (" + str(len(history)) + " rows, need 25). Skipping.")
        action_summary.append((label, "ERROR - no data"))
        return

    ema9, ema21, rsi = compute_signal(history["close"])
    current_price = history["close"].iloc[-1]

    price_str = str(round(current_price, 2))
    ema9_str = str(round(ema9, 2))
    ema21_str = str(round(ema21, 2))
    rsi_str = str(round(rsi, 2))
    log(label + ": price=" + price_str + " EMA9=" + ema9_str + " EMA21=" + ema21_str + " RSI=" + rsi_str)

    position = state["position"]
    entry_price = state["entry_price"]
    stop_price = state["stop_price"]
    held_quantity = state.get("quantity", 0)

    entry_signal = ema9 > ema21 and rsi > RSI_ENTRY_MIN and rsi < RSI_ENTRY_MAX
    exit_signal = ema9 < ema21 or rsi < RSI_EXIT_THRESHOLD
    stop_hit = position == 1 and current_price <= stop_price

    max_qty = MAX_QTY_CFD if contract.secType == "CFD" else MAX_QTY_FUTURES

    if position == 0 and entry_signal and not owner.can_trade(label, STRATEGY):
        holder = owner.owner_of(label)
        log(label + ": ENTRY SIGNAL but owned by '" + str(holder) +
            "'. Skipping to avoid two strategies fighting over one contract.")
        action_summary.append((label, "HOLD (owned by " + str(holder) + ")"))

    elif position == 0 and entry_signal:
        account_values = app.get_account_values()
        net_liq = account_values.get("NetLiquidation", (0.0, "USD"))[0]
        dynamic_qty = calculate_position_size(
            net_liq, current_price, POSITION_SIZE_PCT,
            multiplier=multiplier, max_qty=max_qty
        )
        log(label + ": ENTRY SIGNAL. Net Liq=$" + str(round(net_liq, 2)) + " -> sizing to " + str(dynamic_qty) + ". Placing BUY.")
        order = market(BUY, dynamic_qty)
        order_id = app.send_order(contract, order)
        log(label + ": order sent (id=" + str(order_id) + "). Waiting to confirm fill...")
        time.sleep(5)
        log(label + ": check TWS Trades tab to confirm this order actually filled.")
        new_stop = current_price * 0.95
        save_state(state_file, 1, current_price, new_stop, dynamic_qty, False)
        owner.claim(label, STRATEGY, dynamic_qty, float(current_price))
        action_summary.append((label, "BUY (new position, " + str(dynamic_qty) + " units, price " + price_str + ")"))

    elif position == 0 and ema9 > ema21 and rsi >= RSI_ENTRY_MAX:
        log(label + ": signal fired but RSI is overbought. Skipping entry.")
        action_summary.append((label, "HOLD (flat, overbought - skipped entry)"))

    elif position == 1 and is_manual:
        entry_str = str(round(entry_price, 2)) if entry_price else "unknown"
        log(label + ": MANUAL position (x" + str(held_quantity) + ", entry=" + entry_str + ") — bot will not auto-sell.")
        action_summary.append((label, "HOLD (manual position, not managed by bot)"))

    elif position == 1 and stop_hit:
        log(label + ": STOP-LOSS HIT. Placing SELL x" + str(held_quantity))
        order = market(SELL, held_quantity)
        order_id = app.send_order(contract, order)
        log(label + ": close order sent (id=" + str(order_id) + ")")
        time.sleep(5)
        save_state(state_file, 0, 0.0, 0.0, 0, False)
        owner.release(label, STRATEGY)
        action_summary.append((label, "SELL (stop-loss hit, price " + price_str + ")"))

    elif position == 1 and exit_signal:
        log(label + ": EXIT SIGNAL. Placing SELL x" + str(held_quantity))
        order = market(SELL, held_quantity)
        order_id = app.send_order(contract, order)
        log(label + ": close order sent (id=" + str(order_id) + ")")
        time.sleep(5)
        save_state(state_file, 0, 0.0, 0.0, 0, False)
        owner.release(label, STRATEGY)
        action_summary.append((label, "SELL (trend exit, price " + price_str + ")"))

    elif position == 1:
        entry_str = str(round(entry_price, 2))
        current_str = str(round(current_price, 2))
        stop_str = str(round(stop_price, 2))
        log(label + ": holding x" + str(held_quantity) + ". entry=" + entry_str + " current=" + current_str + " stop=" + stop_str)
        action_summary.append((label, "HOLD (in position, entry " + entry_str + ", now " + current_str + ")"))

    else:
        log(label + ": no signal, staying flat.")
        action_summary.append((label, "HOLD (flat, no signal)"))


if __name__ == "__main__":
    app = IBApp("127.0.0.1", 7497, 173, "DUQ153118")
    app.reqMarketDataType(3)

    log("=== HOURLY futures/CFD signal check started ===")

    real_positions = app.get_positions()
    log("Broker-reported positions: " + str(list(real_positions.keys())))

    cleared = owner.sync_with_broker(real_positions)
    if cleared:
        log("Cleared stale ownership claims: " + str(cleared))

    open_orders = app.get_open_orders()
    log("Pending open orders: " + str(list(open_orders.keys())))

    instruments = build_instruments()

    i = 0
    for label in instruments:
        config = instruments[label]
        try:
            process_instrument(app, label, config, 90000 + i, real_positions, open_orders)
        except Exception as e:
            log(label + ": EXCEPTION - " + str(e))
            action_summary.append((label, "ERROR - " + str(e)))
        i = i + 1
        time.sleep(3)

    log("=== HOURLY signal check complete ===")
    app.disconnect()

    if has_notable_action():
        send_summary_email()
        send_telegram_summary()
    else:
        print("No BUY/SELL execution this run — skipping notifications to avoid noise.")