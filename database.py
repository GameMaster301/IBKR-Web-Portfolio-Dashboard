import sqlite3
from datetime import datetime

DB_PATH = 'portfolio.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            total_value REAL NOT NULL,
            total_pnl REAL NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS rebalance_targets (
            ticker     TEXT PRIMARY KEY,
            target_pct REAL NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS dividend_events (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker       TEXT    NOT NULL,
            ex_date      TEXT    NOT NULL,
            amount_per_share REAL NOT NULL,
            quantity     REAL    NOT NULL,
            total_amount REAL    NOT NULL,
            recorded_at  TEXT    NOT NULL,
            UNIQUE(ticker, ex_date)
        )
    ''')
    conn.commit()
    conn.close()

def save_snapshot(total_value, total_pnl):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    today = datetime.now().strftime('%Y-%m-%d')

    cursor.execute('SELECT id FROM snapshots WHERE date = ?', (today,))
    existing = cursor.fetchone()

    if existing:
        cursor.execute(
            'UPDATE snapshots SET total_value = ?, total_pnl = ? WHERE date = ?',
            (total_value, total_pnl, today)
        )
    else:
        cursor.execute(
            'INSERT INTO snapshots (date, total_value, total_pnl) VALUES (?, ?, ?)',
            (today, total_value, total_pnl)
        )

    conn.commit()
    conn.close()

def get_last_30_days():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT date, total_value, total_pnl
        FROM snapshots
        ORDER BY date DESC
        LIMIT 30
    ''')
    rows = cursor.fetchall()
    conn.close()
    rows.reverse()
    return rows

def get_all_snapshots():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT date, total_value, total_pnl
        FROM snapshots
        ORDER BY date ASC
    ''')
    rows = cursor.fetchall()
    conn.close()
    return rows

def save_rebalance_targets(targets: dict):
    """targets: {ticker: target_pct}"""
    now  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    for ticker, pct in targets.items():
        cursor.execute('''
            INSERT INTO rebalance_targets (ticker, target_pct, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                target_pct = excluded.target_pct,
                updated_at = excluded.updated_at
        ''', (ticker, pct, now))
    conn.commit()
    conn.close()

def get_rebalance_targets() -> dict:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT ticker, target_pct FROM rebalance_targets')
    rows = cursor.fetchall()
    conn.close()
    return {ticker: pct for ticker, pct in rows}

def save_dividend_events(div_data, positions):
    """
    Upsert upcoming dividend events.
    div_data: {ticker: {next_date, next_amount, ...}}
    positions: [{ticker, quantity, ...}]
    """
    qty_map = {p['ticker']: p['quantity'] for p in positions}
    now     = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn    = sqlite3.connect(DB_PATH)
    cursor  = conn.cursor()
    for ticker, d in div_data.items():
        if not d.get('next_date') or not d.get('next_amount'):
            continue
        qty          = qty_map.get(ticker, 0)
        total_amount = round(d['next_amount'] * qty, 2)
        cursor.execute('''
            INSERT INTO dividend_events (ticker, ex_date, amount_per_share, quantity, total_amount, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, ex_date) DO UPDATE SET
                amount_per_share = excluded.amount_per_share,
                quantity         = excluded.quantity,
                total_amount     = excluded.total_amount,
                recorded_at      = excluded.recorded_at
        ''', (ticker, d['next_date'], d['next_amount'], qty, total_amount, now))
    conn.commit()
    conn.close()

def get_dividend_events():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT ticker, ex_date, amount_per_share, quantity, total_amount, recorded_at
        FROM dividend_events
        ORDER BY ex_date ASC
    ''')
    rows = cursor.fetchall()
    conn.close()
    return rows

def bulk_insert_snapshots(rows):
    """Insert (date, total_value, total_pnl) rows, skipping dates already present."""
    if not rows:
        return
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.executemany('''
        INSERT OR IGNORE INTO snapshots (date, total_value, total_pnl)
        VALUES (?, ?, ?)
    ''', rows)
    conn.commit()
    conn.close()