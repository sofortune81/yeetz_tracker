import os
import asyncio
from datetime import datetime, timedelta, date
from dotenv import load_dotenv
from supabase import create_client
import pytz
from master_logger import setup_logger
import pandas_market_calendars as mcal
import pandas as pd

# Reuse existing logic to ensure consistency
from theta_api_client import get_theta_date_int
from daily_tracker import (
    get_market_data,
    process_trade_state,
    EST
)

# --- CONFIG ---
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Ensure we catch all levels of logs
logger = setup_logger(name="cleanup_script", log_filename="cleanup.log")

# Initialize CBOE Calendar (Options market)
cboe = mcal.get_calendar('CBOE_Equity_Options')


def debug_construct_url_v3(ticker, exp_date, strike, right, date_int):
    """
    Constructs a ThetaData v3 URL for debugging.
    Matches format: .../v3/option/history/ohlc?symbol=AAPL...&strike=170.000...
    """
    try:
        # 1. Format Expiration: 2025-12-26 -> 20251226
        exp_fmt = exp_date.replace("-", "")

        # 2. Format Strike: 1.5 -> 1500 (v2) vs 1.500 (v3)
        # You requested v3 decimal format with 3 decimal places
        strike_fmt = f"{float(strike):.3f}"

        # 3. Format Right: C -> call, P -> put (v3 standard)
        right_map = {
            'C': 'call', 'CALL': 'call',
            'P': 'put', 'PUT': 'put'
        }
        right_fmt = right_map.get(str(right).upper(), 'call')  # Default to call if unknown

        url = (
            f"http://127.0.0.1:25503/v3/option/history/ohlc?"
            f"symbol={ticker}&expiration={exp_fmt}&strike={strike_fmt}&right={right_fmt}"
            f"&date={date_int}&interval=1m"
        )
        return url
    except Exception as e:
        return f"Error constructing debug URL: {e}"


async def run_cleanup():
    logger.info("🧹 Starting Cleanup of Expired 'OPEN' Trades (v3 Mode)...")

    today = datetime.now(EST).date()

    # 1. Fetch Stale Trades
    response = supabase.table("whale_alerts") \
        .select("*") \
        .eq("status", "OPEN") \
        .lt("expiration_date", today.isoformat()) \
        .execute()

    stale_trades = response.data

    if not stale_trades:
        logger.info("✅ No stale trades found. Data is clean!")
        return

    logger.info(f"📉 Found {len(stale_trades)} stale trades to process.")

    for i, trade in enumerate(stale_trades):
        ticker = trade['ticker']
        exp_str = trade['expiration_date']

        # Use correct DB columns
        strike = trade.get('strike', 0)
        right = trade.get('option_type', 'N/A')

        logger.info(f"[{i + 1}/{len(stale_trades)}] Fixing {ticker} (Exp: {exp_str})...")

        entry_dt = datetime.fromisoformat(trade['discord_timestamp']).astimezone(EST)
        start_date = entry_dt.date()
        exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()

        # Reset High/Low
        trade['highest_price'] = trade['entry_price']
        trade['lowest_price'] = trade['entry_price']

        payload = {}

        # --- DATA CHECK ---
        if not strike or strike == 0:
            logger.error(f"   ⛔ SKIPPING: Invalid Strike for {ticker}: {strike}")
            valid_days = []
            last_check_date = exp_date
        else:
            valid_days = cboe.valid_days(start_date=start_date, end_date=exp_date)
            last_check_date = start_date

        if len(valid_days) == 0 and last_check_date != exp_date:
            logger.warning(f"⚠️ No valid market days found between {start_date} and {exp_date}")
            last_check_date = exp_date

        # --- SIMULATION LOOP ---
        for timestamp in valid_days:
            check_date = timestamp.date()
            last_check_date = check_date

            date_int = get_theta_date_int(check_date)
            is_day_0 = (check_date == start_date)

            # --- DEBUG LOGGING (v3) ---
            debug_url = debug_construct_url_v3(ticker, exp_str, strike, right, date_int)
            logger.info(f"🔍 DEBUG DAY: {check_date}")
            logger.info(f"   🔗 v3 URL: {debug_url}")

            # Fetch Data (Delegates to daily_tracker.py)
            market_data = get_market_data(trade, date_int, is_day_0, entry_dt)

            if not market_data:
                logger.warning(f"   ⚠️ No data for {ticker} on {check_date}")
                continue

            new_status, payload = process_trade_state(
                trade,
                market_data['high'],
                market_data['low'],
                market_data['close'],
                market_data['oi'],
                trade['status'],
                trade['expiration_date'],
                current_date=check_date
            )

            trade.update(payload)
            logger.info(f"   🗓️ {check_date}: High ${market_data['high']:.2f} | Status: {new_status}")

            if new_status in ["SCALED_EXP", "EXPIRED", "STOP_OI"]:
                break

        # --- FINAL DB UPDATE ---
        if trade['status'] == "OPEN" and last_check_date >= exp_date:
            trade['status'] = "EXPIRED"
            payload['status'] = "EXPIRED"
            logger.info("   👉 Force closing as EXPIRED")

        if payload:
            supabase.table("whale_alerts").update(payload).eq("id", trade['id']).execute()
            logger.info(f"   ✅ Fixed: {ticker} -> {trade['status']}")
        else:
            logger.info(f"   ⏭️ No changes made for {ticker}")

        await asyncio.sleep(0.5)


if __name__ == "__main__":
    asyncio.run(run_cleanup())