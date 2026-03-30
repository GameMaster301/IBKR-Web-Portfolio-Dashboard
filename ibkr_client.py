import asyncio
import threading
from ib_insync import IB, Forex, ExecutionFilter

_lock = threading.Lock()

def fetch_all_data():
    if not _lock.acquire(blocking=False):
        print("Fetch already in progress, skipping.")
        return None

    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    ib = IB()
    try:
        ib.connect('127.0.0.1', 7497, clientId=1, timeout=10, readonly=True)
        print("Connected to IBKR successfully")

        # ── Portfolio positions ──────────────────────────────────────────────
        items = ib.portfolio()
        positions = []
        contracts = []
        for item in items:
            positions.append({
                'ticker':          item.contract.symbol,
                'conId':           item.contract.conId,
                'quantity':        item.position,
                'avg_cost':        round(item.averageCost, 2),
                'current_price':   round(item.marketPrice, 2),
                'market_value':    round(item.marketValue, 2),
                'unrealized_pnl':  round(item.unrealizedPNL, 2),
                'realized_pnl':    round(item.realizedPNL, 2),
            })
            contracts.append(item.contract)

        # ── Market data per position ─────────────────────────────────────────
        market_data = {}
        if contracts:
            try:
                ib.qualifyContracts(*contracts)
                tickers = ib.reqTickers(*contracts)
                for t in tickers:
                    sym = t.contract.symbol
                    def safe(v):
                        try:
                            return round(float(v), 4) if v == v and v is not None else None
                        except Exception:
                            return None
                    market_data[sym] = {
                        'bid':         safe(t.bid),
                        'ask':         safe(t.ask),
                        'open':        safe(t.open),
                        'high':        safe(t.high),
                        'low':         safe(t.low),
                        'prev_close':  safe(t.close),
                        'volume':      safe(t.volume),
                        'low_52w':     safe(t.low52week),
                        'high_52w':    safe(t.high52week),
                        'vwap':        safe(t.vwap),
                    }
            except Exception as e:
                print(f"Market data fetch failed: {e}")

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

        # This account reports in EUR — main currency is EUR
        account = {
            'cash_usd':            get_av('CashBalance', 'USD'),
            'cash_eur':            get_av('TotalCashValue', 'EUR'),
            'buying_power':        get_av('BuyingPower', 'EUR'),
            'net_liquidation':     get_av('NetLiquidation', 'EUR'),
            'available_funds':     get_av('AvailableFunds', 'EUR'),
            'excess_liquidity':    get_av('ExcessLiquidity', 'EUR'),
            'gross_position_value':get_av('GrossPositionValue', 'EUR'),
            'maint_margin':        get_av('MaintMarginReq', 'EUR'),
            'init_margin':         get_av('InitMarginReq', 'EUR'),
            'cushion':             get_av('Cushion', ''),
            'leverage':            get_av('Leverage', ''),
            'equity_with_loan':    get_av('EquityWithLoanValue', 'EUR'),
            'sma':                 get_av('SMA', 'EUR'),
            'day_trades_remaining':get_av('DayTradesRemaining', ''),
            'eurusd_rate':         1.08,  # filled below
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
            if pnl_obj.dailyPnL == pnl_obj.dailyPnL:  # not nan
                daily_pnl = round(pnl_obj.dailyPnL, 2)
            ib.cancelPnL(acct_name)
            print(f"Daily P&L: {daily_pnl}")
        except Exception as e:
            print(f"Daily P&L fetch failed: {e}")

        account['daily_pnl'] = daily_pnl

        # ── Recent orders + commissions ──────────────────────────────────────
        fills = ib.reqExecutions(ExecutionFilter())
        orders = []
        total_commission = 0.0
        wins = 0
        losses = 0

        for f in sorted(fills, key=lambda x: x.time, reverse=True):
            commission = 0.0
            rpnl = 0.0
            try:
                commission = round(f.commissionReport.commission, 4)
                rpnl = f.commissionReport.realizedPNL
                total_commission += commission
                if rpnl == rpnl and rpnl != 0:  # not nan
                    if rpnl > 0:
                        wins += 1
                    else:
                        losses += 1
            except Exception:
                pass

            if len(orders) < 20:
                orders.append({
                    'date':       f.time.strftime('%Y-%m-%d %H:%M') if hasattr(f.time, 'strftime') else str(f.time),
                    'ticker':     f.contract.symbol,
                    'action':     'Buy' if f.execution.side == 'BOT' else 'Sell',
                    'quantity':   int(f.execution.shares),
                    'price':      round(f.execution.price, 2),
                    'value':      round(f.execution.shares * f.execution.price, 2),
                    'commission': commission,
                    'realized_pnl': round(rpnl, 2) if rpnl == rpnl else None,
                })

        trade_stats = {
            'total_trades':      len(fills),
            'total_commission':  round(total_commission, 2),
            'wins':              wins,
            'losses':            losses,
            'win_rate':          round(wins / (wins + losses) * 100, 1) if (wins + losses) > 0 else None,
        }

        ib.disconnect()
        print("Disconnected from IBKR")
        _lock.release()

        return {
            'positions':    positions,
            'market_data':  market_data,
            'account':      account,
            'orders':       orders,
            'trade_stats':  trade_stats,
        }

    except Exception as e:
        print(f"Connection failed: {e}")
        try:
            ib.disconnect()
        except Exception:
            pass
        _lock.release()
        return None
