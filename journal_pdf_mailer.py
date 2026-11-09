#!/usr/bin/env python3
"""
Executive Daily Trading Journal PDF Generator & Automated Email Dispatcher
Generates an institutional-grade PDF performance audit for Vijay Sai's Dhan Algo
and dispatches it directly to the trader's email with executive metrics.
"""

import os
import sys
import json
import time
import datetime
import subprocess
import pandas as pd
from typing import Dict, Any, Tuple, Optional

try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

# Ensure project root is available
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from journal import journal
from config import config

EXPORTS_DIR = os.path.join(PROJECT_DIR, "exports")
os.makedirs(EXPORTS_DIR, exist_ok=True)

EDGE_PATH = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
if not os.path.exists(EDGE_PATH):
    EDGE_PATH = r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"


def compute_journal_kpis(df: pd.DataFrame) -> Dict[str, Any]:
    """Computes comprehensive institutional-grade trading analytics from the trades DataFrame."""
    if df.empty:
        return {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "breakeven": 0,
            "win_rate": 0.0,
            "gross_pnl": 0.0,
            "total_charges": 0.0,
            "net_pnl": 0.0,
            "profit_factor": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "max_win": 0.0,
            "max_loss": 0.0,
            "stt": 0.0,
            "brokerage": 0.0,
            "gst": 0.0,
            "exchange_charges": 0.0,
            "sebi_charges": 0.0,
            "stamp_duty": 0.0,
            "trade_list": []
        }

    total_trades = len(df)
    winning_df = df[df["net_pnl"] > 0]
    losing_df = df[df["net_pnl"] < 0]
    breakeven_df = df[df["net_pnl"] == 0]

    wins = len(winning_df)
    losses = len(losing_df)
    breakeven = len(breakeven_df)
    win_rate = round((wins / total_trades) * 100.0, 1) if total_trades > 0 else 0.0

    gross_pnl = round(float(df["gross_pnl"].sum()), 2)
    total_charges = round(float(df["total_charges"].sum()), 2)
    net_pnl = round(float(df["net_pnl"].sum()), 2)

    gross_wins = float(winning_df["gross_pnl"].sum()) if not winning_df.empty else 0.0
    gross_losses = abs(float(losing_df["gross_pnl"].sum())) if not losing_df.empty else 0.0
    profit_factor = round(gross_wins / gross_losses, 2) if gross_losses > 0 else (99.9 if gross_wins > 0 else 0.0)

    avg_win = round(float(winning_df["net_pnl"].mean()), 2) if not winning_df.empty else 0.0
    avg_loss = round(float(losing_df["net_pnl"].mean()), 2) if not losing_df.empty else 0.0
    max_win = round(float(winning_df["net_pnl"].max()), 2) if not winning_df.empty else 0.0
    max_loss = round(float(losing_df["net_pnl"].min()), 2) if not losing_df.empty else 0.0

    stt = round(float(df["stt"].sum()), 2) if "stt" in df.columns else 0.0
    brokerage = round(float(df["brokerage"].sum()), 2) if "brokerage" in df.columns else 0.0
    gst = round(float(df["gst"].sum()), 2) if "gst" in df.columns else 0.0
    exchange_charges = round(float(df["exchange_charges"].sum()), 2) if "exchange_charges" in df.columns else 0.0
    sebi_charges = round(float(df["sebi_charges"].sum()), 2) if "sebi_charges" in df.columns else 0.0
    stamp_duty = round(float(df["stamp_duty"].sum()), 2) if "stamp_duty" in df.columns else 0.0

    # Sort descending by trade completion
    trades_records = df.to_dict("records")
    trades_records.reverse()

    return {
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate": win_rate,
        "gross_pnl": gross_pnl,
        "total_charges": total_charges,
        "net_pnl": net_pnl,
        "profit_factor": profit_factor,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "max_win": max_win,
        "max_loss": max_loss,
        "stt": stt,
        "brokerage": brokerage,
        "gst": gst,
        "exchange_charges": exchange_charges,
        "sebi_charges": sebi_charges,
        "stamp_duty": stamp_duty,
        "trade_list": trades_records
    }


def render_journal_html(kpis: Dict[str, Any], target_date: str) -> str:
    """Generates a high-resolution, institutional-grade printable HTML report."""
    net_pnl = kpis["net_pnl"]
    is_profit = net_pnl >= 0
    pnl_color = "#059669" if is_profit else "#dc2626"
    pnl_bg = "#ecfdf5" if is_profit else "#fef2f2"
    pnl_sign = "+" if is_profit else ""

    now_str = datetime.datetime.now().strftime("%d %b %Y, %I:%M %p IST")

    # Build rows
    rows_html = []
    for i, t in enumerate(kpis["trade_list"], 1):
        t_pnl = float(t.get("net_pnl", 0.0))
        is_win = t_pnl >= 0
        badge_cls = "badge-win" if is_win else "badge-loss"
        badge_txt = "WIN" if is_win else "LOSS"
        row_pnl_cls = "text-win" if is_win else "text-loss"
        row_sign = "+" if is_win else ""
        sym = t.get("symbol", "NIFTY")
        action = t.get("action", "BUY")
        qty = t.get("quantity", 65)
        entry_p = f"{float(t.get('entry_price', 0.0)):.2f}"
        exit_p = f"{float(t.get('exit_price', 0.0)):.2f}"
        duration = t.get("duration_str", "1m 30s")
        reason = t.get("exit_reason", "Target Hit")
        charges = f"₹{float(t.get('total_charges', 0.0)):.2f}"
        net_str = f"{row_sign}₹{t_pnl:,.2f}"
        roi = f"{row_sign}{float(t.get('net_pnl_pct', 0.0)):.1f}%"
        time_str = t.get("exit_time", "").split("T")[-1][:8] if "T" in str(t.get("exit_time", "")) else str(t.get("exit_time", ""))[:8]
        if not time_str or time_str == "None":
            time_str = t.get("order_executed_time", "")[:8]

        rows_html.append(f"""
        <tr>
            <td style="font-weight: 700; color: #64748b; text-align: center;">{i}</td>
            <td style="font-family: monospace; font-size: 11px; color: #475569;">{time_str}</td>
            <td style="font-weight: 700; color: #0f172a;">{sym}</td>
            <td><span class="pill-action">{action}</span></td>
            <td style="text-align: right; font-family: monospace;">{qty}</td>
            <td style="text-align: right; font-family: monospace;">₹{entry_p}</td>
            <td style="text-align: right; font-family: monospace;">₹{exit_p}</td>
            <td style="font-size: 11px; color: #64748b;">{duration}</td>
            <td style="font-size: 11px; color: #334155; max-width: 150px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="{reason}">{reason}</td>
            <td style="text-align: right; font-family: monospace; font-size: 11px; color: #64748b;">{charges}</td>
            <td style="text-align: right; font-weight: 800; font-family: monospace;" class="{row_pnl_cls}">{net_str}</td>
            <td style="text-align: center;"><span class="{badge_cls}">{badge_txt} ({roi})</span></td>
        </tr>
        """)

    table_rows_joined = "\n".join(rows_html)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Vijay Sai's Dhan Algo — Daily Trading Journal ({target_date})</title>
    <style>
        @page {{
            size: A4 portrait;
            margin: 12mm 10mm 15mm 10mm;
        }}
        * {{
            box-sizing: border-box;
            -webkit-print-color-adjust: exact !important;
            print-color-adjust: exact !important;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            color: #0f172a;
            background: #ffffff;
            margin: 0;
            padding: 0;
            font-size: 12px;
            line-height: 1.4;
        }}
        .header-bar {{
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            color: #ffffff;
            padding: 20px 24px;
            border-radius: 12px;
            margin-bottom: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .header-title {{
            font-size: 20px;
            font-weight: 800;
            letter-spacing: -0.5px;
            margin: 0 0 4px 0;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .header-sub {{
            font-size: 11px;
            color: #94a3b8;
            margin: 0;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
        }}
        .meta-badges {{
            text-align: right;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            font-size: 11px;
        }}
        .meta-tag {{
            display: inline-block;
            background: rgba(255, 255, 255, 0.12);
            padding: 4px 10px;
            border-radius: 6px;
            margin-bottom: 4px;
            font-weight: 600;
        }}
        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 12px;
            margin-bottom: 20px;
        }}
        .kpi-card {{
            background: #f8fafc;
            border: 1px solid #e2e8f0;
            border-radius: 10px;
            padding: 12px 14px;
        }}
        .kpi-card.highlight {{
            background: {pnl_bg};
            border-color: {pnl_color}44;
            grid-column: span 2;
        }}
        .kpi-title {{
            font-size: 10px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: #64748b;
            margin-bottom: 4px;
        }}
        .kpi-val {{
            font-size: 18px;
            font-weight: 800;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            color: #0f172a;
        }}
        .kpi-val.highlight-val {{
            font-size: 26px;
            color: {pnl_color};
        }}
        .kpi-sub {{
            font-size: 10px;
            color: #64748b;
            margin-top: 2px;
        }}
        .sec-title {{
            font-size: 13px;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: #1e293b;
            margin: 16px 0 8px 0;
            border-left: 3px solid #3b82f6;
            padding-left: 8px;
        }}
        .tax-table {{
            width: 100%;
            border-collapse: collapse;
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            overflow: hidden;
            margin-bottom: 20px;
            font-size: 11px;
        }}
        .tax-table th, .tax-table td {{
            padding: 8px 12px;
            border-bottom: 1px solid #f1f5f9;
        }}
        .tax-table th {{
            background: #f8fafc;
            font-weight: 700;
            color: #475569;
            text-align: left;
        }}
        .ledger-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 10.5px;
            border: 1px solid #cbd5e1;
            border-radius: 8px;
            overflow: hidden;
        }}
        .ledger-table thead {{
            background: #0f172a;
            color: #ffffff;
        }}
        .ledger-table th {{
            padding: 7px 8px;
            font-weight: 700;
            font-size: 9.5px;
            text-transform: uppercase;
            letter-spacing: 0.3px;
            border-bottom: 1px solid #334155;
            text-align: left;
        }}
        .ledger-table tbody tr {{
            border-bottom: 1px solid #f1f5f9;
        }}
        .ledger-table tbody tr:nth-child(even) {{
            background: #f8fafc;
        }}
        .ledger-table td {{
            padding: 6px 8px;
            vertical-align: middle;
        }}
        .badge-win {{
            background: #dcfce7;
            color: #15803d;
            border: 1px solid #86efac;
            padding: 2px 6px;
            border-radius: 4px;
            font-weight: 800;
            font-size: 9.5px;
            font-family: ui-monospace, monospace;
            display: inline-block;
        }}
        .badge-loss {{
            background: #fee2e2;
            color: #b91c1c;
            border: 1px solid #fca5a5;
            padding: 2px 6px;
            border-radius: 4px;
            font-weight: 800;
            font-size: 9.5px;
            font-family: ui-monospace, monospace;
            display: inline-block;
        }}
        .pill-action {{
            background: #e0f2fe;
            color: #0369a1;
            padding: 2px 6px;
            border-radius: 4px;
            font-weight: 700;
            font-size: 9px;
            font-family: ui-monospace, monospace;
        }}
        .text-win {{
            color: #059669;
        }}
        .text-loss {{
            color: #dc2626;
        }}
        .footer {{
            margin-top: 24px;
            padding-top: 12px;
            border-top: 1px solid #e2e8f0;
            text-align: center;
            font-size: 10px;
            color: #94a3b8;
            font-family: ui-monospace, monospace;
        }}
        .page-break {{
            page-break-before: always;
        }}
    </style>
</head>
<body>

    <!-- Header Banner -->
    <div class="header-bar">
        <div>
            <div class="header-title">
                <span>⚡ VIJAY SAI'S DHAN ALGO</span>
            </div>
            <div class="header-sub">Daily Executive Performance Ledger & Regulatory Audit • DhanHQ API</div>
        </div>
        <div class="meta-badges">
            <div class="meta-tag">📅 Session: {target_date}</div>
            <div style="color: #cbd5e1; font-size: 10px;">Generated: {now_str}</div>
            <div style="color: #94a3b8; font-size: 10px;">Client ID: 1104958987 | Mode: {config.trading_mode}</div>
        </div>
    </div>

    <!-- Executive KPI Metric Cards -->
    <div class="kpi-grid">
        <div class="kpi-card highlight">
            <div class="kpi-title">Net Realized Session P&L (Post-Tax & Charges)</div>
            <div class="kpi-val highlight-val">{pnl_sign}₹{net_pnl:,.2f}</div>
            <div class="kpi-sub">Calculated after exact STT, GST, Exchange, SEBI & Brokerage deductions</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-title">Gross Trading Profit</div>
            <div class="kpi-val" style="color: #0f172a;">+₹{kpis['gross_pnl']:,.2f}</div>
            <div class="kpi-sub">Total institutional trade gains</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-title">Total Taxes & Charges</div>
            <div class="kpi-val" style="color: #64748b;">-₹{kpis['total_charges']:,.2f}</div>
            <div class="kpi-sub">STT, GST, SEBI, Exchange fees</div>
        </div>

        <div class="kpi-card">
            <div class="kpi-title">Win Rate</div>
            <div class="kpi-val" style="color: #059669;">{kpis['win_rate']}%</div>
            <div class="kpi-sub">{kpis['wins']} Wins / {kpis['losses']} Losses ({kpis['total_trades']} Total)</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-title">Profit Factor</div>
            <div class="kpi-val" style="color: #2563eb;">{kpis['profit_factor']}x</div>
            <div class="kpi-sub">Gross Gains vs Gross Drawdown</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-title">Average Win Trade</div>
            <div class="kpi-val text-win">+₹{kpis['avg_win']:,.2f}</div>
            <div class="kpi-sub">Max Single Win: +₹{kpis['max_win']:,.2f}</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-title">Average Loss Trade</div>
            <div class="kpi-val text-loss">-₹{abs(kpis['avg_loss']):,.2f}</div>
            <div class="kpi-sub">Max Single Loss: -₹{abs(kpis['max_loss']):,.2f}</div>
        </div>
    </div>

    <!-- Statutory Fee & Tax Audit Table -->
    <div class="sec-title">Regulatory Tax & Statutory Fee Audit (Securities Appellate / SEBI Schedule)</div>
    <table class="tax-table">
        <thead>
            <tr>
                <th>Securities Transaction Tax (STT)</th>
                <th>Brokerage</th>
                <th>Goods & Services Tax (GST 18%)</th>
                <th>Exchange Turnover Charges</th>
                <th>SEBI Turnover Fees</th>
                <th>Stamp Duty</th>
                <th>Total Charges Deducted</th>
            </tr>
        </thead>
        <tbody>
            <tr style="font-family: ui-monospace, monospace; font-weight: 700; color: #334155;">
                <td>₹{kpis['stt']:,.2f}</td>
                <td>₹{kpis['brokerage']:,.2f}</td>
                <td>₹{kpis['gst']:,.2f}</td>
                <td>₹{kpis['exchange_charges']:,.2f}</td>
                <td>₹{kpis['sebi_charges']:,.2f}</td>
                <td>₹{kpis['stamp_duty']:,.2f}</td>
                <td style="color: #dc2626; font-weight: 800;">₹{kpis['total_charges']:,.2f}</td>
            </tr>
        </tbody>
    </table>

    <!-- Complete Trade Execution Ledger -->
    <div class="sec-title">Chronological Execution Ledger ({kpis['total_trades']} Completed Executions)</div>
    <table class="ledger-table">
        <thead>
            <tr>
                <th style="width: 25px; text-align: center;">#</th>
                <th style="width: 65px;">Time</th>
                <th>Scrip / Instrument</th>
                <th style="width: 45px;">Type</th>
                <th style="width: 45px; text-align: right;">Qty</th>
                <th style="width: 60px; text-align: right;">Entry</th>
                <th style="width: 60px; text-align: right;">Exit</th>
                <th style="width: 55px;">Duration</th>
                <th>Exit Rationale</th>
                <th style="width: 55px; text-align: right;">Charges</th>
                <th style="width: 75px; text-align: right;">Net P&L</th>
                <th style="width: 75px; text-align: center;">Outcome</th>
            </tr>
        </thead>
        <tbody>
            {table_rows_joined}
        </tbody>
    </table>

    <!-- Footer -->
    <div class="footer">
        Vijay Sai's Dhan Algo • Proprietary Automated Quantitative Trading Engine • Client: 1104958987 • End of Official Journal Audit
    </div>

</body>
</html>
"""
    return html


def generate_journal_pdf(target_date: Optional[str] = None) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Renders today's journal into HTML and compiles it to PDF using Headless Edge.
    Returns (success, pdf_path, kpis).
    """
    if not target_date:
        target_date = datetime.date.today().strftime("%Y-%m-%d")

    df = journal.get_all_trades_df()
    if not df.empty:
        today_df = df[df["trade_date"] == target_date]
    else:
        today_df = pd.DataFrame()

    kpis = compute_journal_kpis(today_df)

    # 1. Write HTML report
    html_content = render_journal_html(kpis, target_date)
    html_filename = f"Trading_Journal_{target_date}.html"
    pdf_filename = f"Trading_Journal_{target_date}.pdf"
    html_path = os.path.join(EXPORTS_DIR, html_filename)
    pdf_path = os.path.join(EXPORTS_DIR, pdf_filename)

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"[*] HTML Trading Report written to: {html_path}")

    # 2. Compile to PDF with Edge Headless
    if not os.path.exists(EDGE_PATH):
        return False, f"Edge executable not found at {EDGE_PATH}", kpis

    file_uri = "file:///" + html_path.replace("\\", "/")
    cmd = [
        EDGE_PATH,
        "--headless",
        "--disable-gpu",
        "--no-pdf-header-footer",
        f"--print-to-pdf={pdf_path}",
        file_uri
    ]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        time.sleep(1.0) # Ensure disk flush
        if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 1000:
            size_kb = round(os.path.getsize(pdf_path) / 1024.0, 1)
            print(f"[✓] Executive PDF generated successfully ({size_kb} KB): {pdf_path}")
            return True, pdf_path, kpis
        else:
            return False, f"PDF file not generated or empty. Output: {res.stderr}", kpis
    except Exception as e:
        return False, f"Exception invoking Edge PDF renderer: {e}", kpis


def send_journal_email(
    to_email: str = "vijayaram9@gmail.com",
    target_date: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Generates the executive PDF and emails it to the requested recipient.
    Primary channel: Outlook COM automation (configured natively on this workstation).
    """
    if not target_date:
        target_date = datetime.date.today().strftime("%Y-%m-%d")

    # 1. Generate PDF
    ok, pdf_path, kpis = generate_journal_pdf(target_date)
    if not ok:
        return False, f"PDF generation failed: {pdf_path}"

    net_pnl = kpis["net_pnl"]
    win_rate = kpis["win_rate"]
    total_trades = kpis["total_trades"]
    sign = "+" if net_pnl >= 0 else ""

    subject = f"🚀 Vijay Sai's Dhan Algo — Daily Trading Journal ({target_date}) | Net P&L: {sign}₹{net_pnl:,.2f} (Win Rate: {win_rate}%)"

    email_body_html = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #0f172a; max-width: 650px; margin: 0 auto; padding: 20px; border: 1px solid #e2e8f0; border-radius: 12px; background: #ffffff;">
        <div style="background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); color: #ffffff; padding: 20px; border-radius: 8px; margin-bottom: 20px;">
            <h2 style="margin: 0 0 6px 0; font-size: 20px; font-weight: 800;">⚡ VIJAY SAI'S DHAN ALGO</h2>
            <p style="margin: 0; font-size: 12px; color: #94a3b8; font-family: monospace;">Daily Trading Journal & Audit Report • DhanHQ Client ID: 1104958987</p>
        </div>

        <p style="font-size: 14px; line-height: 1.5;">Dear Vijay,</p>
        <p style="font-size: 14px; line-height: 1.5;">Here is your official automated <strong>Daily Trading Journal & Performance Ledger</strong> for session <strong>{target_date}</strong>. The executive-grade PDF audit report is attached to this email.</p>

        <!-- KPI Summary Cards -->
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 20px 0;">
            <div style="background: {'#ecfdf5' if net_pnl >= 0 else '#fef2f2'}; border: 1px solid {'#86efac' if net_pnl >= 0 else '#fca5a5'}; padding: 14px; border-radius: 8px;">
                <div style="font-size: 11px; font-weight: 700; color: #64748b; text-transform: uppercase;">Net Realized Session P&L</div>
                <div style="font-size: 24px; font-weight: 800; color: {'#059669' if net_pnl >= 0 else '#dc2626'}; font-family: monospace;">{sign}₹{net_pnl:,.2f}</div>
                <div style="font-size: 10px; color: #64748b; margin-top: 2px;">Post-tax, brokerage & statutory charges</div>
            </div>
            <div style="background: #f8fafc; border: 1px solid #e2e8f0; padding: 14px; border-radius: 8px;">
                <div style="font-size: 11px; font-weight: 700; color: #64748b; text-transform: uppercase;">Win Rate & Accuracy</div>
                <div style="font-size: 24px; font-weight: 800; color: #2563eb; font-family: monospace;">{win_rate}%</div>
                <div style="font-size: 10px; color: #64748b; margin-top: 2px;">{kpis['wins']} Wins / {kpis['losses']} Losses ({total_trades} Executions)</div>
            </div>
        </div>

        <!-- Metrics Table -->
        <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px; font-size: 12px;">
            <tr style="border-bottom: 1px solid #f1f5f9;">
                <td style="padding: 8px 0; color: #64748b;">Gross Trading Profit:</td>
                <td style="padding: 8px 0; text-align: right; font-weight: 700; font-family: monospace;">+₹{kpis['gross_pnl']:,.2f}</td>
            </tr>
            <tr style="border-bottom: 1px solid #f1f5f9;">
                <td style="padding: 8px 0; color: #64748b;">Total Taxes & Charges (STT + GST + SEBI):</td>
                <td style="padding: 8px 0; text-align: right; font-weight: 700; font-family: monospace; color: #dc2626;">-₹{kpis['total_charges']:,.2f}</td>
            </tr>
            <tr style="border-bottom: 1px solid #f1f5f9;">
                <td style="padding: 8px 0; color: #64748b;">Profit Factor:</td>
                <td style="padding: 8px 0; text-align: right; font-weight: 700; font-family: monospace; color: #059669;">{kpis['profit_factor']}x</td>
            </tr>
            <tr style="border-bottom: 1px solid #f1f5f9;">
                <td style="padding: 8px 0; color: #64748b;">Average Win / Max Win:</td>
                <td style="padding: 8px 0; text-align: right; font-weight: 700; font-family: monospace;">+₹{kpis['avg_win']:,.2f} / +₹{kpis['max_win']:,.2f}</td>
            </tr>
            <tr>
                <td style="padding: 8px 0; color: #64748b;">Average Loss / Max Loss:</td>
                <td style="padding: 8px 0; text-align: right; font-weight: 700; font-family: monospace;">-₹{abs(kpis['avg_loss']):,.2f} / -₹{abs(kpis['max_loss']):,.2f}</td>
            </tr>
        </table>

        <!-- Attachment note -->
        <div style="background: #eff6ff; border: 1px dashed #60a5fa; padding: 12px 16px; border-radius: 8px; margin-bottom: 20px;">
            <p style="margin: 0; font-size: 12px; color: #1e40af;">
                📎 <strong>Attached Document:</strong> <code>{os.path.basename(pdf_path)}</code> ({round(os.path.getsize(pdf_path)/1024, 1)} KB)<br>
                Contains the complete itemized execution ledger ({total_trades} orders) and full regulatory fee breakdown.
            </p>
        </div>

        <p style="font-size: 11px; color: #94a3b8; margin-top: 30px; text-align: center; font-family: monospace;">
            Vijay Sai's Dhan Algo Engine • Autonomous Multi-Strategy Scalper • DhanHQ Connected
        </p>
    </div>
    """

    # 2. Dispatch via PowerShell Outlook COM Automation
    body_file = os.path.join(EXPORTS_DIR, "_email_body.html")
    with open(body_file, "w", encoding="utf-8") as f:
        f.write(email_body_html)

    ps_script = """param($toEmail, $subject, $bodyFile, $attachmentPath)
$ol = New-Object -ComObject Outlook.Application
$mail = $ol.CreateItem(0)
$mail.To = $toEmail
$mail.Subject = $subject
$mail.HTMLBody = Get-Content -Path $bodyFile -Raw -Encoding UTF8
if (Test-Path $attachmentPath) {
    $mail.Attachments.Add($attachmentPath)
}
$mail.Send()

# Trigger immediate Send/Receive flush
try {
    $ns = $ol.GetNamespace("MAPI")
    $syncObjects = $ns.SyncObjects
    if ($syncObjects.Count -ge 1) {
        $syncObjects.Item(1).Start()
    }
} catch {}

Write-Host "EMAIL_SENT_SUCCESSFULLY"
"""

    ps_file = os.path.join(EXPORTS_DIR, "_dispatch_email.ps1")
    try:
        with open(ps_file, "w", encoding="utf-8") as f:
            f.write(ps_script)

        res = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps_file,
             "-toEmail", to_email, "-subject", subject, "-bodyFile", body_file, "-attachmentPath", pdf_path],
            capture_output=True,
            text=True,
            timeout=30
        )

        if "EMAIL_SENT_SUCCESSFULLY" in res.stdout:
            print(f"[✓] Email dispatched successfully to {to_email} with PDF attachment!")
            return True, f"Email with PDF attachment successfully sent to {to_email}"
        else:
            print(f"[!] Outlook COM output: {res.stdout}\nError: {res.stderr}")
            return False, f"Outlook COM error: {res.stderr or res.stdout}"
    except Exception as e:
        return False, f"Exception sending email: {e}"
    finally:
        for tmp in [ps_file, body_file]:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass


if __name__ == "__main__":
    recipient = sys.argv[1] if len(sys.argv) > 1 else "vijayaram9@gmail.com"
    print(f"[*] Generating PDF and sending trading journal to: {recipient}...")
    success, msg = send_journal_email(recipient)
    if success:
        print(f"[SUCCESS] {msg}")
        sys.exit(0)
    else:
        print(f"[FAILURE] {msg}")
        sys.exit(1)
