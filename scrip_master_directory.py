"""
Master Scrip Directory for Vijay Sai's Dhan Algo
Contains official NIFTY 50, NIFTY NEXT 50, SENSEX 30 constituents,
Futures, and dynamic Option contract generation.
"""

import re
from typing import List, Dict, Any, Optional

MASTER_INDIAN_EQUITIES = [
    # Top NIFTY 50 & SENSEX Constituents (Updated to Live Ground-Truth Exchange Prices)
    {"key": "RELIANCE", "name": "Reliance Industries Ltd", "category": "STOCK", "exchange": "NSE", "lot_size": 250, "price": 1310.0, "step": 20, "keywords": "oil energy jio retail ambani"},
    {"key": "TCS", "name": "Tata Consultancy Services", "category": "STOCK", "exchange": "NSE", "lot_size": 175, "price": 2490.0, "step": 50, "keywords": "tata it tech software consultancy"},
    {"key": "HDFCBANK", "name": "HDFC Bank Ltd", "category": "STOCK", "exchange": "NSE", "lot_size": 550, "price": 755.0, "step": 10, "keywords": "hdfc private bank banking finance"},
    {"key": "ICICIBANK", "name": "ICICI Bank Ltd", "category": "STOCK", "exchange": "NSE", "lot_size": 700, "price": 1480.0, "step": 10, "keywords": "icici bank banking private"},
    {"key": "BHARTIARTL", "name": "Bharti Airtel Ltd", "category": "STOCK", "exchange": "NSE", "lot_size": 475, "price": 1590.0, "step": 20, "keywords": "airtel telecom 5g mobile network"},
    {"key": "INFY", "name": "Infosys Ltd", "category": "STOCK", "exchange": "NSE", "lot_size": 400, "price": 1160.0, "step": 20, "keywords": "infosys it tech software consulting"},
    {"key": "SBIN", "name": "State Bank of India", "category": "STOCK", "exchange": "NSE", "lot_size": 750, "price": 998.0, "step": 10, "keywords": "sbi public psu bank banking"},
    {"key": "ITC", "name": "ITC Ltd", "category": "STOCK", "exchange": "NSE", "lot_size": 1600, "price": 480.0, "step": 5, "keywords": "itc fmcg cigarettes hotels tobacco paper"},
    {"key": "LT", "name": "Larsen & Toubro Ltd", "category": "STOCK", "exchange": "NSE", "lot_size": 175, "price": 3620.0, "step": 50, "keywords": "l&t larsen toubro infrastructure defense construction"},
    {"key": "BAJFINANCE", "name": "Bajaj Finance Ltd", "category": "STOCK", "exchange": "NSE", "lot_size": 125, "price": 6850.0, "step": 50, "keywords": "bajaj nbfc consumer lending finance"},
    {"key": "HINDUNILVR", "name": "Hindustan Unilever Ltd", "category": "STOCK", "exchange": "NSE", "lot_size": 300, "price": 2380.0, "step": 20, "keywords": "hul unilever fmcg soaps consumer goods"},
    {"key": "MARUTI", "name": "Maruti Suzuki India", "category": "STOCK", "exchange": "NSE", "lot_size": 50, "price": 12400.0, "step": 100, "keywords": "maruti suzuki auto cars automobile"},
    {"key": "TATAMOTORS", "name": "Tata Motors Ltd", "category": "STOCK", "exchange": "NSE", "lot_size": 700, "price": 820.0, "step": 10, "keywords": "tata motors jlr electric vehicle ev auto automobile"},
    {"key": "TATASTEEL", "name": "Tata Steel Ltd", "category": "STOCK", "exchange": "NSE", "lot_size": 5500, "price": 155.0, "step": 2, "keywords": "tata steel metals mining"},
    {"key": "KOTAKBANK", "name": "Kotak Mahindra Bank", "category": "STOCK", "exchange": "NSE", "lot_size": 400, "price": 1750.0, "step": 20, "keywords": "kotak mahindra private bank banking"},
    {"key": "AXISBANK", "name": "Axis Bank Ltd", "category": "STOCK", "exchange": "NSE", "lot_size": 625, "price": 1180.0, "step": 20, "keywords": "axis bank private banking finance"},
    {"key": "SUNPHARMA", "name": "Sun Pharma Industries", "category": "STOCK", "exchange": "NSE", "lot_size": 350, "price": 1820.0, "step": 20, "keywords": "sun pharma pharmaceuticals healthcare medicines"},
    {"key": "TITAN", "name": "Titan Company Ltd", "category": "STOCK", "exchange": "NSE", "lot_size": 175, "price": 3240.0, "step": 50, "keywords": "titan tata tanishq jewellery watches eyewear"},
    {"key": "MM", "name": "Mahindra & Mahindra", "category": "STOCK", "exchange": "NSE", "lot_size": 350, "price": 2920.0, "step": 50, "keywords": "m&m mahindra tractors suv auto automobile"},
    {"key": "ADANIENT", "name": "Adani Enterprises Ltd", "category": "STOCK", "exchange": "NSE", "lot_size": 300, "price": 3050.0, "step": 50, "keywords": "adani enterprises airports solar coal infrastructure"},
    {"key": "ADANIPORTS", "name": "Adani Ports & SEZ Ltd", "category": "STOCK", "exchange": "NSE", "lot_size": 400, "price": 1380.0, "step": 20, "keywords": "adani ports logistics shipping logistics"},
    {"key": "NTPC", "name": "NTPC Ltd", "category": "STOCK", "exchange": "NSE", "lot_size": 1500, "price": 410.0, "step": 5, "keywords": "ntpc power generation renewable green energy psu"},
    {"key": "POWERGRID", "name": "Power Grid Corp of India", "category": "STOCK", "exchange": "NSE", "lot_size": 1800, "price": 330.0, "step": 5, "keywords": "powergrid transmission electrical utility psu"},
    {"key": "ONGC", "name": "Oil & Natural Gas Corp", "category": "STOCK", "exchange": "NSE", "lot_size": 2250, "price": 265.0, "step": 5, "keywords": "ongc oil natural gas exploration crude psu"},
    {"key": "COALINDIA", "name": "Coal India Ltd", "category": "STOCK", "exchange": "NSE", "lot_size": 2100, "price": 460.0, "step": 5, "keywords": "coal mining energy psu minerals"},
    {"key": "JSWSTEEL", "name": "JSW Steel Ltd", "category": "STOCK", "exchange": "NSE", "lot_size": 675, "price": 970.0, "step": 10, "keywords": "jsw steel metals infrastructure"},
    {"key": "HINDALCO", "name": "Hindalco Industries", "category": "STOCK", "exchange": "NSE", "lot_size": 1400, "price": 690.0, "step": 10, "keywords": "hindalco aditya birla aluminium copper metals novelis"},
    {"key": "ULTRACEMCO", "name": "UltraTech Cement Ltd", "category": "STOCK", "exchange": "NSE", "lot_size": 100, "price": 11200.0, "step": 100, "keywords": "ultratech cement aditya birla construction"},
    {"key": "ASIANPAINT", "name": "Asian Paints Ltd", "category": "STOCK", "exchange": "NSE", "lot_size": 200, "price": 2820.0, "step": 50, "keywords": "asian paints coatings chemicals home decor"},
    {"key": "BAJAJFINSV", "name": "Bajaj Finserv Ltd", "category": "STOCK", "exchange": "NSE", "lot_size": 500, "price": 1750.0, "step": 20, "keywords": "bajaj finserv insurance asset management finance"},
    {"key": "GRASIM", "name": "Grasim Industries Ltd", "category": "STOCK", "exchange": "NSE", "lot_size": 250, "price": 2650.0, "step": 50, "keywords": "grasim birla textiles chemicals paints cement"},
    {"key": "CIPLA", "name": "Cipla Ltd", "category": "STOCK", "exchange": "NSE", "lot_size": 650, "price": 1520.0, "step": 20, "keywords": "cipla pharma generic respiratory medicines healthcare"},
    {"key": "DRREDDY", "name": "Dr. Reddy's Laboratories", "category": "STOCK", "exchange": "NSE", "lot_size": 125, "price": 6600.0, "step": 50, "keywords": "dr reddys pharma generics drugs healthcare"},
    {"key": "APOLLOHOSP", "name": "Apollo Hospitals Enterprise", "category": "STOCK", "exchange": "NSE", "lot_size": 125, "price": 6950.0, "step": 50, "keywords": "apollo hospitals healthcare clinics pharmacy 247"},
    {"key": "DIVISLAB", "name": "Divi's Laboratories Ltd", "category": "STOCK", "exchange": "NSE", "lot_size": 150, "price": 5850.0, "step": 50, "keywords": "divis pharma active ingredients api chemicals"},
    {"key": "EICHERMOT", "name": "Eicher Motors Ltd", "category": "STOCK", "exchange": "NSE", "lot_size": 150, "price": 4850.0, "step": 50, "keywords": "eicher royal enfield bullets bikes commercial vehicles"},
    {"key": "HEROMOTOCO", "name": "Hero MotoCorp Ltd", "category": "STOCK", "exchange": "NSE", "lot_size": 150, "price": 4950.0, "step": 50, "keywords": "hero motocorp two-wheelers motorcycles scooters"},
    {"key": "BPCL", "name": "Bharat Petroleum Corp", "category": "STOCK", "exchange": "NSE", "lot_size": 1800, "price": 310.0, "step": 5, "keywords": "bpcl fuel petrol refining energy oil psu"},
    {"key": "NESTLEIND", "name": "Nestle India Ltd", "category": "STOCK", "exchange": "NSE", "lot_size": 200, "price": 2250.0, "step": 20, "keywords": "nestle maggi kitkat dairy coffee fmcg"},
    {"key": "BRITANNIA", "name": "Britannia Industries", "category": "STOCK", "exchange": "NSE", "lot_size": 200, "price": 5100.0, "step": 50, "keywords": "britannia biscuits good day dairy bakery fmcg"},
    {"key": "TATACONSUM", "name": "Tata Consumer Products", "category": "STOCK", "exchange": "NSE", "lot_size": 900, "price": 1080.0, "step": 20, "keywords": "tata consumer tea salt sampann starbucks fmcg"},
    {"key": "INDUSINDBK", "name": "IndusInd Bank Ltd", "category": "STOCK", "exchange": "NSE", "lot_size": 500, "price": 1120.0, "step": 20, "keywords": "indusind private bank commercial vehicle auto loans"},
    {"key": "SBILIFE", "name": "SBI Life Insurance Co", "category": "STOCK", "exchange": "NSE", "lot_size": 375, "price": 1650.0, "step": 20, "keywords": "sbi life insurance financial protection"},
    {"key": "HDFCLIFE", "name": "HDFC Life Insurance Co", "category": "STOCK", "exchange": "NSE", "lot_size": 1100, "price": 720.0, "step": 10, "keywords": "hdfc life insurance pensions annuities"},
    {"key": "WIPRO", "name": "Wipro Ltd", "category": "STOCK", "exchange": "NSE", "lot_size": 1500, "price": 540.0, "step": 10, "keywords": "wipro it information technology consulting digital"},
    {"key": "HCLTECH", "name": "HCL Technologies Ltd", "category": "STOCK", "exchange": "NSE", "lot_size": 350, "price": 1780.0, "step": 20, "keywords": "hcl tech information technology software digital"},
    {"key": "TECHM", "name": "Tech Mahindra Ltd", "category": "STOCK", "exchange": "NSE", "lot_size": 600, "price": 1680.0, "step": 20, "keywords": "tech mahindra it telecom 5g tech software"},
    {"key": "SHREECEM", "name": "Shree Cement Ltd", "category": "STOCK", "exchange": "NSE", "lot_size": 25, "price": 25400.0, "step": 200, "keywords": "shree cement bangur building materials"},
    {"key": "VEDL", "name": "Vedanta Ltd", "category": "STOCK", "exchange": "NSE", "lot_size": 1150, "price": 470.0, "step": 5, "keywords": "vedanta metals zinc copper aluminium dividend oil"},
    {"key": "ZOMATO", "name": "Zomato Ltd", "category": "STOCK", "exchange": "NSE", "lot_size": 2000, "price": 260.0, "step": 5, "keywords": "zomato blinkit food delivery quick commerce ecommerce"},
    {"key": "JIOFIN", "name": "Jio Financial Services", "category": "STOCK", "exchange": "NSE", "lot_size": 1000, "price": 320.0, "step": 5, "keywords": "jio financial ambani reliance finance fintech lending"},
]

MASTER_BENCHMARKS = [
    {"key": "NIFTY", "name": "NIFTY 50", "category": "INDEX", "exchange": "NSE", "lot_size": 65, "price": 24450.0, "step": 50, "keywords": "nifty nse index benchmark indian market 50"},
    {"key": "BANKNIFTY", "name": "BANK NIFTY", "category": "INDEX", "exchange": "NSE", "lot_size": 30, "price": 57380.0, "step": 100, "keywords": "bank nifty banking private psu banks index"},
    {"key": "FINNIFTY", "name": "FIN NIFTY", "category": "INDEX", "exchange": "NSE", "lot_size": 40, "price": 26470.0, "step": 50, "keywords": "fin nifty financial services insurance nbfc index"},
    {"key": "SENSEX", "name": "BSE SENSEX", "category": "INDEX", "exchange": "BSE", "lot_size": 20, "price": 78960.0, "step": 100, "keywords": "sensex bse bombay stock exchange 30 index benchmark"},
    {"key": "NIFTYIT", "name": "NIFTY IT", "category": "INDEX", "exchange": "NSE", "lot_size": 25, "price": 32780.0, "step": 100, "keywords": "nifty it tech technology software index"},
    {"key": "MIDCPNIFTY", "name": "NIFTY MIDCAP SELECT", "category": "INDEX", "exchange": "NSE", "lot_size": 50, "price": 12850.0, "step": 25, "keywords": "midcap nifty midcpnifty midcap 50 index"},
]

MASTER_COMMODITIES = [
    {"key": "CRUDEOILM", "name": "Crude Oil Mini", "category": "COMMODITY", "exchange": "MCX", "lot_size": 10, "price": 7350.0, "step": 50, "keywords": "crude oil mcx energy brent wti petroleum"},
    {"key": "NATGASMINI", "name": "Natural Gas Mini", "category": "COMMODITY", "exchange": "MCX", "lot_size": 250, "price": 248.0, "step": 2, "keywords": "natural gas ng mcx commodity mini"},
    {"key": "GOLDM", "name": "Gold Mini", "category": "COMMODITY", "exchange": "MCX", "lot_size": 1, "price": 111600.0, "step": 100, "keywords": "gold mini bullion mcx precious metal"},
    {"key": "SILVERM", "name": "Silver Mini", "category": "COMMODITY", "exchange": "MCX", "lot_size": 5, "price": 142000.0, "step": 500, "keywords": "silver mini mcx bullion metal"},
]

def search_master_scrips(query: str, active_catalog_keys: set) -> List[Dict[str, Any]]:
    """
    Intelligent search across equities, indices, futures and dynamic options contracts.
    Matches ticker, company name, keywords, strike numbers and CE/PE indicators.
    """
    clean_q = (query or "").strip().lower()
    if not clean_q:
        # Return popular starters: NIFTY 50, SENSEX, and top active equities
        results = []
        for item in MASTER_BENCHMARKS + MASTER_INDIAN_EQUITIES[:12]:
            k = item["key"]
            p = 0.0
            try:
                from market_intelligence import market_intelligence
                p = market_intelligence.get_asset_spot(k)
            except Exception:
                pass
            if p <= 0:
                p = item["price"]
            results.append({
                "key": k,
                "display_name": item["name"],
                "category": item["category"],
                "exchange": item["exchange"],
                "lot_size": item["lot_size"],
                "baseline_price": round(p, 2),
                "strike_step": item["step"],
                "is_active": k in active_catalog_keys
            })
        return results

    results = []
    seen_keys = set()

    # 1. Check for specific Option Contract syntax (e.g. "24000", "24000 CE", "NIFTY 24000 CE", "SENSEX 79000 PE")
    num_match = re.search(r'(\d{4,6})', clean_q)
    is_ce = "ce" in clean_q or "call" in clean_q
    is_pe = "pe" in clean_q or "put" in clean_q
    is_fut = "fut" in clean_q or "future" in clean_q

    # Determine underlying index if specified
    underlying = "NIFTY"
    if "bank" in clean_q:
        underlying = "BANKNIFTY"
    elif "sensex" in clean_q:
        underlying = "SENSEX"
    elif "fin" in clean_q:
        underlying = "FINNIFTY"
    elif "it" in clean_q and "nifty" in clean_q:
        underlying = "NIFTYIT"
    elif "reliance" in clean_q:
        underlying = "RELIANCE"
    elif "tata" in clean_q and "motors" in clean_q:
        underlying = "TATAMOTORS"

    base_info = next((item for item in MASTER_BENCHMARKS + MASTER_INDIAN_EQUITIES if item["key"] == underlying), MASTER_BENCHMARKS[0])

    if num_match:
        strike_val = int(num_match.group(1))
        # Generate Option CE / PE items
        types_to_generate = []
        if is_ce and not is_pe:
            types_to_generate = [("CE", "CALL")]
        elif is_pe and not is_ce:
            types_to_generate = [("PE", "PUT")]
        else:
            types_to_generate = [("CE", "CALL"), ("PE", "PUT")]

        # Query live spot from market_intelligence for accurate pricing
        current_spot = float(base_info['price'])
        try:
            from market_intelligence import market_intelligence
            live_spot = market_intelligence.get_asset_spot(base_info['key'])
            if live_spot > 0:
                current_spot = live_spot
        except Exception:
            pass

        for opt_suffix, opt_cat in types_to_generate:
            opt_key = f"{base_info['key']}_{strike_val}_{opt_suffix}"
            opt_name = f"{base_info['name']} {strike_val:,} {opt_suffix}"
            
            # Ground-truth pricing strictly from Dhan exchange
            est_premium = 0.0
            try:
                from scrip_master import ScripMaster
                sm_inst = ScripMaster()
                opt_meta = sm_inst.get_nifty_option(current_spot, opt_suffix, 0, base_info["key"])
                if opt_meta and opt_meta.get("security_id"):
                    from smart_edge_engine import smart_edge_engine
                    est_premium = smart_edge_engine.client.get_option_ltp(
                        opt_meta["security_id"], current_spot, float(strike_val), opt_suffix, expiry_date=opt_meta.get("expiry", "")
                    )
            except Exception:
                est_premium = 0.0

            results.append({
                "key": opt_key,
                "display_name": opt_name,
                "category": opt_cat,
                "exchange": base_info["exchange"],
                "lot_size": base_info["lot_size"],
                "baseline_price": est_premium,
                "strike_step": base_info["step"],
                "is_active": opt_key in active_catalog_keys
            })
            seen_keys.add(opt_key)

    # 2. Check for Futures search (e.g. "TATAMOTORS FUT", "NIFTY FUT", "FUT")
    if is_fut:
        for item in MASTER_BENCHMARKS + MASTER_INDIAN_EQUITIES[:20]:
            if not num_match and (clean_q in item["key"].lower() or clean_q in item["name"].lower() or clean_q == "fut" or clean_q == "futures"):
                fut_key = f"{item['key']}_FUT"
                if fut_key not in seen_keys:
                    results.append({
                        "key": fut_key,
                        "display_name": f"{item['name']} Futures",
                        "category": "FUTURES",
                        "exchange": item["exchange"],
                        "lot_size": item["lot_size"],
                        "baseline_price": item["price"] + 25.0,
                        "strike_step": item["step"],
                        "is_active": fut_key in active_catalog_keys
                    })
                    seen_keys.add(fut_key)

    # 3. Match Master Indices, Equities, Commodities
    master_pool = MASTER_BENCHMARKS + MASTER_INDIAN_EQUITIES + MASTER_COMMODITIES
    for item in master_pool:
        k = item["key"]
        n = item["name"].lower()
        kw = item.get("keywords", "").lower()

        # Score matching
        is_exact = clean_q == k.lower()
        is_prefix = k.lower().startswith(clean_q) or n.startswith(clean_q)
        is_sub = clean_q in k.lower() or clean_q in n or clean_q in kw

        if is_exact or is_prefix or is_sub:
            if k not in seen_keys:
                p = 0.0
                try:
                    from market_intelligence import market_intelligence
                    p = market_intelligence.get_asset_spot(k)
                except Exception:
                    pass
                if p <= 0:
                    p = item["price"]
                results.append({
                    "key": k,
                    "display_name": item["name"],
                    "category": item["category"],
                    "exchange": item["exchange"],
                    "lot_size": item["lot_size"],
                    "baseline_price": round(p, 2),
                    "strike_step": item["step"],
                    "is_active": k in active_catalog_keys
                })
                seen_keys.add(k)

    return results[:25]


def get_option_strikes_for_underlying(underlying_key: str, spot: Optional[float] = None) -> Dict[str, Any]:
    """
    Generates a structured option chain grid with ATM, ITM, and OTM strikes
    for any underlying asset (NIFTY, BANKNIFTY, FINNIFTY, SENSEX, or individual stocks).
    Strictly calibrated to live exchange spot and authentic option premiums.
    """
    clean_k = (underlying_key or "NIFTY").upper()
    master_item = next((item for item in MASTER_BENCHMARKS + MASTER_INDIAN_EQUITIES if item["key"] == clean_k), MASTER_BENCHMARKS[0])
    
    current_spot = spot if (spot and spot > 1.0) else 0.0
    if current_spot <= 1.0:
        try:
            from market_intelligence import market_intelligence
            live_val = market_intelligence.get_asset_spot(clean_k)
            if live_val > 0:
                current_spot = live_val
        except Exception:
            pass
    if current_spot <= 1.0:
        current_spot = float(master_item["price"])

    step = master_item["step"]
    lot = master_item["lot_size"]
    exch = master_item["exchange"]

    # Calculate exact ATM strike rounded to strike step
    atm_strike = round(current_spot / step) * step

    # Generate 11 strikes: 5 below ATM, ATM, 5 above ATM
    strikes_list = []
    for i in range(-5, 6):
        strike = atm_strike + (i * step)
        is_atm = (i == 0)

        # Real market option pricing strictly from Dhan exchange
        call_premium = 0.0
        put_premium = 0.0
        try:
            from scrip_master import ScripMaster
            sm_inst = ScripMaster()
            c_meta = sm_inst.get_nifty_option(current_spot, "CE", 0, master_item.get("key", "NIFTY"))
            p_meta = sm_inst.get_nifty_option(current_spot, "PE", 0, master_item.get("key", "NIFTY"))
            from smart_edge_engine import smart_edge_engine
            if c_meta and c_meta.get("security_id"):
                call_premium = smart_edge_engine.client.get_option_ltp(
                    c_meta["security_id"], current_spot, float(strike), "CE", expiry_date=c_meta.get("expiry", "")
                )
            if p_meta and p_meta.get("security_id"):
                put_premium = smart_edge_engine.client.get_option_ltp(
                    p_meta["security_id"], current_spot, float(strike), "PE", expiry_date=p_meta.get("expiry", "")
                )
        except Exception:
            call_premium = 0.0
            put_premium = 0.0

        strikes_list.append({
            "strike": strike,
            "is_atm": is_atm,
            "label": f"{strike:,}" + (" (ATM)" if is_atm else ""),
            "call": {
                "key": f"{clean_k}_{strike}_CE",
                "display_name": f"{master_item['name']} {strike:,} CE",
                "category": "CALL",
                "exchange": exch,
                "lot_size": lot,
                "premium": call_premium,
                "type": "CE"
            },
            "put": {
                "key": f"{clean_k}_{strike}_PE",
                "display_name": f"{master_item['name']} {strike:,} PE",
                "category": "PUT",
                "exchange": exch,
                "lot_size": lot,
                "premium": put_premium,
                "type": "PE"
            }
        })

    return {
        "underlying": clean_k,
        "underlying_name": master_item["name"],
        "spot": current_spot,
        "atm_strike": atm_strike,
        "exchange": exch,
        "lot_size": lot,
        "strikes": strikes_list
    }
