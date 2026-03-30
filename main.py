import asyncio
asyncio.set_event_loop(asyncio.new_event_loop())

import threading
import webbrowser
from database import init_db
from dashboard import app

def open_browser():
    import time
    time.sleep(1.5)
    webbrowser.open('http://localhost:8050')

if __name__ == '__main__':
    init_db()
    threading.Thread(target=open_browser, daemon=True).start()
    app.run(debug=False)
