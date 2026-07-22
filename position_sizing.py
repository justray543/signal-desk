def calculate_position_size(net_liquidation, price, position_size_pct, multiplier=1, min_qty=1, max_qty=None):
    """
    Calculate position size based on a percentage of account net liquidation.

    net_liquidation: total account value (USD)
    price: current price of the instrument
    position_size_pct: fraction of account to risk per position (e.g. 0.01 = 1%)
    multiplier: contract multiplier for futures (1 for stocks)
    min_qty: minimum quantity floor (avoid 0-quantity orders)
    max_qty: optional hard cap on quantity, regardless of calculation
    """
    if price <= 0 or net_liquidation <= 0:
        return min_qty

    allocation = net_liquidation * position_size_pct
    contract_value = price * multiplier
    quantity = int(allocation / contract_value)

    if quantity < min_qty:
        quantity = min_qty

    if max_qty is not None and quantity > max_qty:
        quantity = max_qty

    return quantity