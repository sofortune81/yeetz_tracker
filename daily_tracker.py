import os
import httpx
from datetime import datetime, date, timedelta
from dotenv import load_dotenv
from supabase import create_client
import pytz
import json
from theta_api_client import (
    filter_and_get_post_alert_high,
    filter_and_get_post_alert_low,
    fetch_trade_quote_data, # <-- NEW IMPORT
    fetch_eod_data,         # <-- NEW IMPORT
    get_option_root_params, # <-- NEW IMPORT
    get_theta_date_int      # <-- NEW IMPORT
)


# --- STRATEGY CONFIGURATION (Single Source of Truth) ---
TP_PCT = 20.0       # Take Profit target (+20%)
SCALE_PCT = 80.0    # Percentage of position to sell at TP (80%)
MOON_PCT = 100.0 - SCALE_PCT
STOP_OI_PCT = 20.0  # Stop loss if OI drops below 20% of entry
# -------------------------------------------------------

# --- CONFIGURATION ---
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
THETA_HTTP_URL = "http://127.0.0.1:25503/v3"
EST = pytz.timezone('US/Eastern')

# Connect DB
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
else:
    supabase = None


def get_option_root_params(trade):
    """Helper to build standard params for ThetaData (Symbol, Exp, Strike, Right) using JSON format."""
    right_code = trade['option_type'].lower()[0]

    return {
        "symbol": trade['ticker'],
        "expiration": get_theta_date_int(datetime.strptime(trade['expiration_date'], "%Y-%m-%d")),
        "strike": f"{float(trade['strike']):.3f}",
        "right": right_code,
        "format": "json"
    }


def fetch_nested_json_data(endpoint, params):
    """
    Handles a standard GET request and extracts the 'data' list from the
    nested ThetaData structure: {"response": [{"contract": {...}, "data": [...]}]}
    Returns the 'data' list or None.
    """
    url = f"{THETA_HTTP_URL}{endpoint}"
    print(f"      📡 Request: {url} | Params: {params}")

    try:
        response = httpx.get(url, params=params, timeout=30)
        print(f"      ➡️ Status: {response.status_code}")

        if response.status_code == 472:
            print("      No data found (HTTP 472).")
            return None

        response.raise_for_status()
        full_response = response.json()

        # Navigate the nested structure: full_response['response'][0]['data']
        if (isinstance(full_response, dict) and 'response' in full_response and
                isinstance(full_response['response'], list) and full_response['response']):

            contract_data = full_response['response'][0]
            if 'data' in contract_data and contract_data['data']:
                return contract_data['data']

        return None

    except httpx.HTTPStatusError as e:
        if e.response.status_code != 472:
            print(f"   ❌ HTTP Error for {endpoint}: {e}")
        return None
    except Exception as e:
        print(f"   ❌ API Fetch Error for {endpoint}: {e}")
        return None


# --- THETADATA FETCHERS ---



def fetch_open_interest(trade, date_int):
    """Fetches Open Interest (OI) using the dedicated endpoint."""
    params = get_option_root_params(trade)
    params["date"] = date_int
    endpoint = "/option/history/open_interest"

    data_list = fetch_nested_json_data(endpoint, params)

    if not data_list:
        return 0

    try:
        # OI field is 'open_interest'
        return int(data_list[0].get('open_interest', 0))
    except (ValueError, TypeError) as e:
        print(f"      OI Data Parsing Error: {e}")
        return 0


def check_intraday_high(trade, today_int, alert_dt):
    """
    Gets the maximum price achieved *after* the alert time on Day 0
    using /option/history/trade.
    """
    params = get_option_root_params(trade)
    params["date"] = today_int
    endpoint = "/option/history/trade"

    data_list = fetch_nested_json_data(endpoint, params)

    # Use the centralized helper to filter the ticks and find the high
    post_alert_high = filter_and_get_post_alert_high(data_list, alert_dt)

    return post_alert_high

def check_intraday_low(trade, today_int, alert_dt):
    """
    Gets the minimum price achieved *after* the alert time on Day 0
    using /option/history/trade.
    """
    params = get_option_root_params(trade)
    params["date"] = today_int
    endpoint = "/option/history/trade"

    data_list = fetch_nested_json_data(endpoint, params)

    # Use the centralized helper to filter the ticks and find the low
    post_alert_low = filter_and_get_post_alert_low(data_list, alert_dt)

    return post_alert_low

# --- PERFORMANCE CHECKERS ---

def get_market_data(trade, data_date_int, is_day_0, alert_dt):
    """Unified function to fetch EOD, OI, and calculate High/Low."""

    # 1. Get Official EOD Close and High/Low
    # Note: We pass expiration as a datetime object for the helper
    exp_dt = datetime.strptime(trade['expiration_date'], "%Y-%m-%d")

    eod_data = fetch_eod_data(
        trade['ticker'],
        trade['strike'],
        trade['option_type'][0],
        exp_dt,
        data_date_int
    )



    # 2. Get Open Interest (OI)
    oi = fetch_open_interest(trade, data_date_int)

    if not eod_data:
        # Fallback if EOD fails: treat entry as the high/low/close
        return {"high": float(trade['entry_price']), "close": float(trade['entry_price']),
                "low": float(trade['entry_price']), "oi": oi, "iv": None}

    eod_close = eod_data['close']
    eod_day_high = eod_data['high']
    eod_day_low = eod_data['low']
    entry_price = float(trade['entry_price'])

    if is_day_0:
        # FIX: Fetch the intraday ticks to define data_list
        data_list = fetch_trade_quote_data(
            trade['ticker'],
            trade['strike'],
            trade['option_type'][0],
            exp_dt,
            data_date_int
        )

        # Get Intraday High/Low (Post-Alert) from the fetched data_list
        post_alert_high = filter_and_get_post_alert_high(data_list, alert_dt)
        post_alert_low = filter_and_get_post_alert_low(data_list, alert_dt)

        # LOGIC: High is the max of (Entry, Ticks, EOD High)
        final_high = max(entry_price, post_alert_high, eod_day_high)

        # LOGIC: Low is the min of (Entry, Ticks, EOD Low) - ignoring 0.0
        valid_ticks_low = post_alert_low if post_alert_low > 0 else entry_price
        valid_eod_low = eod_day_low if eod_day_low > 0 else entry_price
        final_low = min(entry_price, valid_ticks_low, valid_eod_low)
    else:
        # Day 1+ logic: Compare EOD against entry (High cannot be < entry)
        final_high = max(eod_day_high, entry_price)
        final_low = eod_day_low if eod_day_low > 0 else entry_price

    return {
        "high": final_high,
        "close": eod_close,
        "low": final_low,
        "oi": oi,
        "iv": None
    }

# Replace the existing calculate_trade_pnl_percentages function:

def calculate_trade_pnl_percentages(trade, high, exit_price):
    entry = float(trade['entry_price'])
    if entry == 0: return 0.0, 0.0

    target_price = entry * (1 + TP_PCT / 100.0)
    hit_tp = high >= target_price

    # 1. Curve 1: Baseline (100% Exit at 20% TP)
    if hit_tp:
        final_tp_pnl_pct = TP_PCT # Locked
    else:
        # 100% position is valued at exit (e.g. 0.0 if expired)
        final_tp_pnl_pct = ((exit_price - entry) / entry) * 100.0

    # 2. Curve 2: Scaled (80/20 Strategy)
    if hit_tp:
        # 80% locked at 20% gain
        scale_gain = (SCALE_PCT / 100.0) * TP_PCT
        # 20% moonshot locked at Peak High
        moon_gain = (MOON_PCT / 100.0) * (((high - entry) / entry) * 100.0)
        final_sim_pnl_pct = scale_gain + moon_gain
    else:
        # Strategy behaves as 100% block for losers/untriggered trades
        final_sim_pnl_pct = ((exit_price - entry) / entry) * 100.0

    return final_sim_pnl_pct, final_tp_pnl_pct

# --- MAIN EXECUTION ---

def run_daily_update():
    print("🚀 Starting Daily Portfolio Simulator Update (No Greeks)...")

    today = datetime.now(EST).date()
    today_int = get_theta_date_int(today)

    # Calculate the latest market close date to fetch data for.
    data_date = today - timedelta(days=1)

    # Handle weekend: if yesterday was Saturday or Sunday, we need Friday's date.
    if data_date.weekday() == 5:
        data_date -= timedelta(days=1)
    elif data_date.weekday() == 6:
        data_date -= timedelta(days=2)

    data_date_int = get_theta_date_int(data_date)
    print(f"🗓️ Today: {today} | Data Date: {data_date}")

    response = supabase.table("whale_alerts").select("*").in_("status", ["OPEN", "SCALED"]).execute()
    active_trades = response.data

    print(f"📂 Processing {len(active_trades)} active trades.")

    for trade in active_trades:
        print(f"\n--- Processing Trade: {trade['ticker']} | ID: {trade['id']} ---")

        # --- A. Entry IV is permanently skipped ---
        # NOTE: We can update the DB here to set entry_iv=0 if it's currently null,
        # but leaving it as null might be better if you re-enable Greeks later.
        pass

        # --- B. Expiration Check ---
        exp_date = datetime.strptime(trade['expiration_date'], "%Y-%m-%d").date()

        # --- C. Get Market Data ---
        trade_entry_dt = datetime.fromisoformat(trade['discord_timestamp']).astimezone(EST)
        trade_entry_date = trade_entry_dt.date()
        market_data = None

        is_day_0 = (trade_entry_date == data_date)

        if trade_entry_date <= data_date:
            # Day 0 or Day 1+
            market_data = get_market_data(trade, data_date_int, is_day_0, trade_entry_dt)  # <-- UPDATED CALL
        else:
            print(f"   ⏩ Skipping {trade['ticker']}. Alert date {trade_entry_date} is after data date {data_date}.")
            continue

        if not market_data:
            print(f"   ⚠️ No data for {trade['ticker']}")
            continue

        high = float(market_data['high'])
        close = float(market_data['close'])
        oi = int(market_data['oi'])
        if data_date >= exp_date:
            actual_last_price = 0.0
        elif close > 0:
            actual_last_price = close
        else:
            # Only fallback to previous last_price if it's not expired and market data is missing
            actual_last_price = float(trade.get('last_price', 0))

        new_status, update_payload = process_trade_state(
            trade,
            market_data['high'],
            market_data['low'],
            actual_last_price,
            oi,
            trade['status'],
            trade['expiration_date']
        )

        # Update local trade object for consistency
        trade.update(update_payload)

        # CONDITIONAL LOWEST PRICE UPDATE (Pre-Scale Drawdown Tracking)
        current_lowest = float(trade.get('lowest_price') or 99999)
        day_low = market_data['low']



        supabase.table("whale_alerts").update(update_payload).eq("id", trade['id']).execute()

        # --- E. Daily Snapshot (History) ---
        snapshot = {
            "alert_id": trade['id'],
            "date": data_date.isoformat(),
            "price_high": high,
            "price_close": close,
            "price_low": market_data['low'],  # <-- NEW: Save Daily Low
            "current_oi": oi,
            "implied_volatility": None  # Always None
        }
        supabase.table("whale_performance").upsert(
            # The lines after the block to replace:
            snapshot,
            on_conflict="alert_id, date"
        ).execute()

    print("\n✅ Daily Update Complete.")


# --- IN daily_tracker.py (New Shared Function) ---

# --- IN daily_tracker.py ---

def process_trade_state(trade, high, low, close, oi, current_status, exp_date_str):
    """
    The Single Source of Truth for trade transitions.
    """
    entry = float(trade['entry_price'])
    target = float(trade['profit_target'])
    stop_oi_level = int(trade['stop_oi_level'])
    exp_date = datetime.strptime(exp_date_str, "%Y-%m-%d").date()

    # We use a dummy date for calculate_trade_pnl_percentages because the
    # math inside that function is price-based, not time-based.
    today_est = datetime.now(EST).date()

    new_status = current_status
    close_reason = None

    # 1. Check Expiration First
    if today_est >= exp_date:
        new_status = "EXPIRED"
        close_reason = "expiration"
    # 2. Check Stop OI (Only if still OPEN)
    elif current_status == "OPEN" and 0 < oi < stop_oi_level:
        new_status = "STOP_OI"
        close_reason = "stop_oi"
    # 3. Check Scale (Only if still OPEN)
    elif current_status == "OPEN" and high >= target:
        new_status = "SCALED"

    # Capture the "Memorialized" PnL
    # If the status is no longer OPEN, we lock the values.
    # Note: For SCALED trades, 'high' ensures moonshot captures the peak.
    current_high = max(high, float(trade.get('highest_price') or 0))
    sim_pnl, tp_pnl = calculate_trade_pnl_percentages(trade, current_high, close if new_status != "SCALED" else target)

    payload = {
        "status": new_status,
        "highest_price": current_high,
        "last_price": close,
        "last_oi": oi,
        "final_sim_pnl_pct": sim_pnl if new_status != "OPEN" else None,
        "final_tp_pnl_pct": tp_pnl if new_status != "OPEN" else None
    }

    if close_reason:
        payload["close_reason"] = close_reason
        payload["close_date"] = today_est.isoformat()
        payload["close_price"] = close

    # Lowest price only tracks during the high-risk 'OPEN' phase
    if current_status == "OPEN":
        existing_low = float(trade.get('lowest_price') or entry)
        payload["lowest_price"] = min(low, existing_low) if low > 0 else existing_low
    return new_status, payload

if __name__ == "__main__":
    run_daily_update()