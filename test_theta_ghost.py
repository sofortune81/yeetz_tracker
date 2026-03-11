import requests
import json

# Connection Details
THETA_PORT = "25503"
BASE_URL = f"http://127.0.0.1:{THETA_PORT}"


def test_ghost(name, ticker, exp, strike, right, date):
    print(f"\n👻 TESTING GHOST RESPONSE: {name}")

    # Using the exact URL format that failed before
    url = f"{BASE_URL}/v3/option/history/ohlc"
    params = {
        "symbol": ticker,
        "expiration": exp,
        "strike": strike,
        "right": right,
        "date": date,
        "interval": "1m"
    }

    print(f"🔗 URL: {url}")
    print(f"⚙️ Params: {params}")

    try:
        # 1. Get the Raw Response
        response = requests.get(url, params=params, timeout=5)

        print(f"📡 Status Code: {response.status_code}")
        print(f"📄 Content-Type: {response.headers.get('Content-Type', 'Unknown')}")

        # 2. Print the RAW TEXT (The "Ghost")
        raw_text = response.text
        print(f"👻 RAW RESPONSE BODY (Start):")
        print("-" * 30)
        print(f"'{raw_text}'")  # We quote it to see if it's truly empty
        print("-" * 30)

        # 3. Try to Parse Manually
        if not raw_text.strip():
            print("❌ FAILURE: Received EMPTY STRING. (No Data for this query?)")
        else:
            try:
                data = response.json()
                print(f"✅ JSON SUCCESS: {str(data)[:100]}...")
            except json.JSONDecodeError as e:
                print(f"❌ JSON PARSE ERROR: {e}")
                print("👉 This means the terminal sent text/csv/html, not JSON.")

    except Exception as e:
        print(f"💥 CRITICAL ERROR: {e}")


if __name__ == "__main__":
    # Test the Failing Case (ABTC)
    test_ghost("FAILING CASE", "ABTC", "20251205", "5.000", "call", "20251202")

    # Test the Control Case (SPY)
    test_ghost("CONTROL CASE", "SPY", "20251219", "680.000", "call", "20251212")