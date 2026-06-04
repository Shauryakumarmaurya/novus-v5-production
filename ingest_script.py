import requests
import json
import os

url = "http://127.0.0.1:5001/ingest_local"
payload = {
    "ticker": "HINDUNILVR",
    "folder_path": "/Users/shauryaiitd/Desktop/giga-finanalytix copy 2/company_docs/09_HINDUNILVR_Hind._Unilever"
}

headers = {
    "Content-Type": "application/json"
}

# Add API key if needed
api_key = os.getenv("NOVUS_API_KEY")
if api_key:
    headers["X-API-Key"] = api_key

try:
    print(f"Sending request to {url}...")
    print(f"Payload: {json.dumps(payload, indent=2)}")
    response = requests.post(url, json=payload, headers=headers, timeout=600)
    print(f"Status Code: {response.status_code}")
    try:
        data = response.json()
        print(f"Response: {json.dumps(data, indent=2)}")
    except:
        print(f"Raw Response: {response.text[:500]}")
except Exception as e:
    print(f"Error: {e}")
