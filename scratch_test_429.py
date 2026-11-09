import requests
from config import config
import time

print("Waiting 12 seconds for Dhan rate limit bucket to clear...")
time.sleep(12)

headers = {
    "access-token": config.access_token,
    "client-id": config.client_id,
    "Content-Type": "application/json"
}

url_quote = "https://api.dhan.co/v2/marketfeed/quote"
payload = {"IDX_I": [13]}
r = requests.post(url_quote, json=payload, headers=headers)
print("status_code:", r.status_code)
print("text:", r.text[:200])
