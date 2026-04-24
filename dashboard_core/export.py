"""PDF export of the current portfolio snapshot.

Single callback, isolated. Lazily imports reportlab inside the callback so
launching the app doesn't pay the import cost when nobody clicks Export.
"""

from __future__ import annotations

import io
from datetime import datetime

import pandas as pd
from dash import Input, Output, State, dcc, no_update

from ibkr_client import is_demo_mode


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
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

        df = pd.DataFrame(data['positions'])
        s = data.get('summary', {})
        a = data.get('account', {})
        rate = a.get('eurusd_rate', 1.08)

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4,
                                leftMargin=20*mm, rightMargin=20*mm,
                                topMargin=20*mm, bottomMargin=20*mm)
        styles = getSampleStyleSheet()
        story = []

        demo = is_demo_mode()

        # Title
        title_text = "Portfolio Snapshot — DEMO MODE" if demo else "Portfolio Snapshot"
        story.append(Paragraph(title_text, ParagraphStyle(
            'title', parent=styles['Heading1'], fontSize=18, spaceAfter=4)))
        story.append(Paragraph(
            f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            ParagraphStyle('sub', parent=styles['Normal'], fontSize=9,
                           textColor=colors.HexColor('#888888'), spaceAfter=14)))
        if demo:
            story.append(Paragraph(
                "Sample portfolio — not real trading data.",
                ParagraphStyle('demo', parent=styles['Normal'], fontSize=9,
                               textColor=colors.HexColor('#92400e'),
                               backColor=colors.HexColor('#fffbeb'),
                               borderColor=colors.HexColor('#fcd34d'),
                               borderWidth=0.5, borderPadding=6,
                               spaceAfter=12)))

        # Summary table
        total_val   = s.get('total_value', 0)
        unreal_pnl  = s.get('total_unrealized_pnl', 0)
        real_pnl    = s.get('total_realized_pnl', 0) or 0
        daily_pnl   = a.get('daily_pnl') or s.get('total_daily_pnl', 0) or 0
        summary_data = [
            ['Metric', 'USD', 'EUR'],
            ['Total Value',    f"${total_val:,.2f}",   f"€{total_val/rate:,.2f}"],
            ['Unrealized P&L', f"${unreal_pnl:+,.2f}", f"€{unreal_pnl/rate:+,.2f}"],
            ['Realized P&L',   f"${real_pnl:+,.2f}",   f"€{real_pnl/rate:+,.2f}"],
            ["Today's P&L",    f"${daily_pnl:+,.2f}",  f"€{daily_pnl/rate:+,.2f}"],
            ['Cash',           f"${a.get('cash_usd', 0) or 0:,.2f}", f"€{a.get('cash_eur', 0):,.2f}"],
        ]
        t = Table(summary_data, colWidths=[80*mm, 40*mm, 40*mm])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f5f5f5')),
            ('FONTNAME',   (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE',   (0, 0), (-1, -1), 9),
            ('ALIGN',      (1, 0), (-1, -1), 'RIGHT'),
            ('GRID',       (0, 0), (-1, -1), 0.25, colors.HexColor('#e0e0e0')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#fafafa')]),
            ('TOPPADDING',  (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        story.append(t)
        story.append(Spacer(1, 6*mm))

        # Best / worst performer callout
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
        pt = Table(perf_data, colWidths=[82*mm, 82*mm])
        pt.setStyle(TableStyle([
            ('BACKGROUND',    (0, 0), (-1, 0), colors.HexColor('#f5f5f5')),
            ('FONTNAME',      (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE',      (0, 0), (-1, -1), 9),
            ('ALIGN',         (0, 0), (-1, -1), 'CENTER'),
            ('GRID',          (0, 0), (-1, -1), 0.25, colors.HexColor('#e0e0e0')),
            ('TEXTCOLOR',     (0, 1), (0, 1), colors.HexColor('#166534')),  # best = green
            ('TEXTCOLOR',     (1, 1), (1, 1), colors.HexColor('#991b1b')),  # worst = red
            ('FONTNAME',      (0, 1), (-1, 1), 'Helvetica-Bold'),
            ('TOPPADDING',    (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        story.append(pt)
        story.append(Spacer(1, 10*mm))

        # Holdings table — includes daily change
        story.append(Paragraph("Holdings", ParagraphStyle(
            'h2', parent=styles['Heading2'], fontSize=12, spaceAfter=6)))
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
        ht = Table(hold_data, colWidths=[20*mm, 12*mm, 22*mm, 22*mm, 16*mm, 26*mm, 16*mm, 16*mm])
        # colour positive/negative day % and P&L % cells per row
        ht_style = [
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f5f5f5')),
            ('FONTNAME',   (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE',   (0, 0), (-1, -1), 8),
            ('ALIGN',      (1, 0), (-1, -1), 'RIGHT'),
            ('ALIGN',      (0, 0), (0, -1), 'LEFT'),
            ('GRID',       (0, 0), (-1, -1), 0.25, colors.HexColor('#e0e0e0')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#fafafa')]),
            ('TOPPADDING',  (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]
        for i, row in enumerate(df.itertuples(), start=1):
            day_pct = getattr(row, 'daily_change_pct', None)
            if day_pct is not None and pd.notna(day_pct):
                col = colors.HexColor('#166534') if day_pct >= 0 else colors.HexColor('#991b1b')
                ht_style.append(('TEXTCOLOR', (4, i), (4, i), col))
            pnl = getattr(row, 'pnl_pct', 0) or 0
            col = colors.HexColor('#166534') if pnl >= 0 else colors.HexColor('#991b1b')
            ht_style.append(('TEXTCOLOR', (6, i), (6, i), col))
        ht.setStyle(TableStyle(ht_style))
        story.append(ht)

        doc.build(story)
        buf.seek(0)
        prefix = "portfolio_demo" if demo else "portfolio"
        filename = f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        return dcc.send_bytes(buf.read(), filename)
