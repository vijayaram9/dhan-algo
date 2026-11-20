import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass
class TradingConfig:
    # Dhan API
    client_id: str = os.getenv("DHAN_CLIENT_ID", "")
    access_token: str = os.getenv("DHAN_ACCESS_TOKEN", "")
    
    # Mode: 'PAPER' or 'LIVE'
    trading_mode: str = os.getenv("TRADING_MODE", "PAPER").upper()
    
    # Instrument Setup (NIFTY October 2025 revision: 65 units/lot)
    index_name: str = "NIFTY"
    strike_step: int = 50       # Nifty strike interval is 50 points
    expiry_type: str = os.getenv("EXPIRY_TYPE", "CURRENT_WEEK").upper()
    lot_size: int = int(os.getenv("LOT_SIZE", "65"))
    max_lots: int = int(os.getenv("MAX_LOTS", "1"))
    
    # RMS - Risk Management System (100% Percentage-Based — Scales Infinitely to Any Capital)
    account_capital: float = float(os.getenv("ACCOUNT_CAPITAL", "150000.0"))
    risk_per_trade_pct: float = float(os.getenv("RISK_PER_TRADE_PCT", "1.0"))  # 1.0% of Active Capital per trade (= 1R)
    max_portfolio_heat_pct: float = float(os.getenv("MAX_PORTFOLIO_HEAT_PCT", "3.0"))  # Max 3.0% combined open risk across concurrent trades
    max_daily_loss_pct: float = float(os.getenv("MAX_DAILY_LOSS_PCT", "4.0"))  # 4.0% of Active Capital daily stop
    max_daily_loss: float = float(os.getenv("MAX_DAILY_LOSS", "6000.0"))
    max_daily_profit: float = float(os.getenv("MAX_DAILY_PROFIT", "1000000.0"))  # Uncapped growth engine
    max_trades_per_day: int = int(os.getenv("MAX_TRADES_PER_DAY", "50"))
    max_consecutive_losses: int = 99
    
    # Multi-Instrument Execution & Concurrency Control
    max_concurrent_positions: int = int(os.getenv("MAX_CONCURRENT_POSITIONS", "5"))   # Allows up to 5 concurrent positions across instruments (like Zerodha/Dhan)
    enforce_single_position: bool = False
    cooldown_seconds: int = int(os.getenv("COOLDOWN_SECONDS", "15"))  # 15s agile scalping cooldown per instrument
    
    # Quick 10-20 Points Scalp Parameters (Target fast 10-20 pts profit with tight risk defense)
    quick_scalp_target_nifty: float = 16.0   # Target 14-18 pts in NIFTY options
    quick_scalp_target_bn: float = 25.0      # Target 20-30 pts in BANKNIFTY / SENSEX
    quick_scalp_target_mcx: float = 18.0     # Target 15-20 pts in CRUDEOILM / Commodities
    quick_scalp_sl_nifty: float = 7.0        # Controlled 7 pts SL for 1:2 R:R
    quick_scalp_sl_bn: float = 12.0          # Controlled 12 pts SL for 1:2 R:R
    quick_scalp_sl_mcx: float = 9.0          # Controlled 9 pts SL for 1:2 R:R
    
    # Strategy Execution Rules (NIFTY SMART EDGE v1.0)
    sl_percent: float = float(os.getenv("SL_PERCENT", "10.0"))        # Tight 10% Hard Stop on premium (never 20% bleed)
    target_percent: float = float(os.getenv("TARGET_PERCENT", "25.0")) # 1:2.5R target expansion
    trailing_sl_percent: float = float(os.getenv("TRAILING_SL_PERCENT", "10.0"))
    
    # Market Timing (Section 8 Schedule)
    market_open_time: str = "09:15"
    strategy_start_time: str = "09:30"   # Let opening range form
    no_new_trades_time: str = os.getenv("NO_NEW_TRADES_TIME", "15:10")    # Extended to 15:10 PM for 3 PM scalp momentum
    square_off_time: str = os.getenv("SQUARE_OFF_TIME", "15:20")

config = TradingConfig()
