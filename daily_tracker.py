import os
import httpx
from datetime import datetime, date, timedelta
from dotenv import load_dotenv
from supabase import create_client
import pytz
from master_logger import setup_logger
import json
from theta_api_client import (
    filter_and_get_post_alert_high,
    filter_and_get_post_alert_low,
    fetch_trade_quote_data,
    fetch_eod_data,
    get_option_root_params,
    get_theta_date_int
)

log_name = os.path.splitext(os.path.basename(__file__))[0]
logger = setup_logger(name=log_name, log_filename="yeetz.log")

# --- STRATEGY CONFIGURATION (Single Source of Truth) ---
TP_PCT = 20.0  # Take Profit target (+20%)
SCALE_PCT = 80.0  # Percentage of position to sell at TP (80%)
MOON_PCT = 100.0 - SCALE_PCT
STOP_OI_PCT = 20.0  # Stop loss if OI drops below 20% of entry
# -------------------------------------------------------

# --- CONFIGURATION ---
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
THETA_HTTP_URL = "http://127.0.0.1:25503/v3"
EST = pytz.timezone('US/Eastern')

if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
else:
    supabase = None



# --- SHARED API FETCHERS ---

def fetch_open_interest(trade, date_int):
    """Fetches Open Interest (OI)."""
    params = get_option_root_params(trade['ticker'], float(trade['strike']), trade['option_type'][0],
                                    datetime.strptime(trade['expiration_date'], "%Y-%m-%d"))
    params["date"] = date_int
    endpoint = "/option/history/open_interest"

    # Simple direct fetch (Assuming helper in theta_api_client handles this or we inline simple logic)
    # Re-implementing simplified inline fetch to avoid circular dependency hell if theta_api_client is simple
    url = f"{THETA_HTTP_URL}{endpoint}"
    try:
        resp = httpx.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            # Handle nested response structure
            if 'response' in data and data['response']:
                item = data['response'][0]
                if isinstance(item, dict) and 'open_interest' in item:
                    return int(item['open_interest'])
                # Some endpoints return [ { "date":..., "open_interest":... } ] directly or in 'data'
                if 'data' in item and item['data']:
                    return int(item['data'][0].get('open_interest', 0))
        return 0
    except Exception as e:
        logger.error(f"OI Fetch Error: {e}")
        return 0

def calculate_pnl_snapshots(entry, high, close, status):
    """
    Calculates the definitive % return for all 3 curves.
    """
    if entry == 0: return 0.0, 0.0

    # 1. BASELINE CURVE (100% @ TP)
    # If it's a "SCALED" win, we locked 20%. If not, we are floating at current price.
    if status == "SCALED":
        baseline_pct = TP_PCT
    else:
        baseline_pct = ((close - entry) / entry) * 100.0

    # 2. STRATEGY CURVE (80% @ TP + 20% @ Moonshot)
    if status == "SCALED":
        # 80% is locked at TP (20% gain)
        # 20% is sold at the PEAK High (Moonshot)
        moon_return = ((high - entry) / entry) * 100.0
        strategy_pct = (0.8 * TP_PCT) + (0.2 * moon_return)
    else:
        # If not a winner, the whole position floats at current price
        strategy_pct = ((close - entry) / entry) * 100.0

    return strategy_pct, baseline_pct

def get_market_data(trade, data_date_int, is_day_0, alert_dt):
    """Unified function to fetch EOD, OI, and calculate High/Low."""
    exp_dt = datetime.strptime(trade['expiration_date'], "%Y-%m-%d")

    # 1. Get Official EOD Data
    eod_data = fetch_eod_data(
        trade['ticker'],
        trade['strike'],
        trade['option_type'][0],
        exp_dt,
        data_date_int
    )

    # 2. Get Open Interest
    oi = fetch_open_interest(trade, data_date_int)

    if not eod_data:
        # No data found (likely holiday or missing), fallback to existing state
        return None

    eod_close = eod_data['close']
    eod_day_high = eod_data['high']
    eod_day_low = eod_data['low']
    entry_price = float(trade['entry_price'])

    # 3. Refine High/Low logic
    final_high = eod_day_high
    final_low = eod_day_low

    if is_day_0:
        # On Day 0, we must ensure High/Low respects the Alert Timestamp
        data_list = fetch_trade_quote_data(
            trade['ticker'],
            trade['strike'],
            trade['option_type'][0],
            exp_dt,
            data_date_int
        )
        post_alert_high = filter_and_get_post_alert_high(data_list, alert_dt)
        post_alert_low = filter_and_get_post_alert_low(data_list, alert_dt)

        # High is max of (Entry, Ticks, EOD High)
        final_high = max(entry_price, post_alert_high, eod_day_high)

        # Low logic: If ticks exist, use them. If not, fallback to EOD. Ignore 0.
        valid_ticks_low = post_alert_low if post_alert_low > 0 else 99999
        valid_eod_low = eod_day_low if eod_day_low > 0 else 99999
        final_low = min(entry_price, valid_ticks_low, valid_eod_low)
        if final_low == 99999: final_low = entry_price
    else:
        # Day 1+: Standard High/Low
        if final_high == 0: final_high = eod_close  # Fallback
        if final_low == 0: final_low = eod_close  # Fallback

    return {
        "high": final_high,
        "close": eod_close,
        "low": final_low,
        "oi": oi
    }


# --- CORE STRATEGY LOGIC ---

def calculate_strategy_pnl(entry, high, close, status):
    """
    Calculates the 3 PnL curves.
    Returns: (Strategy_PnL_%, Baseline_PnL_%, Max_PnL_%)
    """
    if entry == 0: return 0.0, 0.0, 0.0

    target_price = entry * (1 + TP_PCT / 100.0)

    # Check if TP was EVER hit (implied by SCALED status or current high)
    hit_tp = (status == "SCALED") or (high >= target_price)

    if hit_tp:
        # --- SCENARIO: WINNER ---

        # 1. Baseline (100% @ 20% TP)
        baseline_pct = TP_PCT

        # 2. Strategy (80% @ 20% TP + 20% @ Peak High)
        # Moonshot portion assumes we sell at the absolute HIGHEST price reached
        moon_ret_pct = ((high - entry) / entry) * 100.0
        strategy_pct = (SCALE_PCT / 100.0 * TP_PCT) + (MOON_PCT / 100.0 * moon_ret_pct)

        # 3. Max / "No Pussy" (100% @ Peak High)
        max_pct = moon_ret_pct

    else:
        # --- SCENARIO: LOSER / STILL RUNNING ---
        # For all curves, if TP isn't hit, we hold the full bag to the current/close price.
        current_ret_pct = ((close - entry) / entry) * 100.0

        baseline_pct = current_ret_pct
        strategy_pct = current_ret_pct
        max_pct = current_ret_pct

    return strategy_pct, baseline_pct, max_pct


def process_trade_state(trade, high, low, close, oi, current_status, exp_date_str, current_date=None):
    """
    State Machine for Trade Lifecycle.
    """
    entry = float(trade['entry_price'])
    target = float(trade['profit_target'])
    stop_oi_level = int(trade['stop_oi_level'])
    exp_date = datetime.strptime(exp_date_str, "%Y-%m-%d").date()

    check_date = current_date or datetime.now(EST).date()

    # 1. Update Persistent High/Low
    prev_high = float(trade.get('highest_price') or 0)
    new_highest = max(high, prev_high)
    if new_highest == 0: new_highest = float(trade['entry_price'])

    # Logic: "Lowest Price" tracks drawdown.
    prev_low = float(trade.get('lowest_price') or 99999)
    if prev_low == 99999: prev_low = entry
    new_lowest = min(low, prev_low) if low > 0 else prev_low

    # 2. Determine Status (Once SCALED, always SCALED)
    new_status = current_status
    close_reason = None

    if current_status == "SCALED":
        # Already won. We just update the moonshot peak (handled by new_highest)
        pass
    else:
        # Still OPEN, check for events
        if new_highest >= target:
            new_status = "SCALED"
        elif check_date >= exp_date:
            new_status = "EXPIRED"
            close_reason = "expiration"
        elif 0 < oi < stop_oi_level:
            new_status = "STOP_OI"
            close_reason = "stop_oi"

        # 3. CALCULATE HARD % NUMBERS HERE
        strat_pct, base_pct = calculate_pnl_snapshots(
            float(trade['entry_price']),
            new_highest,
            close,
            new_status
        )

        payload = {
            "status": new_status,
            "highest_price": new_highest,
            "last_price": close,
            "last_oi": oi,
            # SAVE THE HARD NUMBERS TO DB
            "final_sim_pnl_pct": strat_pct,  # Curve B (Strategy)
            "final_tp_pnl_pct": base_pct,  # Curve A (Baseline)
        }

    # 4. Construct Payload
    payload = {
        "status": new_status,
        "highest_price": new_highest,
        "lowest_price": new_lowest,
        "last_price": close,
        "last_oi": oi,
        # We update these DAILY so the DB always has the latest "Curve" value
        "final_sim_pnl_pct": strat_pct,
        "final_tp_pnl_pct": base_pct,
        # We can add a custom field for Max Curve if schema allows, otherwise Dashboard calculates it
        # "final_max_pnl_pct": max_pct
    }

    if close_reason:
        payload["close_reason"] = close_reason
        payload["close_date"] = check_date.isoformat()
        payload["close_price"] = close

    return new_status, payload


def run_daily_update():
    print("🚀 Starting Daily Portfolio Simulator Update...")
    today = datetime.now(EST).date()
    today_int = get_theta_date_int(today)

    # Data Date = Yesterday (unless market is open, but usually we run this next morning)
    # If running intra-day, handle logic to get current quote.
    # For safety, let's assume we want Close of Yesterday.
    data_date = today - timedelta(days=1)
    if data_date.weekday() >= 5:  # Skip weekends
        data_date -= timedelta(days=1 if data_date.weekday() == 5 else 2)

    data_date_int = get_theta_date_int(data_date)
    print(f"🗓️ Data Date: {data_date}")

    # Fetch OPEN or SCALED trades (SCALED trades need their moonshot value updated)
    response = supabase.table("whale_alerts").select("*").in_("status", ["OPEN", "SCALED"]).execute()
    active_trades = response.data

    print(f"📂 Processing {len(active_trades)} active/moonshot trades.")

    for trade in active_trades:
        print(f"   Processing {trade['ticker']}...")

        trade_entry_dt = datetime.fromisoformat(trade['discord_timestamp']).astimezone(EST)
        is_day_0 = (trade_entry_dt.date() == data_date)

        if trade_entry_dt.date() > data_date:
            continue

        market_data = get_market_data(trade, data_date_int, is_day_0, trade_entry_dt)
        if not market_data:
            print(f"      ⚠️ No data for {trade['ticker']}")
            continue

        new_status, payload = process_trade_state(
            trade,
            market_data['high'],
            market_data['low'],
            market_data['close'],
            market_data['oi'],
            trade['status'],
            trade['expiration_date'],
            current_date=data_date
        )

        # Update DB
        supabase.table("whale_alerts").update(payload).eq("id", trade['id']).execute()

        # Save History
        supabase.table("whale_performance").upsert({
            "alert_id": trade['id'],
            "date": data_date.isoformat(),
            "price_high": market_data['high'],
            "price_low": market_data['low'],
            "price_close": market_data['close'],
            "current_oi": market_data['oi']
        }, on_conflict="alert_id, date").execute()

    print("✅ Daily Update Complete.")


if __name__ == "__main__":
    run_daily_update()