from ibapi.order import Order

BUY = "BUY"
SELL = "SELL"


def _clean_order(order):
    order.eTradeOnly = False
    order.firmQuoteOnly = False
    return order


def market(action, quantity):
    order = Order()
    order.action = action
    order.orderType = "MKT"
    order.totalQuantity = quantity
    return _clean_order(order)


def limit(action, quantity, limit_price):
    order = Order()
    order.action = action
    order.orderType = "LMT"
    order.totalQuantity = quantity
    order.lmtPrice = limit_price
    return _clean_order(order)


def stop(action, quantity, stop_price):
    order = Order()
    order.action = action
    order.orderType = "STP"
    order.auxPrice = stop_price
    order.totalQuantity = quantity
    return _clean_order(order)