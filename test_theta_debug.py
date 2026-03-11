import requests
import pandas as pd
from io import StringIO
from datetime import datetime

# --- CONFIG ---
THETA_PORT = "25503"
BASE_URL = f"http://127.0.0.1:{THETA_PORT}/v3/option/history/ohlc"


# --- 1. THE NEW PARSER FUNCTION ---
def parse_thetadata_csv(csv_content):
    """
    Parses the CSV string provided by ThetaData.
    """
    try:
        # Read the CSV string into a pandas DataFrame
        df = pd.read_csv(StringIO(csv_content))

        # Clean columns (strip whitespace)
        df.columns = [c.strip() for c in df.columns]

        # Ensure timestamp is datetime
        if 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp'])

        return df
    except Exception as e:
        print(f"   ❌ Error inside parser: {e}")
        return pd.DataFrame()


# --- 2. TESTS ---
def run_tests():
    print("🛡️  STARTING PRE-FLIGHT CHECKS...\n")

    # ==========================================
    # TEST A: Verify Logic on Your Sample Data
    # ==========================================
    print("--- TEST A: Static CSV Parsing Logic ---")
    raw_sample = """symbol,expiration,strike,right,timestamp,open,high,low,close,volume,count,vwap
"SPY","2025-12-19",680.000,"CALL",2025-12-12T09:30:00,10.04,10.09,10.04,10.06,10,3,10.05
"SPY","2025-12-19",680.000,"CALL",2025-12-12T09:31:00,9.96,9.96,9.96,9.96,15,1,9.99
"SPY","2025-12-19",680.000,"CALL",2025-12-12T09:32:00,10.25,10.27,10.15,10.15,5,3,10.03"""

    df = parse_thetadata_csv(raw_sample)

    if not df.empty:
        # Calculate the stats we need for cleanup
        day_high = df['high'].max()
        day_low = df['low'].min()
        day_close = df.iloc[-1]['close']

        print(f"✅ Parser worked! Found {len(df)} rows.")
        print(f"   Calculated High:  {day_high} (Expected 10.27)")
        print(f"   Calculated Low:   {day_low} (Expected 9.96)")
        print(f"   Calculated Close: {day_close} (Expected 10.15)")
    else:
        print("❌ Parser FAILED on static data.")

    print("\n" + "-" * 40 + "\n")

    # ==========================================
    # TEST B: Verify Live API Format
    # ==========================================
    print("--- TEST B: Live API Format Check (SPY Control) ---")

    # Using the SPY parameters from your sample
    params = {
        "symbol": "SPY",
        "expiration": "20251219",
        "strike": "680.000",
        "right": "call",
        "date": "20251212",
        "interval": "1m",
        # "use_csv": "true"  <-- Uncomment if your API supports this param to force CSV
    }

    url = f"{BASE_URL}?{'&'.join([f'{k}={v}' for k, v in params.items()])}"
    print(f"🔗 Requesting: {url}")

    try:
        response = requests.get(url, timeout=5)
        print(f"📡 Status: {response.status_code}")

        if response.status_code == 200:
            content_type = response.headers.get('Content-Type', '')
            content_text = response.text.strip()

            print(f"   Content-Type Header: {content_type}")
            print(f"   First 50 chars: {content_text[:50]}...")

            # CHECK 1: Is it CSV?
            if content_text.startswith("symbol,expiration") or "," in content_text.splitlines()[0]:
                print("✅ API returned CSV format. (Matches your sample)")
                live_df = parse_thetadata_csv(content_text)
                if not live_df.empty:
                    print(f"   ✅ Live parse successful: {len(live_df)} rows.")
                else:
                    print("   ❌ Live CSV returned but failed to parse.")

            # CHECK 2: Is it JSON?
            elif content_text.startswith("[") or content_text.startswith("{"):
                print("⚠️  API returned JSON, not CSV.")
                print("   Action: You must add `use_csv=true` to params OR update the parser to handle JSON.")

            else:
                print("❌ Unknown format returned.")
        else:
            print(f"❌ API Error: {response.text}")

    except Exception as e:
        print(f"❌ Connection Error: {e}")


if __name__ == "__main__":
    run_tests()