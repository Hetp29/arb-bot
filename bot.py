import time
import requests
import os
import json
from datetime import datetime

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

def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN:
        print(msg)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg})

def get_kalshi_markets():
    try:
        url = "https://api.elections.kalshi.com/trade-api/v2/markets?series_ticker=KXWCGAME&limit=100"
        headers = {"Authorization": f"Bearer {KALSHI_API_KEY}", "Content-Type": "application/json", "Accept": "application/json"}
        r = requests.get(url, headers=headers, timeout=5)
        data = r.json()
        markets = []
        for market in data.get("markets", []):
            # Print raw market to see all fields
            print(f"RAW: {list(market.keys())} | yes_ask={market.get('yes_ask')} | yes_ask_price={market.get('yes_ask_price')} | last_price={market.get('last_price')}")
            markets.append({
                "title": market.get("title", ""),
                "ticker": market.get("ticker", ""),
                "yes_ask": market.get("yes_ask_price") or market.get("yes_ask") or market.get("last_price") or 0,
                "no_ask": market.get("no_ask_price") or market.get("no_ask") or 0,
            })
        print(f"Found {len(markets)} Kalshi markets")
        return markets
    except Exception as e:
        print(f"Kalshi error: {e}")
        return []

def get_polymarket_markets():
    try:
        # Use FIFA World Cup specific tag instead of generic soccer
        url = "https://gamma-api.polymarket.com/markets?tag=fifa-world-cup&active=true&closed=false&limit=100"
        r = requests.get(url, timeout=5)
        data = r.json()
        print(f"Found {len(data)} Polymarket markets")
        for m in data[:5]:
            print(f"Poly sample: {m.get('question', '')}")
        return data
    except Exception as e:
        print(f"Polymarket error: {e}")
        return []

def match_markets(kalshi_markets, poly_markets):
    pairs = []
    for km in kalshi_markets:
        km_words = set(w.lower() for w in km["title"].split() if len(w) > 3)
        for pm in poly_markets:
            pm_words = set(w.lower() for w in pm.get("question", "").split() if len(w) > 3)
            overlap = km_words & pm_words
            if len(overlap) >= 2:
                pairs.append((km, pm))
                print(f"Matched: {km['title']} <-> {pm.get('question', '')}")
    print(f"Total pairs matched: {len(pairs)}")
    return pairs

def find_arb(kalshi_markets, poly_markets):
    opportunities = []
    pairs = match_markets(kalshi_markets, poly_markets)
    for km, pm in pairs:
        try:
            k_price = float(km.get("yes_ask", 0))
            prices = pm.get("outcomePrices", "[]")
            if isinstance(prices, str):
                prices = json.loads(prices)
            p_price = float(prices[0]) if prices else 0
            if k_price <= 0 or p_price <= 0:
                continue
            k_odds = round(1 / k_price, 3)
            p_odds = round(1 / p_price, 3)
            best_odds = max(k_odds, p_odds)
            worst_odds = min(k_odds, p_odds)
            edge = (best_odds - worst_odds) / worst_odds
            print(f"Checking: {km['title']} | K:{k_odds} P:{p_odds} Edge:{round(edge*100,1)}%")
            if edge >= MIN_EDGE:
                opportunities.append({"title": km["title"], "kalshi_ticker": km["ticker"], "poly_id": pm.get("id", ""), "kalshi_odds": k_odds, "poly_odds": p_odds, "edge": round(edge * 100, 2), "buy_on": "Kalshi" if k_odds > p_odds else "Polymarket", "fade_on": "Polymarket" if k_odds > p_odds else "Kalshi"})
        except Exception as e:
            print(f"Arb detection error: {e}")
            continue
    return sorted(opportunities, key=lambda x: x["edge"], reverse=True)

def place_kalshi_order(ticker, side, amount):
    try:
        url = "https://api.elections.kalshi.com/trade-api/v2/portfolio/orders"
        headers = {"Authorization": f"Bearer {KALSHI_API_KEY}", "Content-Type": "application/json"}
        payload = {"ticker": ticker, "side": side, "type": "market", "count": int(amount * 100)}
        r = requests.post(url, headers=headers, json=payload, timeout=5)
        print(f"Kalshi order response: {r.text[:200]}")
        return r.json()
    except Exception as e:
        print(f"Kalshi order error: {e}")
        return None

def place_poly_order(market_id, side, amount):
    try:
        url = "https://clob.polymarket.com/order"
        headers = {"Authorization": f"Bearer {POLYMARKET_API_KEY}", "Content-Type": "application/json"}
        payload = {"market": market_id, "side": side, "price": amount, "size": amount, "orderType": "MARKET"}
        r = requests.post(url, headers=headers, json=payload, timeout=5)
        print(f"Poly order response: {r.text[:200]}")
        return r.json()
    except Exception as e:
        print(f"Polymarket order error: {e}")
        return None

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
    msg = (f"🤖 TRADE EXECUTED\nMarket: {opp['title']}\nEdge: {opp['edge']}%\nBUY {opp['buy_on']}: ${buy_bet}\nFADE {opp['fade_on']}: ${fade_bet}\nEst. Profit: ${estimated_profit}\nSession P&L: ${session_pnl}\nTime: {datetime.now().strftime('%H:%M:%S')}")
    send_telegram(msg)
    print(msg)

def main():
    global session_pnl
    send_telegram(f"🚀 Arb Bot Started!\nMax Bet: ${MAX_BET}\nMin Edge: {int(MIN_EDGE*100)}%\nStop Loss: ${STOP_LOSS}")
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