import os
import httpx
from datetime import datetime, date, timedelta
from dotenv import load_dotenv
from supabase import create_client
import pytz
from master_logger import setup_logger
from yeetz_config import TP_PCT, SCALE_PCT, MOON_PCT
from theta_api_client import (
    filter_and_get_post_alert_high,
    filter_and_get_post_alert_low,
    fetch_ohlc_data,
    fetch_eod_data,
    get_option_root_params,
    get_theta_date_int
)

# --- SETUP ---
log_name = os.path.splitext(os.path.basename(__file__))[0]
logger = setup_logger(name=log_name, log_filename="yeetz.log")

load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
THETA_HTTP_URL = "http://127.0.0.1:25503/v3"
EST = pytz.timezone('US/Eastern')

if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
else:
    logger.error("❌ Supabase Credentials Missing in .env")
    supabase = None

# --- WORKER STATE MANAGEMENT ---
# ID 2 is dedicated to Yeetz Tracker to avoid conflict with GEX worker (ID 1)
YEETZ_WORKER_ID = 2 

def get_last_processed_date():
    """Fetch the last successfully processed date from DB."""
    try:
        resp = supabase.table("worker_status").select("last_run").eq("id", YEETZ_WORKER_ID).execute()
        if resp.data and resp.data[0]['last_run']:
            return datetime.strptime(resp.data[0]['last_run'], "%Y-%m-%d").date()
    except Exception as e:
        logger.warning(f"⚠️ Could not fetch worker status: {e}")
    
    # Default to 2 days ago if no record found (Safe fallback)
    return datetime.now(EST).date() - timedelta(days=2)

def update_last_processed_date(processed_date):
    """Checkpoint the date we just finished."""
    try:
        supabase.table("worker_status").upsert({
            "id": YEETZ_WORKER_ID,
            "last_run": processed_date.isoformat(),
            "status": "OK",
            "worker_name": "YEETZ_DAILY_TRACKER"
        }).execute()
        logger.info(f"💾 Checkpoint saved: {processed_date}")
    except Exception as e:
        logger.error(f"❌ Failed to save checkpoint: {e}")

# --- SHARED API FETCHERS ---

def fetch_open_interest(trade, date_int):
    """Fetches Open Interest (OI)."""
    try:
        params = get_option_root_params(
            trade['ticker'],
            float(trade['strike']),
            trade['option_type'][0],
            datetime.strptime(trade['expiration_date'], "%Y-%m-%d")
        )
        params["date"] = date_int
        endpoint = "/option/history/open_interest"
        url = f"{THETA_HTTP_URL}{endpoint}"

        resp = httpx.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if 'response' in data and data['response']:
                item = data['response'][0]
                if isinstance(item, dict) and 'open_interest' in item:
                    return int(item['open_interest'])
                if 'data' in item and item['data']:
                    return int(item['data'][0].get('open_interest', 0))
        return 0
    except Exception as e:
        logger.error(f"OI Fetch Error for {trade['ticker']}: {e}")
        return 0

def calculate_pnl_snapshots(entry, high, close, status):
    if entry == 0: return 0.0, 0.0, 0.0

    # 1. BASELINE CURVE (100% sold at TP)
    if status in ["SCALED", "SCALED_EXP"]:
        baseline_pct = TP_PCT
    else:
        baseline_pct = ((close - entry) / entry) * 100.0

    # 2. STRATEGY CURVE (80% @ TP + 20% @ Peak High)
    if status in ["SCALED", "SCALED_EXP"]:
        moon_return = ((high - entry) / entry) * 100.0
        min_moon = 0.20 * TP_PCT
        final_moon = max(moon_return, min_moon)
        strategy_pct = ((SCALE_PCT / 100.0) * TP_PCT) + ((MOON_PCT / 100.0) * final_moon)
    else:
        strategy_pct = ((close - entry) / entry) * 100.0

    # 3. DBAP CURVE (The "What If" Ceiling)
    if status in ["SCALED", "SCALED_EXP"]:
        dbap_pct = ((high - entry) / entry) * 100.0
    else:
        dbap_pct = ((close - entry) / entry) * 100.0

    return strategy_pct, baseline_pct, dbap_pct

def get_market_data(trade, data_date_int, is_day_0, alert_dt):
    exp_dt = datetime.strptime(trade['expiration_date'], "%Y-%m-%d")

    eod_data = fetch_eod_data(
        trade['ticker'],
        trade['strike'],
        trade['option_type'][0],
        exp_dt,
        data_date_int
    )
    oi = fetch_open_interest(trade, data_date_int)

    if not eod_data:
        return None

    eod_close = eod_data['close']
    eod_day_high = eod_data['high']
    eod_day_low = eod_data['low']
    entry_price = float(trade['entry_price'])

    final_high = eod_day_high
    final_low = eod_day_low

    if is_day_0:
        logger.debug(f"   🔎 Day 0 Detected for {trade['ticker']}. Fetching 1m OHLC...")
        data_list = fetch_ohlc_data(
            trade['ticker'],
            trade['strike'],
            trade['option_type'][0],
            exp_dt,
            data_date_int,
            interval=1
        )
        post_alert_high = filter_and_get_post_alert_high(data_list, alert_dt)
        post_alert_low = filter_and_get_post_alert_low(data_list, alert_dt)

        final_high = max(entry_price, post_alert_high)
        if final_high == 0: final_high = eod_day_high

        valid_ticks_low = post_alert_low if post_alert_low > 0 else 99999
        final_low = min(entry_price, valid_ticks_low)
        if final_low == 99999: final_low = eod_day_low

    else:
        if final_high == 0: final_high = eod_close
        if final_low == 0: final_low = eod_close
        final_high = max(final_high, eod_close)

    return {
        "high": final_high,
        "close": eod_close,
        "low": final_low,
        "oi": oi
    }

def process_trade_state(trade, high, low, close, oi, current_status, exp_date_str, current_date=None):
    entry = float(trade['entry_price'])
    target = float(trade['profit_target'])
    stop_oi_level = int(trade['stop_oi_level'])
    exp_date = datetime.strptime(exp_date_str, "%Y-%m-%d").date()
    check_date = current_date or datetime.now(EST).date()

    prev_high = float(trade.get('highest_price') or entry)
    new_highest = max(high, prev_high, entry)

    prev_low = float(trade.get('lowest_price') or entry)
    if prev_low == 0: prev_low = entry

    if current_status == "OPEN":
        new_lowest = min(low, prev_low) if low > 0 else prev_low
    else:
        new_lowest = prev_low

    new_status = current_status
    close_reason = None
    entry_dt = datetime.fromisoformat(trade['discord_timestamp']).astimezone(EST)
    entry_date = entry_dt.date()
    payload = {}

    if current_status == "OPEN" and check_date > entry_date:
        if not trade.get('oi_settled', False):
            stop_oi_level = int(oi * 0.20)
            payload["stop_oi_level"] = stop_oi_level
            payload["oi_settled"] = True
            logger.info(f"⚓ SETTLEMENT: {trade['ticker']} OI settled at {oi}. New Stop: {stop_oi_level}")

    if current_status == "SCALED":
        if check_date >= exp_date:
            new_status = "SCALED_EXP"
            logger.info(f"🏁 {trade['ticker']} (SCALED) has expired. Moving to SCALED_EXP.")

    elif current_status == "OPEN":
        if new_highest >= target:
            new_status = "SCALED"
            close_reason = "target_hit"
        elif check_date >= exp_date:
            new_status = "EXPIRED"
            close_reason = "expiration"
            close = 0.0
        elif check_date > entry_date:
            if oi < stop_oi_level:
                new_status = "STOP_OI"
                close_reason = "stop_oi"

    strat_pct, base_pct, dbap_pct = calculate_pnl_snapshots(entry, new_highest, close, new_status)

    payload.update({
        "status": new_status,
        "highest_price": new_highest,
        "lowest_price": new_lowest,
        "last_price": close,
        "last_oi": oi,
        "final_sim_pnl_pct": strat_pct,
        "final_tp_pnl_pct": base_pct,
        "final_dbap_pnl_pct": dbap_pct
    })

    if close_reason:
        payload["close_date"] = check_date.isoformat()
        payload["close_price"] = close

    return new_status, payload

# --- CORE PROCESSING FUNCTION (Single Day) ---

def process_single_day(processing_date):
    """Processes ALL active trades for a specific historical date."""
    
    date_int = get_theta_date_int(processing_date)
    logger.info(f"🗓️ Processing Batch: {processing_date}")

    # Fetch OPEN or SCALED trades
    # We re-fetch from DB every day-loop because a trade might have closed in the previous loop iteration
    response = supabase.table("whale_alerts").select("*").in_("status", ["OPEN", "SCALED"]).execute()
    active_trades = response.data
    updates_count = 0

    if not active_trades:
        logger.info(f"   💤 No active trades to process for {processing_date}.")
        return

    for trade in active_trades:
        try:
            trade_entry_dt = datetime.fromisoformat(trade['discord_timestamp']).astimezone(EST)
            
            # Skip if this trade didn't exist yet on 'processing_date'
            if trade_entry_dt.date() > processing_date:
                continue

            is_day_0 = (trade_entry_dt.date() == processing_date)

            # 1. GET DATA
            market_data = get_market_data(trade, date_int, is_day_0, trade_entry_dt)

            # 2. SELF-HEALING EXPIRATION CHECK
            # If API returns no data, check if the contract expired ON or BEFORE this processing date
            if not market_data:
                exp_dt = datetime.strptime(trade['expiration_date'], "%Y-%m-%d").date()
                if processing_date >= exp_dt:
                    logger.warning(f"      🧟 Zombie Trade Detected: {trade['ticker']} (Exp: {exp_dt} vs Date: {processing_date})")
                    # Force data to trigger expiration logic
                    market_data = {
                        "high": float(trade.get('highest_price') or 0),
                        "low": float(trade.get('lowest_price') or 0),
                        "close": 0.0,
                        "oi": 0
                    }
                else:
                    logger.warning(f"      ⚠️ No data found for {trade['ticker']} on {processing_date}. Skipping.")
                    continue

            # 3. PROCESS STATE
            new_status, payload = process_trade_state(
                trade,
                market_data['high'],
                market_data['low'],
                market_data['close'],
                market_data['oi'],
                trade['status'],
                trade['expiration_date'],
                current_date=processing_date
            )

            # 4. UPDATE DB
            supabase.table("whale_alerts").update(payload).eq("id", trade['id']).execute()
            
            supabase.table("whale_performance").upsert({
                "alert_id": trade['id'],
                "date": processing_date.isoformat(),
                "price_high": market_data['high'],
                "price_low": market_data['low'],
                "price_close": market_data['close'],
                "current_oi": market_data['oi']
            }, on_conflict="alert_id, date").execute()

            updates_count += 1
            if new_status != trade['status']:
                logger.info(f"      👉 {trade['ticker']} updated: {trade['status']} -> {new_status}")

        except Exception as e:
            logger.error(f"❌ Error processing {trade.get('ticker', 'Unknown')}: {e}")

    logger.info(f"   ✅ Batch {processing_date} Complete. Updated {updates_count} trades.")


# --- MAIN SELF-HEALING LOOP ---

def run_smart_catchup():
    logger.info("🚀 Starting Smart Tracker...")

    # 1. Determine Date Range
    last_processed = get_last_processed_date()
    yesterday = datetime.now(EST).date() - timedelta(days=1)
    
    if last_processed >= yesterday:
        logger.info(f"✅ System Up to Date. (Last processed: {last_processed}). Nothing to do.")
        return

    logger.info(f"📉 Gap Detected! Catching up from {last_processed + timedelta(days=1)} to {yesterday}")

    # 2. Loop through every day in the gap
    current_date = last_processed + timedelta(days=1)
    
    while current_date <= yesterday:
        # Check for weekends
        if current_date.weekday() >= 5: # 5=Sat, 6=Sun
            logger.info(f"⏭️ Skipping Weekend: {current_date}")
        else:
            # RUN THE LOGIC FOR THIS SPECIFIC DAY
            process_single_day(current_date)
            # Save checkpoint immediately so if it crashes, we don't redo this day
            update_last_processed_date(current_date)
        
        current_date += timedelta(days=1)

    logger.info("🏁 All caught up!")

if __name__ == "__main__":
    run_smart_catchup()