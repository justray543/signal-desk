"""
position_ownership.py

Stops multiple strategies fighting over the same broker position.

The problem it solves: live_trade.py (daily), live_trade_nq_30min.py and
live_trade_nq_5min.py all trade NQ Sep 2026. IBKR reports ONE aggregate NQ
position, so each script's reconcile_state() sees the others' contracts.
Whoever runs first claims it; the rest either adopt it as "manual" and
freeze, or worse, sell a position they did not open.

The fix: a single registry file recording which strategy owns which symbol.
Before a script trades a symbol it claims ownership. If another strategy
already owns it, the script skips rather than trading. Ownership is released
on exit.

This is advisory locking, not enforcement. It works because all scripts are
cooperative and run sequentially from cron on one machine. It would not be
safe for concurrent processes.
"""

import json
import os
from datetime import datetime

OWNERSHIP_FILE = "position_ownership.json"


def _read(path=OWNERSHIP_FILE):
    if not os.path.exists(path):
        return {}
    try:
        f = open(path, "r")
        data = json.load(f)
        f.close()
        return data
    except (json.JSONDecodeError, IOError):
        # A corrupt registry must not stop trading; treat as empty and let
        # the reconcile logic in each script fall back to broker truth.
        return {}


def _write(data, path=OWNERSHIP_FILE):
    tmp = path + ".tmp"
    f = open(tmp, "w")
    json.dump(data, f, indent=2)
    f.close()
    os.replace(tmp, path)   # atomic, so a crash mid-write cannot corrupt it


def owner_of(symbol, path=OWNERSHIP_FILE):
    """Which strategy currently owns this symbol, or None."""
    data = _read(path)
    entry = data.get(symbol)
    if entry is None:
        return None
    return entry.get("strategy")


def can_trade(symbol, strategy, path=OWNERSHIP_FILE):
    """
    True if this strategy may act on this symbol.

    Free symbols are tradeable. Symbols owned by this same strategy are
    tradeable. Symbols owned by another strategy are not.
    """
    current = owner_of(symbol, path)
    if current is None:
        return True
    return current == strategy


def claim(symbol, strategy, quantity, entry_price, path=OWNERSHIP_FILE):
    """
    Record that `strategy` now holds `symbol`.

    Returns False if another strategy already owns it, in which case the
    caller must not place an order.
    """
    data = _read(path)
    existing = data.get(symbol)

    if existing is not None and existing.get("strategy") != strategy:
        return False

    data[symbol] = {
        "strategy": strategy,
        "quantity": quantity,
        "entry_price": entry_price,
        "claimed_at": datetime.now().isoformat(timespec="seconds"),
    }
    _write(data, path)
    return True


def release(symbol, strategy, path=OWNERSHIP_FILE):
    """
    Give up ownership after closing a position.

    Refuses to release a symbol owned by a different strategy, so a buggy
    script cannot unlock another strategy's position.
    """
    data = _read(path)
    existing = data.get(symbol)

    if existing is None:
        return True
    if existing.get("strategy") != strategy:
        return False

    del data[symbol]
    _write(data, path)
    return True


def sync_with_broker(real_positions, path=OWNERSHIP_FILE):
    """
    Drop ownership entries for symbols the broker no longer holds.

    Call once at the start of each run. Without this, a position closed
    manually in TWS leaves a stale claim that blocks the strategy forever.
    Returns the list of symbols that were cleared.
    """
    data = _read(path)
    stale = []

    for symbol in list(data.keys()):
        qty = 0
        if symbol in real_positions:
            qty = real_positions[symbol].get("position", 0)
        if qty == 0:
            stale.append(symbol)
            del data[symbol]

    if stale:
        _write(data, path)
    return stale


def all_claims(path=OWNERSHIP_FILE):
    """Full registry, for the dashboard and for debugging."""
    return _read(path)