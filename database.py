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