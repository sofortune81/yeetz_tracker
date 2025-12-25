import httpx
from datetime import datetime
import pytz
import logging

# --- LOGGER SETUP ---
logger = logging.getLogger("yeetz_logger")

# --- CONFIGURATION ---
THETA_HTTP_URL = "http://127.0.0.1:25503/v3"
EST = pytz.timezone('US/Eastern')


# --- UTILITY HELPERS ---

def get_theta_date_int(dt_obj):
    """Converts a date object/datetime to a ThetaData-compatible YYYYMMDD integer."""
    return int(dt_obj.strftime('%Y%m%d'))


def get_option_root_params(ticker, strike, opt_type_char, exp_date):
    """Helper to build standard params for ThetaData (Symbol, Exp, Strike, Right)."""
    opt_type_full = "call" if opt_type_char.lower() == 'c' else "put"
    return {
        "symbol": ticker,
        "expiration": get_theta_date_int(exp_date),
        "strike": f"{strike:.3f}",
        "right": opt_type_full,
        "format": "json"  # <--- This is the correct parameter now
    }


def fetch_data_and_handle_error(endpoint, params):
    url = f"{THETA_HTTP_URL}{endpoint}"
    # Log the request URL for debugging
    # logger.debug(f"📡 Requesting: {url} | Params: {params}")

    try:
        response = httpx.get(url, params=params, timeout=30)

        # 1. Handle Known ThetaData Codes
        if response.status_code in [472, 473]:
            # 472 = No Data (Common on weekends/holidays)
            # logger.warning(f"⚠️ API {response.status_code} for {endpoint}: {response.text}")
            return None

        # 2. Handle HTTP Errors
        if response.is_error:
            logger.error(f"❌ API HTTP Error {response.status_code} for {endpoint}")
            logger.error(f"   Raw Response: {response.text}")
            return None

        # 3. Parse JSON
        full_response = response.json()

        if (isinstance(full_response, dict) and 'response' in full_response and
                isinstance(full_response['response'], list) and full_response['response']):

            contract_data = full_response['response'][0]

            # Case A: Nested 'data' key (Trade/Quote endpoints)
            if isinstance(contract_data, dict) and 'data' in contract_data:
                return contract_data['data']

            # Case B: Direct list (EOD endpoint)
            return full_response['response']

        return None

    except httpx.HTTPStatusError as e:
        logger.error(f"❌ HTTP Status Error: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ Critical API Exception: {e}")
        return None


def filter_and_get_post_alert_high(data_list, alert_dt):
    post_alert_high = 0.0
    if not data_list: return 0.0

    for tick in data_list:
        try:
            trade_timestamp = tick.get('trade_timestamp') or tick.get('ms_of_day')
            price = float(tick.get('price', 0.0))
            if not trade_timestamp or isinstance(trade_timestamp, int): continue

            dt_utc = datetime.fromisoformat(trade_timestamp.replace('Z', '+00:00'))
            dt_est = dt_utc.astimezone(EST)

            if dt_est >= alert_dt:
                if price > post_alert_high:
                    post_alert_high = price
        except Exception:
            continue
    return post_alert_high


def filter_and_get_post_alert_low(data_list, alert_dt):
    post_alert_low = float('inf')
    found_any = False
    if not data_list: return 0.0

    for tick in data_list:
        try:
            trade_timestamp = tick.get('trade_timestamp')
            price = float(tick.get('price', 0.0))
            if not trade_timestamp or price == 0.0: continue

            dt_utc = datetime.fromisoformat(trade_timestamp.replace('Z', '+00:00'))
            dt_est = dt_utc.astimezone(EST)

            if dt_est >= alert_dt:
                found_any = True
                if price < post_alert_low:
                    post_alert_low = price
        except Exception:
            continue

    return post_alert_low if found_any else 0.0


# --- ENDPOINTS ---

def fetch_trade_quote_data(ticker, strike, opt_type_char, exp_date, date_int):
    """Fetches intraday trade/quote data."""
    params = get_option_root_params(ticker, strike, opt_type_char, exp_date)
    params["date"] = date_int
    # REMOVED: params["use_csv"] = "false" (This caused the 410 Error)
    endpoint = "/option/history/trade_quote"
    return fetch_data_and_handle_error(endpoint, params)


def fetch_eod_data(ticker, strike, opt_type_char, exp_date, date_int):
    """Fetches EOD Summary."""
    params = get_option_root_params(ticker, strike, opt_type_char, exp_date)
    params["start_date"] = date_int
    params["end_date"] = date_int
    endpoint = "/option/history/eod"

    data_list = fetch_data_and_handle_error(endpoint, params)

    if data_list and data_list[0]:
        eod_dict = data_list[0]
        if 'close' in eod_dict:
            oi_val = eod_dict.get('open_interest') or eod_dict.get('oi', 0)
            return {
                "high": float(eod_dict.get('high', 0.0)),
                "close": float(eod_dict.get('close', 0.0)),
                "low": float(eod_dict.get('low', 0.0)),
                "oi": int(oi_val)
            }
    return None