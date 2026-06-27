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
POLYMARKET_API_KEY = os.environ.get("POLYMARKET_API_KEY")
POLYMARKET_API_SECRET = os.environ.get("POLYMARKET_API_SECRET")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

MAX_BET = 25
MIN_EDGE = 0.05
STOP_LOSS = 25
SCAN_INTERVAL = 3
session_pnl = 0

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

def get_poly_headers(method, path):
    try:
        timestamp = str(int(time.time() * 1000))
        message = f"{timestamp}{method}{path}"
        private_key = ed25519.Ed25519PrivateKey.from_private_bytes(
            base64.b64decode(POLYMARKET_API_SECRET)[:32]
        )
        signature = base64.b64encode(private_key.sign(message.encode())).decode()
        return {
            "X-PM-Access-Key": POLYMARKET_API_KEY,
            "X-PM-Timestamp": timestamp,
            "X-PM-Signature": signature,
            "Content-Type": "application/json",
        }
    except Exception as e:
        print(f"Poly signing error: {e}")
        return {"Content-Type": "application/json"}

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
                prices = market.get("outcomePrices", "[]")
                if isinstance(prices, str):
                    prices = json.loads(prices)
                if not prices:
                    continue
                price = float(prices[0])
                team = market.get("groupItemTitle", "").lower().strip()
                if not team or "draw" in team or price < 0.05 or price > 0.95:
                    continue
                markets.append({
                    "question": question,
                    "team": team,
                    "id": market.get("id", ""),
                    "slug": market.get("slug", ""),
                    "price": price,
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
                print(f"Matched: {km['title']} [{subtitle}] <-> {pm['question']}")
    print(f"Total valid pairs: {len(pairs)}")
    return pairs

def find_arb(kalshi_markets, poly_markets):
    opportunities = []
    pairs = match_markets(kalshi_markets, poly_markets)
    for km, pm in pairs:
        try:
            k_price = float(km.get("yes_ask", 0))
            p_price = float(pm.get("price", 0))
            if k_price <= 0 or p_price <= 0:
                continue
            k_odds = round(1 / k_price, 3)
            p_odds = round(1 / p_price, 3)
            best_odds = max(k_odds, p_odds)
            worst_odds = min(k_odds, p_odds)
            edge = (best_odds - worst_odds) / worst_odds
            print(f"Edge: {km['subtitle']} | K:{k_price} P:{p_price} | {round(edge*100,1)}%")
            if MIN_EDGE <= edge <= 1.0:
                print(f"Poly ID: {pm.get('id')} | slug: {pm.get('slug')}")
                opportunities.append({
                    "title": km["title"],
                    "subtitle": km["subtitle"],
                    "kalshi_ticker": km["ticker"],
                    "poly_id": pm.get("id", ""),
                    "poly_slug": pm.get("slug", ""),
                    "kalshi_odds": k_odds,
                    "poly_odds": p_odds,
                    "k_price": k_price,
                    "p_price": p_price,
                    "edge": round(edge * 100, 2),
                    "buy_on": "Kalshi" if k_odds > p_odds else "Polymarket",
                    "fade_on": "Polymarket" if k_odds > p_odds else "Kalshi",
                })
        except Exception as e:
            print(f"Arb error: {e}")
            continue
    return sorted(opportunities, key=lambda x: x["edge"], reverse=True)

def place_kalshi_order(ticker, side, amount, price):
    try:
        path = "/trade-api/v2/portfolio/events/orders"
        headers = get_kalshi_headers("POST", path)
        count = max(1, int(amount / price))
        payload = {
            "ticker": ticker,
            "client_order_id": f"arb-{int(time.time())}",
            "side": "bid" if side == "yes" else "ask",
            "count": f"{count}.00",
            "price": f"{price:.4f}",
            "time_in_force": "immediate_or_cancel",
            "self_trade_prevention_type": "taker_at_cross",
        }
        print(f"Kalshi placing: {count} contracts @ ${price} = ${count * price:.2f}")
        r = requests.post(
            f"https://api.elections.kalshi.com{path}",
            headers=headers,
            json=payload,
            timeout=5
        )
        print(f"Kalshi order: {r.text[:200]}")
        data = r.json()
        if "error" in data:
            print(f"Kalshi order failed: {data['error']}")
            return None
        return data
    except Exception as e:
        print(f"Kalshi order error: {e}")
        return None

def place_poly_order(market_id, side, amount):
    try:
        path = "/v1/orders"
        headers = get_poly_headers("POST", path)
        intent = "ORDER_INTENT_BUY_LONG" if side == "yes" else "ORDER_INTENT_BUY_SHORT"
        payload = {
            "marketSlug": market_id,
            "intent": intent,
            "type": "ORDER_TYPE_MARKET",
            "cashOrderQty": {"value": str(amount), "currency": "USD"},
        }
        r = requests.post(
            f"https://api.polymarket.us{path}",
            headers=headers,
            json=payload,
            timeout=5
        )
        print(f"Poly order: {r.text[:200]}")
        data = r.json()
        if "error" in str(data).lower() or "code" in str(data):
            print(f"Poly order failed: {data}")
            return None
        return data
    except Exception as e:
        print(f"Poly order error: {e}")
        return None

def execute_trade(opp):
    global session_pnl
    buy_bet = round(MAX_BET * 0.55, 2)
    fade_bet = round(MAX_BET * 0.45, 2)

    if opp["buy_on"] == "Kalshi":
        k_result = place_kalshi_order(opp["kalshi_ticker"], "yes", buy_bet, opp["k_price"])
        p_result = place_poly_order(opp["poly_slug"], "no", fade_bet)
    else:
        p_result = place_poly_order(opp["poly_slug"], "yes", buy_bet)
        k_result = place_kalshi_order(opp["kalshi_ticker"], "no", fade_bet, opp["k_price"])

    if not k_result or not p_result:
        print("⚠️ Orders failed - not counting P&L")
        return

    estimated_profit = round(buy_bet * opp["kalshi_odds"] - MAX_BET, 2)
    session_pnl = round(session_pnl + estimated_profit, 2)
    msg = (
        f"🤖 TRADE EXECUTED\n"
        f"Market: {opp['title']}\n"
        f"{opp['subtitle']}\n"
        f"Edge: {opp['edge']}%\n"
        f"BUY {opp['buy_on']}: ${buy_bet}\n"
        f"FADE {opp['fade_on']}: ${fade_bet}\n"
        f"Est. Profit: ${estimated_profit}\n"
        f"Session P&L: ${session_pnl}\n"
        f"Time: {datetime.datetime.now().strftime('%H:%M:%S')}"
    )
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
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Scanning...")
        kalshi = get_kalshi_markets()
        poly = get_polymarket_markets()
        opps = find_arb(kalshi, poly)
        if opps:
            print(f"Found {len(opps)} opps! Best: {opps[0]['edge']}%")
            execute_trade(opps[0])
        else:
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] No arb found")
        time.sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    main()