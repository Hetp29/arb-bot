import time
import requests
import os
import json
import base64
import datetime
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, ed25519

KALSHI_API_KEY = os.environ.get("KALSHI_API_KEY")
KALSHI_API_SECRET = os.environ.get("KALSHI_API_SECRET")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

MAX_BET = 25
MIN_EDGE = 0.05
STOP_LOSS = 25
SCAN_INTERVAL = 3

def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN:
        print(msg)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg})

def get_kalshi_headers(method, path):
    try:
        timestamp = str(int(datetime.datetime.now().timestamp() * 1000))
        msg = timestamp + method.upper() + path
        pem = KALSHI_API_SECRET.replace("\\n", "\n")
        if "-----BEGIN" not in pem:
            pem = f"-----BEGIN RSA PRIVATE KEY-----\n{pem}\n-----END RSA PRIVATE KEY-----"
        private_key = serialization.load_pem_private_key(pem.encode(), password=None)
        signature = private_key.sign(
            msg.encode(),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH
            ),
            hashes.SHA256()
        )
        return {
            "KALSHI-ACCESS-KEY": KALSHI_API_KEY,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
    except Exception as e:
        print(f"Kalshi signing error: {e}")
        return {"Authorization": f"Bearer {KALSHI_API_KEY}", "Content-Type": "application/json"}

def get_kalshi_markets():
    try:
        path = "/trade-api/v2/markets"
        params = "?series_ticker=KXWCGAME&limit=100"
        headers = get_kalshi_headers("GET", path)
        r = requests.get(
            f"https://api.elections.kalshi.com{path}{params}",
            headers=headers,
            timeout=5
        )
        data = r.json()
        markets = []
        for market in data.get("markets", []):
            yes_ask = float(market.get("yes_ask_dollars", 0) or 0)
            no_ask = float(market.get("no_ask_dollars", 0) or 0)
            if 0.05 < yes_ask < 0.95:
                markets.append({
                    "title": market.get("title", ""),
                    "subtitle": market.get("yes_sub_title", ""),
                    "ticker": market.get("ticker", ""),
                    "yes_ask": yes_ask,
                    "no_ask": no_ask,
                })
        print(f"Found {len(markets)} Kalshi markets with prices")
        return markets
    except Exception as e:
        print(f"Kalshi error: {e}")
        return []

def get_polymarket_markets():
    try:
        url = "https://gamma-api.polymarket.com/events?series_slug=soccer-fifwc&active=true&closed=false&limit=10"
        r = requests.get(url, timeout=10)
        events_list = r.json()
        markets = []
        for event in events_list:
            title = event.get("title", "")
            slug = event.get("slug", "")
            if "vs." not in title:
                continue
            er = requests.get(f"https://gamma-api.polymarket.com/events?slug={slug}", timeout=5)
            if er.status_code != 200:
                continue
            event_data = er.json()
            if not event_data:
                continue
            for market in event_data[0].get("markets", []):
                question = market.get("question", "")
                if any(x in question.lower() for x in ["halftime", "half", "leading", "first", "corner", "score"]):
                    continue
                
                # Parse outcomes and prices safely
                outcomes = market.get("outcomes", [])
                prices = market.get("outcomePrices", "[]")
                if isinstance(prices, str):
                    prices = json.loads(prices)
                if not prices or len(prices) != len(outcomes):
                    continue
                
                team = market.get("groupItemTitle", "").lower().strip()
                if not team or "draw" in team:
                    continue
                
                # CRITICAL FIX: Match the actual team name to the correct price index
                target_price = None
                for idx, outcome_name in enumerate(outcomes):
                    # Checks if the outcome matches the group item title (e.g., "Ghana" or "Yes")
                    if outcome_name.lower().strip() == team or outcome_name.lower().strip() == "yes":
                        target_price = float(prices[idx])
                        break
                
                if target_price is None or target_price < 0.05 or target_price > 0.95:
                    continue
                    
                markets.append({
                    "question": question,
                    "team": team,
                    "id": market.get("id", ""),
                    "slug": market.get("slug", ""),
                    "price": target_price,
                })
        print(f"Found {len(markets)} Polymarket match markets")
        for m in markets[:5]:
            print(f"Poly: {m['team']} @ {m['price']}")
        return markets
    except Exception as e:
        print(f"Polymarket error: {e}")
        return []

def match_markets(kalshi_markets, poly_markets):
    pairs = []
    for km in kalshi_markets:
        subtitle = km.get("subtitle", "").lower().replace("reg time:", "").strip()
        for pm in poly_markets:
            team = pm.get("team", "").lower()
            if subtitle and len(subtitle) > 3 and subtitle == team:
                pairs.append((km, pm))
    print(f"Total valid pairs: {len(pairs)}")
    return pairs

def find_arb(kalshi_markets, poly_markets):
    opportunities = []
    pairs = match_markets(kalshi_markets, poly_markets)
    
    for km, pm in pairs:
        try:
            # 1. Get the actual contract prices (0.0 to 1.0)
            k_yes = float(km.get("yes_ask", 0))
            k_no = float(km.get("no_ask", 0))
            p_yes = float(pm.get("price", 0))
            p_no = 1.0 - p_yes  # Dynamic approximation for Polymarket No
            
            if k_yes <= 0 or p_yes <= 0:
                continue

            # --- ARB STRATEGY 1: Buy Kalshi YES + Polymarket NO ---
            total_cost_1 = k_yes + p_no
            if total_cost_1 < 1.0:
                edge_1 = 1.0 - total_cost_1
                if edge_1 >= MIN_EDGE:
                    opportunities.append({
                        "title": km["title"],
                        "subtitle": km["subtitle"],
                        "kalshi_ticker": km["ticker"],
                        "poly_id": pm.get("id", ""),
                        "poly_slug": pm.get("slug", ""),
                        "edge": round(edge_1 * 100, 2),
                        "buy_on": "Kalshi",  # Meaning Kalshi YES, Poly NO
                        "kalshi_odds": round(1/k_yes, 2),
                        "poly_odds": round(1/p_no, 2),
                    })

            # --- ARB STRATEGY 2: Buy Kalshi NO + Polymarket YES ---
            total_cost_2 = k_no + p_yes
            if total_cost_2 < 1.0:
                edge_2 = 1.0 - total_cost_2
                if edge_2 >= MIN_EDGE:
                    opportunities.append({
                        "title": km["title"],
                        "subtitle": km["subtitle"],
                        "kalshi_ticker": km["ticker"],
                        "poly_id": pm.get("id", ""),
                        "poly_slug": pm.get("slug", ""),
                        "edge": round(edge_2 * 100, 2),
                        "buy_on": "Polymarket", # Meaning Poly YES, Kalshi NO
                        "kalshi_odds": round(1/k_no, 2),
                        "poly_odds": round(1/p_yes, 2),
                    })

        except Exception as e:
            print(f"Arb error: {e}")
            continue
            
    return sorted(opportunities, key=lambda x: x["edge"], reverse=True)

def execute_trade(opp):
    poly_url = f"https://polymarket.com/event/{opp['poly_slug'].rsplit('-', 1)[0]}"

    # To build a true arbitrage, we want both sides to pay out the same amount (MAX_BET)
    # Payout = Bet Amount / Price
    # Therefore: Bet Amount = Payout * Price
    
    if opp["buy_on"] == "Kalshi":
        # Strategy 1: Kalshi YES + Polymarket NO
        k_price = 1 / opp["kalshi_odds"]
        p_price = 1 / opp["poly_odds"]  # This is the Poly NO price
        
        kalshi_bet = round(MAX_BET * k_price, 2)
        poly_bet = round(MAX_BET * p_price, 2)
        
        kalshi_action = f"BUY YES ${kalshi_bet}"
        poly_action = f"BUY NO ${poly_bet}"
    else:
        # Strategy 2: Kalshi NO + Polymarket YES
        k_price = 1 / opp["kalshi_odds"]  # This is the Kalshi NO price
        p_price = 1 / opp["poly_odds"]
        
        kalshi_bet = round(MAX_BET * k_price, 2)
        poly_bet = round(MAX_BET * p_price, 2)
        
        kalshi_action = f"BUY NO ${kalshi_bet}"
        poly_action = f"BUY YES ${poly_bet}"

    msg = (
        f"🚨 REAL ARB OPPORTUNITY FOUND!\n\n"
        f"Market: {opp['title']}\n"
        f"Net Profit Margin: {opp['edge']}%\n\n"
        f"1️⃣ KALSHI: {kalshi_action}\n"
        f"→ Team: {opp['subtitle']}\n"
        f"→ Odds: {opp['kalshi_odds']}x\n\n"
        f"2️⃣ POLYMARKET: {poly_action}\n"
        f"→ Odds: {opp['poly_odds']}x\n"
        f"→ {poly_url}\n\n"
        f"⏰ Act fast!\n"
        f"Time: {datetime.datetime.now().strftime('%H:%M:%S')}"
    )
    send_telegram(msg)
    print(msg)

def main():
    alerted_markets = set()
    send_telegram(
        f"🚀 Arb Bot Started! (Alert Mode)\n"
        f"Max Bet: ${MAX_BET}\n"
        f"Min Edge: {int(MIN_EDGE*100)}%\n"
        f"Stop Loss: ${STOP_LOSS}"
    )
    print("Bot running...")
    while True:
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Scanning...")
        kalshi = get_kalshi_markets()
        poly = get_polymarket_markets()
        opps = find_arb(kalshi, poly)
        if opps:
            best = opps[0]
            print(f"Found {len(opps)} opps! Best: {best['edge']}%")
            if best["kalshi_ticker"] not in alerted_markets:
                execute_trade(best)
                alerted_markets.add(best["kalshi_ticker"])
        else:
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] No arb found")
        time.sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    main()