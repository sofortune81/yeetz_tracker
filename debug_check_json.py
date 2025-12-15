import json

import httpx
import os

# --- CONFIGURATION ---

# IMPORTANT: Ensure your local ThetaData server is running before executing this script.
THETA_HTTP_URL = "http://127.0.0.1:25503/v3"


def run_eod_test():
    """
    Tests the specific EOD endpoint with known working parameters
    and requests the result in JSON format.
    """
    print("--- Starting ThetaData EOD Endpoint Test ---")

    endpoint = "/option/history/eod"

    # These are the exact parameters you confirmed working:
    params = {
        "symbol": "SPXW",
        "expiration": "20251212",
        "strike": "6500.000",
        "right": "c",  # 'call' simplified to 'c'
        "start_date": "20251209",
        "end_date": "20251211",
        "format": "json"
    }

    url = f"{THETA_HTTP_URL}{endpoint}"

    print(f"📡 Requesting: {url}")
    print(f"📦 Parameters: {params}")

    try:
        response = httpx.get(url, params=params, timeout=10)

        print(f"\n➡️ HTTP Status Code: {response.status_code}")

        # Check for successful response
        response.raise_for_status()

        # Parse and print the JSON response beautifully
        data = response.json()

        print("\n✅ Successfully received and parsed JSON data:")
        print("--------------------------------------------------")
        # Use json.dumps for pretty printing the output
        print(json.dumps(data, indent=4))
        print("--------------------------------------------------")

        if isinstance(data, list) and len(data) > 0:
            print(f"Total records found: {len(data)}")

    except httpx.HTTPStatusError as e:
        print(f"\n❌ Error: HTTP request failed with status code {e.response.status_code}")
        if e.response.status_code == 472:
            print("   (472 is 'No data found' from ThetaData.)")
        print(f"   Response Body: {e.response.text}")
    except httpx.RequestError as e:
        print(f"\n❌ Error: Could not connect to ThetaData server at {THETA_HTTP_URL}.")
        print("   Please ensure your local ThetaData server is running.")
    except Exception as e:
        print(f"\n❌ An unexpected error occurred: {e}")


if __name__ == "__main__":
    # Note: If you run this script outside of the directory containing the other files,
    # you might need to manually install the 'httpx' library: pip install httpx
    run_eod_test()