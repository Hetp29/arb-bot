import time
import requests
import os
from datetime import datetime

# ── CONFIG ────────────────────────────────────────────────
KALSHI_API_KEY = os.environ.get("KALSHI_API_KEY")
KALSHI_API_SECRET = os.environ.get("KALSHI_API_SECRET")
POLYMARKET_API_KEY = os.environ.get("POLYMARKET_API_KEY")
POLYMARKET_API_SECRET = os.environ.get("POLYMARKET_API_SECRET")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

MAX_BET = 25
MIN_EDGE = 0.08
STOP_LOSS = 25
SCAN_INTERVAL = 3

session_pnl = 0

# ── TELEGRAM ──────────────────────────────────────────────
def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN:
        print(msg)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg})

# ── KALSHI ────────────────────────────────────────────────
def get_kalshi_markets():
    try:
        url = "https://trading-api.kalshi.com/trade-api/v2/events?status=open&series_ticker=KXWORLDSOCCER"
        headers = {
            "Authorization": f"Bearer {KALSHI_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        r = requests.get(url, headers=headers, timeout=5)
        print(f"Kalshi status: {r.status_code}")
        print(f"Kalshi response: {r.text[:200]}")
        data = r.json()
        markets = []
        for event in data.get("events", []):
            for market in event.get("markets", []):
                markets.append({
                    "title": event.get("title", ""),
                    "ticker": market.get("ticker", ""),
                    "yes_ask": market.get("yes_ask", 0),
                    "no_ask": market.get("no_ask", 0),
                })
        return markets
    except Exception as e:
        print(f"Kalshi error: {e}")
        return []

# ── POLYMARKET ────────────────────────────────────────────
def get_polymarket_markets():
    try:
        url = "https://gamma-api.polymarket.com/markets?tag=soccer&active=true&closed=false&limit=100"
        r = requests.get(url, timeout=5)
        return r.json()
    except Exception as e:
        print(f"Polymarket error: {e}")
        return []

# ── MATCH MARKETS ─────────────────────────────────────────
def match_markets(kalshi_markets, poly_markets):
    pairs = []
    for km in kalshi_markets:
        km_words = set(w.lower() for w in km["title"].split() if len(w) > 3)
        for pm in poly_markets:
            pm_words = set(w.lower() for w in pm.get("question", "").split() if len(w) > 3)
            overlap = km_words & pm_words
            if len(overlap) >= 2:
                pairs.append((km, pm))
    return pairs

# ── ARB DETECTION ─────────────────────────────────────────
def find_arb(kalshi_markets, poly_markets):
    opportunities = []
    pairs = match_markets(kalshi_markets, poly_markets)
    
    for km, pm in pairs:
        try:
            k_price = float(km.get("yes_ask", 0))
            prices = pm.get("outcomePrices", "[]")
            if isinstance(prices, str):
                import json
                prices = json.loads(prices)
            p_price = float(prices[0]) if prices else 0

            if k_price <= 0 or p_price <= 0:
                continue

            k_odds = round(1 / k_price, 3)
            p_odds = round(1 / p_price, 3)
            
            best_odds = max(k_odds, p_odds)
            worst_odds = min(k_odds, p_odds)
            edge = (best_odds - worst_odds) / worst_odds

            if edge >= MIN_EDGE:
                opportunities.append({
                    "title": km["title"],
                    "kalshi_ticker": km["ticker"],
                    "poly_id": pm.get("id", ""),
                    "kalshi_odds": k_odds,
                    "poly_odds": p_odds,
                    "edge": round(edge * 100, 2),
                    "buy_on": "Kalshi" if k_odds > p_odds else "Polymarket",
                    "fade_on": "Polymarket" if k_odds > p_odds else "Kalshi",
                })
        except Exception as e:
            print(f"Arb detection error: {e}")
            continue

    return sorted(opportunities, key=lambda x: x["edge"], reverse=True)

# ── PLACE KALSHI ORDER ────────────────────────────────────
def place_kalshi_order(ticker, side, amount):
    try:
        url = "https://trading-api.kalshi.com/trade-api/v2/portfolio/orders"
        headers = {
            "Authorization": f"Bearer {KALSHI_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "ticker": ticker,
            "side": side,
            "type": "market",
            "count": int(amount * 100),
        }
        r = requests.post(url, headers=headers, json=payload, timeout=5)
        return r.json()
    except Exception as e:
        print(f"Kalshi order error: {e}")
        return None

# ── PLACE POLYMARKET ORDER ────────────────────────────────
def place_poly_order(market_id, side, amount):
    try:
        url = "https://clob.polymarket.com/order"
        headers = {
            "Authorization": f"Bearer {POLYMARKET_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "market": market_id,
            "side": side,
            "price": amount,
            "size": amount,
            "orderType": "MARKET",
        }
        r = requests.post(url, headers=headers, json=payload, timeout=5)
        return r.json()
    except Exception as e:
        print(f"Polymarket order error: {e}")
        return None

# ── EXECUTE TRADE ─────────────────────────────────────────
def execute_trade(opp):
    global session_pnl
    buy_bet = round(MAX_BET * 0.55, 2)
    fade_bet = round(MAX_BET * 0.45, 2)

    if opp["buy_on"] == "Kalshi":
        place_kalshi_order(opp["kalshi_ticker"], "yes", buy_bet)
        place_poly_order(opp["poly_id"], "no", fade_bet)
    else:
        place_poly_order(opp["poly_id"], "yes", buy_bet)
        place_kalshi_order(opp["kalshi_ticker"], "no", fade_bet)

    estimated_profit = round(buy_bet * opp["kalshi_odds"] - MAX_BET, 2)
    session_pnl = round(session_pnl + estimated_profit, 2)

    msg = (
        f"🤖 TRADE EXECUTED\n"
        f"Market: {opp['title']}\n"
        f"Edge: {opp['edge']}%\n"
        f"BUY {opp['buy_on']}: ${buy_bet}\n"
        f"FADE {opp['fade_on']}: ${fade_bet}\n"
        f"Est. Profit: ${estimated_profit}\n"
        f"Session P&L: ${session_pnl}\n"
        f"Time: {datetime.now().strftime('%H:%M:%S')}"
    )
    send_telegram(msg)
    print(msg)

# ── MAIN LOOP ─────────────────────────────────────────────
def main():
    global session_pnl
    send_telegram(
        f"🚀 Arb Bot Started!\n"
        f"Max Bet: ${MAX_BET}\n"
        f"Min Edge: {int(MIN_EDGE*100)}%\n"
        f"Stop Loss: ${STOP_LOSS}"
    )
    print("Bot running...")

    while True:
        if session_pnl <= -STOP_LOSS:
            send_telegram(f"🛑 STOP LOSS HIT: ${session_pnl}\nBot paused.")
            break

        print(f"[{datetime.now().strftime('%H:%M:%S')}] Scanning...")
        kalshi = get_kalshi_markets()
        poly = get_polymarket_markets()
        opps = find_arb(kalshi, poly)

        if opps:
            print(f"Found {len(opps)} opportunities! Best edge: {opps[0]['edge']}%")
            execute_trade(opps[0])
        else:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] No arb found")

        time.sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    main()