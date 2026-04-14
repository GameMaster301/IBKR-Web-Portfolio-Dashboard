"""
Persistent IBKR connection — hardened for production use.

Design
------
- Single IB() instance in a dedicated daemon thread with its own event loop.
- Exponential back-off on reconnect: 5 s → 10 s → 20 s → … capped at 120 s.
- Passive heartbeat: every `heartbeat_interval` seconds a lightweight
  reqCurrentTime() is sent so the OS TCP layer cannot silently drop the
  connection without us noticing.
- All external-facing calls degrade gracefully: if TWS is unreachable the
  dashboard shows a "disconnected" banner and keeps displaying cached data.
- fetch_all_data() is non-blocking: a threading.Lock prevents overlapping
  fetches without ever blocking the Dash refresh thread.
"""

import asyncio
import logging
import threading
import time
from ib_async import IB, Forex

log = logging.getLogger(__name__)

_MAX_BACKOFF   = 120   # seconds
_BASE_BACKOFF  =   5   # seconds


class _IBKRConnection:
    def __init__(self):
        self._ib:        IB | None               = None
        self._loop:      asyncio.AbstractEventLoop | None = None
        self._connected: bool                    = False
        self._fetch_lock   = threading.Lock()
        self._conn_attempt = 0   # counts consecutive failures for back-off

        # Config (set by start())
        self._host               = '127.0.0.1'
        self._port               = 7497
        self._client_id          = 1
        self._readonly           = True
        self._reconnect_delay    = _BASE_BACKOFF
        self._heartbeat_interval = 30

    # ── Public interface ──────────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ib is not None and self._ib.isConnected()

    @property
    def status(self) -> str:
        return 'connected' if self.is_connected else 'disconnected'

    def start(self, host='127.0.0.1', port=7497, client_id=1,
              readonly=True, reconnect_delay=_BASE_BACKOFF,
              heartbeat_interval=30):
        self._host               = host
        self._port               = port
        self._client_id          = client_id
        self._readonly           = readonly
        self._reconnect_delay    = reconnect_delay   # used as base delay
        self._heartbeat_interval = heartbeat_interval
        t = threading.Thread(target=self._thread_main,
                             name='ib-async-loop', daemon=True)
        t.start()

    def fetch_all_data(self) -> dict | None:
        """Called from the Dash callback thread. Returns data dict or None."""
        if not self.is_connected:
            return None
        if not self._fetch_lock.acquire(blocking=False):
            log.info("Fetch already in progress — skipping this refresh")
            return None
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._do_fetch(), self._loop)
            return future.result(timeout=60)
        except Exception as e:
            log.error("fetch_all_data error: %s", e, exc_info=False)
            return None
        finally:
            self._fetch_lock.release()

    # ── Background thread ─────────────────────────────────────────────────────

    def _thread_main(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._connection_loop())

    async def _connection_loop(self):
        """Connect → heartbeat → reconnect on drop, forever, with back-off."""
        while True:
            ib          = IB()
            disc_event  = asyncio.Event()

            def _on_disconnect():
                self._loop.call_soon_threadsafe(disc_event.set)

            ib.disconnectedEvent += _on_disconnect

            try:
                log.info("Connecting to TWS at %s:%d (attempt %d) …",
                         self._host, self._port, self._conn_attempt + 1)
                await ib.connectAsync(
                    self._host, self._port,
                    clientId=self._client_id,
                    timeout=15,
                    readonly=self._readonly,
                )
                self._ib           = ib
                self._connected    = True
                self._conn_attempt = 0   # reset back-off counter on success
                log.info("Connected to TWS ✓")

                # Stay connected; heartbeat until disconnected
                await self._heartbeat_loop(ib, disc_event)
                log.warning("Disconnected from TWS")

            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("Connection failed: %s", e)
                self._conn_attempt += 1

            finally:
                self._connected = False
                self._ib        = None
                ib.disconnectedEvent -= _on_disconnect
                try:
                    ib.disconnect()
                except Exception:
                    pass

            delay = min(
                _BASE_BACKOFF * (2 ** min(self._conn_attempt, 8)),
                _MAX_BACKOFF,
            )
            log.info("Reconnecting in %ds …", delay)
            await asyncio.sleep(delay)

    async def _heartbeat_loop(self, ib: IB, disc_event: asyncio.Event):
        """
        Send a lightweight reqCurrentTime() ping every heartbeat_interval
        seconds. If the ping raises or the disconnect event fires, return
        so _connection_loop can attempt a reconnect.
        """
        interval = self._heartbeat_interval
        while not disc_event.is_set():
            try:
                await asyncio.wait_for(
                    disc_event.wait(), timeout=interval)
                # disc_event fired inside wait_for → exit
                return
            except asyncio.TimeoutError:
                pass   # interval elapsed, time to ping

            if not ib.isConnected():
                log.warning("Heartbeat: IB reports disconnected")
                return

            try:
                await asyncio.wait_for(ib.reqCurrentTimeAsync(), timeout=10)
                log.debug("Heartbeat OK")
            except Exception as e:
                log.warning("Heartbeat ping failed: %s — forcing reconnect", e)
                return

    # ── Async fetch (runs on the IB event loop) ───────────────────────────────

    async def _do_fetch(self) -> dict | None:
        ib = self._ib
        if ib is None or not ib.isConnected():
            return None

        def safe(v):
            try:
                return round(float(v), 4) if v == v and v is not None else None
            except Exception:
                return None

        # ── Portfolio positions (synchronous TWS cache — fast) ────────────────
        items     = ib.portfolio()
        positions = []
        contracts = []

        for item in items:
            market_price = item.marketPrice
            price_stale  = market_price != market_price   # True when NaN
            if price_stale:
                if item.position and item.marketValue == item.marketValue:
                    market_price = item.marketValue / item.position
                else:
                    market_price = item.averageCost

            positions.append({
                'ticker':         item.contract.symbol,
                'conId':          item.contract.conId,
                'quantity':       item.position,
                'avg_cost':       round(item.averageCost, 2),
                'current_price':  round(market_price, 2),
                'market_value':   round(item.marketValue, 2),
                'unrealized_pnl': round(item.unrealizedPNL, 2),
                'realized_pnl':   round(item.realizedPNL, 2),
                'price_stale':    price_stale,
            })
            contracts.append(item.contract)

        # ── Account values (synchronous TWS cache — fast) ─────────────────────
        av = ib.accountValues()

        def get_av(tag, currency='USD'):
            for v in av:
                if v.tag == tag and v.currency == currency:
                    try:
                        return float(v.value)
                    except Exception:
                        return 0.0
            return 0.0

        account = {
            'cash_usd':             get_av('CashBalance',        'USD'),
            'cash_eur':             get_av('TotalCashValue',      'EUR'),
            'buying_power':         get_av('BuyingPower',         'EUR'),
            'net_liquidation':      get_av('NetLiquidation',      'EUR'),
            'available_funds':      get_av('AvailableFunds',      'EUR'),
            'excess_liquidity':     get_av('ExcessLiquidity',     'EUR'),
            'gross_position_value': get_av('GrossPositionValue',  'EUR'),
            'maint_margin':         get_av('MaintMarginReq',      'EUR'),
            'init_margin':          get_av('InitMarginReq',       'EUR'),
            'cushion':              get_av('Cushion',             ''),
            'leverage':             get_av('Leverage',            ''),
            'equity_with_loan':     get_av('EquityWithLoanValue', 'EUR'),
            'sma':                  get_av('SMA',                 'EUR'),
            'day_trades_remaining': get_av('DayTradesRemaining',  ''),
            'eurusd_rate':          1.08,
        }

        if not contracts:
            account['daily_pnl'] = 0.0
            return {'positions': positions, 'market_data': {}, 'div_data': {}, 'account': account}

        # Request delayed market data type once (sync, fast) before parallel fetches
        try:
            ib.reqMarketDataType(3)
        except Exception as e:
            log.debug("reqMarketDataType(3) failed: %s", e)

        # ── Parallel async chains ─────────────────────────────────────────────
        # These four chains are independent — run them concurrently to cut
        # total fetch time from sum-of-latencies to max-of-latencies.

        # Chain A: qualify + snapshot tickers for all positions
        async def _fetch_tickers():
            try:
                await ib.qualifyContractsAsync(*contracts)
                return await ib.reqTickersAsync(*contracts)
            except Exception as e:
                log.warning("Market data fetch failed: %s", e)
                return []

        # Chain B: dividend tick subscriptions (type 59)
        # sleep reduced 3 s → 1.5 s; runs in parallel so adds zero extra latency
        async def _fetch_dividends():
            try:
                div_tickers = [
                    ib.reqMktData(c, genericTickList='59', snapshot=False)
                    for c in contracts
                ]
                await asyncio.sleep(1.5)
                result = {}
                for t in div_tickers:
                    sym = t.contract.symbol
                    d   = t.dividends
                    if d and (d.next12Months or d.past12Months):
                        result[sym] = {
                            'past_12m':    safe(d.past12Months),
                            'next_12m':    safe(d.next12Months),
                            'next_date':   d.nextDate.isoformat() if d.nextDate else None,
                            'next_amount': safe(d.nextAmount),
                        }
                    ib.cancelMktData(t.contract)
                log.debug("Dividend data fetched for: %s", list(result.keys()))
                return result
            except Exception as e:
                log.warning("Dividend data fetch failed: %s", e)
                return {}

        # Chain C: EUR/USD live rate
        async def _fetch_fx():
            try:
                eurusd = Forex('EURUSD')
                await ib.qualifyContractsAsync(eurusd)
                [fx_ticker] = await ib.reqTickersAsync(eurusd)
                rate = fx_ticker.marketPrice()
                if rate and rate > 0 and rate == rate:
                    log.debug("EUR/USD rate: %.6f", rate)
                    return round(rate, 6)
            except Exception as e:
                log.warning("EUR/USD rate fetch failed, using fallback 1.08: %s", e)
            return None

        # Chain D: daily P&L
        async def _fetch_pnl():
            try:
                acct_name = av[0].account if av else ''
                pnl_obj   = ib.reqPnL(acct_name)
                await asyncio.sleep(0.5)
                result = round(pnl_obj.dailyPnL, 2) if pnl_obj.dailyPnL == pnl_obj.dailyPnL else 0.0
                ib.cancelPnL(acct_name)
                log.debug("Daily P&L: %.2f", result)
                return result
            except Exception as e:
                log.warning("Daily P&L fetch failed: %s", e)
                return 0.0

        raw_tickers, div_data, fx_rate, daily_pnl = await asyncio.gather(
            _fetch_tickers(),
            _fetch_dividends(),
            _fetch_fx(),
            _fetch_pnl(),
        )

        # ── Assemble market_data from tickers ─────────────────────────────────
        market_data: dict = {}
        if isinstance(raw_tickers, list):
            for t in raw_tickers:
                sym = t.contract.symbol
                market_data[sym] = {
                    'bid':        safe(t.bid),
                    'ask':        safe(t.ask),
                    'open':       safe(t.open),
                    'high':       safe(t.high),
                    'low':        safe(t.low),
                    'prev_close': safe(t.close),
                    'volume':     safe(t.volume),
                    'low_52w':    safe(t.low52week),
                    'high_52w':   safe(t.high52week),
                    'vwap':       safe(t.vwap),
                }

        if fx_rate is not None:
            account['eurusd_rate'] = fx_rate
        account['daily_pnl'] = daily_pnl if isinstance(daily_pnl, (int, float)) else 0.0

        return {
            'positions':   positions,
            'market_data': market_data,
            'div_data':    div_data if isinstance(div_data, dict) else {},
            'account':     account,
        }



# ── Module-level singleton ────────────────────────────────────────────────────

_conn = _IBKRConnection()


def start_connection(host='127.0.0.1', port=7497, client_id=1,
                     readonly=True, reconnect_delay=_BASE_BACKOFF,
                     heartbeat_interval=30):
    """Call once at startup to launch the background IB thread."""
    _conn.start(
        host=host, port=port, client_id=client_id,
        readonly=readonly, reconnect_delay=reconnect_delay,
        heartbeat_interval=heartbeat_interval,
    )


def fetch_all_data() -> dict | None:
    return _conn.fetch_all_data()


def connection_status() -> str:
    return _conn.status
