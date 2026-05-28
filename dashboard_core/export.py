"""PDF export of the current portfolio snapshot.

Single callback, isolated. Lazily imports reportlab inside the callback so
launching the app doesn't pay the import cost when nobody clicks Export.
"""

from __future__ import annotations

import io
from datetime import datetime

import pandas as pd
from dash import Input, Output, State, dcc, no_update

from dashboard_core.helpers import EURUSD_FALLBACK, ccy_symbol, to_base
from ibkr_client import is_demo_mode
from styles import (
    COLOR_BAD_DEEP,
    COLOR_GOOD_DEEP,
    COLOR_WARN_BG,
    COLOR_WARN_BORDER,
    COLOR_WARN_DEEP,
)

# Reportlab needs actual hex values — CSS variables don't work with colors.HexColor().
_PDF_SURFACE      = '#f5f5f5'
_PDF_SURFACE_SOFT = '#fafafa'
_PDF_BORDER       = '#e0e0e0'


def register(app):
    @app.callback(
        Output('download-pdf', 'data'),
        Input('export-pdf-btn', 'n_clicks'),
        State('portfolio-data', 'data'),
        prevent_initial_call=True,
    )
    def export_pdf(_, data):
        if not data or 'positions' not in data:
            return no_update
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            HRFlowable,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )

        df       = pd.DataFrame(data['positions'])
        s        = data.get('summary', {})
        a        = data.get('account', {})
        rate     = a.get('eurusd_rate', EURUSD_FALLBACK)
        base_ccy = a.get('base_currency', 'EUR')
        sym      = ccy_symbol(base_ccy)
        sec_ccy  = 'USD' if base_ccy != 'USD' else 'EUR'
        sec_sym  = ccy_symbol(sec_ccy)

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4,
                                leftMargin=20*mm, rightMargin=20*mm,
                                topMargin=20*mm, bottomMargin=20*mm)
        styles = getSampleStyleSheet()
        story = []

        demo = is_demo_mode()

        # Every section spans the same usable width (A4 210mm − 2×20mm margins)
        # so tables line up edge-to-edge and the page reads symmetrically.
        CONTENT_W   = 170 * mm
        SECTION_GAP = 8 * mm

        def section_header(text: str):
            """Uppercase label + thin full-width rule — one consistent style."""
            story.append(Paragraph(text.upper(), ParagraphStyle(
                'section', parent=styles['Normal'], fontSize=10,
                fontName='Helvetica-Bold', textColor=colors.HexColor('#555555'),
                leading=12, spaceAfter=3)))
            story.append(HRFlowable(width=CONTENT_W, thickness=0.6,
                                    color=colors.HexColor(_PDF_BORDER),
                                    spaceAfter=6, spaceBefore=0))

        # Title
        title_text = "Portfolio Snapshot — DEMO MODE" if demo else "Portfolio Snapshot"
        story.append(Paragraph(title_text, ParagraphStyle(
            'title', parent=styles['Heading1'], fontSize=18, spaceAfter=4)))
        story.append(Paragraph(
            f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            ParagraphStyle('sub', parent=styles['Normal'], fontSize=9,
                           textColor=colors.HexColor('#888888'),
                           spaceAfter=12 if demo else SECTION_GAP)))
        if demo:
            story.append(Paragraph(
                "Sample portfolio — not real trading data.",
                ParagraphStyle('demo', parent=styles['Normal'], fontSize=9,
                               textColor=colors.HexColor(COLOR_WARN_DEEP),
                               backColor=colors.HexColor(COLOR_WARN_BG),
                               borderColor=colors.HexColor(COLOR_WARN_BORDER),
                               borderWidth=0.5, borderPadding=6,
                               spaceAfter=SECTION_GAP)))

        # ── Summary ───────────────────────────────────────────────────────────
        section_header("Summary")
        total_val   = s.get('total_value', 0)
        unreal_pnl  = s.get('total_unrealized_pnl', 0)
        real_pnl    = s.get('total_realized_pnl', 0) or 0
        daily_pnl   = a.get('daily_pnl') or s.get('total_daily_pnl', 0) or 0
        def _base(v):
            return f"{sym}{to_base(v, rate, base_ccy):,.2f}"
        def _base_signed(v):
            return f"{sym}{to_base(v, rate, base_ccy):+,.2f}"
        def _sec(v):
            sec_val = v / rate if base_ccy == 'USD' else v * rate
            return f"{sec_sym}{sec_val:,.2f}"

        summary_data = [
            ['Metric', base_ccy, sec_ccy],
            ['Total Value',    _base(total_val),         _sec(total_val)],
            ['Unrealized P&L', _base_signed(unreal_pnl), _sec(unreal_pnl)],
            ['Realized P&L',   _base_signed(real_pnl),   _sec(real_pnl)],
            ["Today's P&L",    _base_signed(daily_pnl),  _sec(daily_pnl)],
            ['Cash',           f"{sym}{a.get('cash_base', 0):,.2f}",
                               f"${a.get('cash_usd', 0) or 0:,.2f}"],
        ]
        t = Table(summary_data, colWidths=[90*mm, 40*mm, 40*mm])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor(_PDF_SURFACE)),
            ('FONTNAME',   (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE',   (0, 0), (-1, -1), 9),
            ('ALIGN',      (1, 0), (-1, -1), 'RIGHT'),
            ('GRID',       (0, 0), (-1, -1), 0.25, colors.HexColor(_PDF_BORDER)),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor(_PDF_SURFACE_SOFT)]),
            ('TOPPADDING',  (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        story.append(t)

        # Income summary — one-liner showing projected dividends, yield, and
        # how many holdings actually pay. Hidden when no holdings pay anything.
        div_data   = data.get('div_data') or {}
        qty_by_tk  = dict(zip(df['ticker'], df['quantity'], strict=False))
        annual_usd = sum(
            qty_by_tk.get(tk, 0) * float(d.get('next_12m') or 0)
            for tk, d in div_data.items()
        )
        if annual_usd > 0 and total_val > 0:
            annual_base = to_base(annual_usd, rate, base_ccy)
            yield_pct   = (annual_base / total_val) * 100
            payers      = sum(1 for d in div_data.values() if (d.get('next_12m') or 0) > 0)
            total_hold  = len(df)
            story.append(Spacer(1, 3*mm))
            story.append(Paragraph(
                f"Projected 12-month dividends: <b>{sym}{annual_base:,.0f}</b> &nbsp;·&nbsp; "
                f"Portfolio yield: <b>{yield_pct:.2f}%</b> &nbsp;·&nbsp; "
                f"{payers} of {total_hold} holdings pay",
                ParagraphStyle('income', parent=styles['Normal'], fontSize=9,
                               textColor=colors.HexColor('#444444'),
                               backColor=colors.HexColor(_PDF_SURFACE_SOFT),
                               borderColor=colors.HexColor(_PDF_BORDER),
                               borderWidth=0.5, borderPadding=6),
            ))
        story.append(Spacer(1, SECTION_GAP))

        # ── Performance ───────────────────────────────────────────────────────
        section_header("Performance")
        best   = s.get('best_performer', '—')
        worst  = s.get('worst_performer', '—')
        best_row  = df[df['ticker'] == best].iloc[0]  if best  != '—' and best  in df['ticker'].values else None
        worst_row = df[df['ticker'] == worst].iloc[0] if worst != '—' and worst in df['ticker'].values else None
        best_str  = f"{best} ({best_row['pnl_pct']:+.2f}%)"   if best_row  is not None else best
        worst_str = f"{worst} ({worst_row['pnl_pct']:+.2f}%)" if worst_row is not None else worst
        perf_data = [
            ['Best Performer', 'Worst Performer'],
            [best_str, worst_str],
        ]
        pt = Table(perf_data, colWidths=[85*mm, 85*mm])
        pt.setStyle(TableStyle([
            ('BACKGROUND',    (0, 0), (-1, 0), colors.HexColor(_PDF_SURFACE)),
            ('FONTNAME',      (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE',      (0, 0), (-1, -1), 9),
            ('ALIGN',         (0, 0), (-1, -1), 'CENTER'),
            ('GRID',          (0, 0), (-1, -1), 0.25, colors.HexColor(_PDF_BORDER)),
            ('TEXTCOLOR',     (0, 1), (0, 1), colors.HexColor(COLOR_GOOD_DEEP)),  # best = green
            ('TEXTCOLOR',     (1, 1), (1, 1), colors.HexColor(COLOR_BAD_DEEP)),  # worst = red
            ('FONTNAME',      (0, 1), (-1, 1), 'Helvetica-Bold'),
            ('TOPPADDING',    (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        story.append(pt)
        story.append(Spacer(1, SECTION_GAP))

        # ── Holdings ──────────────────────────────────────────────────────────
        section_header("Holdings")
        hold_data = [['Ticker', 'Qty', 'Avg Cost', 'Price', 'Day %', 'Mkt Value', 'P&L %', 'Weight']]
        for _, row in df.iterrows():
            day_pct = row.get('daily_change_pct')
            day_str = f"{day_pct:+.2f}%" if pd.notna(day_pct) and day_pct is not None else '—'
            hold_data.append([
                row['ticker'],
                str(int(row['quantity'])),
                f"${row['avg_cost']:,.2f}",
                f"${row['current_price']:,.2f}",
                day_str,
                f"${row['market_value']:,.2f}",
                f"{row['pnl_pct']:+.2f}%",
                f"{row['allocation_pct']:.1f}%",
            ])
        # Widths sum to 170mm — same span as the Summary/Performance tables.
        ht = Table(hold_data, colWidths=[22*mm, 14*mm, 24*mm, 24*mm, 18*mm, 30*mm, 18*mm, 20*mm])
        # colour positive/negative day % and P&L % cells per row
        ht_style = [
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor(_PDF_SURFACE)),
            ('FONTNAME',   (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE',   (0, 0), (-1, -1), 8),
            ('ALIGN',      (1, 0), (-1, -1), 'RIGHT'),
            ('ALIGN',      (0, 0), (0, -1), 'LEFT'),
            ('GRID',       (0, 0), (-1, -1), 0.25, colors.HexColor(_PDF_BORDER)),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor(_PDF_SURFACE_SOFT)]),
            ('TOPPADDING',  (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]
        for i, row in enumerate(df.itertuples(), start=1):
            day_pct = getattr(row, 'daily_change_pct', None)
            if day_pct is not None and pd.notna(day_pct):
                col = colors.HexColor(COLOR_GOOD_DEEP) if day_pct >= 0 else colors.HexColor(COLOR_BAD_DEEP)
                ht_style.append(('TEXTCOLOR', (4, i), (4, i), col))
            pnl = getattr(row, 'pnl_pct', 0) or 0
            col = colors.HexColor(COLOR_GOOD_DEEP) if pnl >= 0 else colors.HexColor(COLOR_BAD_DEEP)
            ht_style.append(('TEXTCOLOR', (6, i), (6, i), col))
        ht.setStyle(TableStyle(ht_style))
        story.append(ht)

        doc.build(story)
        buf.seek(0)
        prefix = "portfolio_demo" if demo else "portfolio"
        filename = f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        return dcc.send_bytes(buf.read(), filename)
