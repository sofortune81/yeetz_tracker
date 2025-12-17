import httpx
from datetime import datetime
import pytz

# --- CONFIGURATION (Assumed from environment) ---
THETA_HTTP_URL = "http://127.0.0.1:25503/v3"
EST = pytz.timezone('US/Eastern')


# --- UTILITY HELPERS ---

def get_theta_date_int(dt_obj):
    """Converts a date object/datetime to a ThetaData-compatible YYYYMMDD integer."""
    return int(dt_obj.strftime('%Y%m%d'))


def get_option_root_params(ticker, strike, opt_type_char, exp_date):
    """Helper to build standard params for ThetaData (Symbol, Exp, Strike, Right)."""
    # FIX: REST APIs use 'call' or 'put' (not 'c' or 'p').
    opt_type_full = "call" if opt_type_char.lower() == 'c' else "put"

    return {
        "symbol": ticker,
        "expiration": get_theta_date_int(exp_date),
        "strike": f"{strike:.3f}",
        "right": opt_type_full,
        "format": "json"
    }


def fetch_data_and_handle_error(endpoint, params):
    """
    Centralized function to perform GET requests, log the full URL, and handle
    common ThetaData errors (472, 473, etc.).
    Returns the response JSON data or None.
    """
    url = f"{THETA_HTTP_URL}{endpoint}"

    # --- LOG THE FULL URL ---
    # Construct the full URL with parameters for easy manual testing
    full_url = str(httpx.URL(url, params=params))
    print(f"      📡 Requesting URL: {full_url}")
    # --- END LOG ---

    try:
        response = httpx.get(url, params=params, timeout=30)

        # Handle specific ThetaData errors
        if response.status_code in [472, 473]:
            # 472: No Data, 473: Often a rate limit/concurrency error
            print(f"      ➡️ Status: {response.status_code}. Data not found or API denied request.")
            return None

        response.raise_for_status()  # Raises exception for standard 4xx/5xx errors

        # Navigate the nested structure common to ThetaData EOD/Trade endpoints
        full_response = response.json()
        if (isinstance(full_response, dict) and 'response' in full_response and
                isinstance(full_response['response'], list) and full_response['response']):

            contract_data = full_response['response'][0]
            if 'data' in contract_data and contract_data['data']:
                # The response structure can be {"response": [..., "data": [...] ]} or {"response": [ <list of values> ]}
                return contract_data['data']

            # Handle the case where the response is a direct list of values (e.g., EOD)
            return full_response['response']

        return None

    except httpx.HTTPStatusError as e:
        print(f"   ❌ HTTP Error for {endpoint}: Client error '{e.response.status_code}'")
        return None
    except Exception as e:
        print(f"   ❌ API Fetch Error for {endpoint}: {e}")
        return None

def filter_and_get_post_alert_high(data_list, alert_dt):
    """
    Takes a list of trade ticks (data_list) and an EST datetime object (alert_dt).
    Returns the maximum price achieved AFTER the alert_dt timestamp.
    """
    post_alert_high = 0.0

    if not data_list:
        return 0.0

    for tick in data_list:
        try:
            trade_timestamp = tick.get('trade_timestamp')
            price = float(tick.get('price', 0.0))

            if not trade_timestamp:
                continue

            # Convert UTC ISO string to offset-aware EST datetime object
            dt_utc = datetime.fromisoformat(trade_timestamp.replace('Z', '+00:00'))
            dt_est = dt_utc.astimezone(EST)

            # Robust Direct Datetime Comparison
            if dt_est >= alert_dt:
                if price > post_alert_high:
                    post_alert_high = price
        except (KeyError, ValueError, IndexError, AttributeError) as parse_e:
            print(f"      Tick Parsing Error: {parse_e}")
            continue

    return post_alert_high

def filter_and_get_post_alert_low(data_list, alert_dt):
    """
    Takes a list of trade ticks (data_list) and an EST datetime object (alert_dt).
    Returns the minimum price achieved AFTER the alert_dt timestamp.
    """
    post_alert_low = float('inf')

    if not data_list:
        return 0.0

    for tick in data_list:
        try:
            trade_timestamp = tick.get('trade_timestamp')
            price = float(tick.get('price', 0.0))

            if not trade_timestamp or price == 0.0:
                continue

            # Convert UTC ISO string to offset-aware EST datetime object
            dt_utc = datetime.fromisoformat(trade_timestamp.replace('Z', '+00:00'))
            dt_est = dt_utc.astimezone(EST)

            # Robust Direct Datetime Comparison
            if dt_est >= alert_dt:
                if price < post_alert_low:
                    post_alert_low = price
        except (KeyError, ValueError, IndexError, AttributeError) as parse_e:
            print(f"      Tick Parsing Error: {parse_e}")
            continue

    # Return 0.0 if no trades were found after the alert, otherwise return the low price
    return post_alert_low if post_alert_low != float('inf') else 0.0
# --- THETADATA FETCHERS (Used by backfill_manager.py) ---

def fetch_trade_quote_data(ticker, strike, opt_type_char, exp_date, date_int):
    """Fetches intraday trade/quote data (Trade Quote)."""
    params = get_option_root_params(ticker, strike, opt_type_char, exp_date)
    params["date"] = date_int
    endpoint = "/option/history/trade_quote"

    return fetch_data_and_handle_error(endpoint, params)


# Add this to theta_api_client.py

def get_intraday_performance(ticker, strike, opt_type_char, exp_date, date_int, alert_dt):
    """
    Fetches all trade ticks for the day and filters for the high/low
    that occurred strictly AFTER the alert timestamp.
    """
    params = get_option_root_params(ticker, strike, opt_type_char, exp_date)
    params["date"] = date_int
    # We use /trade instead of /trade_quote for higher performance/simpler parsing
    endpoint = "/option/history/trade"

    data_list = fetch_data_and_handle_error(endpoint, params)

    if not data_list:
        return {"high": 0.0, "low": 0.0}

    high = filter_and_get_post_alert_high(data_list, alert_dt)
    low = filter_and_get_post_alert_low(data_list, alert_dt)

    return {"high": high, "low": low}


def fetch_eod_data(ticker, strike, opt_type_char, exp_date, date_int):
    """Fetches the End-Of-Day (EOD) data (High/Close/OI)."""
    params = get_option_root_params(ticker, strike, opt_type_char, exp_date)
    params["start_date"] = date_int
    params["end_date"] = date_int
    endpoint = "/option/history/eod"

    # EOD response is typically a list containing one dictionary: [{"high": 0.05, "close": 0.02, "oi": 0, ...}]
    data_list = fetch_data_and_handle_error(endpoint, params)

    # Check if the data list exists and has at least one element (the EOD row)
    if data_list and data_list[0]:
        eod_dict = data_list[0]

        # Check for required keys and ensure 'oi' is present and can be converted
        if all(key in eod_dict for key in ['high', 'close']):  # Removed 'volume' from check for robustness
            # Note: The EOD response you provided does not have 'oi' but 'volume'.
            # Assuming 'oi' is actually the correct key for Open Interest in other cases,
            # but using 'volume' as a substitute or assuming it's missing entirely.

            # Use 0 if 'open_interest' or 'oi' is not present
            oi_val = eod_dict.get('open_interest') or eod_dict.get('oi', 0)

            return {
                "high": float(eod_dict['high']),
                "close": float(eod_dict['close']),
                "low": float(eod_dict.get('low', 0.0)),  # <-- NEW: Include 'low'
                "oi": int(oi_val)
            }

    print(f"      EOD {date_int}: Failed to fetch or parse EOD dictionary data.")
    # The lines after the block to replace:
    return None