"""IBKR portfolio dashboard — entry point. All UI lives in dashboard_core/."""

from __future__ import annotations

import dash

from config import cfg
from dashboard_core import coach_ui as _coach_ui_mod
from dashboard_core import data_callbacks as _data_callbacks_mod
from dashboard_core import detail as _detail_mod
from dashboard_core import export as _export_mod
from dashboard_core import intel as _intel_mod
from dashboard_core import layout as _layout_mod
from dashboard_core import summary as _summary_mod
from dashboard_core import valuation as _valuation_mod
import health
from styles import LINK_PILL

app = dash.Dash(__name__, suppress_callback_exceptions=True)

# Re-exported so any legacy reference to `dashboard._LINK_STYLE` keeps working.
_LINK_STYLE = LINK_PILL

_REFRESH_MS = cfg['dashboard']['refresh_interval_seconds'] * 1000
app.layout = _layout_mod.build_layout(_REFRESH_MS)

_data_callbacks_mod.register(app)
_summary_mod.register(app)
_export_mod.register(app)
_detail_mod.register(app)
_intel_mod.register(app)
_valuation_mod.register(app)
_coach_ui_mod.register(app)

health.register(app.server)
