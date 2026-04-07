import asyncio
import threading
from ib_insync import IB, Forex

_lock = threading.Lock()


def _do_fetch():
    """Runs inside a dedicated thread that owns a fresh event loop."""
    ib = IB()
    try:
        ib.connect('127.0.0.1', 7497, clientId=1, timeout=10, readonly=True)
        print("Connected to IBKR successfully")

        # ── Portfolio positions ──────────────────────────────────────────────
        items = ib.portfolio()
        positions = []
        contracts = []
        for item in items:
            market_price = item.marketPrice
            price_stale  = market_price != market_price  # True when nan
            if price_stale:
                # Derive from marketValue if available, else fall back to avg cost
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

        def safe(v):
            try:
                return round(float(v), 4) if v == v and v is not None else None
            except Exception:
                return None

        # ── Market data per position ─────────────────────────────────────────
        market_data = {}
        div_data    = {}
        if contracts:
            try:
                ib.qualifyContracts(*contracts)
                tickers = ib.reqTickers(*contracts)
                for t in tickers:
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
            except Exception as e:
                print(f"Market data fetch failed: {e}")

            # ── Dividend data (tick type 59) ─────────────────────────────────
            try:
                div_subscriptions = []
                for contract in contracts:
                    t = ib.reqMktData(contract, genericTickList='59', snapshot=False)
                    div_subscriptions.append(t)
                ib.sleep(3)
                for t in div_subscriptions:
                    sym = t.contract.symbol
                    d   = t.dividends
                    if d and (d.next12Months or d.past12Months):
                        div_data[sym] = {
                            'past_12m':    safe(d.past12Months),
                            'next_12m':    safe(d.next12Months),
                            'next_date':   d.nextDate.isoformat() if d.nextDate else None,
                            'next_amount': safe(d.nextAmount),
                        }
                    ib.cancelMktData(t.contract)
                print(f"Dividend data fetched for: {list(div_data.keys())}")
            except Exception as e:
                print(f"Dividend data fetch failed: {e}")

        # ── Account values ───────────────────────────────────────────────────
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
            'cash_usd':             get_av('CashBalance', 'USD'),
            'cash_eur':             get_av('TotalCashValue', 'EUR'),
            'buying_power':         get_av('BuyingPower', 'EUR'),
            'net_liquidation':      get_av('NetLiquidation', 'EUR'),
            'available_funds':      get_av('AvailableFunds', 'EUR'),
            'excess_liquidity':     get_av('ExcessLiquidity', 'EUR'),
            'gross_position_value': get_av('GrossPositionValue', 'EUR'),
            'maint_margin':         get_av('MaintMarginReq', 'EUR'),
            'init_margin':          get_av('InitMarginReq', 'EUR'),
            'cushion':              get_av('Cushion', ''),
            'leverage':             get_av('Leverage', ''),
            'equity_with_loan':     get_av('EquityWithLoanValue', 'EUR'),
            'sma':                  get_av('SMA', 'EUR'),
            'day_trades_remaining': get_av('DayTradesRemaining', ''),
            'eurusd_rate':          1.08,
        }

        # ── EUR/USD live rate ────────────────────────────────────────────────
        try:
            eurusd = Forex('EURUSD')
            ib.qualifyContracts(eurusd)
            [fx_ticker] = ib.reqTickers(eurusd)
            rate = fx_ticker.marketPrice()
            if rate and rate > 0 and rate == rate:
                account['eurusd_rate'] = round(rate, 6)
                print(f"EUR/USD rate: {account['eurusd_rate']}")
        except Exception as e:
            print(f"EUR/USD rate fetch failed, using fallback 1.08: {e}")

        # ── Daily P&L ────────────────────────────────────────────────────────
        daily_pnl = 0.0
        try:
            acct_name = av[0].account if av else ''
            pnl_obj = ib.reqPnL(acct_name)
            ib.sleep(0.5)
            if pnl_obj.dailyPnL == pnl_obj.dailyPnL:
                daily_pnl = round(pnl_obj.dailyPnL, 2)
            ib.cancelPnL(acct_name)
            print(f"Daily P&L: {daily_pnl}")
        except Exception as e:
            print(f"Daily P&L fetch failed: {e}")

        account['daily_pnl'] = daily_pnl

        ib.disconnect()
        print("Disconnected from IBKR")
        return {
            'positions':   positions,
            'market_data': market_data,
            'div_data':    div_data,
            'account':     account,
        }

    except Exception as e:
        print(f"Connection failed: {e}")
        try:
            ib.disconnect()
        except Exception:
            pass
        return None


def fetch_all_data():
    if not _lock.acquire(blocking=False):
        print("Fetch already in progress, skipping.")
        return None

    try:
        result = [None]

        def run_in_thread():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result[0] = _do_fetch()
            finally:
                loop.close()

        t = threading.Thread(target=run_in_thread)
        t.start()
        t.join(timeout=30)
        return result[0]

    finally:
        _lock.release()
