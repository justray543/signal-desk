from ibapi.contract import Contract, ComboLeg


def stock(symbol, exchange, currency, primary_exchange=None):
    contract = Contract()
    contract.symbol = symbol
    contract.exchange = exchange
    contract.currency = currency
    contract.secType = "STK"
    if primary_exchange:
        contract.primaryExchange = primary_exchange
    return contract


def future(symbol, exchange, contract_month, currency="USD", multiplier=None, trading_class=None):
    contract = Contract()
    contract.symbol = symbol
    contract.exchange = exchange
    contract.lastTradeDateOrContractMonth = contract_month
    contract.secType = "FUT"
    contract.currency = currency
    if multiplier:
        contract.multiplier = multiplier
    if trading_class:
        contract.tradingClass = trading_class
    return contract


def option(symbol, exchange, contract_month, strike, right):
    contract = Contract()
    contract.symbol = symbol
    contract.exchange = exchange
    contract.lastTradeDateOrContractMonth = contract_month
    contract.strike = strike
    contract.right = right
    contract.secType = "OPT"
    return contract


def forex(base_currency, quote_currency):
    contract = Contract()
    contract.symbol = base_currency
    contract.currency = quote_currency
    contract.exchange = "IDEALPRO"
    contract.secType = "CASH"
    return contract


def index(symbol, exchange, currency):
    contract = Contract()
    contract.symbol = symbol
    contract.exchange = exchange
    contract.currency = currency
    contract.secType = "IND"
    return contract


def crypto(symbol, exchange="PAXOS", currency="USD"):
    contract = Contract()
    contract.symbol = symbol
    contract.secType = "CRYPTO"
    contract.exchange = exchange
    contract.currency = currency
    return contract


def cfd(symbol, exchange="SMART", currency="USD"):
    contract = Contract()
    contract.symbol = symbol
    contract.secType = "CFD"
    contract.exchange = exchange
    contract.currency = currency
    return contract


def combo_leg(contract_details, ratio, action):
    leg = ComboLeg()
    leg.conId = contract_details.contract.conId
    leg.ratio = ratio
    leg.action = action
    leg.exchange = contract_details.contract.exchange
    return leg


def spread(legs):
    contract = Contract()
    contract.symbol = "USD"
    contract.secType = "BAG"
    contract.currency = "USD"
    contract.exchange = "SMART"
    contract.comboLegs = legs
    return contract