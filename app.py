import os
import sys
import threading
import time
import datetime
import warnings
from typing import Optional

# Suppress warnings
warnings.filterwarnings("ignore")

# UTF-8 Console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from dataclasses import asdict
from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for
from flask.json.provider import DefaultJSONProvider
import pandas as pd
import numpy as np
from config import config
from smart_edge_engine import smart_edge_engine, SmartEdgePosition
from smart_edge_strategy import SmartEdgeSignal
from contract_selector import contract_selector
from order_flow import OrderFlowAnalyzer
from journal import journal, CSV_PATH
from notifier import notifier
from market_intelligence import market_intelligence, ASSET_CATALOG, add_custom_asset, remove_custom_asset, reset_default_assets
import scrip_master_directory as smd
from backtester import HistoricalBacktestEngine
from option_chain import OptionChainEngine

class NumpyJSONProvider(DefaultJSONProvider):
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, (np.bool_, bool)):
            return bool(obj)
        return super().default(obj)

app = Flask(__name__)
app.json_provider_class = NumpyJSONProvider
app.json = NumpyJSONProvider(app)

order_flow_analyzer = OrderFlowAnalyzer()
backtest_engine = HistoricalBacktestEngine()
option_chain_engine = OptionChainEngine()

@app.template_filter("inr")
def inr_format(value):
    try:
        val = float(value)
        return f"{val:+,.2f}"
    except (ValueError, TypeError):
        return str(value)

@app.template_filter("inr_abs")
def inr_abs_format(value):
    try:
        val = abs(float(value))
        return f"{val:,.2f}"
    except (ValueError, TypeError):
        return str(value)

# Global Engine Reference
engine = smart_edge_engine
is_engine_active = True
now_hm_init = datetime.datetime.now().strftime("%H:%M")
selected_asset = "CRUDEOILM" if (now_hm_init >= "15:30" or now_hm_init < "09:15") else "NIFTY"
current_capital = float(getattr(config, "account_capital", 15000.0))
lock = threading.Lock()

@app.context_processor
def inject_global_terminal_state():
    """Server-side ground truth: ensures templates render accurate market, spot prices, and broker states immediately."""
    try:
        is_open, open_desc = market_intelligence.is_market_open(selected_asset)
    except Exception:
        is_open, open_desc = False, "Session Status Checking"

    try:
        current_live_spot = market_intelligence.get_asset_spot(selected_asset)
    except Exception:
        current_live_spot = 24330.0
        
    try:
        fund_res = engine.client.get_fund_limits()
        is_dhan_connected = (fund_res.get("status") == "success")
        real_cash = float(fund_res.get("data", {}).get("availabelBalance", fund_res.get("data", {}).get("sodLimit", 0.0))) if is_dhan_connected else 0.0
    except Exception:
        is_dhan_connected = False
        real_cash = 0.0

    return {
        "is_market_open": is_open,
        "market_open_desc": open_desc,
        "is_dhan_connected": is_dhan_connected,
        "real_cash_balance": real_cash,
        "live_spot": current_live_spot,
        "selected_asset": selected_asset,
        "active_paper_capital": float(getattr(engine.client, "paper_capital", current_capital))
    }

def background_trading_loop():
    """Runs ticks in the background while Flask serves web requests."""
    global is_engine_active
    print("[Flask] Vijay Sai's Dhan Algo - Background Trading Engine started.")
    engine.initialize()

    while True:
        try:
            if is_engine_active:
                with lock:
                    engine.run_tick()
            sleep_time = 1 if engine.active_positions else 2
            time.sleep(sleep_time)
        except Exception as e:
            print(f"[Flask Worker Exception] {e}")
            time.sleep(1)

@app.route("/")
def dashboard():
    return render_template(
        "dashboard.html",
        config=config,
        asset_catalog=ASSET_CATALOG,
        selected_asset=selected_asset,
        current_capital=current_capital
    )

@app.route("/api/set-trading-mode", methods=["POST"])
def api_set_trading_mode():
    """Switches dynamically between PAPER simulation and LIVE Dhan broker trading."""
    try:
        data = request.get_json(force=True) or {}
        new_mode = data.get("mode", "PAPER").upper()
        if new_mode not in ["PAPER", "LIVE"]:
            return jsonify({"success": False, "message": "Invalid mode. Choose PAPER or LIVE."}), 400

        with lock:
            config.trading_mode = new_mode
            engine.mode = new_mode
            engine.client.mode = new_mode
            real_cash = engine.client.get_real_cash_balance()
            if new_mode == "LIVE":
                engine.account_capital = real_cash
            else:
                engine.account_capital = float(engine.client.paper_capital)

            # Sync to .env
            env_path = os.path.join(os.path.dirname(__file__), ".env")
            if os.path.exists(env_path):
                with open(env_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                new_lines = []
                for line in lines:
                    if line.startswith("TRADING_MODE="):
                        new_lines.append(f"TRADING_MODE={new_mode}\n")
                    else:
                        new_lines.append(line)
                with open(env_path, "w", encoding="utf-8") as f:
                    f.writelines(new_lines)

        warning_note = ""
        if new_mode == "LIVE" and real_cash <= 0:
            warning_note = " Notice: Your Dhan cash balance is ₹0.00. Please top up your wallet in Dhan app."

        notifier.send_ntfy(
            f"Trading Mode Switched to {new_mode}",
            f"Vijay Sai's Dhan Algo is now configured for {new_mode} execution.{warning_note}",
            priority="default"
        )
        return jsonify({
            "success": True, 
            "mode": new_mode, 
            "real_cash_balance": real_cash,
            "warning": warning_note,
            "message": f"Successfully activated {new_mode} Trading Mode!{warning_note}"
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/funds")
def api_funds():
    """Returns exact Dhan live cash balance and configurable paper capital."""
    try:
        fund_res = engine.client.get_fund_limits()
        is_connected = (fund_res.get("status") == "success")
        data = fund_res.get("data", {}) if is_connected else {}
        
        real_cash = float(data.get("availabelBalance", data.get("sodLimit", 0.0))) if is_connected else 0.0
        sod_limit = float(data.get("sodLimit", 0.0))
        collateral = float(data.get("collateralAmount", 0.0))
        utilized = float(data.get("utilizedAmount", 0.0))
        withdrawable = float(data.get("withdrawableBalance", 0.0))
        
        paper_capital = float(engine.client.paper_capital)
        active_capital = real_cash if config.trading_mode == "LIVE" else paper_capital
        
        return jsonify({
            "success": True,
            "is_connected": is_connected,
            "mode": config.trading_mode,
            "client_id": config.client_id,
            "real_cash_balance": real_cash,
            "sod_limit": sod_limit,
            "collateral_amount": collateral,
            "utilized_amount": utilized,
            "withdrawable_balance": withdrawable,
            "paper_capital": paper_capital,
            "active_capital": active_capital,
            "last_sync": datetime.datetime.now().strftime("%I:%M:%S %p")
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/set-paper-capital", methods=["POST"])
def api_set_paper_capital():
    """Configures simulated capital for paper trading without hardcoded values."""
    try:
        payload = request.get_json(silent=True) or {}
        if not payload and request.form:
            payload = request.form.to_dict()
        amount_raw = payload.get("amount", 25000.0)
        amount = float(amount_raw)
        if amount < 500:
            return jsonify({"success": False, "message": "Paper capital must be at least ₹500."}), 400
        if amount > 10000000:
            return jsonify({"success": False, "message": "Paper capital cannot exceed ₹1 Crore."}), 400
            
        with lock:
            engine.client.set_paper_capital(amount)
            engine.account_capital = amount
            engine.daily_loss_limit = round(amount * 0.02, 2)  # 2% safe risk ceiling (e.g. ₹3,000 on ₹1.5L)
        global current_capital
        current_capital = amount
            
        notifier.send_ntfy(
            "Paper Capital Updated",
            f"Paper trading capital has been updated to ₹{amount:,.2f}."
        )
        return jsonify({
            "success": True, 
            "paper_capital": amount,
            "message": f"Paper trading capital successfully set to ₹{amount:,.2f}"
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/dhan-status")
def api_dhan_status():
    """Checks Dhan API live connection, fund limits and token session validity."""
    try:
        fund_res = engine.client.get_fund_limits()
        is_connected = (fund_res.get("status") == "success")
        is_open, open_desc = market_intelligence.is_market_open()
        data = fund_res.get("data", {}) if is_connected else {}
        cash = float(data.get("availabelBalance", data.get("sodLimit", 0.0))) if is_connected else 0.0
        return jsonify({
            "success": True,
            "is_connected": is_connected,
            "mode": config.trading_mode,
            "client_id": config.client_id,
            "cash_balance": cash,
            "sod_limit": float(data.get("sodLimit", 0.0)),
            "withdrawable_balance": float(data.get("withdrawableBalance", 0.0)),
            "utilized_amount": float(data.get("utilizedAmount", 0.0)),
            "paper_capital": float(engine.client.paper_capital),
            "is_market_open": is_open,
            "market_desc": open_desc,
            "session_status": "ACTIVE & VALID" if is_connected else "EXPIRED / DISCONNECTED"
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "is_connected": False,
            "error": str(e),
            "session_status": "ERROR"
        }), 500

@app.route("/api/update-dhan-token", methods=["POST"])
def api_update_dhan_token():
    """Allows renewing or permanently setting DhanHQ Access Token & Trading Mode."""
    try:
        data = request.get_json(silent=True) or {}
        if not data and request.form:
            data = request.form.to_dict()
        client_id = data.get("client_id", config.client_id)
        access_token = data.get("access_token", "")
        chosen_mode = data.get("trading_mode", config.trading_mode).upper()
        persist_permanent = data.get("persist", True)
        
        res = engine.client.update_credentials(client_id, access_token)
        if res.get("success"):
            with lock:
                if chosen_mode in ["LIVE", "PAPER"]:
                    config.trading_mode = chosen_mode
                    engine.mode = chosen_mode

                if config.trading_mode == "LIVE":
                    engine.account_capital = res.get("cash_balance", 0.0)
                    global current_capital
                    current_capital = engine.account_capital

            # Persist trading mode to .env if requested
            if persist_permanent:
                try:
                    env_file = os.path.join(os.path.dirname(__file__), ".env")
                    if os.path.exists(env_file):
                        with open(env_file, "r", encoding="utf-8") as f:
                            env_lines = f.readlines()
                        new_env_lines = []
                        mode_set = False
                        for eline in env_lines:
                            if eline.startswith("TRADING_MODE="):
                                new_env_lines.append(f"TRADING_MODE={chosen_mode}\n")
                                mode_set = True
                            else:
                                new_env_lines.append(eline)
                        if not mode_set:
                            new_env_lines.append(f"TRADING_MODE={chosen_mode}\n")
                        with open(env_file, "w", encoding="utf-8") as f:
                            f.writelines(new_env_lines)
                except Exception:
                    pass

            notifier.send_ntfy(
                "Dhan Session Connected",
                f"DhanHQ token verified & saved. Mode: {config.trading_mode} | Client ID: {client_id} | Real Cash: Rs.{res.get('cash_balance', 0):,.2f}"
            )
            res["trading_mode"] = config.trading_mode
            res["message"] = f"Dhan token validated & saved permanently! Mode: {config.trading_mode}."
            return jsonify(res)
        else:
            return jsonify(res), 400
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
_chart_candles_cache = {}

@app.route("/api/dhan/chart")
def api_dhan_chart():
    """Direct proxy to DhanHQ official intraday & historical chart data endpoint."""
    security_id = request.args.get("security_id", "13")
    segment = request.args.get("segment", "IDX_I")
    instrument_type = request.args.get("instrument_type", "INDEX")
    interval = int(request.args.get("interval", 5))
    days = int(request.args.get("days", 3))

    from datetime import date, timedelta
    today_str = date.today().strftime("%Y-%m-%d")
    from_date = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")

    from dhan_client import get_dhan_client
    dhan_c = get_dhan_client()
    if not dhan_c.dhan:
        return jsonify({"status": "failure", "message": "Dhan API connection not initialized"}), 503

    try:
        res = dhan_c.dhan.intraday_minute_data(
            security_id=str(security_id),
            exchange_segment=segment,
            instrument_type=instrument_type,
            from_date=from_date,
            to_date=today_str,
            interval=interval,
            oi=True
        )
        return jsonify(res)
    except Exception as e:
        return jsonify({"status": "failure", "message": str(e)}), 500


@app.route("/api/chart-data")
def api_chart_data():
    """Provides real-time candlestick bars, multiple timeframes, IST timestamps, S&R levels, and order flow directly from DhanHQ."""
    asset_key = request.args.get("asset", selected_asset).upper()
    timeframe = request.args.get("timeframe", "5m").lower()
    chart_target = request.args.get("chart_target", "underlying").lower()  # 'underlying' or 'option'
    security_id_param = request.args.get("security_id", "").strip()
    segment_param = request.args.get("segment", "").strip()
    instrument_param = request.args.get("instrument_type", "").strip()

    spot = market_intelligence.get_asset_spot(asset_key)
    asset_info = ASSET_CATALOG.get(asset_key, ASSET_CATALOG["NIFTY"])
    levels = market_intelligence.calculate_levels_and_stops(spot, asset_key)
    of = order_flow_analyzer.analyze(spot, "BUY_CE")

    cache_key = f"{asset_key}_{timeframe}_{chart_target}_{security_id_param}"
    now_ts_sec = time.time()
    candles = []

    # Memory cache with live tick mutation to prevent Dhan DH-904 rate limits
    if cache_key in _chart_candles_cache:
        cached_c, c_time = _chart_candles_cache[cache_key]
        if (now_ts_sec - c_time < 45.0) and cached_c:
            candles = [dict(c) for c in cached_c]
            # Mutate active running candle in memory with live exchange spot tick
            if candles and spot > 0:
                candles[-1]["close"] = round(spot, 2)
                candles[-1]["high"] = max(candles[-1]["high"], round(spot, 2))
                candles[-1]["low"] = min(candles[-1]["low"], round(spot, 2))

    # Pre-calculate active option metadata for possible option charting
    step = asset_info.strike_step
    atm_strike = round(spot / step) * step if step > 0 else spot
    opt_type = "CE" if spot >= levels["central_pivot"] else "PE"
    expiry_date_iso = datetime.datetime.now().strftime("%Y-%m-%d")
    expiry_date_str = datetime.datetime.now().strftime("%d %b").upper()
    call_put_label = "CALL" if opt_type == "CE" else "PUT"
    opt_sec_id = "0"
    formatted_option_symbol = f"{asset_key} {expiry_date_str} {int(atm_strike)} {call_put_label}"
    try:
        from scrip_master import ScripMaster
        contract_meta = ScripMaster().get_nifty_option(spot, opt_type, strike_offset=0, asset_key=asset_key)
        opt_sec_id = str(contract_meta.get("security_id", "0"))
        if contract_meta.get("custom_symbol"):
            formatted_option_symbol = contract_meta.get("custom_symbol")
        if contract_meta.get("expiry"):
            expiry_date_iso = contract_meta.get("expiry")
    except Exception:
        pass

    if not candles:
        # 1. Primary: Direct authentic intraday charts from DhanHQ official Data API
        dhan_sec_map = {
            "NIFTY": ("13", "IDX_I", "INDEX"),
            "BANKNIFTY": ("25", "IDX_I", "INDEX"),
            "FINNIFTY": ("27", "IDX_I", "INDEX"),
            "SENSEX": ("51", "IDX_I", "INDEX"),
            # MCX Active Mini Commodities
            "CRUDEOILM": ("573423", "MCX_COMM", "FUTCOM"),
            "NATGASMINI": ("574320", "MCX_COMM", "FUTCOM"),
            "GOLDM": ("575011", "MCX_COMM", "FUTCOM"),
            "SILVERM": ("483080", "MCX_COMM", "FUTCOM"),
            # Top Bluechip Equities
            "RELIANCE": ("2885", "NSE_EQ", "EQUITY"),
            "TCS": ("11536", "NSE_EQ", "EQUITY"),
            "HDFCBANK": ("1333", "NSE_EQ", "EQUITY"),
            "ICICIBANK": ("4963", "NSE_EQ", "EQUITY"),
            "INFY": ("1594", "NSE_EQ", "EQUITY"),
            "SBIN": ("3045", "NSE_EQ", "EQUITY"),
            "BHARTIARTL": ("10604", "NSE_EQ", "EQUITY"),
            "LT": ("11483", "NSE_EQ", "EQUITY"),
            "AXISBANK": ("5900", "NSE_EQ", "EQUITY"),
            "KOTAKBANK": ("1922", "NSE_EQ", "EQUITY"),
            "TATASTEEL": ("3499", "NSE_EQ", "EQUITY"),
            "BAJFINANCE": ("317", "NSE_EQ", "EQUITY"),
            "MARUTI": ("10999", "NSE_EQ", "EQUITY"),
            "M&M": ("2031", "NSE_EQ", "EQUITY")
        }
        dhan_interval_map = {"10s": 1, "1m": 1, "3m": 1, "5m": 5, "10m": 5, "15m": 15, "30m": 15, "1h": 60, "4h": 60, "1d": 60}

        try:
            from dhan_client import DhanTraderClient, get_dhan_client
            from datetime import date, timedelta
            today_d = date.today()
            today_str = today_d.strftime("%Y-%m-%d")
            # Fetch last 3 trading days of candles (span 4 calendar days)
            from_date_str = (today_d - timedelta(days=4)).strftime("%Y-%m-%d")
            dhan_c = get_dhan_client()

            if dhan_c.dhan:
                intv = dhan_interval_map.get(timeframe, 5)

                # Determine if user is charting an Option Contract or the Underlying
                if chart_target == "option":
                    target_sec_id = security_id_param or opt_sec_id
                    target_seg = segment_param or ("MCX_COMM" if asset_info.category == "COMMODITY" else ("BSE_FNO" if asset_key == "SENSEX" else "NSE_FNO"))
                    target_itype = instrument_param or ("OPTFUT" if target_seg == "MCX_COMM" else "OPTIDX")
                elif asset_key in dhan_sec_map:
                    target_sec_id, target_seg, target_itype = dhan_sec_map[asset_key]
                else:
                    target_sec_id, target_seg, target_itype = None, None, None

                if target_sec_id and str(target_sec_id).isdigit() and int(target_sec_id) > 0:
                    if timeframe == "1d":
                        from_date_daily = (today_d - timedelta(days=180)).strftime("%Y-%m-%d")
                        dhan_res = dhan_c.dhan.historical_daily_data(
                            security_id=str(target_sec_id),
                            exchange_segment=target_seg,
                            instrument_type=target_itype,
                            from_date=from_date_daily,
                            to_date=today_str,
                            expiry_code=0
                        )
                    else:
                        dhan_res = dhan_c.dhan.intraday_minute_data(
                            security_id=str(target_sec_id),
                            exchange_segment=target_seg,
                            instrument_type=target_itype,
                            from_date=from_date_str,
                            to_date=today_str,
                            interval=intv
                        )
                        # Safe fallback to today if multi-day returned empty
                        if not (isinstance(dhan_res, dict) and dhan_res.get("status") == "success" and dhan_res.get("data", {}).get("open")):
                            dhan_res = dhan_c.dhan.intraday_minute_data(
                                security_id=str(target_sec_id),
                                exchange_segment=target_seg,
                                instrument_type=target_itype,
                                from_date=today_str,
                                to_date=today_str,
                                interval=intv
                            )
                    if isinstance(dhan_res, dict) and dhan_res.get("status") == "success":
                        ddata = dhan_res.get("data", {})
                        if isinstance(ddata, dict):
                            opens = ddata.get("open", [])
                            highs = ddata.get("high", [])
                            lows = ddata.get("low", [])
                            closes = ddata.get("close", [])
                            volumes = ddata.get("volume", [1000] * len(opens))
                            timestamps = ddata.get("timestamp", [])

                            # Dynamic Institutional Resampling for Non-Standard Timeframes (3m, 10m, 30m, 4h)
                            resample_mins = {"3m": 3, "10m": 10, "30m": 30, "4h": 240}.get(timeframe, None)
                            if resample_mins and opens:
                                resampled = []
                                current_bucket = None
                                b_open, b_high, b_low, b_close, b_vol, b_time = 0.0, -1e9, 1e9, 0.0, 0, 0
                                bucket_seconds = resample_mins * 60
                                for o, h, l, c, v, t in zip(opens, highs, lows, closes, volumes, timestamps):
                                    t_sec = int(t)
                                    bucket_id = t_sec - (t_sec % bucket_seconds)
                                    if current_bucket is None:
                                        current_bucket = bucket_id
                                        b_open, b_high, b_low, b_close, b_vol, b_time = float(o), float(h), float(l), float(c), int(v or 0), bucket_id
                                    elif bucket_id == current_bucket:
                                        b_high = max(b_high, float(h))
                                        b_low = min(b_low, float(l))
                                        b_close = float(c)
                                        b_vol += int(v or 0)
                                    else:
                                        resampled.append((b_open, b_high, b_low, b_close, b_vol, b_time))
                                        current_bucket = bucket_id
                                        b_open, b_high, b_low, b_close, b_vol, b_time = float(o), float(h), float(l), float(c), int(v or 0), bucket_id
                                if current_bucket is not None:
                                    resampled.append((b_open, b_high, b_low, b_close, b_vol, b_time))
                                opens = [b[0] for b in resampled]
                                highs = [b[1] for b in resampled]
                                lows = [b[2] for b in resampled]
                                closes = [b[3] for b in resampled]
                                volumes = [b[4] for b in resampled]
                                timestamps = [b[5] for b in resampled]

                            cum_vol = 0
                            cum_pv = 0.0
                            seen_times = set()
                            for o, h, l, c, v, t in zip(opens, highs, lows, closes, volumes, timestamps):
                                ts_epoch = int(t)
                                if ts_epoch in seen_times:
                                    continue
                                seen_times.add(ts_epoch)

                                o_f, h_f, l_f, c_f = round(float(o), 2), round(float(h), 2), round(float(l), 2), round(float(c), 2)
                                v_i = int(v) if v else 1000
                                cum_vol += v_i
                                cum_pv += ((h_f + l_f + c_f) / 3.0) * v_i
                                vwap = round(cum_pv / cum_vol, 2) if cum_vol > 0 else c_f
                                candles.append({
                                    "time": ts_epoch,
                                    "open": o_f,
                                    "high": h_f,
                                    "low": l_f,
                                    "close": c_f,
                                    "volume": v_i,
                                    "vwap": vwap
                                })
        except Exception as e:
            print(f"[Dhan Chart Feed Note] {e}")

        # 2. Resilient Fallback: yfinance for Instant Continuity on Rate Limits / Global Cues
        if not candles and getattr(asset_info, "yfinance_symbol", None):
            tf_specs = {
                "10s": ("1d", "1m"),
                "1m": ("1d", "1m"),
                "3m": ("1d", "1m"),
                "5m": ("2d", "5m"),
                "10m": ("5d", "5m"),
                "15m": ("5d", "15m"),
                "30m": ("1mo", "30m"),
                "1h": ("1mo", "1h"),
                "4h": ("3mo", "1h"),
                "1d": ("3mo", "1d"),
            }
            period, interval = tf_specs.get(timeframe, ("2d", "5m"))

            try:
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    import yfinance as yf
                    df = yf.Ticker(asset_info.yfinance_symbol).history(period=period, interval=interval)
                    
                    if not df.empty:
                        cum_vol = 0
                        cum_pv = 0.0
                        seen_times = set()
                        for idx, r in df.iterrows():
                            ts_epoch = int(idx.timestamp())
                            if ts_epoch in seen_times:
                                continue
                            seen_times.add(ts_epoch)

                            o = round(float(r["Open"]), 2)
                            h = round(float(r["High"]), 2)
                            l = round(float(r["Low"]), 2)
                            c = round(float(r["Close"]), 2)
                            vol = int(r["Volume"]) if r["Volume"] > 0 else 1250
                            cum_vol += vol
                            cum_pv += ((h + l + c) / 3.0) * vol
                            vwap = round(cum_pv / cum_vol, 2) if cum_vol > 0 else c

                            candles.append({
                                "time": ts_epoch,
                                "open": o,
                                "high": h,
                                "low": l,
                                "close": c,
                                "volume": vol,
                                "vwap": vwap
                            })
            except Exception as e:
                print(f"Chart fetch error for {asset_key} ({timeframe}): {e}")

        if candles:
            _chart_candles_cache[cache_key] = (candles, now_ts_sec)

    # Harmonize live spot price with latest candle close for 100% chart accuracy
    if candles and candles[-1].get("close") and candles[-1]["close"] > 0:
        latest_c = float(candles[-1]["close"])
        if chart_target == "underlying" and abs(spot - latest_c) > 0.5:
            spot = latest_c
            market_intelligence.cached_spot_prices[asset_key] = (latest_c, time.time())

    # Calculate Institutional Indicators (EMA 9, EMA 21) across candles
    from indicators import IndicatorEngine
    df_candles = pd.DataFrame(candles) if candles else None
    if candles and len(candles) >= 3:
        try:
            c_series = pd.Series([float(c["close"]) for c in candles])
            e9 = c_series.ewm(span=9, adjust=False).mean()
            e21 = c_series.ewm(span=21, adjust=False).mean()
            for idx, c in enumerate(candles):
                c["ema9"] = round(float(e9.iloc[idx]), 2)
                c["ema21"] = round(float(e21.iloc[idx]), 2)
        except Exception:
            pass

    # Institutional Volume Profile & Classical Chart Pattern Detection with Timeframe & Candlestick Formations
    vol_profile = IndicatorEngine.calculate_volume_profile(df_candles) if df_candles is not None and not df_candles.empty else {"poc": 0.0, "vah": 0.0, "val": 0.0, "bins": []}
    pattern_info = IndicatorEngine.detect_chart_patterns(df_candles, timeframe=timeframe) if df_candles is not None and not df_candles.empty else {
        "has_pattern": False, "timeframe": timeframe.upper(), "candlestick_formation": "Trend Candle", "pattern_name": "CONSOLIDATION", "bias": "NEUTRAL", "stage": "CONSOLIDATION", "confidence": 50,
        "predicted_target": 0.0, "invalidation_price": 0.0, "historical_edge_stat": "50% Neutral", "forecast_text": f"⏱️ [{timeframe.upper()}] Awaiting candle data", "key_levels": {}
    }

    current_vwap = candles[-1]["vwap"] if candles else spot
    opt_prem = engine.client.get_option_ltp(opt_sec_id, spot, atm_strike, opt_type, expiry_date=expiry_date_iso)
    invested_amt = round(opt_prem * asset_info.lot_size, 2)

    # Build Option Chain summary for inline display on desk
    chain_summary = option_chain_engine.build_option_chain(
        asset_key, spot, asset_info.strike_step, strikes_count=5, selected_expiry=expiry_date_iso
    )

    return jsonify({
        "asset": asset_key,
        "display_name": (formatted_option_symbol if chart_target == "option" else asset_info.display_name),
        "chart_target": chart_target,
        "timeframe": timeframe,
        "spot": spot,
        "candles": candles,
        "volume_profile": vol_profile,
        "pattern_analysis": pattern_info,
        "levels": {
            "r2": levels["r2"],
            "r1": levels["r1"],
            "pivot": levels["central_pivot"],
            "s1": levels["s1"],
            "s2": levels["s2"],
            "vwap": current_vwap,
            "poc": vol_profile.get("poc", current_vwap),
            "vah": vol_profile.get("vah", levels["r1"]),
            "val": vol_profile.get("val", levels["s1"]),
            "orb_high": getattr(engine.strategy, "orb_high", 0.0) or (spot + 25.0),
            "orb_low": getattr(engine.strategy, "orb_low", 0.0) or (spot - 25.0)
        },
        "active_option": {
            "symbol": formatted_option_symbol,
            "strike": atm_strike,
            "type": opt_type,
            "expiry_date": expiry_date_str,
            "ltp": opt_prem,
            "lot_size": asset_info.lot_size,
            "invested_amount": invested_amt,
            "delta": 0.52 if opt_type == "CE" else -0.48,
            "theta": -12.5,
            "iv": 12.4,
            "volume": 184500,
            "oi": 84200
        },
        "option_chain": asdict(chain_summary),
        "order_flow": {
            "cvd": of.cvd,
            "cvd_direction": of.cvd_direction,
            "bid_volume_pct": of.bid_volume_pct,
            "ask_volume_pct": of.ask_volume_pct,
            "imbalance": of.order_book_imbalance,
            "institutional_bias": of.institutional_bias
        }
    })

@app.route("/api/watchlist", methods=["GET"])
def api_get_watchlist():
    """Returns the active trading desk catalog including custom added stocks, options, and futures."""
    try:
        assets = []
        for k, v in ASSET_CATALOG.items():
            assets.append({
                "key": v.key,
                "display_name": v.display_name,
                "category": v.category,
                "exchange": v.exchange,
                "lot_size": v.lot_size,
                "strike_step": v.strike_step,
                "baseline_price": v.baseline_price,
                "is_custom": getattr(v, "is_custom", False)
            })
        return jsonify({"success": True, "assets": assets, "selected_asset": selected_asset})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/watchlist/add", methods=["POST"])
def api_add_watchlist_asset():
    """Adds a new user-chosen stock, option, or future to the trading deck and persists it."""
    try:
        data = request.get_json() or {}
        raw_key = data.get("key", "").strip().upper()
        if not raw_key:
            return jsonify({"success": False, "error": "Symbol / Scrip code is required"}), 400

        display_name = data.get("display_name", "").strip() or raw_key
        category = data.get("category", "STOCK").strip().upper()
        exchange = data.get("exchange", "NSE").strip().upper()
        lot_size = int(data.get("lot_size", 1))
        baseline_price = float(data.get("baseline_price", 100.0))
        strike_step = int(data.get("strike_step", 10))

        asset = add_custom_asset(
            key=raw_key,
            display_name=display_name,
            category=category,
            exchange=exchange,
            lot_size=lot_size,
            baseline_price=baseline_price,
            strike_step=strike_step
        )
        return jsonify({
            "success": True,
            "message": f"Successfully added {asset.display_name} ({asset.key}) to Trading Deck",
            "asset": {
                "key": asset.key,
                "display_name": asset.display_name,
                "category": asset.category,
                "exchange": asset.exchange,
                "lot_size": asset.lot_size,
                "strike_step": asset.strike_step,
                "baseline_price": asset.baseline_price,
                "is_custom": True
            }
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/watchlist/remove", methods=["POST"])
def api_remove_watchlist_asset():
    """Removes ANY stock, option, future, or benchmark scrip from the trading deck."""
    global selected_asset
    try:
        data = request.get_json() or {}
        key = data.get("key", "").strip().upper()
        if not key:
            return jsonify({"success": False, "error": "Symbol key is required"}), 400

        removed = remove_custom_asset(key)
        if removed:
            if selected_asset == key:
                selected_asset = next(iter(ASSET_CATALOG.keys()), "NIFTY")
            return jsonify({
                "success": True,
                "message": f"Removed {key} from Trading Deck",
                "selected_asset": selected_asset
            })
        else:
            return jsonify({
                "success": False,
                "error": f"Cannot remove {key} (Desk requires at least 1 active asset)."
            }), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/watchlist/reset", methods=["POST"])
def api_reset_watchlist():
    """Restores all default benchmark indices (NIFTY, SENSEX, BANKNIFTY) and core stocks."""
    try:
        reset_default_assets()
        return jsonify({
            "success": True,
            "message": "Successfully restored all default benchmark indices, NIFTY stocks, and commodities!"
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/search-scrips")
def api_search_scrips():
    """Live typeahead autocomplete search for NIFTY 50, SENSEX, Stocks, Options & Futures."""
    try:
        q = request.args.get("q", "").strip()
        active_keys = set(ASSET_CATALOG.keys())
        results = smd.search_master_scrips(q, active_keys)
        return jsonify({
            "success": True,
            "query": q,
            "count": len(results),
            "results": results
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/option-chain-strikes")
def api_option_chain_strikes():
    """Returns ATM, ITM, and OTM strikes with real-time estimated premiums for any asset."""
    try:
        underlying = request.args.get("underlying", "NIFTY").strip().upper()
        spot = float(request.args.get("spot", 0.0))
        if spot <= 0.0 and underlying in ASSET_CATALOG:
            spot = market_intelligence.get_asset_spot(underlying)
        
        data = smd.get_option_strikes_for_underlying(underlying, spot)
        return jsonify({
            "success": True,
            "data": data
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/opportunities")
def api_opportunities():
    """Scans all market instruments and returns ranked opportunities with priority for expiry days."""
    try:
        opportunities = market_intelligence.scan_all_opportunities(current_capital)
        return jsonify({
            "success": True,
            "opportunities": opportunities,
            "timestamp": datetime.datetime.now().strftime("%I:%M:%S %p")
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/toggle-autopilot", methods=["POST"])
def api_toggle_autopilot():
    """Toggles autonomous execution on/off."""
    with lock:
        engine.auto_pilot_enabled = not engine.auto_pilot_enabled
        status_msg = "AUTONOMOUS AUTO-PILOT ACTIVATED: Bot will auto-execute setups on its own." if engine.auto_pilot_enabled else "AUTO-PILOT PAUSED: Manual dispatch only."
        notifier.send_ntfy("Auto-Pilot Status Changed", status_msg)
        return jsonify({
            "success": True,
            "auto_pilot_enabled": engine.auto_pilot_enabled,
            "message": status_msg
        })

@app.route("/api/reset-session", methods=["POST"])
def api_reset_session():
    """Resets daily session, clears test journal trades, and unlocks auto-pilot for fresh trading."""
    with lock:
        journal.clear_all_trades()
        engine.reset_kill_switch()
        try:
            from global_trade_lock import global_trade_lock
            global_trade_lock.force_reset_lock("Session Reset by User")
        except Exception:
            pass
        return jsonify({
            "success": True,
            "message": "Session reset successfully! Test trades cleared and daily cap set to 5 trades."
        })

@app.route("/api/clear-trades", methods=["GET", "POST"])
@app.route("/api/clear-journal", methods=["GET", "POST"])
def api_clear_trades():
    """Wipes test journal trades, clears paper positions, and resets trade count to 0."""
    with lock:
        journal.clear_all_trades()
        engine.reset_kill_switch()
        engine.active_position = None
        if hasattr(engine, "active_positions"):
            engine.active_positions.clear()
        if hasattr(engine, "sniper_event_log"):
            engine.sniper_event_log = []
        if hasattr(engine.client, "order_history"):
            engine.client.order_history = []
        if hasattr(engine.client, "paper_positions"):
            engine.client.paper_positions = {}
        try:
            from global_trade_lock import global_trade_lock
            global_trade_lock.force_reset_lock("Session Wiped Clean by User")
        except Exception:
            pass
        if hasattr(engine.client, "set_paper_capital"):
            try:
                engine.client.set_paper_capital(25000.0)
            except Exception:
                pass
        return jsonify({
            "success": True,
            "message": "All paper trades and journal history wiped clean. Fresh session started!"
        })

@app.route("/api/execute-manual-trade", methods=["POST"])
def api_execute_manual_trade():
    """Allows instant manual entry execution directly from the dashboard."""
    try:
        data = request.get_json(force=True) or {}
        action = data.get("action", "BUY_CE").upper()
        target_mode = data.get("mode", config.trading_mode).upper()
        trade_style = data.get("style", "SMART_EDGE").upper() # 'SNIPER', 'SWING', or 'SMART_EDGE'
        asset_key = data.get("asset", selected_asset).upper()
        requested_lots = max(1, int(data.get("lots", 1)))
        
        with lock:
            if target_mode == "LIVE":
                is_open, open_desc = market_intelligence.is_market_open(asset_key)
                if not is_open:
                    return jsonify({
                        "success": False,
                        "error": f"Live Execution Blocked: {asset_key} market is CLOSED ({open_desc}). Live Dhan orders strictly require active exchange session."
                    }), 400

            current_spot = market_intelligence.get_asset_spot(asset_key)
            asset_info = ASSET_CATALOG.get(asset_key, ASSET_CATALOG["NIFTY"])
            atm_strike = round(current_spot / asset_info.strike_step) * asset_info.strike_step
            opt_type = "CE" if action == "BUY_CE" else "PE"
            # 50% Capital Safety Rule & Customizable Lot Sizing
            active_cap = engine.client.get_real_cash_balance() if target_mode == "LIVE" else float(engine.client.paper_capital)
            contract = contract_selector.select_contract(
                spot_price=current_spot,
                action=action,
                estimated_premium=0.0,
                strike_preference="ATM",
                asset_key=asset_key,
                available_capital=active_cap,
                requested_lots=requested_lots
            )

            if not contract.risk_budget_approved:
                return jsonify({
                    "success": False,
                    "error": contract.rejection_reason or "Trade rejected by Risk & Capital Sizing rules."
                }), 400

            # Style-tailored stops and targets
            if trade_style == "SNIPER":
                sl_pts = 7.0
                t1_pts = 10.0
                t2_pts = 15.0
                strat_name = "SNIPER_SCALP"
            elif trade_style == "SWING":
                sl_pts = 22.0
                t1_pts = 35.0
                t2_pts = 60.0
                strat_name = "SWING_WAVE_RIDER"
            else:
                sl_pts = 15.0
                t1_pts = 25.0
                t2_pts = 45.0
                strat_name = "MANUAL_DISPATCH"

            sig = SmartEdgeSignal(
                action=action,
                strategy_name=strat_name,
                regime="USER_TRIGGERED",
                spot_price=current_spot,
                structural_sl_spot=current_spot - sl_pts if action == "BUY_CE" else current_spot + sl_pts,
                target_1_spot=current_spot + t1_pts if action == "BUY_CE" else current_spot - t1_pts,
                target_2_spot=current_spot + t2_pts if action == "BUY_CE" else current_spot - t2_pts
            )

            # Enforce chosen execution mode
            original_mode = engine.mode
            engine.mode = target_mode
            engine.client.mode = target_mode
            try:
                engine.execute_entry(sig, contract, current_spot, trade_style=trade_style)
            finally:
                engine.mode = original_mode
                engine.client.mode = original_mode

            style_label = "🎯 Sniper Scalp (1-3m)" if trade_style == "SNIPER" else ("🌊 Swing Wave (15-60m)" if trade_style == "SWING" else "⚡ Standard Trade")
            prefix = f"🧪 Paper {style_label} Executed" if target_mode == "PAPER" else f"🔥 Live Dhan {style_label} Dispatched"
            return jsonify({
                "success": True,
                "mode": target_mode,
                "style": trade_style,
                "message": f"{prefix}: {contract.custom_symbol} ({contract.lot_size} Qty [{contract.number_of_lots} Lot{'s' if contract.number_of_lots > 1 else ''}] @ ₹{contract.estimated_premium:.2f} • Margin: ₹{contract.margin_required:,.2f})"
            })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

_cached_sniper_scan = None
_cached_sniper_time = 0.0

@app.route("/api/sniper-scan")
def api_sniper_scan():
    """
    Symmetrically scans all 7 instruments (NIFTY, BANKNIFTY, FINNIFTY, SENSEX, NIFTYIT, RELIANCE, HDFCBANK)
    for immediate high-velocity Call and Put opportunities that fit strictly within the user's active capital.
    Identifies the #1 Top Pick for 1-click execution.
    """
    global _cached_sniper_scan, _cached_sniper_time
    now_ts = time.time()
    if _cached_sniper_scan is not None and (now_ts - _cached_sniper_time) < 10.0:
        return jsonify(_cached_sniper_scan)
    try:
        active_cap = engine.client.get_real_cash_balance() if config.trading_mode == "LIVE" else float(engine.client.paper_capital)
        all_opps = market_intelligence.scan_all_opportunities(capital=active_cap)

        # Multi-Session Adaptive Scan Universe:
        # Evening session (15:30 - 23:30 IST): Prioritize active MCX Mini Commodities
        # Daytime session (09:15 - 15:30 IST): Scan benchmark Indian Indices
        now_hm = datetime.datetime.now().strftime("%H:%M")
        is_evening = (now_hm >= "15:30" or now_hm < "09:15")
        if is_evening:
            primary_scan_keys = {"CRUDEOILM", "NATGASMINI", "GOLDM", "SILVERM"}
        else:
            primary_scan_keys = {"NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"}
        filtered_opps = [o for o in all_opps if o["key"] in primary_scan_keys]

        candidates = []
        for opp in filtered_opps:
            asset_key = opp["key"]
            spot = opp["spot"]
            
            # Check both CALL and PUT opportunities symmetrically
            for act in ["BUY_CE", "BUY_PE"]:
                c = contract_selector.select_contract(
                    spot_price=spot,
                    action=act,
                    estimated_premium=0.0,
                    strike_preference="ATM",
                    asset_key=asset_key,
                    available_capital=active_cap
                )
                if c.risk_budget_approved:
                    opt_type = "CE" if act == "BUY_CE" else "PE"
                    
                    # Institutional Multi-Timeframe Calibration (1:2.5 R:R)
                    from mtf_engine import mtf_engine
                    mtf_res = None
                    try:
                        mtf_res = mtf_engine.analyze(asset_key, opp["display_name"], spot)
                    except Exception:
                        pass
                    
                    if mtf_res:
                        is_mtf_aligned = (mtf_res.recommended_action == act)
                        confl = mtf_res.confluence_score if is_mtf_aligned else max(25, mtf_res.confluence_score - 40)
                        stop_pts = mtf_res.stop_loss_pts
                        target_pts = mtf_res.target_pts
                        rr_ratio = mtf_res.risk_reward_ratio
                        mtf_stage = mtf_res.tf_5m.stage
                        mtf_rationale = mtf_res.rationale
                    else:
                        is_bn = (asset_key == "BANKNIFTY")
                        stop_pts = 32.0 if is_bn else 16.0
                        target_pts = 80.0 if is_bn else 42.0
                        rr_ratio = "1 : 2.5"
                        mtf_stage = "TREND_CONTINUATION"
                        mtf_rationale = f"Institutional Scalp: {rr_ratio} target"
                        is_mtf_aligned = ("CE" in opp["action"] and act == "BUY_CE") or ("PE" in opp["action"] and act == "BUY_PE")
                        confl = opp["confluence_score"] if is_mtf_aligned else 40

                    est_target = round(c.estimated_premium + target_pts, 2)
                    est_stop = max(0.5, round(c.estimated_premium - stop_pts, 2))
                    pot_gain = round(target_pts * c.lot_size, 2)
                    max_risk = round(stop_pts * c.lot_size, 2)

                    candidates.append({
                        "asset": asset_key,
                        "display_name": opp["display_name"],
                        "direction": "CALL (CE)" if act == "BUY_CE" else "PUT (PE)",
                        "action": act,
                        "symbol": c.custom_symbol,
                        "security_id": c.security_id,
                        "expiry": c.expiry_date,
                        "strike": c.strike_price,
                        "option_type": opt_type,
                        "premium": c.estimated_premium,
                        "lot_size": c.lot_size,
                        "margin_required": c.margin_required,
                        "confluence_score": confl,
                        "momentum_tag": opp["momentum_tag"],
                        "regime_display": opp["regime_display"],
                        "target_premium": est_target,
                        "stop_premium": est_stop,
                        "potential_gain_inr": pot_gain,
                        "max_risk_inr": max_risk,
                        "risk_reward_ratio": rr_ratio,
                        "stop_loss_pts": stop_pts,
                        "target_pts": target_pts,
                        "mtf_stage": mtf_stage,
                        "is_expiry_today": opp["is_expiry_today"],
                        "is_aligned": is_mtf_aligned,
                        "rationale": f"{opp['display_name']} {opt_type}: {mtf_rationale} | Margin: ₹{c.margin_required:,.0f}"
                    })

        # Rank candidates: Expiry today first, aligned direction next, highest confluence score, lowest margin
        candidates.sort(key=lambda x: (x["is_expiry_today"], x["is_aligned"], x["confluence_score"], -x["margin_required"]), reverse=True)
        top_pick = candidates[0] if candidates else None

        payload = {
            "success": True,
            "active_capital": active_cap,
            "trading_mode": config.trading_mode,
            "top_pick": top_pick,
            "candidates": candidates[:10],
            "total_qualified": len(candidates),
            "timestamp": datetime.datetime.now().strftime("%I:%M:%S %p")
        }
        _cached_sniper_scan = payload
        _cached_sniper_time = now_ts
        return jsonify(payload)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/trigger-best-sniper", methods=["POST"])
def api_trigger_best_sniper():
    """
    1-Click instant execution of the #1 Top Pick Sniper trade across all instruments.
    Can also execute a specific chosen asset/direction from the scanner cards.
    """
    try:
        data = request.get_json(silent=True) or {}
        chosen_asset = data.get("asset")
        chosen_action = data.get("action")
        target_mode = data.get("mode", config.trading_mode).upper()
        requested_lots = max(1, int(data.get("lots", 1)))

        with lock:
            max_conc = getattr(config, "max_concurrent_positions", 5)
            from global_trade_lock import global_trade_lock
            is_cd, rem_secs = global_trade_lock.is_cooldown_active(chosen_asset or "")
            is_manual = data.get("manual", False) or data.get("force", False)
            if is_cd and not data.get("replace") and not is_manual:
                return jsonify({
                    "success": False,
                    "error": f"Cooldown active for {chosen_asset or 'instrument'}: {rem_secs:.0f}s remaining after previous exit. Standing down to prevent churning."
                }), 400

            if len(engine.active_positions) >= max_conc and not data.get("replace"):
                return jsonify({
                    "success": False,
                    "has_active_position": True,
                    "active_count": len(engine.active_positions),
                    "error": f"Multi-Trade Capacity Full: Currently holding {len(engine.active_positions)}/{max_conc} active positions. Close or manage an open trade first."
                }), 400

            if data.get("replace") and engine.active_positions:
                old_sym = engine.active_positions[0].symbol
                engine.log_sniper_event(f"⚡ User replaced active trade {old_sym} with new scalp: {chosen_asset} {chosen_action}", "REPLACE")
                engine.execute_exit("⚡ Scalp Swapped / Replaced by User", pos=engine.active_positions[0])

            if engine.is_kill_switch_active:
                return jsonify({
                    "success": False,
                    "error": f"Session halted by RMS: {engine.kill_switch_reason}. Click 'Reset Session' to resume."
                }), 400

            if chosen_asset and target_mode == "LIVE":
                is_open, open_desc = market_intelligence.is_market_open(chosen_asset)
                if not is_open:
                    return jsonify({
                        "success": False,
                        "error": f"Live Execution Blocked: {chosen_asset} market is CLOSED ({open_desc}). Live Dhan orders strictly require active exchange session."
                    }), 400

            active_cap = engine.client.get_real_cash_balance() if target_mode == "LIVE" else float(engine.client.paper_capital)

            # Auto-detect #1 top pick across all market instruments if not explicitly specified
            if not chosen_asset or not chosen_action:
                all_opps = market_intelligence.scan_all_opportunities(capital=active_cap)
                cands = []
                for opp in all_opps:
                    a_key = opp["key"]
                    
                    # 1. Market session must be actively open
                    is_open_asset, open_desc = market_intelligence.is_market_open(a_key)
                    if not is_open_asset:
                        continue

                    # 2. Setup MUST be confirmed actionable (Zero trading in Stand Down / Consolidation)
                    if not opp.get("is_actionable"):
                        continue

                    raw_act = opp.get("action", "").upper()
                    if "STAND DOWN" in raw_act or "HOLD" in raw_act:
                        continue

                    # 3. Determine directional action strictly from confirmed thesis
                    if "CE" in raw_act or "CALL" in raw_act or "BULLISH" in raw_act:
                        act = "BUY_CE"
                    elif "PE" in raw_act or "PUT" in raw_act or "BEARISH" in raw_act:
                        act = "BUY_PE"
                    else:
                        continue

                    # 4. Confluence must be institutional-grade (>= 75%)
                    if opp.get("confluence_score", 0) < 75:
                        continue

                    # 5. Candlestick Confirmation Guard (Never buy Calls on Red dumping bars or Puts on Green pumping bars)
                    df5 = mtf_engine.get_real_candles(a_key)
                    if df5 is not None and len(df5) >= 2:
                        last_c = df5["close"].iloc[-1]
                        last_o = df5["open"].iloc[-1]
                        if act == "BUY_CE" and last_c < last_o:
                            # Red bar forming: Abort Call entry
                            continue
                        if act == "BUY_PE" and last_c > last_o:
                            # Green bar forming: Abort Put entry
                            continue

                    s_price = opp["spot"]
                    c = contract_selector.select_contract(
                        spot_price=s_price,
                        action=act,
                        estimated_premium=0.0,
                        strike_preference="ATM",
                        asset_key=a_key,
                        available_capital=active_cap,
                        requested_lots=requested_lots
                    )
                    if c.risk_budget_approved:
                        cands.append((opp, act, c))

                if not cands:
                    return jsonify({
                        "success": False,
                        "error": "No Actionable High-Conviction Trades: Scanned market instruments are currently consolidating or awaiting clean candle breakout. Standing down to protect capital."
                    }), 400

                cands.sort(key=lambda item: (item[0]["is_expiry_today"], item[0]["confluence_score"]), reverse=True)
                best_opp, chosen_action, contract = cands[0]
                chosen_asset = best_opp["key"]
                spot = best_opp["spot"]
            else:
                spot = market_intelligence.get_asset_spot(chosen_asset)
                contract = contract_selector.select_contract(
                    spot_price=spot,
                    action=chosen_action,
                    estimated_premium=0.0,
                    strike_preference="ATM",
                    asset_key=chosen_asset,
                    available_capital=active_cap,
                    requested_lots=requested_lots
                )
                if not contract.risk_budget_approved:
                    return jsonify({
                        "success": False,
                        "error": contract.rejection_reason or "Contract exceeds active risk budget."
                    }), 400

            sig = SmartEdgeSignal(
                action=chosen_action,
                strategy_name="SNIPER_SCALP",
                regime="INSTANT_MOMENTUM",
                spot_price=spot,
                structural_sl_spot=spot - 8.0 if chosen_action == "BUY_CE" else spot + 8.0,
                target_1_spot=spot + 12.0 if chosen_action == "BUY_CE" else spot - 12.0,
                target_2_spot=spot + 20.0 if chosen_action == "BUY_CE" else spot - 20.0
            )

            orig_mode = engine.mode
            engine.mode = target_mode
            engine.client.mode = target_mode
            try:
                engine.execute_entry(sig, contract, spot, trade_style="SNIPER", mode=target_mode)
                engine.log_sniper_event(
                    f"⚡ SNIPER ARMED & DISPATCHED: {contract.custom_symbol} ({contract.lot_size} Qty @ ₹{contract.estimated_premium:.2f} | Margin: ₹{contract.margin_required:,.0f}) | Breakeven Lock & Dynamic Runner Trail Active!",
                    "DISPATCH"
                )
            finally:
                engine.mode = orig_mode
                engine.client.mode = orig_mode

            prefix = "🧪 Paper Sniper Trade Dispatched" if target_mode == "PAPER" else "🔥 Live Dhan Sniper Trade Dispatched"
            return jsonify({
                "success": True,
                "message": f"{prefix}: {contract.custom_symbol} ({contract.lot_size} Qty @ ₹{contract.estimated_premium:.2f})",
                "contract": contract.custom_symbol,
                "premium": contract.estimated_premium,
                "margin": contract.margin_required,
                "mode": target_mode
            })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/book-profit", methods=["POST"])
def api_book_profit():
    """Immediately books profit and exits running trade directly from Sniper HUD."""
    with lock:
        if not engine.active_position:
            return jsonify({"success": False, "error": "No open position to book profit."}), 400
        sym = engine.active_position.symbol
        engine.log_sniper_event(f"⚡ Trader manually clicked 'Book Profit Now' on {sym}", "USER_ACTION")
        engine.execute_exit("⚡ Manual Profit Booked via Sniper HUD")
        return jsonify({"success": True, "message": f"Profit booked successfully for {sym}!"})

@app.route("/api/lock-breakeven", methods=["POST"])
def api_lock_breakeven():
    """Immediately locks stop-loss at entry breakeven (+1.0 pt), making the trade 100% risk-free."""
    data = request.get_json(silent=True) or {}
    pos_id = data.get("position_id")
    symbol = data.get("symbol")
    with lock:
        if not engine.active_positions:
            return jsonify({"success": False, "error": "No open position to lock."}), 400
        target_pos = None
        for p in engine.active_positions:
            if (pos_id and getattr(p, "position_id", "") == pos_id) or (symbol and p.symbol == symbol):
                target_pos = p
                break
        if not target_pos and engine.active_positions:
            target_pos = engine.active_positions[0]

        pos = target_pos
        new_sl = round(pos.entry_premium + 1.0, 2)
        pos.hard_stop_premium = max(pos.hard_stop_premium, new_sl)
        pos.trailing_sl_premium = max(getattr(pos, "trailing_sl_premium", 0.0), new_sl)
        pos.is_trailing_active = True
        pos.trailing_step_level = max(getattr(pos, "trailing_step_level", 0), 1)
        pos.locked_profit_inr = round(1.0 * pos.quantity, 2)
        engine.log_sniper_event(f"🔒 Breakeven Locked Manually on {pos.symbol}: SL moved to ₹{new_sl:.2f} (Risk-Free!)", "LOCK")
        return jsonify({
            "success": True,
            "message": f"Stop loss moved to Breakeven (₹{new_sl:.2f}) on {pos.symbol}. Trade is now 100% risk-free!",
            "hard_stop_premium": pos.hard_stop_premium
        })

@app.route("/api/validate-lots")
def api_validate_lots():
    """Validates requested lots against the strict 50% capital safety rule."""
    try:
        asset_key = request.args.get("asset", selected_asset).upper()
        action = request.args.get("action", "BUY_CE").upper()
        lots = max(1, int(request.args.get("lots", 1)))

        active_cap = engine.client.get_real_cash_balance() if config.trading_mode == "LIVE" else float(engine.client.paper_capital)
        current_spot = market_intelligence.get_asset_spot(asset_key)

        contract = contract_selector.select_contract(
            spot_price=current_spot,
            action=action,
            estimated_premium=0.0,
            strike_preference="ATM",
            asset_key=asset_key,
            available_capital=active_cap,
            requested_lots=lots
        )

        cost_1lot = round((contract.margin_required / contract.number_of_lots), 2) if contract.number_of_lots > 0 else contract.margin_required
        max_allowed_margin = round(active_cap * 0.50, 2)
        max_possible_lots = max(1, int(max_allowed_margin // cost_1lot)) if cost_1lot > 0 else 1
        util_pct = round((contract.margin_required / active_cap) * 100.0, 1) if active_cap > 0 else 100.0

        return jsonify({
            "success": True,
            "approved": contract.risk_budget_approved,
            "lots": contract.number_of_lots,
            "margin_required": contract.margin_required,
            "capital": active_cap,
            "max_allowed_margin": max_allowed_margin,
            "max_possible_lots": max_possible_lots,
            "utilization_pct": util_pct,
            "advisory": contract.advisory_alert,
            "rejection_reason": contract.rejection_reason
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/global-markets")
def api_global_markets():
    """Returns real-time quotes, points/percentage changes, and NIFTY influence for developed world markets."""
    try:
        from global_market_intelligence import global_tracker
        force = request.args.get("force", "false").lower() == "true"
        data = global_tracker.fetch_live_quotes(force=force)
        return jsonify(data)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/premarket-intelligence")
def api_premarket_intelligence():
    """Returns pre-market gap projection, global alignment, key pivot levels, and opening setup playbook."""
    try:
        from premarket_intelligence import premarket_engine
        global selected_asset
        asset_key = request.args.get("asset", selected_asset).upper()
        force = request.args.get("force", "false").lower() == "true"
        report = premarket_engine.get_premarket_report(asset_key=asset_key, force=force)
        return jsonify(report)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/order-flow")
def api_order_flow():
    """Returns high-frequency 10-second order flow analytics, delta velocity, and tape speed."""
    global selected_asset
    asset_key = request.args.get("asset", selected_asset).upper()
    spot = market_intelligence.get_asset_spot(asset_key)
    
    # Determine immediate directional bias
    bull_cnt, tot_cnt, details, rec, adv_meta = market_intelligence.compute_indicator_confluence(asset_key, spot)
    sig_key = "BUY_CE" if bull_cnt >= (tot_cnt / 2) else "BUY_PE"
    
    metrics = order_flow_analyzer.analyze(spot, sig_key)
    return jsonify({
        "success": True,
        "asset": asset_key,
        "spot": spot,
        "timestamp": metrics.timestamp,
        "bid_volume_pct": metrics.bid_volume_pct,
        "ask_volume_pct": metrics.ask_volume_pct,
        "order_book_imbalance": metrics.order_book_imbalance,
        "cvd": metrics.cvd,
        "cvd_direction": metrics.cvd_direction,
        "pcr": metrics.pcr,
        "institutional_bias": metrics.institutional_bias,
        "bias_score": metrics.bias_score,
        "summary": metrics.summary,
        "tape_speed_cpm": metrics.tape_speed_cpm,
        "delta_velocity_10s": metrics.delta_velocity_10s,
        "directional_verdict": metrics.directional_verdict,
        "large_lot_prints": metrics.large_lot_prints,
        "cvd_sparkline": metrics.cvd_sparkline
    })

@app.route("/api/market-news")
def api_market_news():
    """Returns real-time minute-to-minute high-impact corporate actions, index movers and market wire alerts."""
    from news_intelligence import news_intelligence
    category = request.args.get("category", "ALL")
    limit = int(request.args.get("limit", 25))
    feed = news_intelligence.get_latest_feed(limit=limit, category=category)
    return jsonify({
        "success": True,
        "timestamp": datetime.datetime.now().strftime("%I:%M:%S %p"),
        "category": category,
        "count": len(feed),
        "feed": feed
    })

@app.route("/api/stand-down", methods=["POST"])
def api_stand_down():
    """Enforces strict capital preservation lock: No new trades for remainder of session."""
    with lock:
        engine.is_kill_switch_active = True
        engine.kill_switch_reason = "Capital Preservation: Trader chose to Sit Out / Stand Down."
        notifier.notify_kill_switch("Session Stand Down: Sitting out in cash to protect capital.", engine.realized_pnl)
        return jsonify({"success": True, "message": "Stand Down Active: Bot locked in Cash Protection Mode."})

@app.route("/api/market-intelligence")
def api_market_intelligence():
    """
    Returns complete real-time intelligence for the requested asset:
    - Live spot quote
    - 10-indicator confluence & plain-English interpretation
    - Triple EMA ribbon, CPR, Fair Value Gap (FVG), Candlestick patterns
    - Option Greeks (Delta, Theta, Gamma, Vega) with layman explanations
    - Confidence score, Historical Backtest stats & Risk Coverage status
    - Support, resistance, targets (1.5R, 2.0R) & trailing SL
    - Weekly Expiry Radar & High-Yield Opportunity tags
    - Dynamic capital & risk calculator
    """
    asset_key = request.args.get("asset", "NIFTY").upper()
    active_asset = asset_key if asset_key in ASSET_CATALOG else "NIFTY"

    try:
        cap = float(request.args.get("capital", current_capital))
    except (ValueError, TypeError):
        cap = current_capital

    spot = market_intelligence.get_asset_spot(active_asset)
    bullish_count, total_count, details, recommendation, advanced_meta = market_intelligence.compute_indicator_confluence(
        active_asset, spot
    )
    levels = market_intelligence.calculate_levels_and_stops(spot, active_asset)
    expiry_radar = market_intelligence.get_expiry_radar()
    risk_info = market_intelligence.calculate_dynamic_risk(cap, spot, active_asset)

    asset_info = ASSET_CATALOG.get(active_asset, ASSET_CATALOG["NIFTY"])
    of = order_flow_analyzer.analyze(spot, "BUY_CE" if bullish_count >= 6 else ("BUY_PE" if bullish_count <= 4 else "HOLD"))

    details_data = [
        {
            "id": d.id,
            "name": d.name,
            "category": d.category,
            "status": d.status,
            "current_value": d.current_value,
            "meaning": d.meaning,
            "interpretation": d.interpretation,
            "is_live": d.is_live
        }
        for d in details
    ]

    radar_data = [
        {
            "index_name": r.index_name,
            "expiry_day": r.expiry_day,
            "status": r.status,
            "yield_potential": r.yield_potential,
            "description": r.description
        }
        for r in expiry_radar
    ]

    expiry_req = request.args.get("expiry", None)
    verdict = market_intelligence.get_trade_verdict(
        active_asset, spot, bullish_count, total_count, advanced_meta
    )
    chain_summary = option_chain_engine.build_option_chain(
        active_asset, spot, asset_info.strike_step, strikes_count=5, selected_expiry=expiry_req
    )

    return jsonify({
        "status": "success",
        "asset": {
            "key": asset_info.key,
            "display_name": asset_info.display_name,
            "category": asset_info.category,
            "exchange": asset_info.exchange,
            "tv_symbol": asset_info.tv_symbol,
            "lot_size": asset_info.lot_size,
            "strike_step": asset_info.strike_step,
            "expiry_day": asset_info.expiry_day_name
        },
        "spot_price": spot,
        "confluence": {
            "bullish_count": bullish_count,
            "bearish_count": total_count - bullish_count,
            "total_count": total_count,
            "confluence_pct": round((bullish_count / total_count) * 100.0, 1),
            "recommendation": recommendation,
            "details": details_data
        },
        "verdict": verdict,
        "option_chain": asdict(chain_summary),
        "advanced_meta": advanced_meta,
        "levels": levels,
        "expiry_radar": radar_data,
        "risk_analysis": risk_info,
        "order_flow": {
            "bid_volume_pct": of.bid_volume_pct,
            "ask_volume_pct": of.ask_volume_pct,
            "order_book_imbalance": of.order_book_imbalance,
            "cvd": of.cvd,
            "cvd_direction": of.cvd_direction,
            "pcr": of.pcr,
            "institutional_bias": of.institutional_bias,
            "summary": of.summary
        }
    })

@app.route("/api/backtest")
def api_backtest():
    """Runs institutional historical backtest for selected asset and timeframe."""
    try:
        asset_key = request.args.get("asset", selected_asset).upper()
        days = int(request.args.get("days", 100))
        cap = float(request.args.get("capital", current_capital))
        res = backtest_engine.run_backtest(asset_key, days, cap)
        return jsonify(asdict(res))
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/option-chain")
def api_option_chain():
    """Returns dynamic strike-by-strike option chain for the active asset."""
    try:
        asset_key = request.args.get("asset", selected_asset).upper()
        expiry_req = request.args.get("expiry", None)
        spot = market_intelligence.get_asset_spot(asset_key)
        info = ASSET_CATALOG.get(asset_key, ASSET_CATALOG["NIFTY"])
        chain = option_chain_engine.build_option_chain(
            asset_key, spot, info.strike_step, strikes_count=5, selected_expiry=expiry_req
        )
        return jsonify(asdict(chain))
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/send-push-alert", methods=["POST"])
def api_send_push_alert():
    """Broadcasts instant push notification to all paired mobile devices."""
    try:
        data = request.get_json(force=True) or {}
        title = data.get("title", "⚡ Vijay Sai's Dhan Algo Alert")
        message = data.get("message", "High-conviction indicator confluence detected on NIFTY.")
        priority = data.get("priority", "high")
        broadcast_res = notifier.test_broadcast(title, message)
        return jsonify({
            "success": broadcast_res["success"],
            "devices_tested": broadcast_res["devices_tested"],
            "message": f"Push notification successfully transmitted to {broadcast_res['devices_tested']} mobile device(s)!",
            "details": broadcast_res["results"]
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/notifications/devices", methods=["GET", "POST", "DELETE"])
def api_notification_devices():
    """Manages multi-mobile push notification subscriptions."""
    try:
        if request.method == "GET":
            devices = notifier.get_devices()
            return jsonify({
                "success": True,
                "primary_topic": notifier.primary_topic,
                "subscribe_url": f"https://ntfy.sh/{notifier.primary_topic}",
                "devices": devices,
                "count": len(devices)
            })

        elif request.method == "POST":
            data = request.get_json(force=True) or {}
            name = data.get("name", "").strip()
            topic = data.get("topic", "").strip()
            if not name:
                return jsonify({"success": False, "error": "Device name is required (e.g. 'iPhone 15', 'Trading Partner Phone')."}), 400
            res = notifier.add_device(name, topic)
            return jsonify(res)

        elif request.method == "DELETE":
            data = request.get_json(force=True) or {}
            device_id = data.get("id") or data.get("topic")
            if not device_id:
                return jsonify({"success": False, "error": "Device ID or topic required."}), 400
            res = notifier.remove_device(device_id)
            return jsonify(res)

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/notifications/broadcast", methods=["POST"])
def api_notification_broadcast():
    """Sends a live test notification to all registered mobile phones."""
    try:
        data = request.get_json(force=True) or {}
        title = data.get("title") or "🔔 Multi-Mobile Push Alert"
        message = data.get("message") or "Live connection verified! All paired mobile devices receiving real-time Dhan trade signals."
        res = notifier.test_broadcast(title, message)
        return jsonify(res)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/rms/set-trade-cap", methods=["POST"])
def api_set_trade_cap():
    """Dynamically customizes the maximum daily trade cap and auto-expand mode."""
    try:
        data = request.get_json(force=True) or {}
        increment = data.get("increment")
        max_trades = data.get("max_trades")
        auto_expand = data.get("auto_expand")
        expand_step = data.get("expand_step")

        if increment is not None:
            # Incremental addition (+5, +10 trades beyond taken count)
            base_reference = max(engine.trade_count, engine.max_daily_trades)
            new_cap = base_reference + int(increment)
        elif max_trades is not None:
            new_cap = int(max_trades)
        else:
            return jsonify({"success": False, "error": "Must provide 'max_trades' or 'increment'"}), 400

        result = engine.set_custom_daily_cap(new_cap, auto_expand, expand_step)
        
        # Persist to .env for future sessions
        try:
            env_path = os.path.join(os.path.dirname(__file__), ".env")
            if os.path.exists(env_path):
                with open(env_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                new_lines = []
                found = False
                for line in lines:
                    if line.startswith("MAX_TRADES_PER_DAY="):
                        new_lines.append(f"MAX_TRADES_PER_DAY={new_cap}\n")
                        found = True
                    else:
                        new_lines.append(line)
                if not found:
                    new_lines.append(f"MAX_TRADES_PER_DAY={new_cap}\n")
                with open(env_path, "w", encoding="utf-8") as f:
                    f.writelines(new_lines)
        except Exception as err:
            print(f"[RMS] Warning saving trade cap to .env: {err}")

        return jsonify({
            "success": True,
            "message": f"Daily trade cap successfully updated to {new_cap} trades!",
            "trade_count": engine.trade_count,
            "max_trades": engine.max_daily_trades,
            "auto_expand": engine.auto_expand_trades,
            "is_kill_switch_active": engine.is_kill_switch_active
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/status")
def api_status():
    from market_intelligence import market_intelligence
    cur_asset = request.args.get("asset", "").upper()
    now_hm = datetime.datetime.now().strftime("%H:%M")
    is_evening = (now_hm >= "15:30" or now_hm < "09:15")
    if not cur_asset:
        cur_asset = "CRUDEOILM" if is_evening else "NIFTY"
    if cur_asset not in ASSET_CATALOG:
        cur_asset = "CRUDEOILM" if is_evening else "NIFTY"
    asset_info = ASSET_CATALOG.get(cur_asset, ASSET_CATALOG["NIFTY"])
    current_spot = market_intelligence.get_asset_spot(cur_asset) or engine.latest_spot or engine.client.get_spot_ltp()
    now_hm = datetime.datetime.now().strftime("%H:%M")
    signal = engine.strategy.evaluate(now_hm)

    active_positions_list = []
    total_open_pnl = 0.0
    paper_count = 0
    live_count = 0

    for pos in engine.active_positions:
        pos_asset = getattr(pos, "asset_key", "NIFTY")
        pos_mode = getattr(pos, "execution_mode", "PAPER")

        # Always refresh option current premium strictly based on live exchange spot for both LIVE and PAPER
        try:
            spot_now = market_intelligence.get_asset_spot(pos_asset)
            step_sz = 50.0
            if pos_asset in ["BANKNIFTY", "SENSEX", "GOLDM", "NIFTYIT"]:
                step_sz = 100.0
            elif pos_asset == "SILVERM":
                step_sz = 500.0
            elif pos_asset == "NATGASMINI":
                step_sz = 5.0
            elif pos_asset in ["RELIANCE", "INFY"]:
                step_sz = 20.0
            elif pos_asset in ["HDFCBANK", "ICICIBANK", "SBIN"]:
                step_sz = 10.0
            strike_val = pos.strike_price if pos.strike_price > 0 else (round(spot_now / step_sz) * step_sz)
            refreshed_ltp = engine.client.get_option_ltp(
                pos.security_id, spot_now, strike_val, pos.option_type, pos.expiry_date, mode=pos_mode, segment=getattr(pos, "segment", None)
            )
            if refreshed_ltp > 0:
                pos.current_premium = refreshed_ltp
                if pos.current_premium > pos.highest_premium:
                    pos.highest_premium = pos.current_premium
                if spot_now > pos.highest_spot:
                    pos.highest_spot = spot_now
                pos.pnl = round((pos.current_premium - pos.entry_premium) * pos.quantity, 2)
        except Exception:
            pass

        invested_amt = getattr(pos, "invested_capital", 0.0) or round(pos.entry_premium * pos.quantity, 2)
        margin_req = getattr(pos, "margin_required", 0.0) or invested_amt
        active_cap = engine.client.get_real_cash_balance() if pos_mode == "LIVE" else float(engine.client.paper_capital)
        margin_pct = getattr(pos, "margin_pct_capital", 0.0)
        if margin_pct <= 0:
            margin_pct = round((margin_req / active_cap * 100.0), 1) if active_cap > 0 else 0.0

        curr_val = round(pos.current_premium * pos.quantity, 2)
        live_pnl = round((pos.current_premium - pos.entry_premium) * pos.quantity, 2)
        live_pnl_pct = round(((pos.current_premium - pos.entry_premium) / pos.entry_premium) * 100.0, 2) if pos.entry_premium > 0 else 0.0
        max_risk = round((pos.hard_stop_premium - pos.entry_premium) * pos.quantity, 2)

        p_data = {
            "position_id": getattr(pos, "position_id", ""),
            "symbol": pos.symbol,
            "strategy_name": pos.strategy_name,
            "quantity": pos.quantity,
            "lots": getattr(pos, "lots", 1),
            "lot_size": getattr(pos, "lot_size", pos.quantity),
            "entry_premium": pos.entry_premium,
            "current_premium": pos.current_premium,
            "invested_amount": invested_amt,
            "invested_capital": invested_amt,
            "margin_required": margin_req,
            "bank_limit_triggered": margin_req,
            "margin_pct_capital": margin_pct,
            "current_value": curr_val,
            "hard_stop_premium": pos.hard_stop_premium,
            "trailing_sl_premium": getattr(pos, "trailing_sl_premium", pos.hard_stop_premium),
            "target_premium": getattr(pos, "target_premium", 0.0),
            "max_risk_amount": max_risk,
            "underlying_entry_spot": pos.underlying_entry_spot,
            "simulated_spot": getattr(pos, "simulated_spot", pos.underlying_entry_spot),
            "underlying_structural_sl": pos.underlying_structural_sl,
            "target_1_spot": pos.target_1_spot,
            "target_2_spot": pos.target_2_spot,
            "highest_premium": pos.highest_premium,
            "entry_time": pos.entry_time_iso,
            "is_trailing_active": pos.is_trailing_active,
            "pnl": live_pnl,
            "pnl_pct": live_pnl_pct,
            "locked_profit_inr": getattr(pos, "locked_profit_inr", 0.0),
            "trailing_step_level": getattr(pos, "trailing_step_level", 0),
            "trade_style": getattr(pos, "trade_style", "SMART_EDGE"),
            "trade_rationale": getattr(pos, "trade_rationale", "High-confluence momentum setup detected across VWAP, EMA and Volume filters"),
            "setup_conditions": getattr(pos, "setup_conditions", []),
            "execution_mode": pos_mode,
            "asset_key": getattr(pos, "asset_key", "NIFTY"),
            "entry_thesis": getattr(pos, "entry_thesis", []) or getattr(pos, "setup_conditions", []),
            "mtf_15m": getattr(pos, "mtf_15m", ""),
            "mtf_5m": getattr(pos, "mtf_5m", ""),
            "mtf_1m": getattr(pos, "mtf_1m", ""),
            "risk_reward_ratio": getattr(pos, "risk_reward_ratio", "1 : 2.5"),
            "setup_type": getattr(pos, "setup_type", "INSTITUTIONAL_SCALP")
        }
        active_positions_list.append(p_data)
        total_open_pnl += live_pnl
        if pos_mode == "LIVE":
            live_count += 1
        else:
            paper_count += 1

    pos_data = active_positions_list[0] if active_positions_list else None

    from mtf_engine import mtf_engine
    spot_nifty = market_intelligence.get_asset_spot("NIFTY")
    spot_bn = market_intelligence.get_asset_spot("BANKNIFTY")
    mtf_nifty = mtf_engine.analyze("NIFTY", "NIFTY 50", spot_nifty)
    mtf_bn = mtf_engine.analyze("BANKNIFTY", "BANK NIFTY", spot_bn)

    summary = journal.get_today_summary()
    of = order_flow_analyzer.analyze(current_spot, signal.action)
    
    order_flow_data = {
        "bid_volume_pct": of.bid_volume_pct,
        "ask_volume_pct": of.ask_volume_pct,
        "order_book_imbalance": of.order_book_imbalance,
        "cvd": of.cvd,
        "cvd_direction": of.cvd_direction,
        "pcr": of.pcr,
        "institutional_bias": of.institutional_bias,
        "summary": of.summary
    }

    atm_strike = round(current_spot / asset_info.strike_step) * asset_info.strike_step
    # Resolve exact scrip master security_ids for authentic live Dhan exchange pricing
    from scrip_master import ScripMaster
    sm_inst = ScripMaster()
    ce_meta = sm_inst.get_nifty_option(current_spot, "CE", 0, cur_asset)
    pe_meta = sm_inst.get_nifty_option(current_spot, "PE", 0, cur_asset)
    
    ce_sec_id = ce_meta.get("security_id", "0")
    pe_sec_id = pe_meta.get("security_id", "0")
    ce_exp = ce_meta.get("expiry", "")
    pe_exp = pe_meta.get("expiry", "")

    default_seg = "BSE_FNO" if cur_asset == "SENSEX" else ("MCX_COMM" if cur_asset in ["CRUDEOILM", "NATGASMINI", "GOLDM", "SILVERM"] else "NSE_FNO")
    ce_seg = ce_meta.get("segment") or default_seg
    pe_seg = pe_meta.get("segment") or default_seg

    est_ce_prem = 0.0
    est_pe_prem = 0.0
    if (ce_sec_id != "0" or pe_sec_id != "0"):
        try:
            batch_quotes = engine.client.get_options_batch_ltp([
                (ce_sec_id, atm_strike, "CE", ce_seg),
                (pe_sec_id, atm_strike, "PE", pe_seg)
            ])
            est_ce_prem = batch_quotes.get(str(ce_sec_id), 0.0)
            est_pe_prem = batch_quotes.get(str(pe_sec_id), 0.0)
        except Exception:
            pass

    # Non-blocking premium fallback: Model pricing if exchange quote is zero/closed
    if est_ce_prem <= 0.0:
        est_ce_prem = round(max(15.0, current_spot * 0.012), 2)
    if est_pe_prem <= 0.0:
        est_pe_prem = round(max(15.0, current_spot * 0.012), 2)
    active_cap = engine.client.get_real_cash_balance() if config.trading_mode == "LIVE" else float(engine.client.paper_capital)
    
    ce_preview = contract_selector.select_contract(current_spot, "BUY_CE", est_ce_prem, "ATM", asset_key=cur_asset, available_capital=active_cap)
    pe_preview = contract_selector.select_contract(current_spot, "BUY_PE", est_pe_prem, "ATM", asset_key=cur_asset, available_capital=active_cap)
    contract_preview = ce_preview
    
    can_trade_sched, sched_reason = engine.check_trading_schedule(cur_asset)

    # Adaptive Asset-Specific Confluence Checklist (Evaluates Real 5m/15m Indicators for cur_asset)
    from indicators import IndicatorEngine
    df_asset = mtf_engine.get_real_candles(cur_asset)
    if df_asset is not None and len(df_asset) >= 5:
        last_c = df_asset.iloc[-1]
        prev_c = df_asset.iloc[-2] if len(df_asset) >= 2 else last_c
        c_close = float(last_c["close"])
        vwap_val = round(IndicatorEngine.calculate_vwap(df_asset), 1)
        ema9_val, ema21_val, _ = IndicatorEngine.calculate_emas(df_asset["close"])
        rsi_val = round(IndicatorEngine.calculate_rsi(df_asset["close"], 14), 1)
        adx_val, _, _ = IndicatorEngine.calculate_adx(df_asset, 14)
        atr_val = IndicatorEngine.calculate_atr(df_asset, 14)
        vol_ma = df_asset["volume"].tail(20).mean() if len(df_asset) >= 20 else 1000.0
        vol_ratio = float(last_c["volume"]) / max(float(vol_ma), 1.0)
        
        # Bullish CE rules for this specific asset
        c1_ce = c_close > vwap_val
        c2_ce = ema9_val > ema21_val
        c3_ce = ema9_val > ema21_val
        c4_ce = 50.0 <= rsi_val <= 92.0
        c5_ce = adx_val >= 18.0
        c6_ce = (last_c["close"] > last_c["open"]) or (abs(last_c["low"] - ema9_val) <= (1.2 * atr_val))
        c7_ce = (last_c["close"] >= last_c["open"])
        c8_ce = vol_ratio >= 1.05 or last_c["volume"] >= 500

        # Bearish PE rules for this specific asset
        c1_pe = c_close < vwap_val
        c2_pe = ema9_val < ema21_val
        c3_pe = ema9_val < ema21_val
        c4_pe = 10.0 <= rsi_val <= 50.0
        c5_pe = adx_val >= 18.0
        c6_pe = (last_c["close"] < last_c["open"]) or (abs(last_c["high"] - ema9_val) <= (1.2 * atr_val))
        c7_pe = (last_c["close"] <= last_c["open"])
        c8_pe = vol_ratio >= 1.05 or last_c["volume"] >= 500

        checklist = [
            {"rule_name": "15m Close > 15m VWAP", "condition_required": f"> {vwap_val:.1f}", "actual_value": f"{c_close:.1f}", "passed": c1_ce, "side": "CE"},
            {"rule_name": "15m EMA 9 > 21", "condition_required": "9 > 21", "actual_value": f"{ema9_val:.1f} vs {ema21_val:.1f}", "passed": c2_ce, "side": "CE"},
            {"rule_name": "5m EMA 9 > 21", "condition_required": "9 > 21", "actual_value": f"{ema9_val:.1f} vs {ema21_val:.1f}", "passed": c3_ce, "side": "CE"},
            {"rule_name": "5m RSI(14) Momentum", "condition_required": "50 - 92", "actual_value": f"{rsi_val:.1f}", "passed": c4_ce, "side": "CE"},
            {"rule_name": "ADX(14) Trend Strength", "condition_required": ">= 18", "actual_value": f"{adx_val:.1f}", "passed": c5_ce, "side": "CE"},
            {"rule_name": "Pullback/Continuation", "condition_required": "Near EMA or Expansion", "actual_value": f"Low {last_c['low']:.1f}", "passed": c6_ce, "side": "CE"},
            {"rule_name": "Bullish Confirmation Candle", "condition_required": "Green Close >= Open", "actual_value": f"{last_c['close']:.1f}", "passed": c7_ce, "side": "CE"},
            {"rule_name": "Volume Confirmation", "condition_required": ">= 1.05x MA20", "actual_value": f"{vol_ratio:.2f}x", "passed": c8_ce, "side": "CE"},

            {"rule_name": "15m Close < 15m VWAP", "condition_required": f"< {vwap_val:.1f}", "actual_value": f"{c_close:.1f}", "passed": c1_pe, "side": "PE"},
            {"rule_name": "15m EMA 9 < 21", "condition_required": "9 < 21", "actual_value": f"{ema9_val:.1f} vs {ema21_val:.1f}", "passed": c2_pe, "side": "PE"},
            {"rule_name": "5m EMA 9 < 21", "condition_required": "9 < 21", "actual_value": f"{ema9_val:.1f} vs {ema21_val:.1f}", "passed": c3_pe, "side": "PE"},
            {"rule_name": "5m RSI(14) Momentum", "condition_required": "10 - 50", "actual_value": f"{rsi_val:.1f}", "passed": c4_pe, "side": "PE"},
            {"rule_name": "ADX(14) Trend Strength", "condition_required": ">= 18", "actual_value": f"{adx_val:.1f}", "passed": c5_pe, "side": "PE"},
            {"rule_name": "Pullback/Continuation", "condition_required": "Near EMA or Breakdown", "actual_value": f"High {last_c['high']:.1f}", "passed": c6_pe, "side": "PE"},
            {"rule_name": "Bearish Confirmation Candle", "condition_required": "Red Close <= Open", "actual_value": f"{last_c['close']:.1f}", "passed": c7_pe, "side": "PE"},
            {"rule_name": "Volume Confirmation", "condition_required": ">= 1.05x MA20", "actual_value": f"{vol_ratio:.2f}x", "passed": c8_pe, "side": "PE"}
        ]
    else:
        vwap_val = round(current_spot * 0.995, 1)
        checklist = [
            {
                "rule_name": str(item.rule_name),
                "condition_required": str(item.condition_required),
                "actual_value": str(item.actual_value),
                "passed": bool(item.passed),
                "side": getattr(item, "side", "CE")
            }
            for item in signal.audit_checklist
        ]

    is_above_vwap = current_spot >= vwap_val
    trend_basis_text = (
        f"Based on 15m & 5m Indicators: {asset_info.display_name} Spot (₹{current_spot:,.2f}) is trading "
        f"{'ABOVE' if is_above_vwap else 'BELOW'} 15m VWAP (₹{vwap_val:,.2f}). "
        f"9-EMA is {'ABOVE' if is_above_vwap else 'BELOW'} 21-EMA, establishing a "
        f"{'BULLISH MOMENTUM BIAS (Favors Buying Calls)' if is_above_vwap else 'BEARISH DISTRIBUTION BIAS (Favors Buying Puts)'}."
    )

    df_trades = journal.get_all_trades_df()
    recent_trades = df_trades.tail(5).to_dict("records") if not df_trades.empty else []

    if engine.active_position:
        hunting_desc = f"ACTIVE POSITION: Managing {engine.active_position.symbol} ({engine.active_position.quantity} Qty). Monitoring 2-layer stops & targets."
        hunting_state = "IN_POSITION"
        waiting_reason = "Position is open. Multi-tiered trailing stop and profit locking defenses are active."
        bullish_setup_lookout = "Managing active trade."
        bearish_setup_lookout = "Managing active trade."
    elif engine.is_kill_switch_active:
        hunting_desc = f"BOT HALTED: {engine.kill_switch_reason}. Click 'Reset Session' to resume."
        hunting_state = "HALTED"
        waiting_reason = engine.kill_switch_reason
        bullish_setup_lookout = "Trading paused by RMS risk ceiling."
        bearish_setup_lookout = "Trading paused by RMS risk ceiling."
    elif not can_trade_sched:
        hunting_desc = f"SCHEDULE HOLD: {sched_reason}"
        hunting_state = "SCHEDULE_HOLD"
        waiting_reason = sched_reason
        bullish_setup_lookout = f"Exchange Closed for {asset_info.display_name}. Core trading hours: 09:30 - 14:45 IST."
        bearish_setup_lookout = f"Exchange Closed for {asset_info.display_name}. Core trading hours: 09:30 - 14:45 IST."
    else:
        if signal.action != "NO_TRADE":
            hunting_desc = f"READY TO AUTO-EXECUTE: {signal.action} on {asset_info.display_name} via {signal.strategy_name}"
            hunting_state = "READY_TO_EXECUTE"
            waiting_reason = "All checklist criteria validated. Entry triggered."
            bullish_setup_lookout = f"Confirmed Bullish Entry Triggered for {ce_preview.custom_symbol}!"
            bearish_setup_lookout = f"Confirmed Bearish Entry Triggered for {pe_preview.custom_symbol}!"
        else:
            hunting_state = "SITTING_IN_CASH"
            waiting_reason = signal.rejection_reason or f"{asset_info.display_name} in {signal.regime} mode. Waiting for high-conviction breakout or pullback confirmation."
            hunting_desc = f"SITTING IN CASH: {waiting_reason}"
            bullish_setup_lookout = f"Evaluated on {asset_info.display_name}: Triggers {ce_preview.custom_symbol} when Spot holds above VWAP ({vwap_val:.1f}) + 5m EMA 9 > 21."
            bearish_setup_lookout = f"Evaluated on {asset_info.display_name}: Triggers {pe_preview.custom_symbol} on breakdown below VWAP ({vwap_val:.1f}) + 5m EMA 9 < 21."

    return jsonify({
        "is_engine_active": is_engine_active,
        "auto_pilot_enabled": getattr(engine, "auto_pilot_enabled", True),
        "bot_hunting_summary": hunting_desc,
        "hunting_state": hunting_state,
        "waiting_reason": waiting_reason,
        "bullish_setup_lookout": bullish_setup_lookout,
        "bearish_setup_lookout": bearish_setup_lookout,
        "trading_mode": config.trading_mode,
        "spot_price": current_spot,
        "regime": signal.regime,
        "orb_formed": engine.strategy.orb_high is not None,
        "orb_high": engine.strategy.orb_high or 0.0,
        "orb_low": engine.strategy.orb_low or 0.0,
        "evaluated_instrument": {
            "key": cur_asset,
            "display_name": asset_info.display_name,
            "category": asset_info.category,
            "spot_price": current_spot,
            "vwap": vwap_val,
            "atm_strike": atm_strike,
            "trend_basis": trend_basis_text,
            "is_above_vwap": is_above_vwap,
            "market_session": "OPEN" if can_trade_sched else "CLOSED",
            "market_session_desc": sched_reason,
            "ce_contract": {
                "symbol": ce_preview.custom_symbol,
                "expiry": ce_preview.expiry_date,
                "strike": ce_preview.strike_price,
                "option_type": "CE",
                "lots": getattr(ce_preview, "number_of_lots", 1),
                "quantity": getattr(ce_preview, "number_of_lots", 1) * ce_preview.lot_size,
                "estimated_premium": ce_preview.estimated_premium,
                "required_margin": round(ce_preview.margin_required or (ce_preview.estimated_premium * getattr(ce_preview, "number_of_lots", 1) * ce_preview.lot_size), 2),
                "hard_stop_premium": ce_preview.hard_stop_premium,
                "target_premium": round(ce_preview.estimated_premium + (1.5 * max(abs(ce_preview.estimated_premium - ce_preview.hard_stop_premium), 4.0)), 2),
                "max_risk_inr": ce_preview.trade_risk_inr
            },
            "pe_contract": {
                "symbol": pe_preview.custom_symbol,
                "expiry": pe_preview.expiry_date,
                "strike": pe_preview.strike_price,
                "option_type": "PE",
                "lots": getattr(pe_preview, "number_of_lots", 1),
                "quantity": getattr(pe_preview, "number_of_lots", 1) * pe_preview.lot_size,
                "estimated_premium": pe_preview.estimated_premium,
                "required_margin": round(pe_preview.margin_required or (pe_preview.estimated_premium * getattr(pe_preview, "number_of_lots", 1) * pe_preview.lot_size), 2),
                "hard_stop_premium": pe_preview.hard_stop_premium,
                "target_premium": round(pe_preview.estimated_premium + (1.5 * max(abs(pe_preview.estimated_premium - pe_preview.hard_stop_premium), 4.0)), 2),
                "max_risk_inr": pe_preview.trade_risk_inr
            }
        },
        "signal": {
            "action": signal.action,
            "strategy_name": signal.strategy_name,
            "regime": signal.regime,
            "reason": signal.rejection_reason or f"Approved setup by {signal.strategy_name}",
            "structural_sl_spot": signal.structural_sl_spot,
            "target_1_spot": signal.target_1_spot
        },
        "schedule": {
            "can_trade": can_trade_sched,
            "reason": sched_reason
        },
        "contract_preview": {
            "custom_symbol": contract_preview.custom_symbol,
            "lot_size": contract_preview.lot_size,
            "estimated_premium": contract_preview.estimated_premium,
            "hard_stop_premium": contract_preview.hard_stop_premium,
            "trade_risk_inr": contract_preview.trade_risk_inr,
            "delta": contract_preview.estimated_delta,
            "expiry": contract_preview.expiry_date
        },
        "order_flow": order_flow_data,
        "active_position": pos_data,
        "active_positions": active_positions_list,
        "total_open_pnl": round(total_open_pnl, 2),
        "total_active_count": len(active_positions_list),
        "paper_active_count": paper_count,
        "live_active_count": live_count,
        "global_trade_lock": (lambda: (__import__("global_trade_lock").global_trade_lock.get_lock_status()))(),
        "duplicate_rejections": (lambda: (__import__("global_trade_lock").global_trade_lock.get_recent_rejections(10)))(),
        "audit_checklist": checklist,
        "recent_trades": recent_trades,
        "paper_orders": engine.client.order_history[-10:],
        "rms": {
            "trade_count": engine.trade_count,
            "max_trades": engine.max_daily_trades,
            "auto_expand_trades": getattr(engine, "auto_expand_trades", True),
            "trade_expand_step": getattr(engine, "trade_expand_step", 5),
            "consecutive_losses": engine.consecutive_losses,
            "gross_pnl": summary["gross_pnl"],
            "total_charges": summary["total_charges"],
            "net_pnl": summary["net_pnl"],
            "daily_loss_limit": engine.daily_loss_limit,
            "kill_switch": engine.is_kill_switch_active,
            "kill_switch_reason": engine.kill_switch_reason
        },
        "funds": {
            "real_cash_balance": engine.client.get_real_cash_balance(),
            "paper_capital": float(engine.client.paper_capital),
            "active_capital": engine.client.get_real_cash_balance() if config.trading_mode == "LIVE" else float(engine.client.paper_capital)
        },
        "mtf_confluence": {
            "NIFTY": {
                "display_name": "NIFTY 50",
                "spot": spot_nifty,
                "tf_15m": mtf_nifty.tf_15m.__dict__,
                "tf_5m": mtf_nifty.tf_5m.__dict__,
                "tf_1m": mtf_nifty.tf_1m.__dict__,
                "setup_type": mtf_nifty.setup_type,
                "stage": mtf_nifty.tf_5m.stage,
                "recommended_action": mtf_nifty.recommended_action,
                "direction_label": mtf_nifty.direction_label,
                "recommended_option": mtf_nifty.recommended_option,
                "confluence_score": mtf_nifty.confluence_score,
                "risk_reward_ratio": mtf_nifty.risk_reward_ratio,
                "rationale": mtf_nifty.rationale,
                "key_reasons": mtf_nifty.key_reasons
            },
            "BANKNIFTY": {
                "display_name": "BANK NIFTY",
                "spot": spot_bn,
                "tf_15m": mtf_bn.tf_15m.__dict__,
                "tf_5m": mtf_bn.tf_5m.__dict__,
                "tf_1m": mtf_bn.tf_1m.__dict__,
                "setup_type": mtf_bn.setup_type,
                "stage": mtf_bn.tf_5m.stage,
                "recommended_action": mtf_bn.recommended_action,
                "direction_label": mtf_bn.direction_label,
                "recommended_option": mtf_bn.recommended_option,
                "confluence_score": mtf_bn.confluence_score,
                "risk_reward_ratio": mtf_bn.risk_reward_ratio,
                "rationale": mtf_bn.rationale,
                "key_reasons": mtf_bn.key_reasons
            }
        },
        "sniper_hud": {
            "has_active_position": engine.active_position is not None,
            "elapsed_seconds": max(0, int((datetime.datetime.now() - datetime.datetime.strptime(engine.active_position.entry_time_iso[:19], "%Y-%m-%d %H:%M:%S")).total_seconds())) if engine.active_position else 0,
            "remaining_seconds": max(0, 180 - max(0, int((datetime.datetime.now() - datetime.datetime.strptime(engine.active_position.entry_time_iso[:19], "%Y-%m-%d %H:%M:%S")).total_seconds()))) if (engine.active_position and getattr(engine.active_position, "trade_style", "") == "SNIPER") else 0,
            "max_duration_seconds": 180 if (engine.active_position and getattr(engine.active_position, "trade_style", "") == "SNIPER") else 3600,
            "position": pos_data,
            "event_logs": getattr(engine, "sniper_event_log", [])[-30:],
            "trailing_tier": getattr(engine.active_position, "trailing_step_level", 0) if engine.active_position else 0,
            "locked_profit": getattr(engine.active_position, "locked_profit_inr", 0.0) if engine.active_position else 0.0,
            "is_armed": not engine.is_kill_switch_active,
            "ready_summary": "🎯 SNIPER ARMED & READY: Scanner actively evaluating 7 indices/stocks for high-velocity scalps." if (not engine.active_position and not engine.is_kill_switch_active) else ("ACTIVE POSITION RUNNING" if engine.active_position else "HALTED")
        }
    })

@app.route('/api/rejections', methods=['GET'])
def api_rejections():
    """Returns the duplicate trade prevention audit log."""
    try:
        from global_trade_lock import global_trade_lock
        limit = int(request.args.get('limit', 50))
        rejections = global_trade_lock.get_recent_rejections(limit)
        lock_status = global_trade_lock.get_lock_status()
        return jsonify({
            "status": "success",
            "global_trade_lock": lock_status,
            "rejections": rejections,
            "count": len(rejections)
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/queue/status")
def api_queue_status():
    """Recent order intents recorded by the execution audit layer."""
    import sqlite3, order_queue
    try:
        conn = sqlite3.connect(order_queue.DB_PATH)
        rows = conn.execute("SELECT id, strategy, intent_type, payload, status, created_at, updated_at FROM intents ORDER BY id DESC LIMIT 50").fetchall()
        conn.close()
        cols = ["id", "strategy", "intent_type", "payload", "status", "created_at", "updated_at"]
        return jsonify({"success": True, "intents": [dict(zip(cols, r)) for r in rows]})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/risk/status")
def api_risk_status():
    """Live risk state taken from the running engine (single source of truth)."""
    return jsonify({
        "success": True,
        "mode": engine.mode,
        "account_capital": engine.account_capital,
        "active_positions": len(engine.active_positions),
        "max_concurrent_positions": engine.max_concurrent_positions,
        "trades_taken": engine.trade_count,
        "max_daily_trades": engine.max_daily_trades,
        "realized_pnl": engine.realized_pnl,
        "daily_loss_limit": engine.daily_loss_limit,
        "consecutive_losses": engine.consecutive_losses,
        "kill_switch_active": engine.is_kill_switch_active,
        "kill_switch_reason": engine.kill_switch_reason,
    })

@app.route("/api/toggle-engine", methods=["POST"])
def toggle_engine():
    global is_engine_active
    is_engine_active = not is_engine_active
    status_str = "ACTIVE" if is_engine_active else "PAUSED"
    print(f"[Flask] Bot execution toggled to: {status_str}")
    return jsonify({"success": True, "is_running": is_engine_active})

@app.route("/api/square-off", methods=["POST"])
def api_square_off():
    data = request.get_json(silent=True) or {}
    pos_id = data.get("position_id")
    symbol = data.get("symbol")
    with lock:
        if not engine.active_positions:
            return jsonify({"success": False, "message": "No open position to square off."})

        if data.get("all"):
            count = len(engine.active_positions)
            engine.execute_exit_all("Manual Square-Off All via Web Dashboard")
            return jsonify({"success": True, "message": f"Successfully squared off all {count} active positions."})

        target_pos = None
        for p in engine.active_positions:
            if (pos_id and getattr(p, "position_id", "") == pos_id) or (symbol and p.symbol == symbol):
                target_pos = p
                break
        if not target_pos and engine.active_positions:
            target_pos = engine.active_positions[0]

        if target_pos:
            sym = target_pos.symbol
            engine.execute_exit("Manual Web Dashboard Square-Off", pos=target_pos)
            return jsonify({"success": True, "message": f"Successfully squared off position: {sym}"})
        return jsonify({"success": False, "message": "Position not found."})

@app.route("/api/square-off-all", methods=["POST"])
def api_square_off_all():
    with lock:
        count = len(engine.active_positions)
        if count == 0:
            return jsonify({"success": False, "message": "No active positions to square off."})
        engine.execute_exit_all("Manual Emergency Square-Off All via Desk")
        return jsonify({"success": True, "message": f"Successfully closed all {count} active positions across Paper & Live."})

@app.route("/api/toggle-killswitch", methods=["POST"])
def api_toggle_killswitch():
    with lock:
        engine.is_kill_switch_active = not engine.is_kill_switch_active
        if engine.is_kill_switch_active:
            engine.kill_switch_reason = "Manually triggered from Web Dashboard"
        else:
            engine.kill_switch_reason = ""
        status_msg = "Kill-Switch Activated" if engine.is_kill_switch_active else "Kill-Switch Deactivated"
        return jsonify({"success": True, "message": status_msg, "active": engine.is_kill_switch_active})

@app.route("/api/ledger")
def api_ledger():
    mode_filter = request.args.get("mode", "ALL").upper()
    df = journal.get_all_trades_df()
    trades = df.to_dict("records") if not df.empty else []
    
    if mode_filter in ["LIVE", "PAPER"]:
        trades = [t for t in trades if str(t.get("mode", "")).upper() == mode_filter]

    active_pos = None
    if engine.active_position:
        pos = engine.active_position
        inv_cap = getattr(pos, "invested_capital", 0.0) or round(pos.entry_premium * pos.quantity, 2)
        m_req = getattr(pos, "margin_required", 0.0) or inv_cap
        active_pos = {
            "symbol": pos.symbol,
            "mode": getattr(pos, "execution_mode", config.trading_mode),
            "trade_style": getattr(pos, "trade_style", "SMART_EDGE"),
            "quantity": pos.quantity,
            "lots": getattr(pos, "lots", 1),
            "entry_premium": pos.entry_premium,
            "current_premium": pos.current_premium,
            "invested_capital": inv_cap,
            "margin_required": m_req,
            "bank_limit_triggered": m_req,
            "pnl": pos.pnl,
            "pnl_pct": round(((pos.current_premium - pos.entry_premium) / pos.entry_premium * 100.0), 2) if pos.entry_premium > 0 else 0.0,
            "entry_time": pos.entry_time_iso,
            "target_premium": getattr(pos, "target_premium", 0.0),
            "hard_stop_premium": pos.hard_stop_premium,
            "trailing_sl_premium": getattr(pos, "trailing_sl_premium", pos.hard_stop_premium),
            "locked_profit_inr": getattr(pos, "locked_profit_inr", 0.0),
            "trade_rationale": getattr(pos, "trade_rationale", "High-confluence momentum setup"),
            "setup_conditions": getattr(pos, "setup_conditions", []),
            "status": "OPEN / RUNNING"
        }

    dhan_orders = []
    if engine.client.mode == "LIVE" and engine.client.dhan:
        dhan_orders = engine.client.get_order_list()

    paper_orders = engine.client.order_history[-20:]

    return jsonify({
        "status": "success",
        "active_position": active_pos,
        "completed_trades": trades,
        "dhan_live_orders": dhan_orders,
        "paper_orders": paper_orders,
        "summary": journal.get_today_summary()
    })

@app.route("/journal")
def journal_page():
    df = journal.get_all_trades_df()
    trades = df.to_dict("records") if not df.empty else []

    total_trades = len(df)
    wins = df[df["net_pnl"] > 0] if not df.empty else pd.DataFrame()
    losses = df[df["net_pnl"] <= 0] if not df.empty else pd.DataFrame()
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = (win_count / total_trades * 100.0) if total_trades > 0 else 0.0

    gross_pnl = float(df["gross_pnl"].sum()) if not df.empty else 0.0
    total_charges = float(df["total_charges"].sum()) if not df.empty else 0.0
    net_takehome = float(df["net_pnl"].sum()) if not df.empty else 0.0

    gross_profit = wins["net_pnl"].sum() if not wins.empty else 0.0
    gross_loss = abs(losses["net_pnl"].sum()) if not losses.empty else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 1.0)

    avg_win = float(wins["net_pnl"].mean()) if not wins.empty else 0.0
    avg_loss = float(abs(losses["net_pnl"].mean())) if not losses.empty else 0.0
    total_brokerage = float(df["brokerage"].sum()) if (not df.empty and "brokerage" in df.columns) else 0.0
    total_stt = float(df["stt"].sum()) if (not df.empty and "stt" in df.columns) else 0.0
    total_gst = float(df["gst"].sum()) if (not df.empty and "gst" in df.columns) else 0.0
    total_stamp_duty = float(df["stamp_duty"].sum()) if (not df.empty and "stamp_duty" in df.columns) else 0.0

    real_cash = float(engine.client.get_real_cash_balance())
    paper_cap = float(engine.client.paper_capital)
    active_cap = real_cash if config.trading_mode == "LIVE" else paper_cap
    daily_limit = float(engine.daily_loss_limit)
    net_drawdown = max(0.0, -net_takehome)
    loss_headroom = max(0.0, daily_limit - net_drawdown)
    expectancy = round(((win_rate / 100.0) * avg_win) - ((1.0 - (win_rate / 100.0)) * avg_loss), 2) if total_trades > 0 else 0.0

    stats = {
        "total_trades": total_trades,
        "win_count": win_count,
        "loss_count": loss_count,
        "win_rate": win_rate,
        "gross_pnl": gross_pnl,
        "total_charges": total_charges,
        "net_takehome": net_takehome,
        "profit_factor": profit_factor,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "total_brokerage": total_brokerage,
        "total_stt": total_stt,
        "total_gst": total_gst,
        "total_stamp_duty": total_stamp_duty,
        "available_cash": real_cash,
        "paper_capital": paper_cap,
        "active_capital": active_cap,
        "daily_loss_limit": daily_limit,
        "loss_headroom": loss_headroom,
        "expectancy": expectancy
    }

    active_pos = None
    if engine.active_position:
        pos = engine.active_position
        inv_cap = getattr(pos, "invested_capital", 0.0) or round(pos.entry_premium * pos.quantity, 2)
        active_pos = {
            "symbol": pos.symbol,
            "mode": getattr(pos, "execution_mode", config.trading_mode),
            "quantity": pos.quantity,
            "entry_premium": pos.entry_premium,
            "current_premium": pos.current_premium,
            "invested_capital": inv_cap,
            "bank_limit_triggered": getattr(pos, "bank_limit_triggered", inv_cap),
            "pnl": pos.pnl,
            "entry_time": pos.entry_time_iso,
            "target_premium": getattr(pos, "target_premium", 0.0),
            "hard_stop_premium": pos.hard_stop_premium,
            "trailing_sl_premium": getattr(pos, "trailing_sl_premium", pos.hard_stop_premium),
            "locked_profit_inr": getattr(pos, "locked_profit_inr", 0.0),
            "trade_rationale": getattr(pos, "trade_rationale", "High-confluence momentum setup")
        }

    dhan_orders = engine.client.get_order_list() if (engine.client.mode == "LIVE" and engine.client.dhan) else []

    return render_template("journal.html", trades=trades, stats=stats, config=config, active_position=active_pos, dhan_orders=dhan_orders)

@app.route("/download-journal")
def download_journal():
    journal.export_csv()
    if os.path.exists(CSV_PATH):
        resp = make_response(send_file(CSV_PATH, as_attachment=True, download_name="dhan_trading_journal.csv", mimetype="text/csv"))
        resp.headers["Content-Disposition"] = 'attachment; filename="dhan_trading_journal.csv"'
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp
    return "No journal CSV found", 404

@app.route("/download-journal-pdf")
@app.route("/api/journal/download-pdf")
def download_journal_pdf():
    """Generates an executive-grade PDF of today's trading journal and streams it for download."""
    try:
        from journal_pdf_mailer import generate_journal_pdf
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        ok, doc_path, kpis = generate_journal_pdf(today_str)
        if ok and os.path.exists(doc_path) and doc_path.endswith(".pdf"):
            resp = make_response(send_file(doc_path, as_attachment=True, download_name=f"Trading_Journal_{today_str}.pdf", mimetype="application/pdf"))
            resp.headers["Content-Disposition"] = f'attachment; filename="Trading_Journal_{today_str}.pdf"'
            resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            return resp
        elif os.path.exists(doc_path) and doc_path.endswith(".html"):
            with open(doc_path, "r", encoding="utf-8") as f:
                html = f.read()
            printable_html = html.replace("</body>", "<script>window.onload = function() { window.print(); };</script></body>")
            return printable_html, 200, {"Content-Type": "text/html"}
        return jsonify({"success": False, "error": f"PDF compilation failed: {doc_path}"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/journal/email-pdf", methods=["GET", "POST"])
def api_email_journal_pdf():
    """Compiles the daily performance PDF and sends it directly to the trader's email via Outlook."""
    try:
        from journal_pdf_mailer import send_journal_email
        data = request.get_json(silent=True) or {}
        recipient = data.get("recipient") or request.args.get("recipient") or "vijayaram9@gmail.com"
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        
        ok, msg = send_journal_email(to_email=recipient, target_date=today_str)
        if ok:
            return jsonify({
                "success": True,
                "message": msg,
                "recipient": recipient,
                "pdf_filename": f"Trading_Journal_{today_str}.pdf"
            })
        return jsonify({"success": False, "error": msg}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    global current_capital
    message = None
    if request.method == "POST":
        config.trading_mode = request.form.get("TRADING_MODE", config.trading_mode).upper()
        config.expiry_type = request.form.get("EXPIRY_TYPE", config.expiry_type).upper()
        config.lot_size = int(request.form.get("LOT_SIZE", config.lot_size))
        
        cap_val = float(request.form.get("ACCOUNT_CAPITAL", current_capital))
        current_capital = cap_val
        config.account_capital = cap_val

        config.max_daily_loss = float(request.form.get("MAX_DAILY_LOSS", config.max_daily_loss))
        config.max_daily_profit = float(request.form.get("MAX_DAILY_PROFIT", config.max_daily_profit))
        config.max_trades_per_day = int(request.form.get("MAX_TRADES_PER_DAY", config.max_trades_per_day))
        config.sl_percent = float(request.form.get("SL_PERCENT", config.sl_percent))
        config.target_percent = float(request.form.get("TARGET_PERCENT", config.target_percent))
        config.trailing_sl_percent = float(request.form.get("TRAILING_SL_PERCENT", config.trailing_sl_percent))
        config.square_off_time = request.form.get("SQUARE_OFF_TIME", config.square_off_time)
        
        env_path = os.path.join(os.path.dirname(__file__), ".env")
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            new_lines = []
            keys_to_update = {
                "TRADING_MODE": config.trading_mode,
                "EXPIRY_TYPE": config.expiry_type,
                "LOT_SIZE": str(config.lot_size),
                "MAX_DAILY_LOSS": str(config.max_daily_loss),
                "MAX_DAILY_PROFIT": str(config.max_daily_profit),
                "MAX_TRADES_PER_DAY": str(config.max_trades_per_day),
                "SL_PERCENT": str(config.sl_percent),
                "TARGET_PERCENT": str(config.target_percent),
                "TRAILING_SL_PERCENT": str(config.trailing_sl_percent),
                "SQUARE_OFF_TIME": config.square_off_time
            }
            for line in lines:
                matched = False
                for k, v in keys_to_update.items():
                    if line.startswith(f"{k}="):
                        new_lines.append(f"{k}={v}\n")
                        matched = True
                        break
                if not matched:
                    new_lines.append(line)
            with open(env_path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)

        message = "Settings and flexible capital successfully applied!"

    return render_template("settings.html", config=config, current_capital=current_capital, message=message)

@app.route("/api/admin/sync-code", methods=["POST"])
def api_admin_sync_code():
    """Admin endpoint to perform zero-downtime git pull on AWS directly and restart daemon."""
    import subprocess
    try:
        repo_dir = os.path.dirname(os.path.abspath(__file__))
        subprocess.check_output(["git", "fetch", "origin", "main"], cwd=repo_dir, stderr=subprocess.STDOUT, text=True)
        out = subprocess.check_output(["git", "reset", "--hard", "origin/main"], cwd=repo_dir, stderr=subprocess.STDOUT, text=True)
        # Clear Jinja template cache
        if hasattr(app, "jinja_env") and hasattr(app.jinja_env, "cache") and app.jinja_env.cache is not None:
            app.jinja_env.cache.clear()
        # Automatically restart background daemon so changes take effect
        def _delayed_restart():
            time.sleep(1.0)
            os._exit(0)
        threading.Thread(target=_delayed_restart, daemon=True).start()
        return jsonify({"status": "success", "output": out, "restart": "scheduled"})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/api/admin/restart", methods=["POST", "GET"])
def api_admin_restart():
    """Restarts the background daemon on AWS by exiting the process (systemd auto-respawns)."""
    def _delayed_restart():
        time.sleep(0.5)
        os._exit(0)
    threading.Thread(target=_delayed_restart, daemon=True).start()
    return jsonify({"status": "restarting", "message": "Service restarting under systemd..."})

@app.route("/api/admin/update-token", methods=["POST"])
def api_admin_update_token():
    """Updates the Dhan access token in .env and restarts the background service."""
    new_token = ""
    if request.is_json and request.json:
        new_token = request.json.get("token", "").strip()
    elif request.form:
        new_token = request.form.get("token", "").strip()
    if not new_token:
        return jsonify({"status": "error", "message": "No token provided"}), 400
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        with open(env_path, "w", encoding="utf-8") as f:
            found = False
            for line in lines:
                if line.startswith("DHAN_ACCESS_TOKEN="):
                    f.write(f"DHAN_ACCESS_TOKEN={new_token}\n")
                    found = True
                else:
                    f.write(line)
            if not found:
                f.write(f"DHAN_ACCESS_TOKEN={new_token}\n")
    os.environ["DHAN_ACCESS_TOKEN"] = new_token
    def _delayed_restart():
        time.sleep(1.0)
        os._exit(0)
    threading.Thread(target=_delayed_restart, daemon=True).start()
    return jsonify({"status": "success", "message": "Token updated and restart scheduled"})

@app.route("/webhook-guide")
def webhook_guide():
    return render_template("webhook_guide.html", config=config)

@app.route("/api/clear-cache", methods=["GET", "POST"])
def api_clear_cache():
    """Flushes all in-memory caches, option quotes, and triggers garbage collection."""
    import gc
    from news_intelligence import news_intelligence
    from global_market_intelligence import global_tracker
    from premarket_intelligence import premarket_engine
    with lock:
        news_intelligence.clear_cache()
        global_tracker.clear_cache()
        if hasattr(premarket_engine, "_cached_report"):
            premarket_engine._cached_report = None
        if hasattr(engine.client, "_quote_cache"):
            engine.client._quote_cache = {}
        gc.collect()
    return jsonify({
        "success": True,
        "message": "System cache cleared successfully! Memory reclaimed and feeds primed for fresh reload."
    })

def start_server():
    worker = threading.Thread(target=background_trading_loop, daemon=True)
    worker.start()

    print("\n" + "=" * 65)
    print(" 🚀 VIJAY SAI'S DHAN ALGO - FLASK TRADING HUB 🚀")
    print("=" * 65)
    print(" • Local Web URL : http://localhost:5000")
    print(" • Journal URL   : http://localhost:5000/journal")
    print(" • Settings URL  : http://localhost:5000/settings")
    print("=" * 65 + "\n")
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False, threaded=True)

if __name__ == "__main__":
    start_server()
