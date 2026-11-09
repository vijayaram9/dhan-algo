import math
from dataclasses import dataclass
from typing import Dict, Tuple

def normal_cdf(x: float) -> float:
    """Cumulative distribution function for standard normal distribution (erf approximation)."""
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0

def normal_pdf(x: float) -> float:
    """Probability density function for standard normal distribution."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)

@dataclass
class OptionGreeks:
    option_type: str        # 'CE' or 'PE'
    strike_price: float
    spot_price: float
    time_to_expiry_days: float
    implied_volatility: float
    delta: float
    gamma: float
    theta_daily: float      # Time decay per day in Rupees/points
    vega: float             # Sensitivity per 1% IV change
    delta_interpretation: str
    theta_interpretation: str
    gamma_interpretation: str
    vega_interpretation: str
    theo_price: float = 0.0

class GreeksCalculator:
    """
    Institutional Option Greeks Engine (Black-Scholes-Merton model)
    Calculates Delta, Gamma, Theta, Vega and provides crystal-clear layman explanations.
    """
    @staticmethod
    def calculate_greeks(
        spot: float,
        strike: float,
        days_to_expiry: float,
        option_type: str = "CE",
        iv_pct: float = 14.5,
        risk_free_rate: float = 0.068 # 6.8% RBI repo rate
    ) -> OptionGreeks:
        # Avoid division by zero
        if spot <= 0 or strike <= 0:
            return OptionGreeks(
                option_type=option_type,
                strike_price=strike,
                spot_price=spot,
                time_to_expiry_days=days_to_expiry,
                implied_volatility=iv_pct,
                delta=0.5 if option_type.upper() == "CE" else -0.5,
                gamma=0.001,
                theta_daily=-5.0,
                vega=10.0,
                delta_interpretation="Standard ATM baseline sensitivity",
                theta_interpretation="Standard daily time decay",
                gamma_interpretation="Standard rate of delta change",
                vega_interpretation="Standard volatility sensitivity"
            )
        t = max(days_to_expiry, 0.08) / 365.0 # in years
        sigma = max(iv_pct, 5.0) / 100.0
        r = risk_free_rate

        sqrt_t = math.sqrt(t)
        d1 = (math.log(spot / strike) + (r + 0.5 * sigma ** 2) * t) / (sigma * sqrt_t)
        d2 = d1 - sigma * sqrt_t

        pdf_d1 = normal_pdf(d1)

        # Gamma (identical for Call & Put)
        gamma = pdf_d1 / (spot * sigma * sqrt_t)

        # Vega (identical for Call & Put, per 1% IV change)
        vega = (spot * sqrt_t * pdf_d1) / 100.0

        if option_type.upper() == "CE":
            delta = normal_cdf(d1)
            # Black-Scholes Theoretical Call Price
            theo_price = round(max(0.05, spot * normal_cdf(d1) - strike * math.exp(-r * t) * normal_cdf(d2)), 2)
            # Theta in points per year -> convert to daily
            theta_annual = -(spot * pdf_d1 * sigma) / (2.0 * sqrt_t) - (r * strike * math.exp(-r * t) * normal_cdf(d2))
            theta_daily = round(theta_annual / 365.0, 2)
        else: # PE
            delta = normal_cdf(d1) - 1.0
            # Black-Scholes Theoretical Put Price
            theo_price = round(max(0.05, strike * math.exp(-r * t) * normal_cdf(-d2) - spot * normal_cdf(-d1)), 2)
            theta_annual = -(spot * pdf_d1 * sigma) / (2.0 * sqrt_t) + (r * strike * math.exp(-r * t) * normal_cdf(-d2))
            theta_daily = round(theta_annual / 365.0, 2)

        delta = round(delta, 3)
        gamma = round(gamma, 4)
        vega = round(vega, 2)

        # Plain-English Layman Explanations
        delta_pct = abs(delta) * 100.0
        delta_desc = (
            f"If {spot:,.0f} moves up by 10 points, this option will gain ~₹{abs(delta)*10:.1f} per unit "
            f"({delta_pct:.0f}% speed of underlying move). "
            f"{'High sensitivity (ATM/ITM)' if delta_pct >= 45 else 'Low sensitivity (OTM - slower price reaction)'}."
        )

        theta_desc = (
            f"Every single day you hold this option, it loses approximately ₹{abs(theta_daily):.1f} points "
            f"(₹{abs(theta_daily)*65:.1f} per lot) just from the clock ticking! "
            f"This is why the bot enforces a strict 45–60 minute holding limit."
        )

        gamma_desc = (
            f"Gamma measures how fast Delta increases. A high Gamma ({gamma:.4f}) creates "
            f"rapid explosive premium expansion when a breakout happens, especially on expiry day (0DTE)."
        )

        vega_desc = (
            f"If market fear/volatility (India VIX) jumps by 1%, your option premium gains ~₹{vega:.2f} points. "
            f"If IV collapses after events (IV Crush), option loses premium even if price doesn't move."
        )

        return OptionGreeks(
            option_type=option_type.upper(),
            strike_price=strike,
            spot_price=spot,
            time_to_expiry_days=days_to_expiry,
            implied_volatility=iv_pct,
            delta=delta,
            gamma=gamma,
            theta_daily=theta_daily,
            vega=vega,
            delta_interpretation=delta_desc,
            theta_interpretation=theta_desc,
            gamma_interpretation=gamma_desc,
            vega_interpretation=vega_desc,
            theo_price=theo_price
        )

greeks_calculator = GreeksCalculator()
