import requests
import pandas as pd
from datetime import datetime

# --- CONFIG ---
THETA_PORT = "25503"  # Check if your terminal is on 25510 or 25503 (default)
BASE_URL = f"http://127.0.0.1:{THETA_PORT}/v3/option/history/ohlc"


def test_url(name, ticker, exp, strike, right, date_int):
    print(f"\n--- TESTING {name} ({ticker}) ---")

    # Construct URL exactly as your logs showed (v3 format)
    # Note: ThetaData v3 usually requires 'iv' or 'greeks' for some endpoints,
    # but OHLC should work with just these.
    params = {
        "symbol": ticker,
        "expiration": exp,
        "strike": strike,
        "right": right,
        "date": date_int,
        "interval": "1m"
    }

    # Construct string for display matches the log format
    query_string = "&".join([f"{k}={v}" for k, v in params.items()])
    full_url = f"{BASE_URL}?{query_string}"

    print(f"🔗 Requesting: {full_url}")

    try:
        response = requests.get(full_url, timeout=5)
        print(f"📡 Status Code: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            # ThetaData v3 returns a header and a list of lists (or sometimes dicts)
            if not data:
                print("❌ Response is empty list [] (No Data Found)")
            elif "response" in data and not data["response"]:
                print("❌ 'response' key is empty (No Data Found)")
            else:
                # Success - print first row to prove we have data
                print("✅ SUCCESS! Data found.")
                print(f"   Sample: {str(data)[:200]}...")  # Print first 200 chars
        else:
            print(f"❌ API ERROR: {response.text}")

    except Exception as e:
        print(f"❌ CONNECTION ERROR: {e}")


if __name__ == "__main__":
    # TEST 1: The ABTC trade that failed in your logs
    # Log: symbol=ABTC&expiration=20251205&strike=5.000&right=call&date=20251202
    test_url(
        name="FAILING CASE (ABTC)",
        ticker="ABTC",
        exp="20251205",
        strike="5.000",
        right="call",
        date_int="20251202"
    )

    # TEST 2: A Known Liquid Control (SPY or AAPL)
    # We use a date that definitely has data (e.g., a recent trading day)
    # Adjust this date to a known valid trading day in the past if needed
    test_url(
        name="CONTROL CASE (SPY)",
        ticker="SPY",
        exp="20251219",  # Ensure this expiration existed
        strike="680.000",  # Adjust to a strike near money for that date
        right="call",
        date_int="20251212"  # A random valid date
    )