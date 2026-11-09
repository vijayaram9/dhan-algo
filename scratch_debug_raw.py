import requests
from config import config

headers = {
    "access-token": config.access_token,
    "client-id": config.client_id,
    "Content-Type": "application/json"
}

url_quote = "https://api.dhan.co/v2/marketfeed/quote"
payload = {"BSE_FNO": [1148637]}
r = requests.post(url_quote, json=payload, headers=headers)
print("status_code:", r.status_code)
print("text:", r.text)
