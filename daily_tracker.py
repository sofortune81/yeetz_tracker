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

def calculate_trade_pnl_percentages(trade, high, exit_price):
    """
    Deterministically calculates PnL.
    Curve 1 (Baseline): Always +20% if 20% hit, else exit_price return.
    Curve 2 (Scaled): 80% at 20% profit + 20% at High (Moonshot).
    """
    entry = float(trade['entry_price'])
    if entry == 0: return 0.0, 0.0

    target_price = entry * (1 + TP_PCT / 100.0)
    hit_tp = high >= target_price

    # 1. Curve 1: Baseline (100% Exit at 20%)
    final_tp_pnl_pct = TP_PCT if hit_tp else ((exit_price - entry) / entry) * 100.0

    # 2. Curve 2: Baseline Scale (80/20 Strategy)
    if hit_tp:
        # Scale portion: 80% of position realized at 20% gain
        scale_gain = (SCALE_PCT / 100.0) * TP_PCT
        # Moonshot portion: 20% of position at peak high
        moon_gain = (MOON_PCT / 100.0) * (((high - entry) / entry) * 100.0)
        final_sim_pnl_pct = scale_gain + moon_gain
    else:
        # No TP hit: 100% position exits at current/exit price
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
        if data_date >= exp_date:
            print(f"   💀 Trade Expired: {trade['ticker']}")

            supabase.table("whale_alerts").update({
                "status": "EXPIRED",
                "close_date": today.isoformat(),
                "close_price": 0,
                "close_reason": "expiration"
            }).eq("id", trade['id']).execute()

            # For EXPIRED, we assume an exit price of $0.00 unless a price was recorded.
            high_price = max(float(trade.get('highest_price') or 0.0), market_data['high'] if market_data else 0.0)
            exit_price = 0.0

            final_sim_pnl_pct, final_tp_pnl_pct = calculate_trade_pnl_percentages(
                trade, high_price, exit_price
            )

            update_payload = {  # We define update_payload inside this block for EXPIRED
                "status": "EXPIRED",
                "close_date": today.isoformat(),
                "close_price": exit_price,
                "close_reason": "expiration",
                "final_sim_pnl_pct": final_sim_pnl_pct,
                "final_tp_pnl_pct": final_tp_pnl_pct
            }

            supabase.table("whale_alerts").update(update_payload).eq("id", trade['id']).execute()

            continue
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
        # iv is always None now

        # --- D. Strategy Logic ---
        update_payload = {
            "last_price": close,
            "last_oi": oi,
            "highest_price": max(high, float(trade.get('highest_price') or 0))
        }

        # CONDITIONAL LOWEST PRICE UPDATE (Pre-Scale Drawdown Tracking)
        current_lowest = float(trade.get('lowest_price') or 99999)
        day_low = market_data['low']

        # Only update lowest price if the trade is still OPEN
        if trade['status'] == "OPEN" and day_low > 0 and day_low < current_lowest:
            update_payload['lowest_price'] = day_low
            trade['lowest_price'] = day_low  # Update local trade object

        if trade['status'] == "OPEN":
            profit_target = float(trade['profit_target'])
            if high >= profit_target:
                print(f"   💰 SCALE OUT HIT: {trade['ticker']} @ {profit_target:.2f}")
                # Use the utility function to calculate PnL based on hitting TP
                # The high is used for moonshot. profit_target is passed as a relevant exit price.
                final_sim_pnl_pct, final_tp_pnl_pct = calculate_trade_pnl_percentages(
                    trade, high, profit_target
                )

                update_payload["status"] = "SCALED"
                update_payload["tp_hit_date"] = datetime.now(EST).isoformat()
                update_payload["tp_hit_price"] = profit_target
                update_payload["final_sim_pnl_pct"] = final_sim_pnl_pct
                update_payload["final_tp_pnl_pct"] = final_tp_pnl_pct

        elif trade['status'] == "SCALED":
            # Update the peak high first
            # 1. Get the current peak high
            current_high = update_payload["highest_price"]

            # 2. Strategy: 80% at 20% TP + 20% at the NEW Peak High
            # We pass current_high for BOTH high and exit_price because for a
            # SCALED trade, the moonshot is valued at its peak, not the current close.
            final_sim_pnl_pct, _ = calculate_trade_pnl_percentages(
                trade, current_high, current_high
            )

            update_payload.update({
                "final_sim_pnl_pct": final_sim_pnl_pct,
                "final_tp_pnl_pct": 20.0  # HARD LOCK: Baseline is always 20% for scaled trades
            })

        stop_oi = int(trade['stop_oi_level'])
        if oi < stop_oi and trade['status'] != "STOP_OI":
            print(f"   🛑 STOP OI HIT: {trade['ticker']} (OI {oi} < {stop_oi})")

            # FIX: Use the 'highest_price' from update_payload (Lifetime High)
            high_price = update_payload["highest_price"]
            exit_price = close

            # This ensures Moonshot PnL uses the peak from Day 1, even if Stopped on Day 3
            final_sim_pnl_pct, final_tp_pnl_pct = calculate_trade_pnl_percentages(
                trade, high_price, exit_price
            )

            update_payload["final_sim_pnl_pct"] = final_sim_pnl_pct
            update_payload["final_tp_pnl_pct"] = final_tp_pnl_pct

            update_payload["status"] = "STOP_OI"
            update_payload["close_date"] = datetime.now(EST).isoformat()
            update_payload["close_price"] = close
            update_payload["close_reason"] = "stop_oi"

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


if __name__ == "__main__":
    run_daily_update()