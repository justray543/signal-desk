import time
import pandas as pd
from ibapi.client import EClient

from utils import Tick, TRADE_BAR_PROPERTIES, DEFAULT_MARKET_DATA_ID, DEFAULT_CONTRACT_ID
from order import BUY, SELL


class IBClient(EClient):
    def __init__(self, wrapper):
        EClient.__init__(self, wrapper)

    # ---------- orders ----------
    def send_order(self, contract, order):
        order_id = self.wrapper.nextValidOrderId
        self.placeOrder(orderId=order_id, contract=contract, order=order)
        self.wrapper.nextValidOrderId = order_id + 1  # advance locally, don't wait for async callback
        self.reqIds(-1)
        return order_id

    def order_value(self, contract, order_type, value, **kwargs):
        quantity = self._calculate_order_value_quantity(contract, value)
        order = order_type(quantity=quantity, **kwargs)
        return self.send_order(contract, order)

    def order_target_quantity(self, contract, order_type, target, **kwargs):
        quantity = self._calculate_order_target_quantity(contract, target)
        order = order_type(
            action=SELL if quantity < 0 else BUY,
            quantity=abs(quantity),
            **kwargs
        )
        return self.send_order(contract, order)

    def _calculate_order_target_quantity(self, contract, target):
        positions = self.get_positions()
        if contract.symbol in positions.keys():
            current_position = positions[contract.symbol]["position"]
            target -= current_position
        return int(target)

    def order_percent(self, contract, order_type, percent, **kwargs):
        quantity = self._calculate_order_percent_quantity(contract, percent)
        order = order_type(quantity=quantity, **kwargs)
        return self.send_order(contract, order)

    def _calculate_order_percent_quantity(self, contract, percent):
        net_liquidation_value = self.get_account_values(key="NetLiquidation")[0]
        value = net_liquidation_value * percent
        return self._calculate_order_value_quantity(contract, value)

    def order_target_value(self, contract, order_type, target, **kwargs):
        target_quantity = self._calculate_order_value_quantity(contract, target)
        quantity = self._calculate_order_target_quantity(contract, target_quantity)
        order = order_type(
            action=SELL if quantity < 0 else BUY,
            quantity=abs(quantity),
            **kwargs
        )
        return self.send_order(contract, order)

    def _calculate_order_value_quantity(self, contract, value):
        last_price = self.get_market_data(
            request_id=DEFAULT_MARKET_DATA_ID, contract=contract, tick_type=68
        )
        multiplier = contract.multiplier if contract.multiplier != "" else 1
        return int(value / (last_price * multiplier))

    def order_target_percent(self, contract, order_type, target, **kwargs):
        quantity = self._calculate_order_target_percent_quantity(contract, target)
        order = order_type(
            action=SELL if quantity < 0 else BUY,
            quantity=abs(quantity),
            **kwargs
        )
        return self.send_order(contract, order)

    def _calculate_order_target_percent_quantity(self, contract, target):
        target_quantity = self._calculate_order_percent_quantity(contract, target)
        return self._calculate_order_target_quantity(contract, target_quantity)

    # ---------- contracts ----------
    def resolve_contract(self, contract, request_id=DEFAULT_CONTRACT_ID):
        self.reqContractDetails(reqId=request_id, contract=contract)
        time.sleep(2)
        return self.resolved_contract

    # ---------- historical data ----------
    def get_historical_data(
        self, request_id, contract, duration, bar_size, what_to_show="MIDPOINT"
    ):
        # clear any stale/partial data from a previous attempt on this request_id
        self.historical_data[request_id] = []

        self.reqHistoricalData(
            reqId=request_id,
            contract=contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow=what_to_show,
            useRTH=1,
            formatDate=1,
            keepUpToDate=False,
            chartOptions=[],
        )
        time.sleep(5)
        data = self.historical_data.get(request_id, [])
        df = pd.DataFrame(data, columns=TRADE_BAR_PROPERTIES)

        cleaned_time = df["time"].astype(str).str.replace(r"\s+", " ", regex=True).str.strip()

        bar_sizes = ["day", "D", "week", "W", "month"]
        if any(x in bar_size for x in bar_sizes):
            df.set_index(pd.to_datetime(cleaned_time, format="%Y%m%d"), inplace=True)
        else:
            try:
                df.set_index(pd.to_datetime(cleaned_time, format="%Y%m%d %H:%M:%S"), inplace=True)
            except ValueError:
                df.set_index(pd.to_datetime(cleaned_time, format="mixed"), inplace=True)

        df.drop("time", axis=1, inplace=True)
        df["symbol"] = contract.symbol
        df.request_id = request_id
        return df

    def get_historical_data_for_many(
        self, request_id, contracts, duration, bar_size,
        col_to_use="close", what_to_show="MIDPOINT"
    ):
        dfs = []
        for contract in contracts:
            data = self.get_historical_data(
                request_id, contract, duration, bar_size, what_to_show
            )
            dfs.append(data)
            request_id += 1
        return (
            pd.concat(dfs)
            .reset_index()
            .pivot(index="time", columns="symbol", values=col_to_use)
        )

    # ---------- market data ----------
    def get_market_data(self, request_id, contract, tick_type=68):
        self.reqMktData(
            reqId=request_id,
            contract=contract,
            genericTickList="",
            snapshot=True,
            regulatorySnapshot=False,
            mktDataOptions=[]
        )
        time.sleep(8)
        self.cancelMktData(reqId=request_id)
        data = self.market_data.get(request_id, {})
        return data.get(tick_type)

    def get_market_data_for_many(self, contracts, tick_type=68, wait_seconds=15):
        request_map = {}
        for i, contract in enumerate(contracts):
            request_id = 400 + i
            request_map[request_id] = contract.symbol
            self.reqMktData(
                reqId=request_id,
                contract=contract,
                genericTickList="",
                snapshot=True,
                regulatorySnapshot=False,
                mktDataOptions=[]
            )

        time.sleep(wait_seconds)

        prices = {}
        for request_id, symbol in request_map.items():
            self.cancelMktData(reqId=request_id)
            data = self.market_data.get(request_id, {})
            prices[symbol] = data.get(tick_type)

        return prices

    # ---------- streaming data ----------
    def get_streaming_data(self, request_id, contract):
        self.reqTickByTickData(
            reqId=request_id,
            contract=contract,
            tickType="BidAsk",
            numberOfTicks=0,
            ignoreSize=True
        )
        time.sleep(10)
        while True:
            if self.stream_event.is_set():
                yield Tick(*self.streaming_data[request_id])
                self.stream_event.clear()

    def stop_streaming_data(self, request_id):
        self.cancelTickByTickData(reqId=request_id)

    # ---------- orders (open/pending) ----------
    def get_open_orders(self):
        self.open_orders = {}  # clear stale data before fresh request
        self.reqAllOpenOrders()  # sees orders from ALL client IDs, not just this session's
        time.sleep(3)
        return self.open_orders

    # ---------- account ----------
    def get_account_values(self, key=None):
        self.reqAccountUpdates(True, self.account)
        time.sleep(2)
        if key:
            return self.account_values[key]
        return self.account_values

    def get_positions(self):
        self.reqAccountUpdates(True, self.account)
        time.sleep(2)
        return self.positions

    def get_pnl(self, request_id):
        self.reqPnL(request_id, self.account, "")
        time.sleep(2)
        return self.account_pnl