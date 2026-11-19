"""
Live NSE F&O Stock Momentum & News Catalyst Scanner.
Ranks liquid NSE F&O stocks by Relative Volume (RVOL), intraday range expansion,
and live business news headlines (cached 5 minutes, non-blocking).
"""

import time
import urllib.request
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

STOCK_SECTOR_MAP: Dict[str, str] = {
    "RELIANCE": "ENERGY",
    "HDFCBANK": "BANKING",
    "ICICIBANK": "BANKING",
    "SBIN": "BANKING",
    "AXISBANK": "BANKING",
    "KOTAKBANK": "BANKING",
    "INFY": "IT",
    "TCS": "IT",
    "BHARTIARTL": "TELECOM",
    "LT": "INFRA",
    "TATASTEEL": "METALS",
    "BAJFINANCE": "NBFC",
    "MARUTI": "AUTO",
    "M&M": "AUTO",
}

STOCK_NEWS_KEYWORDS: Dict[str, List[str]] = {
    "RELIANCE": ["reliance", "jio", "ril"],
    "HDFCBANK": ["hdfc bank", "hdfcbank"],
    "ICICIBANK": ["icici bank", "icicibank"],
    "SBIN": ["sbi", "state bank"],
    "AXISBANK": ["axis bank", "axisbank"],
    "KOTAKBANK": ["kotak"],
    "INFY": ["infosys", "infy"],
    "TCS": ["tcs", "tata consultancy"],
    "BHARTIARTL": ["bharti airtel", "airtel"],
    "LT": ["l&t", "larsen"],
    "TATASTEEL": ["tata steel", "tatasteel"],
    "BAJFINANCE": ["bajaj finance", "bajfinance"],
    "MARUTI": ["maruti"],
    "M&M": ["mahindra", "m&m"],
}

NEWS_RSS_URLS = [
    "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
    "https://www.moneycontrol.com/rss/marketreports.xml",
]


class StockMomentumScanner:
    def __init__(self):
        self._news_cache: Dict[str, str] = {}
        self._news_ts: float = 0.0

    def get_news_catalysts(self) -> Dict[str, str]:
        """Returns {asset_key: headline} for stocks mentioned in live market RSS (cached 5m)."""
        now = time.time()
        if (now - self._news_ts) < 300.0 and self._news_ts > 0:
            return self._news_cache

        hits: Dict[str, str] = {}
        for url in NEWS_RSS_URLS:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=2.5) as resp:
                    xml_bytes = resp.read()
                root = ET.fromstring(xml_bytes)
                for item in root.findall(".//item")[:40]:
                    title_el = item.find("title")
                    if title_el is None or not title_el.text:
                        continue
                    title = title_el.text.strip()
                    low = title.lower()
                    for sym, kws in STOCK_NEWS_KEYWORDS.items():
                        if sym not in hits and any(kw in low for kw in kws):
                            hits[sym] = title[:120]
            except Exception:
                continue

        self._news_cache = hits
        self._news_ts = now
        return hits

    def get_sector(self, asset_key: str) -> str:
        return STOCK_SECTOR_MAP.get(asset_key.upper(), "OTHER")

    def score_stock(self, asset_key: str, df_bars) -> Dict[str, object]:
        """Computes RVOL, intraday move %, sector, and live news catalyst for an F&O stock."""
        sector = self.get_sector(asset_key)
        rvol = 1.0
        pct_move = 0.0
        try:
            if df_bars is not None and len(df_bars) >= 21 and "volume" in df_bars.columns:
                comp_vol = float(df_bars["volume"].iloc[-2] or 0.0)
                avg_vol20 = float(df_bars["volume"].iloc[-22:-2].mean() or 0.0)
                if avg_vol20 > 0 and comp_vol > 0:
                    rvol = round(comp_vol / avg_vol20, 2)
                open_px = float(df_bars["open"].iloc[-12] if len(df_bars) >= 12 else df_bars["open"].iloc[0])
                last_px = float(df_bars["close"].iloc[-1])
                if open_px > 0:
                    pct_move = round(((last_px - open_px) / open_px) * 100.0, 2)
        except Exception:
            pass

        news_map = self.get_news_catalysts()
        news_title = news_map.get(asset_key.upper(), "")
        news_hit = bool(news_title)
        momentum_ok = (rvol >= 1.35) or (news_hit and rvol >= 1.0 and abs(pct_move) >= 0.4)
        return {
            "asset_key": asset_key,
            "sector": sector,
            "rvol": rvol,
            "pct_move": pct_move,
            "news_hit": news_hit,
            "news_title": news_title,
            "momentum_ok": momentum_ok,
        }


stock_scanner = StockMomentumScanner()
