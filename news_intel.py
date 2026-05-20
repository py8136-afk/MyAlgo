"""
═══════════════════════════════════════════════════════════════════
  news_intel.py — News Intelligence Layer
  by Prateek x Claude

  HOW REAL HEDGE FUNDS USE NEWS (implemented here):
  ─────────────────────────────────────────────────
  1. HARD CALENDAR BLOCKING
     Known event days (RBI MPC, FOMC, Budget, Election results)
     are hardcoded. No trade is EVER placed on these days.
     Hedge funds call this "going flat into events."

  2. GLOBAL MARKET SENTIMENT (pre-market pulse)
     Check Dow futures + SGX Nifty before every trade.
     Strong negative = bearish bias or block.
     Strong positive = bullish confirmation.

  3. NEWS FLOW SCORING (NLP-lite)
     Scan Google News RSS for India/Nifty headlines.
     Score each headline using a weighted keyword model.
     Positive news boosts position size. Negative blocks.

  4. DIRECTION ALIGNMENT BONUS
     If signal direction matches news sentiment → extra conviction.
     If they conflict → reduce size significantly.

  5. VIX REGIME DETECTION
     Track VIX change from yesterday's close.
     Sudden VIX spike (>15% intraday) = danger, block trade.
     VIX falling = calm market, increase size slightly.

  SCORING SYSTEM:
    Score -10 to +10
    < -5  → BLOCK  (no trade)
    -5 to -1 → CAUTION (0.5x size)
    -1 to +2 → NEUTRAL (1.0x size)
    +2 to +5 → POSITIVE (1.25x size)
    > +5  → STRONG  (1.5x size, capped)

  SOURCES USED (all free, no paid API):
    - RBI official RSS — surprise announcements, emergency circulars
    - NSE official RSS — trading halts, circuit breakers, notices
    - SEBI official RSS — bans, emergency orders, circulars
    - ET Markets + Moneycontrol RSS — breaking India financial news
    - Google News RSS — broader India/Nifty headlines
    - yfinance — Dow futures, crude oil, Nifty prev close
    - Finnhub (free tier) — global market news
    - Hardcoded calendar — RBI MPC, FOMC, Budget dates
═══════════════════════════════════════════════════════════════════
"""

import datetime
import os
import json

try:
    import feedparser
    FEEDPARSER_OK = True
except ImportError:
    FEEDPARSER_OK = False

try:
    import yfinance as yf
    YFINANCE_OK = True
except ImportError:
    YFINANCE_OK = False

try:
    import requests
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False

# ─── Finnhub API key (get free at finnhub.io) ──────────────────────
# Paste your key here after signing up at finnhub.io
FINNHUB_KEY = "d7jhmb9r01qhf13etej0d7jhmb9r01qhf13etejg"

# ─── Hardcoded event calendar (NEVER trade on these dates) ─────────
# Format: "YYYY-MM-DD": "Event description"
# RBI MPC FY27: Apr 8, Jun 5, Aug (TBD), Oct (TBD), Dec (TBD), Feb 2027
# FOMC 2026: Jan 28, Mar 18, Apr 29, Jun 17, Jul 29, Sep 16, Oct 28, Dec 9
# India Budget: usually Feb 1
# Election results: update as needed

BLOCKED_CALENDAR = {
    # RBI MPC FY27 (decision announced on last day of 3-day meeting)
    "2026-04-08": "RBI MPC Decision",
    "2026-06-05": "RBI MPC Decision",
    "2026-08-06": "RBI MPC Decision (est.)",
    "2026-10-08": "RBI MPC Decision (est.)",
    "2026-12-10": "RBI MPC Decision (est.)",
    "2027-02-05": "RBI MPC Decision (est.)",

    # FOMC 2026
    "2026-01-28": "US FOMC Decision",
    "2026-03-18": "US FOMC Decision",
    "2026-04-29": "US FOMC Decision",
    "2026-06-17": "US FOMC Decision",
    "2026-07-29": "US FOMC Decision",
    "2026-09-16": "US FOMC Decision",
    "2026-10-28": "US FOMC Decision",
    "2026-12-09": "US FOMC Decision",

    # India Budget
    "2027-02-01": "Union Budget",

    # Add more as known
}

# ─── Keyword scoring model ──────────────────────────────────────────
# (keyword, score, is_hard_block)
# Hard blocks immediately set score to -10, skip everything else

KEYWORD_RULES = [
    # ── HARD BLOCKS (-10, instant) ──────────────────────────────────
    ("emergency meeting",        -10, True),
    ("emergency rate",           -10, True),
    ("circuit breaker",          -10, True),
    ("trading halt",             -10, True),
    ("market suspended",         -10, True),
    ("election result",          -10, True),
    ("budget announcement",      -10, True),
    ("nuclear",                  -10, True),
    ("terror attack",            -10, True),
    ("major earthquake",         -10, True),

    # ── STRONG NEGATIVE (-3 each) ────────────────────────────────────
    ("rbi rate hike",            -3, False),
    ("rate hike",                -3, False),
    ("repo rate hike",           -3, False),
    ("war declared",             -3, False),
    ("sanctions",                -3, False),
    ("sebi ban",                 -3, False),
    ("market crash",             -3, False),
    ("nifty crash",              -3, False),
    ("black monday",             -3, False),
    ("recession fears",          -2, False),

    # ── MODERATE NEGATIVE (-2 each) ─────────────────────────────────
    ("sell-off",                 -2, False),
    ("selloff",                  -2, False),
    ("fii selling",              -2, False),
    ("foreign outflow",          -2, False),
    ("crude oil spike",          -2, False),
    ("oil prices surge",         -2, False),
    ("inflation surge",          -2, False),
    ("gdp miss",                 -2, False),
    ("current account deficit",  -2, False),
    ("rupee falls",              -2, False),
    ("rupee weakens",            -2, False),
    ("trade war",                -2, False),
    ("tariff",                   -1, False),
    ("geopolitical",             -1, False),

    # ── MILD NEGATIVE (-1 each) ──────────────────────────────────────
    ("uncertainty",              -1, False),
    ("volatility surge",         -1, False),
    ("investors cautious",       -1, False),
    ("mixed signals",            -1, False),
    ("rate cut uncertainty",     -1, False),

    # ── MILD POSITIVE (+1 each) ──────────────────────────────────────
    ("market steady",            +1, False),
    ("nifty gains",              +1, False),
    ("fii buying",               +2, False),
    ("foreign inflow",           +2, False),
    ("rate cut",                 +2, False),
    ("repo rate cut",            +2, False),
    ("rbi rate cut",             +2, False),
    ("gdp beat",                 +2, False),
    ("inflation eases",          +2, False),
    ("strong earnings",          +1, False),
    ("rally",                    +1, False),
    ("breakout",                 +1, False),
    ("bull run",                 +2, False),

    # ── STRONG POSITIVE (+2 each) ────────────────────────────────────
    ("record high",              +2, False),
    ("nifty record",             +2, False),
    ("fii net buyers",           +3, False),
    ("strong gdp",               +2, False),
    ("india growth",             +1, False),
    ("market surge",             +2, False),
    ("stimulus",                 +2, False),
    ("policy support",           +2, False),
]

# ─── Score → verdict mapping ────────────────────────────────────────

def score_to_verdict(score):
    if score <= -5:
        return "BLOCK",   0.0
    elif score <= -2:
        return "CAUTION", 0.5
    elif score <= 1:
        return "NEUTRAL", 1.0
    elif score <= 4:
        return "POSITIVE", 1.25
    else:
        return "STRONG",  1.5


# ═══════════════════════════════════════════════════════════════════
#  MODULE 1: CALENDAR CHECK
# ═══════════════════════════════════════════════════════════════════

def check_event_calendar():
    """
    Returns (blocked, reason) based on hardcoded event calendar.
    Also checks ±1 day for events that affect pre/post market.
    """
    today = datetime.date.today()
    tomorrow = today + datetime.timedelta(days=1)

    for date_str, event in BLOCKED_CALENDAR.items():
        event_date = datetime.date.fromisoformat(date_str)
        if event_date == today:
            return True, f"📅 EVENT DAY: {event} ({date_str})"
        # Day before major events — often volatile too
        if event_date == tomorrow and "RBI" in event or "FOMC" in event or "Budget" in event:
            return False, f"⚠️ Tomorrow is {event} — consider caution"

    return False, ""


# ═══════════════════════════════════════════════════════════════════
#  MODULE 2: GLOBAL MARKET PULSE (SGX Nifty + Dow Futures)
# ═══════════════════════════════════════════════════════════════════

def get_global_market_score():
    """
    Returns (score -4 to +4, detail_string)
    Uses yfinance to fetch SGX Nifty and Dow futures.
    """
    if not YFINANCE_OK:
        return 0, "yfinance not installed"

    score = 0
    details = []

    try:
        # SGX Nifty (Singapore-listed Nifty futures)
        # Symbol on yfinance: NI.SI or ^SGNIFTY (may vary)
        # Fallback: use Nifty 50 previous close vs current
        nifty = yf.Ticker("^NSEI")
        nifty_hist = nifty.history(period="2d", interval="1d")
        if len(nifty_hist) >= 2:
            prev_close = nifty_hist["Close"].iloc[-2]
            last_close = nifty_hist["Close"].iloc[-1]
            pct_change = (last_close - prev_close) / prev_close * 100
            if pct_change >= 1.0:
                score += 2
                details.append(f"Nifty prev close: +{pct_change:.1f}% ✅")
            elif pct_change >= 0.3:
                score += 1
                details.append(f"Nifty prev close: +{pct_change:.1f}% 🟢")
            elif pct_change <= -1.5:
                score -= 3
                details.append(f"Nifty prev close: {pct_change:.1f}% 🔴🔴")
            elif pct_change <= -0.5:
                score -= 2
                details.append(f"Nifty prev close: {pct_change:.1f}% 🔴")
            else:
                details.append(f"Nifty prev close: {pct_change:.1f}% (neutral)")
    except Exception as e:
        details.append(f"Nifty data unavailable: {e}")

    try:
        # Dow Jones Futures
        dow = yf.Ticker("YM=F")
        dow_info = dow.history(period="1d", interval="5m")
        if not dow_info.empty:
            open_price = dow_info["Open"].iloc[0]
            last_price = dow_info["Close"].iloc[-1]
            pct = (last_price - open_price) / open_price * 100
            if pct >= 0.5:
                score += 1
                details.append(f"Dow futures: +{pct:.1f}% ✅")
            elif pct <= -1.0:
                score -= 2
                details.append(f"Dow futures: {pct:.1f}% 🔴")
            elif pct <= -0.5:
                score -= 1
                details.append(f"Dow futures: {pct:.1f}% 🟡")
            else:
                details.append(f"Dow futures: {pct:.1f}% (neutral)")
    except Exception as e:
        details.append(f"Dow futures unavailable: {e}")

    try:
        # Crude oil — affects India significantly
        crude = yf.Ticker("CL=F")
        crude_info = crude.history(period="1d", interval="5m")
        if not crude_info.empty:
            open_p = crude_info["Open"].iloc[0]
            last_p = crude_info["Close"].iloc[-1]
            pct = (last_p - open_p) / open_p * 100
            if pct >= 2.0:
                score -= 1
                details.append(f"Crude oil: +{pct:.1f}% ⚠️ (inflation risk)")
            elif pct <= -2.0:
                score += 1
                details.append(f"Crude oil: {pct:.1f}% ✅ (inflation easing)")
            else:
                details.append(f"Crude oil: {pct:.1f}% (neutral)")
    except Exception as e:
        details.append(f"Crude unavailable: {e}")

    return max(-4, min(4, score)), " | ".join(details)


# ═══════════════════════════════════════════════════════════════════
#  MODULE 3: NEWS HEADLINE SCORING (Google News RSS)
# ═══════════════════════════════════════════════════════════════════

def fetch_india_headlines():
    """
    Fetch last 2 hours of India/Nifty headlines from Google News RSS.
    Returns list of headline strings.
    """
    if not FEEDPARSER_OK:
        return []

    queries = [
        "Nifty+stock+market+India",
        "RBI+India+economy",
        "FII+DII+India+market",
        "Sensex+Nifty+today",
    ]

    headlines = []
    cutoff = datetime.datetime.now() - datetime.timedelta(hours=3)

    for q in queries:
        try:
            url = f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
            feed = feedparser.parse(url)
            for entry in feed.entries[:5]:
                title = entry.get("title", "").lower()
                # Check if recent (within 3 hours)
                published = entry.get("published_parsed")
                if published:
                    pub_dt = datetime.datetime(*published[:6])
                    if pub_dt < cutoff:
                        continue
                if title and title not in headlines:
                    headlines.append(title)
        except Exception:
            continue

    return headlines


# ═══════════════════════════════════════════════════════════════════
#  MODULE 3B: RBI / NSE / SEBI OFFICIAL FEEDS (real-time circulars)
#  These are the sources hedge funds monitor for surprise events.
#  Any hit on these feeds = IMMEDIATE BLOCK, no argument.
# ═══════════════════════════════════════════════════════════════════

# Official RSS feeds — all free, no signup
OFFICIAL_FEEDS = {
    "RBI":  "https://rbi.org.in/Scripts/rss.aspx",
    "NSE":  "https://nseindia.com/api/rss?content=circulars",
    "SEBI": "https://www.sebi.gov.in/sebiweb/other/OtherAction.do?doRss=yes",
    "ET_MARKETS": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "MONEYCONTROL": "https://www.moneycontrol.com/rss/latestnews.xml",
}

# Keywords that trigger IMMEDIATE BLOCK if found in official feeds
OFFICIAL_BLOCK_KEYWORDS = [
    "emergency", "unscheduled", "special meeting", "extraordinary",
    "trading halt", "trading suspended", "circuit breaker",
    "market closure", "force majeure", "act of god",
    "terror", "attack", "blast", "explosion",
    "ban on trading", "sebi order", "sebi ban",
    "rbi intervention", "rbi emergency",
    "nuclear", "war declared", "war breaks",
    "rupee freefall", "currency crisis",
    "default", "sovereign default",
    "stock exchange closed",
]

# Keywords that trigger CAUTION (-2) if found in official feeds
OFFICIAL_CAUTION_KEYWORDS = [
    "circular", "advisory", "notice to members",
    "caution", "warning", "risk management",
    "margin revision", "lot size change",
    "expiry change", "holiday", "trading holiday",
    "system maintenance", "technical issue",
]


def check_official_feeds():
    """
    Poll RBI, NSE, SEBI and major financial news RSS feeds.
    TEMPORARILY DISABLED — feeds cause network timeout.
    Re-enable when timeout handling is confirmed working.
    """
    return 0, None, ["Official feeds: disabled (timeout protection)"]

def _check_official_feeds_disabled():
    """
    Poll RBI, NSE, SEBI and major financial news RSS feeds.
    Returns (score, block_reason, details_list)

    This is the most important real-time check:
    - RBI surprise announcements drop here within minutes
    - NSE circuit breaker / trading halt notices appear here
    - SEBI emergency orders show up here
    - ET Markets / Moneycontrol break news here

    All feeds checked within last 2 hours only.
    """
    if not FEEDPARSER_OK:
        return 0, None, ["Official feeds: feedparser not installed"]

    score = 0
    block_reason = None
    details = []
    cutoff = datetime.datetime.now() - datetime.timedelta(hours=2)

    for source, url in OFFICIAL_FEEDS.items():
        try:
            feedparser.api.PREFERRED_XML_PARSERS = []
            feed = feedparser.parse(url, request_headers={'User-Agent': 'Mozilla/5.0'}, timeout=4)
            if not feed.entries:
                details.append(f"{source}: no entries")
                continue

            fresh_count = 0
            for entry in feed.entries[:10]:
                title   = entry.get("title", "").lower()
                summary = entry.get("summary", "").lower()
                text    = title + " " + summary

                # Check freshness
                published = entry.get("published_parsed")
                if published:
                    try:
                        pub_dt = datetime.datetime(*published[:6])
                        if pub_dt < cutoff:
                            continue
                    except Exception:
                        pass  # if can't parse date, include anyway

                fresh_count += 1

                # Check hard block keywords
                for kw in OFFICIAL_BLOCK_KEYWORDS:
                    if kw in text:
                        block_reason = f"BLOCK [{source}] '{kw}' in: {title[:70]}"
                        return -10, block_reason, details

                # Check caution keywords
                for kw in OFFICIAL_CAUTION_KEYWORDS:
                    if kw in text:
                        score -= 1
                        details.append(f"⚠️ [{source}] '{kw}': {title[:50]}")
                        break  # one penalty per entry max

            details.append(f"{source}: {fresh_count} fresh entries scanned")

        except Exception as e:
            details.append(f"{source}: error ({str(e)[:40]})")
            continue

    score = max(-4, score)  # cap caution at -4
    return score, None, details


def score_headlines(headlines):
    """
    Score list of headlines using keyword rules.
    Returns (total_score, matched_keywords, hard_block_reason)
    """
    total_score = 0
    matched = []
    hard_block = None

    for headline in headlines:
        for keyword, pts, is_block in KEYWORD_RULES:
            if keyword in headline:
                if is_block:
                    hard_block = f"HARD BLOCK keyword '{keyword}' in: \"{headline[:80]}\""
                    return -10, matched, hard_block
                total_score += pts
                matched.append(f"'{keyword}' ({pts:+d}): {headline[:60]}")

    # Cap at ±6
    return max(-6, min(6, total_score)), matched, None


# ═══════════════════════════════════════════════════════════════════
#  MODULE 4: FINNHUB GLOBAL NEWS (optional, needs API key)
# ═══════════════════════════════════════════════════════════════════

def get_finnhub_score():
    """
    Fetch general market news from Finnhub.
    Returns (score -2 to +2, details)
    """
    if not FINNHUB_KEY or not REQUESTS_OK:
        return 0, "Finnhub not configured"

    try:
        url = f"https://finnhub.io/api/v1/news?category=general&token={FINNHUB_KEY}"
        r = requests.get(url, timeout=5)
        if r.status_code != 200:
            return 0, f"Finnhub error {r.status_code}"

        articles = r.json()[:10]
        score = 0
        matched = []

        for article in articles:
            headline = article.get("headline", "").lower()
            summary  = article.get("summary", "").lower()
            text = headline + " " + summary

            for keyword, pts, is_block in KEYWORD_RULES:
                if keyword in text and not is_block:
                    score += pts * 0.5  # Half weight for global news
                    matched.append(f"[Finnhub] '{keyword}' ({pts*0.5:+.1f})")

        score = max(-2, min(2, round(score)))
        return score, " | ".join(matched[:3]) if matched else "No strong signals"

    except Exception as e:
        return 0, f"Finnhub exception: {e}"


# ═══════════════════════════════════════════════════════════════════
#  MODULE 5: VIX INTRADAY SPIKE DETECTION
# ═══════════════════════════════════════════════════════════════════

def check_vix_spike(current_vix):
    """
    Detect if VIX has spiked sharply today (>15% above yesterday close).
    Returns (score, detail)
    """
    if not YFINANCE_OK or current_vix is None:
        return 0, "VIX spike check unavailable"

    try:
        vix_ticker = yf.Ticker("^INDIAVIX")
        hist = vix_ticker.history(period="2d", interval="1d")
        if len(hist) >= 2:
            prev_vix = hist["Close"].iloc[-2]
            change_pct = (current_vix - prev_vix) / prev_vix * 100
            if change_pct >= 20:
                return -3, f"VIX SPIKE: +{change_pct:.0f}% vs yesterday ({prev_vix:.1f}→{current_vix:.1f}) 🚨"
            elif change_pct >= 12:
                return -2, f"VIX elevated: +{change_pct:.0f}% vs yesterday ⚠️"
            elif change_pct >= 6:
                return -1, f"VIX rising: +{change_pct:.0f}% vs yesterday"
            elif change_pct <= -10:
                return +1, f"VIX falling: {change_pct:.0f}% vs yesterday ✅ (calm market)"
        return 0, f"VIX normal (current: {current_vix:.1f})"
    except Exception as e:
        return 0, f"VIX history unavailable: {e}"


# ═══════════════════════════════════════════════════════════════════
#  MASTER FUNCTION — call this from strategy.py
# ═══════════════════════════════════════════════════════════════════

def run(signal_direction=None, current_vix=None):
    """
    Run full news intelligence check.

    Args:
        signal_direction: "LONG" or "SHORT" (from strategy signal)
        current_vix: current VIX from Fyers (float)

    Returns:
        verdict: "BLOCK" / "CAUTION" / "NEUTRAL" / "POSITIVE" / "STRONG"
        size_multiplier: float (0.0 to 1.5)
        score: int (-10 to +10)
        report: list of strings (printable summary)
    """
    report = []
    total_score = 0
    report.append("─" * 55)
    report.append("  📰 NEWS INTELLIGENCE CHECK")
    report.append("─" * 55)

    # ── 1. Calendar check ────────────────────────────────────────────
    blocked, cal_reason = check_event_calendar()
    if blocked:
        report.append(f"  🔴 CALENDAR BLOCK: {cal_reason}")
        report.append("─" * 55)
        return "BLOCK", 0.0, -10, report
    elif cal_reason:
        report.append(f"  {cal_reason}")
        total_score -= 1

    # ── 2. Global markets ────────────────────────────────────────────
    global_score, global_detail = get_global_market_score()
    total_score += global_score
    icon = "🟢" if global_score >= 0 else "🔴"
    report.append(f"  {icon} Global markets ({global_score:+d}): {global_detail}")

    # ── 3. Official feeds (RBI, NSE, SEBI) — highest priority ──────
    off_score, off_block, off_details = check_official_feeds()
    if off_block:
        report.append(f"  🚨 OFFICIAL FEED BLOCK: {off_block}")
        report.append("─" * 55)
        return "BLOCK", 0.0, -10, report
    total_score += off_score
    icon = "🟢" if off_score >= 0 else "🟡"
    report.append(f"  {icon} Official feeds (RBI/NSE/SEBI) ({off_score:+d}):")
    for d in off_details[:4]:
        report.append(f"      → {d}")

    # ── 3b. India headlines (Google News) ───────────────────────────
    headlines = fetch_india_headlines()
    if headlines:
        news_score, matched, hard_block = score_headlines(headlines)
        if hard_block:
            report.append(f"  🔴 {hard_block}")
            report.append("─" * 55)
            return "BLOCK", 0.0, -10, report
        total_score += news_score
        icon = "🟢" if news_score >= 0 else "🔴"
        report.append(f"  {icon} India news ({news_score:+d}):")
        for m in matched[:4]:
            report.append(f"      → {m}")
        if not matched:
            report.append(f"      No strong signals in {len(headlines)} headlines")
    else:
        report.append("  ℹ️ India headlines: unavailable (feedparser not installed or no internet)")

    # ── 4. Finnhub (if configured) ───────────────────────────────────
    if FINNHUB_KEY:
        fh_score, fh_detail = get_finnhub_score()
        total_score += fh_score
        icon = "🟢" if fh_score >= 0 else "🔴"
        report.append(f"  {icon} Finnhub ({fh_score:+d}): {fh_detail}")
    else:
        report.append("  ℹ️ Finnhub: not configured (add key to FINNHUB_KEY)")

    # ── 5. VIX spike ─────────────────────────────────────────────────
    vix_score, vix_detail = check_vix_spike(current_vix)
    total_score += vix_score
    icon = "🟢" if vix_score >= 0 else "🔴"
    report.append(f"  {icon} VIX regime ({vix_score:+d}): {vix_detail}")

    # ── 6. Direction alignment bonus ─────────────────────────────────
    if signal_direction and headlines:
        news_bullish = total_score > 0
        news_bearish = total_score < 0
        if signal_direction == "LONG" and news_bullish:
            total_score += 2
            report.append(f"  🟢 Direction alignment: LONG + bullish news (+2)")
        elif signal_direction == "SHORT" and news_bearish:
            total_score += 2
            report.append(f"  🟢 Direction alignment: SHORT + bearish news (+2)")
        elif signal_direction == "LONG" and news_bearish:
            total_score -= 2
            report.append(f"  🔴 Direction conflict: LONG but bearish news (-2)")
        elif signal_direction == "SHORT" and news_bullish:
            total_score -= 2
            report.append(f"  🔴 Direction conflict: SHORT but bullish news (-2)")

    # ── Final verdict ────────────────────────────────────────────────
    total_score = max(-10, min(10, total_score))
    verdict, multiplier = score_to_verdict(total_score)

    verdict_icons = {
        "BLOCK": "🔴", "CAUTION": "🟡",
        "NEUTRAL": "⚪", "POSITIVE": "🔵", "STRONG": "🚀"
    }
    icon = verdict_icons.get(verdict, "⚪")
    report.append("─" * 55)
    report.append(f"  {icon} VERDICT: {verdict} | Score: {total_score:+d}/10 | Size: {multiplier}x")
    report.append("─" * 55)

    return verdict, multiplier, total_score, report


# ═══════════════════════════════════════════════════════════════════
#  STANDALONE TEST
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("\nTesting news_intel.py standalone...\n")
    verdict, mult, score, lines = run(signal_direction="LONG", current_vix=18.0)
    for line in lines:
        print(line)
    print(f"\nResult: {verdict} | Multiplier: {mult}x | Score: {score}")
