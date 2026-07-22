import threading
import time
import smtplib
import requests
from email.mime.text import MIMEText
from datetime import datetime

from wrapper import IBWrapper
from client import IBClient
from email_config import GMAIL_ADDRESS, GMAIL_APP_PASSWORD, RECIPIENT_EMAIL
from telegram_config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

STARTING_BALANCE = 1000000.0


class IBApp(IBWrapper, IBClient):
    def __init__(self, ip, port, client_id, account):
        IBWrapper.__init__(self)
        IBClient.__init__(self, wrapper=self)
        self.account = account
        self.connect(ip, port, client_id)
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()
        time.sleep(3)


def send_email(subject, html_body):
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
            print("Telegram summary sent.")
        else:
            print("Telegram send failed: " + str(response.status_code) + " " + response.text)
    except Exception as e:
        print("Failed to send Telegram summary: " + str(e))


def position_pnl_pct(data):
    avg_cost = data.get("average_cost", 0.0)
    market_price = data.get("market_price", 0.0)
    if avg_cost == 0:
        return 0.0
    return ((market_price - avg_cost) / avg_cost) * 100


if __name__ == "__main__":
    app = IBApp("127.0.0.1", 7497, 141, "DUQ153118")
    app.reqMarketDataType(3)

    print("Fetching account values and positions...")

    account_values = app.get_account_values()
    positions = app.get_positions()

    net_liq = account_values.get("NetLiquidation", (0.0, "USD"))[0]
    total_cash = account_values.get("TotalCashValue", (0.0, "USD"))[0]

    total_unrealized = 0.0
    total_realized = 0.0
    for symbol, data in positions.items():
        total_unrealized += data.get("unrealized_pnl", 0.0)
        total_realized += data.get("realized_pnl", 0.0)

    total_gain = net_liq - STARTING_BALANCE
    total_gain_pct = (total_gain / STARTING_BALANCE) * 100 if STARTING_BALANCE > 0 else 0.0

    print("Net Liquidation:", net_liq)
    print("Total Cash:", total_cash)
    print("Total Gain/Loss vs Starting Balance ($" + str(STARTING_BALANCE) + "):", total_gain, "(" + str(round(total_gain_pct, 2)) + "%)")
    print("Total Unrealized P&L (summed from positions):", total_unrealized)
    print("Total Realized P&L (summed from positions):", total_realized)
    print("Positions:", list(positions.keys()))

    gain_color = "#1a7f37" if total_gain >= 0 else "#cf222e"
    gain_sign = "+" if total_gain >= 0 else ""

    # --- build email ---
    rows_html = ""
    for symbol, data in positions.items():
        pnl = data.get("unrealized_pnl", 0.0)
        pnl_pct = position_pnl_pct(data)
        color = "#1a7f37" if pnl >= 0 else "#cf222e"
        pnl_sign = "+" if pnl >= 0 else ""
        rows_html += (
            "<tr>"
            "<td style='padding:6px 12px; font-weight:600;'>" + symbol + "</td>"
            "<td style='padding:6px 12px;'>" + str(data.get("position", 0)) + "</td>"
            "<td style='padding:6px 12px;'>" + str(round(data.get("average_cost", 0.0), 2)) + "</td>"
            "<td style='padding:6px 12px;'>" + str(round(data.get("market_price", 0.0), 2)) + "</td>"
            "<td style='padding:6px 12px; color:" + color + ";'>" + pnl_sign + str(round(pnl, 2)) +
            " (" + pnl_sign + str(round(pnl_pct, 2)) + "%)</td>"
            "</tr>"
        )

    html_body = (
        "<html><body style=\"font-family: 'Helvetica Neue', Arial, sans-serif; font-size:14px; color:#222; max-width:700px;\">"
        "<h2 style=\"font-family: Georgia, 'Times New Roman', serif; color:#111;\">Portfolio Summary</h2>"
        "<p style='color:#666; font-size:12px;'>" + datetime.now().strftime("%Y-%m-%d %H:%M") + "</p>"
        "<p style='font-size:18px; font-weight:bold; color:" + gain_color + ";'>"
        "Total Gain/Loss: " + gain_sign + "$" + str(round(total_gain, 2)) +
        " (" + gain_sign + str(round(total_gain_pct, 2)) + "%)"
        "</p>"
        "<p><b>Net Liquidation:</b> $" + str(round(net_liq, 2)) + "</p>"
        "<p><b>Starting Balance:</b> $" + str(round(STARTING_BALANCE, 2)) + "</p>"
        "<p><b>Total Cash:</b> $" + str(round(total_cash, 2)) + "</p>"
        "<p><b>Unrealized P&L:</b> $" + str(round(total_unrealized, 2)) + "</p>"
        "<p><b>Realized P&L:</b> $" + str(round(total_realized, 2)) + "</p>"
        "<table style='border-collapse:collapse; width:100%; margin-top:10px;'>"
        "<tr style='font-weight:bold; border-bottom:2px solid #ccc;'>"
        "<td style='padding:6px 12px;'>Symbol</td>"
        "<td style='padding:6px 12px;'>Qty</td>"
        "<td style='padding:6px 12px;'>Avg Cost</td>"
        "<td style='padding:6px 12px;'>Current</td>"
        "<td style='padding:6px 12px;'>Unrealized P&L</td>"
        "</tr>"
        + rows_html +
        "</table>"
        "</body></html>"
    )

    subject = "Portfolio Summary - " + gain_sign + "$" + str(round(total_gain, 2)) + " (" + gain_sign + str(round(total_gain_pct, 2)) + "%) - " + datetime.now().strftime("%Y-%m-%d %H:%M")
    send_email(subject, html_body)

    # --- build telegram message ---
    lines = []
    lines.append("💼 *Portfolio Summary*")
    lines.append("_" + datetime.now().strftime("%A, %b %d — %H:%M") + "_")
    lines.append("")
    gain_emoji = "🟢" if total_gain >= 0 else "🔴"
    lines.append(gain_emoji + " *Total Gain/Loss: " + gain_sign + "$" + str(round(total_gain, 2)) + " (" + gain_sign + str(round(total_gain_pct, 2)) + "%)*")
    lines.append("")
    lines.append("Net Liquidation: $" + str(round(net_liq, 2)))
    lines.append("Starting Balance: $" + str(round(STARTING_BALANCE, 2)))
    lines.append("Total Cash: $" + str(round(total_cash, 2)))
    lines.append("Unrealized P&L: $" + str(round(total_unrealized, 2)))
    lines.append("Realized P&L: $" + str(round(total_realized, 2)))
    lines.append("")
    lines.append("*Positions:*")
    for symbol, data in positions.items():
        pnl = data.get("unrealized_pnl", 0.0)
        pnl_pct = position_pnl_pct(data)
        pnl_sign = "+" if pnl >= 0 else ""
        emoji = "🟢" if pnl >= 0 else "🔴"
        lines.append(
            emoji + " " + symbol + ": " + str(data.get("position", 0)) +
            " @ " + str(round(data.get("average_cost", 0.0), 2)) +
            " (now " + str(round(data.get("market_price", 0.0), 2)) +
            ", P&L " + pnl_sign + "$" + str(round(pnl, 2)) +
            " / " + pnl_sign + str(round(pnl_pct, 2)) + "%)"
        )

    send_telegram("\n".join(lines))

    app.disconnect()