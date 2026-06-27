python3 -c "
import requests, base64, time
from cryptography.hazmat.primitives.asymmetric import ed25519

POLYMARKET_API_KEY = '46d59862-649a-44ef-94ba-261b60153979'
POLYMARKET_API_SECRET = 'z2QvtbZe57auWUEPhqHbD8ho6rZQoxx48+XMhksiNSD5XX6hf3D6znUETqOA8VXUz8PkskO5rza5YgqPBk/tRA=='

timestamp = str(int(time.time() * 1000))
path = '/v1/markets'
message = f'{timestamp}GET{path}'
private_key = ed25519.Ed25519PrivateKey.from_private_bytes(
    base64.b64decode(POLYMARKET_API_SECRET)[:32]
)
signature = base64.b64encode(private_key.sign(message.encode())).decode()

headers = {
    'X-PM-Access-Key': POLYMARKET_API_KEY,
    'X-PM-Timestamp': timestamp,
    'X-PM-Signature': signature,
}

r = requests.get('https://api.polymarket.us/v1/markets?id=1897432', headers=headers)
print(r.text[:500])
"