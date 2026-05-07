"""
Persistent IBKR connection — hardened for production use.

Design
------
- Single IB() instance in a dedicated daemon thread with its own event loop.
- Exponential back-off on reconnect: 5 s → 10 s → 20 s → … capped at 120 s.
- Passive heartbeat: every `heartbeat_interval` seconds a lightweight
  reqCurrentTime() is sent so the OS TCP layer cannot silently drop the
  connection without us noticing.
- All external-facing calls degrade gracefully: if IB Gateway is unreachable the
  dashboard shows a "disconnected" banner and keeps displaying cached data.
- fetch_all_data() is non-blocking: a threading.Lock prevents overlapping
  fetches without ever blocking the Dash refresh thread.

IB Gateway ports (recommended over TWS for API-only use):
  4002 — paper trading
  4001 — live trading

TWS ports (if you prefer TWS):
  7497 — paper trading
  7496 — live trading
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
from datetime import datetime, timedelta

from ib_async import IB, ExecutionFilter, Forex

from config import cfg

log = logging.getLogger(__name__)

_MAX_BACKOFF   = 120   # seconds
_BASE_BACKOFF  =   5   # seconds

# Common IBKR API ports. The configured port is tried first; if it fails we
# fall through the rest so the dashboard works with either IB Gateway or TWS,
# paper or live, without the user having to edit config.
_FALLBACK_PORTS = [7497, 4002, 7496, 4001]


class _IBKRConnection:
    def __init__(self):
        self._ib:        IB | None               = None
        self._loop:      asyncio.AbstractEventLoop | None = None
        self._connected: bool                    = False
        self._fetch_lock   = threading.Lock()
        self._conn_attempt = 0   # counts consecutive failures for back-off
        self._retry_event: asyncio.Event | None = None   # wakes the reconnect sleep

        # Config (set by start())
        self._host               = '127.0.0.1'
        self._port               = 4002
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

    def start(self, host='127.0.0.1', port=4002, client_id=1,
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
        future: concurrent.futures.Future | None = None
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._do_fetch(), self._loop)
            return future.result(timeout=60)
        except concurrent.futures.TimeoutError:
            # TWS hung for >60 s. Cancel the coroutine on the IB loop so it
            # doesn't leak and keep the lock blocked on subsequent ticks.
            log.warning("fetch_all_data timed out after 60 s — cancelling coroutine")
            if future is not None:
                future.cancel()
            return None
        except Exception as e:
            log.error("fetch_all_data error: %s", e, exc_info=False)
            if future is not None and not future.done():
                future.cancel()
            return None
        finally:
            self._fetch_lock.release()

    # ── Background thread ─────────────────────────────────────────────────────

    def _thread_main(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._retry_event = asyncio.Event()
        self._loop.run_until_complete(self._connection_loop())

    def request_retry(self):
        """Wake the reconnect loop immediately and reset back-off.

        Safe to call from any thread (e.g. a Dash callback). No-op if the
        background thread hasn't started yet or is already connected.
        """
        loop = self._loop
        evt  = self._retry_event
        if loop is None or evt is None:
            return
        self._conn_attempt = 0
        loop.call_soon_threadsafe(evt.set)

    async def _sleep_or_retry(self, delay: float):
        """Sleep up to `delay` seconds, but wake early if request_retry() fires."""
        evt = self._retry_event
        if evt is None:
            await asyncio.sleep(delay)
            return
        try:
            await asyncio.wait_for(evt.wait(), timeout=delay)
        except TimeoutError:
            pass
        evt.clear()

    def _candidate_ports(self) -> list[int]:
        """Configured port first, then the other common IBKR ports."""
        ports = [self._port]
        for p in _FALLBACK_PORTS:
            if p not in ports:
                ports.append(p)
        return ports

    async def _connection_loop(self):
        """Connect → heartbeat → reconnect on drop, forever, with back-off.

        On each round we try the configured port first, then fall through the
        other common IBKR ports (IB Gateway 4001/4002, TWS 7496/7497) so the
        dashboard works regardless of which client the user is running.
        """
        while True:
            connected_ib: IB | None     = None
            disc_event:   asyncio.Event = asyncio.Event()
            on_disc                     = None

            for port in self._candidate_ports():
                ib = IB()
                ev = asyncio.Event()

                def _on_disconnect(_e=ev):
                    self._loop.call_soon_threadsafe(_e.set)

                ib.disconnectedEvent += _on_disconnect

                try:
                    log.info("Connecting to IBKR at %s:%d (attempt %d) …",
                             self._host, port, self._conn_attempt + 1)
                    await ib.connectAsync(
                        self._host, port,
                        clientId=self._client_id,
                        timeout=15,
                        readonly=self._readonly,
                    )
                    connected_ib = ib
                    disc_event   = ev
                    on_disc      = _on_disconnect
                    log.info("Connected to IBKR on port %d ✓", port)
                    break
                except asyncio.CancelledError:
                    ib.disconnectedEvent -= _on_disconnect
                    try:
                        ib.disconnect()
                    except Exception:
                        pass
                    raise
                except Exception as e:
                    log.info("Port %d unavailable: %s", port, e)
                    ib.disconnectedEvent -= _on_disconnect
                    try:
                        ib.disconnect()
                    except Exception:
                        pass

            if connected_ib is None:
                self._conn_attempt += 1
                delay = min(
                    _BASE_BACKOFF * (2 ** min(self._conn_attempt, 8)),
                    _MAX_BACKOFF,
                )
                log.warning("No IBKR port responded; retrying in %ds …", delay)
                await self._sleep_or_retry(delay)
                continue

            self._ib           = connected_ib
            self._connected    = True
            self._conn_attempt = 0
            try:
                await self._heartbeat_loop(connected_ib, disc_event)
                log.warning("Disconnected from IBKR")
            except asyncio.CancelledError:
                raise
            finally:
                self._connected = False
                self._ib        = None
                if on_disc is not None:
                    connected_ib.disconnectedEvent -= on_disc
                try:
                    connected_ib.disconnect()
                except Exception:
                    pass

            delay = min(
                _BASE_BACKOFF * (2 ** min(self._conn_attempt, 8)),
                _MAX_BACKOFF,
            )
            log.info("Reconnecting in %ds …", delay)
            await self._sleep_or_retry(delay)

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
            except TimeoutError:
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

        # Detect the account's base currency (e.g. 'EUR', 'USD', 'GBP').
        # BaseCurrency is a string tag — its value is the ISO code, not a number.
        base_ccy = 'EUR'
        for v in av:
            if v.tag == 'BaseCurrency' and v.value:
                base_ccy = v.value.strip().upper()
                break

        account = {
            'base_currency':        base_ccy,
            'cash_usd':             get_av('CashBalance',        'USD'),
            'cash_base':            get_av('TotalCashValue',      base_ccy),
            'buying_power':         get_av('BuyingPower',         base_ccy),
            'net_liquidation':      get_av('NetLiquidation',      base_ccy),
            'available_funds':      get_av('AvailableFunds',      base_ccy),
            'excess_liquidity':     get_av('ExcessLiquidity',     base_ccy),
            'gross_position_value': get_av('GrossPositionValue',  base_ccy),
            'maint_margin':         get_av('MaintMarginReq',      base_ccy),
            'init_margin':          get_av('InitMarginReq',       base_ccy),
            'cushion':              get_av('Cushion',             ''),
            'leverage':             get_av('Leverage',            ''),
            'equity_with_loan':     get_av('EquityWithLoanValue', base_ccy),
            'sma':                  get_av('SMA',                 base_ccy),
            'day_trades_remaining': get_av('DayTradesRemaining',  ''),
            'eurusd_rate':          cfg['display']['eurusd_fallback'],
        }

        if not contracts:
            account['daily_pnl'] = 0.0
            return {'positions': positions, 'market_data': {}, 'div_data': {},
                    'trades': [], 'account': account}

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

        # Chain B: dividend (59) + 52-week range (165) tick subscriptions.
        # Returns (div_dict, range_dict) so the caller can merge range into market_data.
        async def _fetch_dividends():
            try:
                div_tickers = [
                    ib.reqMktData(c, genericTickList='59,165', snapshot=False)
                    for c in contracts
                ]
                # Poll in 0.1 s steps up to 1.5 s; exit early once every ticker
                # has received its 52-week range tick (tick 165).  Stocks with no
                # 52w data from IBKR will always be NaN — we fall through the
                # full 1.5 s for those, same as before.
                for _ in range(15):
                    await asyncio.sleep(0.1)
                    if all(t.low52week == t.low52week for t in div_tickers):
                        break
                div_result   = {}
                range_result = {}
                for t in div_tickers:
                    sym = t.contract.symbol
                    d   = t.dividends
                    if d and (d.next12Months or d.past12Months):
                        div_result[sym] = {
                            'past_12m':    safe(d.past12Months),
                            'next_12m':    safe(d.next12Months),
                            'next_date':   d.nextDate.isoformat() if d.nextDate else None,
                            'next_amount': safe(d.nextAmount),
                        }
                    low  = safe(t.low52week)
                    high = safe(t.high52week)
                    if low and high:
                        range_result[sym] = {'low_52w': low, 'high_52w': high}
                    ib.cancelMktData(t.contract)
                log.debug("Dividend data fetched for: %s", list(div_result.keys()))
                log.debug("52w range fetched for: %s", list(range_result.keys()))
                return div_result, range_result
            except Exception as e:
                log.warning("Dividend data fetch failed: %s", e)
                return {}, {}

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
                log.warning("EUR/USD rate fetch failed, using fallback %.4f: %s",
                            cfg['display']['eurusd_fallback'], e)
            return None

        # Chain E: recent trade executions (IBKR server caps history at ~7 days)
        async def _fetch_trades():
            try:
                filt = ExecutionFilter()
                filt.time = (datetime.now() - timedelta(days=7)).strftime('%Y%m%d-%H:%M:%S')
                fills = await ib.reqExecutionsAsync(filt)
                result = []
                for f in fills:
                    try:
                        shares = float(f.execution.shares)
                        price  = float(f.execution.price)
                        result.append({
                            'ticker': f.contract.symbol,
                            'side':   'BUY' if f.execution.side == 'BOT' else 'SELL',
                            'shares': shares,
                            'price':  round(price, 4),
                            'time':   f.execution.time.isoformat() if f.execution.time else None,
                            'value':  round(shares * price, 2),
                        })
                    except Exception:
                        continue
                result.sort(key=lambda x: x['time'] or '')
                log.debug("Fetched %d trade fills", len(result))
                return result
            except Exception as e:
                log.warning("Trade history fetch failed: %s", e)
                return []

        # Chain D: daily P&L
        async def _fetch_pnl():
            try:
                acct_name = av[0].account if av else ''
                pnl_obj   = ib.reqPnL(acct_name)
                # Poll in 0.05 s steps up to 0.5 s; exit as soon as dailyPnL arrives.
                for _ in range(10):
                    await asyncio.sleep(0.05)
                    if pnl_obj.dailyPnL == pnl_obj.dailyPnL:  # not NaN
                        break
                result = round(pnl_obj.dailyPnL, 2) if pnl_obj.dailyPnL == pnl_obj.dailyPnL else 0.0
                ib.cancelPnL(acct_name)
                log.debug("Daily P&L: %.2f", result)
                return result
            except Exception as e:
                log.warning("Daily P&L fetch failed: %s", e)
                return 0.0

        raw_tickers, (div_data, range_data), fx_rate, daily_pnl, trades = await asyncio.gather(
            _fetch_tickers(),
            _fetch_dividends(),
            _fetch_fx(),
            _fetch_pnl(),
            _fetch_trades(),
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

        # Merge 52-week range from the generic-tick subscription (tick 165).
        # reqTickersAsync snapshots often don't carry low52week/high52week;
        # the dedicated reqMktData subscription above is the reliable source.
        for sym, rng in (range_data or {}).items():
            if sym in market_data:
                if not market_data[sym].get('low_52w'):
                    market_data[sym]['low_52w'] = rng['low_52w']
                if not market_data[sym].get('high_52w'):
                    market_data[sym]['high_52w'] = rng['high_52w']

        if fx_rate is not None:
            account['eurusd_rate'] = fx_rate
        account['daily_pnl'] = daily_pnl if isinstance(daily_pnl, (int, float)) else 0.0

        return {
            'positions':   positions,
            'market_data': market_data,
            'div_data':    div_data if isinstance(div_data, dict) else {},
            'trades':      trades if isinstance(trades, list) else [],
            'account':     account,
        }



# ── Module-level singleton ────────────────────────────────────────────────────

_conn = _IBKRConnection()

# Demo mode short-circuits every public entry point to return a deterministic
# mock payload instead of talking to TWS. Toggled at startup via DEMO_MODE env
# var or at runtime from the dashboard UI.
_demo_mode = False
_conn_params: dict | None = None   # saved at startup; used by ensure_connection_started()


def set_demo_mode(on: bool) -> None:
    global _demo_mode
    _demo_mode = bool(on)


def is_demo_mode() -> bool:
    return _demo_mode


def save_connection_params(host='127.0.0.1', port=4002, client_id=1,
                           readonly=True, reconnect_delay=_BASE_BACKOFF,
                           heartbeat_interval=30):
    """Store connection parameters without starting the thread.
    Called at startup in demo mode so that ensure_connection_started() can
    bring the thread up later when the user exits demo and clicks Retry.
    """
    global _conn_params
    _conn_params = dict(
        host=host, port=port, client_id=client_id,
        readonly=readonly, reconnect_delay=reconnect_delay,
        heartbeat_interval=heartbeat_interval,
    )


def start_connection(host='127.0.0.1', port=4002, client_id=1,
                     readonly=True, reconnect_delay=_BASE_BACKOFF,
                     heartbeat_interval=30):
    """Save params and immediately launch the background IB thread."""
    save_connection_params(
        host=host, port=port, client_id=client_id,
        readonly=readonly, reconnect_delay=reconnect_delay,
        heartbeat_interval=heartbeat_interval,
    )
    _conn.start(**_conn_params)


def ensure_connection_started():
    """Start the IB thread if it has never been started; wake retry if already running.

    Called when the user clicks Retry after exiting demo mode — the thread may
    not exist yet (demo path never started it), so we can't just request_retry().
    """
    if _conn._loop is not None:
        # Thread is already running — just poke the reconnect loop.
        _conn.request_retry()
    elif _conn_params is not None:
        log.info("Starting IBKR connection thread on first retry after demo exit")
        _conn.start(**_conn_params)
    else:
        log.warning("ensure_connection_started: no connection params saved")


def fetch_all_data() -> dict | None:
    if _demo_mode:
        from demo_data import build_demo_payload
        return build_demo_payload()
    return _conn.fetch_all_data()


def connection_status() -> str:
    if _demo_mode:
        return 'connected'
    return _conn.status


def connection_attempt() -> int:
    """Return the current consecutive reconnect-attempt count (0 = first try)."""
    if _demo_mode:
        return 0
    return _conn._conn_attempt


def request_retry():
    """Ask the background thread to attempt reconnecting immediately."""
    if _demo_mode:
        return
    _conn.request_retry()
