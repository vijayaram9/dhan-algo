from dataclasses import dataclass

@dataclass
class TradeCharges:
    buy_turnover: float
    sell_turnover: float
    total_turnover: float
    brokerage: float
    stt: float
    exchange_charges: float
    sebi_charges: float
    stamp_duty: float
    gst: float
    total_charges: float
    gross_pnl: float
    net_pnl: float
    net_pnl_pct: float

def calculate_dhan_options_charges(entry_price: float, exit_price: float, quantity: int) -> TradeCharges:
    """
    Calculates exact Indian regulatory taxes and Dhan brokerages for NSE Equity Options:
    - Brokerage: Rs.20 buy + Rs.20 sell = Rs.40
    - STT (Securities Transaction Tax): 0.1% on sell side premium turnover
    - Exchange Transaction Charges (NSE): 0.0505% on total premium turnover
    - SEBI Charges: Rs.10 per crore (0.0001% of turnover)
    - Stamp Duty: 0.003% on buy side turnover
    - GST: 18% on (Brokerage + Exchange Charges + SEBI Charges)
    """
    buy_turnover = round(entry_price * quantity, 2)
    sell_turnover = round(exit_price * quantity, 2)
    total_turnover = round(buy_turnover + sell_turnover, 2)

    # 1. Dhan Brokerage: Rs.20 flat per executed order
    brokerage = 40.00  # Rs.20 entry + Rs.20 exit

    # 2. STT: 0.1% on sell-side premium turnover
    stt = round(sell_turnover * 0.001, 2)

    # 3. Exchange Transaction Charges: 0.0505% on total turnover
    exchange_charges = round(total_turnover * 0.000505, 2)

    # 4. SEBI Turnover Charges: Rs. 10 / Crore
    sebi_charges = round(total_turnover * 0.000001, 2)

    # 5. Stamp Duty: 0.003% on buy side turnover
    stamp_duty = round(buy_turnover * 0.00003, 2)

    # 6. GST: 18% on (Brokerage + Exchange Charges + SEBI)
    gst_taxable = brokerage + exchange_charges + sebi_charges
    gst = round(gst_taxable * 0.18, 2)

    # Total Government & Broker Deductions
    total_charges = round(brokerage + stt + exchange_charges + sebi_charges + stamp_duty + gst, 2)

    # Gross P&L
    gross_pnl = round((exit_price - entry_price) * quantity, 2)

    # Net P&L after all taxes and brokerage
    net_pnl = round(gross_pnl - total_charges, 2)
    net_pnl_pct = round((net_pnl / buy_turnover) * 100.0, 2) if buy_turnover > 0 else 0.0

    return TradeCharges(
        buy_turnover=buy_turnover,
        sell_turnover=sell_turnover,
        total_turnover=total_turnover,
        brokerage=brokerage,
        stt=stt,
        exchange_charges=exchange_charges,
        sebi_charges=sebi_charges,
        stamp_duty=stamp_duty,
        gst=gst,
        total_charges=total_charges,
        gross_pnl=gross_pnl,
        net_pnl=net_pnl,
        net_pnl_pct=net_pnl_pct
    )

if __name__ == "__main__":
    c = calculate_dhan_options_charges(entry_price=98.59, exit_price=107.17, quantity=75)
    print("Tax Calculation Verification:")
    print(f"Gross P&L: Rs.{c.gross_pnl:+.2f}")
    print(f"Total Taxes & Brokerage: Rs.{c.total_charges:.2f}")
    print(f"  • Brokerage: Rs.{c.brokerage:.2f}")
    print(f"  • STT: Rs.{c.stt:.2f}")
    print(f"  • NSE Charges: Rs.{c.exchange_charges:.2f}")
    print(f"  • GST: Rs.{c.gst:.2f}")
    print(f"  • Stamp Duty: Rs.{c.stamp_duty:.2f}")
    print(f"Net Realized P&L: Rs.{c.net_pnl:+.2f} ({c.net_pnl_pct:+.2f}%)")
