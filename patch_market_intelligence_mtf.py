with open('market_intelligence.py', 'r', encoding='utf-8') as f:
    text = f.read()

# 1. Import mtf_engine at top
import_stmt = '''from indicators import IndicatorEngine
from greeks_calculator import greeks_calculator, OptionGreeks
from mtf_engine import mtf_engine, MultiTimeframeAnalysis'''

old_import = '''from indicators import IndicatorEngine
from greeks_calculator import greeks_calculator, OptionGreeks'''

if old_import in text:
    text = text.replace(old_import, import_stmt, 1)
    print("Added mtf_engine import to market_intelligence.py")

# 2. Replace determine_asset_directional_bias with real MTF evaluation
old_bias = '''    def determine_asset_directional_bias(self, asset_key: str, spot: float) -> bool:
        """
        Determines whether an asset is in a Bullish markup (True) or Bearish distribution (False).
        Allows the algorithm to spot high-conviction opportunities on BOTH Call (CE) and Put (PE) sides!
        """
        # Dynamic sector divergence:
        # NIFTYIT and BANKNIFTY/SENSEX often show bearish pullbacks, creating prime Put buying setups!
        asset_biases = {
            "FINNIFTY": True,   # Expiry Gamma Expansion (Bullish CE)
            "NIFTY": True,      # Trend Wave (Bullish CE)
            "BANKNIFTY": False, # Pullback below VWAP (Bearish PE Opportunity)
            "NIFTYIT": False,   # Tech sector distribution below 21 EMA (Bearish PE Opportunity)
            "SENSEX": False,    # Index retest of breakdown level (Bearish PE Opportunity)
            "RELIANCE": True,   # Bullish momentum (Bullish CE)
            "HDFCBANK": False   # Retracement into support (Bearish PE Opportunity)
        }
        return asset_biases.get(asset_key, True)'''

new_bias = '''    def determine_asset_directional_bias(self, asset_key: str, spot: float) -> bool:
        """
        Dynamically analyzes Multi-Timeframe Trend (15m + 5m + 1m) and Key Support/Resistance
        to determine whether an asset is in high-conviction Bullish markup (True) or Bearish distribution (False).
        Eliminates static biases and adapts in real-time to price action!
        """
        try:
            info = ASSET_CATALOG.get(asset_key, ASSET_CATALOG["NIFTY"])
            analysis = mtf_engine.analyze(asset_key, info.display_name, spot)
            return analysis.recommended_action == "BUY_CE"
        except Exception:
            info = ASSET_CATALOG.get(asset_key, ASSET_CATALOG["NIFTY"])
            pp = round(spot / info.strike_step) * info.strike_step
            return spot >= pp'''

if old_bias in text:
    text = text.replace(old_bias, new_bias, 1)
    print("Replaced static determine_asset_directional_bias with real MTF analysis!")
else:
    print("Could not find old_bias in market_intelligence.py")

# 3. Replace get_trade_verdict to return full MTF Analysis
old_verdict = '''    def get_trade_verdict(
        self,
        asset_key: str,
        spot: float,
        bullish_count: int,
        total_count: int,
        advanced_meta: dict
    ) -> dict:'''

new_verdict_func = '''    def get_trade_verdict(
        self,
        asset_key: str,
        spot: float,
        bullish_count: int,
        total_count: int,
        advanced_meta: dict
    ) -> dict:
        is_open, open_desc = self.is_market_open(asset_key)
        info = ASSET_CATALOG.get(asset_key, ASSET_CATALOG["NIFTY"])
        atm_strike = round(spot / info.strike_step) * info.strike_step
        
        # Real-time Multi-Timeframe Analysis (15m Macro Trend, 5m Support/Resistance Stage, 1m Order Flow)
        mtf = mtf_engine.analyze(asset_key, info.display_name, spot)

        if not is_open:
            return {
                "action": f"STAND DOWN (MARKET CLOSED)",
                "suggested_contract": f"{info.display_name} {atm_strike:,.0f} {mtf.option_type}",
                "timing": f"MARKET CLOSED — AWAIT 09:15 AM IST (MTF Setup: {mtf.setup_type})",
                "opportunity_state": "SESSION ENDED",
                "thesis": [
                    f"Indian exchanges currently closed. Session status: {open_desc}",
                    f"15m Macro Trend: {mtf.tf_15m.trend} ({mtf.tf_15m.location_desc})",
                    f"5m Setup Stage: {mtf.tf_5m.stage} ({mtf.tf_5m.location_desc})",
                    f"Expected Risk-to-Reward: {mtf.risk_reward_ratio}"
                ],
                "mtf_analysis": {
                    "tf_15m": mtf.tf_15m.__dict__,
                    "tf_5m": mtf.tf_5m.__dict__,
                    "tf_1m": mtf.tf_1m.__dict__,
                    "setup_type": mtf.setup_type,
                    "market_regime": mtf.market_regime,
                    "risk_reward_ratio": mtf.risk_reward_ratio,
                    "confluence_score": mtf.confluence_score,
                    "rationale": mtf.rationale,
                    "is_hero_zero_active": mtf.is_hero_zero_active,
                    "key_reasons": mtf.key_reasons
                },
                "is_market_open": False
            }

        return {
            "action": f"{'BUY' if mtf.recommended_action != 'STAND_DOWN' else ''} {mtf.recommended_option}".strip(),
            "suggested_contract": mtf.recommended_option,
            "timing": f"{mtf.setup_type.replace('_', ' ')} (R:R {mtf.risk_reward_ratio})",
            "opportunity_state": mtf.setup_type,
            "thesis": mtf.key_reasons + [mtf.rationale],
            "mtf_analysis": {
                "tf_15m": mtf.tf_15m.__dict__,
                "tf_5m": mtf.tf_5m.__dict__,
                "tf_1m": mtf.tf_1m.__dict__,
                "setup_type": mtf.setup_type,
                "market_regime": mtf.market_regime,
                "risk_reward_ratio": mtf.risk_reward_ratio,
                "confluence_score": mtf.confluence_score,
                "rationale": mtf.rationale,
                "is_hero_zero_active": mtf.is_hero_zero_active,
                "key_reasons": mtf.key_reasons
            },
            "is_market_open": True
        }

    def _old_get_trade_verdict(
        self,
        asset_key: str,
        spot: float,
        bullish_count: int,
        total_count: int,
        advanced_meta: dict
    ) -> dict:'''

if old_verdict in text:
    text = text.replace(old_verdict, new_verdict_func, 1)
    print("Replaced get_trade_verdict with MTF-powered trade selection!")
else:
    print("Could not find old_verdict in market_intelligence.py")

with open('market_intelligence.py', 'w', encoding='utf-8') as f:
    f.write(text)
print("Saved market_intelligence.py successfully!")
