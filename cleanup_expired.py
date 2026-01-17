import os
import asyncio
import requests
from datetime import datetime, timedelta, date
from dotenv import load_dotenv
from supabase import create_client
import pytz
from master_logger import setup_logger
import pandas_market_calendars as mcal
import pandas as pd
from io import StringIO

# Reuse existing logic for state processing
from theta_api_client import get_theta_date_int
from daily_tracker import process_trade_state, EST

# --- CONFIG ---
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Ensure we catch all levels of logs
logger = setup_logger(name="cleanup_script", log_filename="cleanup.log")

# Initialize CBOE Calendar (Options market)
cboe = mcal.get_calendar('CBOE_Equity_Options')

# THETA CONFIG
THETA_PORT = "25503"
BASE_URL = f"http://127.0.0.1:{THETA_PORT}/v3/option/history/ohlc"


def parse_thetadata_csv(csv_content):
    """
    Parses the CSV string provided by ThetaData.
    Expects headers: symbol,expiration,strike,right,timestamp,open,high,low,close,volume,count,vwap
    """
    try:
        df = pd.read_csv(StringIO(csv_content))

        # Clean column names
        df.columns = [c.strip() for c in df.columns]

        # Ensure timestamp is datetime objects
        if 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp'])

        return df
    except Exception as e:
        logger.error(f"Error parsing CSV: {e}")
        return pd.DataFrame()


def fetch_market_data_csv(ticker, exp_date, strike, right, date_int, is_day_0, entry_dt):
    """
    Fetches OHLC data via CSV, parses it, and returns daily stats.
    """
    try:
        # 1. Format Parameters for v3
        # Expiration: 2025-12-26 -> 20251226
        exp_fmt = exp_date.replace("-", "")

        # Strike: 1.5 -> 1.500 (v3 needs 3 decimals usually, or plain float)
        strike_fmt = f"{float(strike):.3f}"

        # Right: C -> call
        right_map = {'C': 'call', 'CALL': 'call', 'P': 'put', 'PUT': 'put'}
        right_fmt = right_map.get(str(right).upper(), 'call')

        # 2. Build URL
        params = {
            "symbol": ticker,
            "expiration": exp_fmt,
            "strike": strike_fmt,
            "right": right_fmt,
            "date": date_int,
            "interval": "1m",
            "use_csv": "true"  # Explicitly request CSV
        }

        query_string = "&".join([f"{k}={v}" for k, v in params.items()])
        full_url = f"{BASE_URL}?{query_string}"

        # 3. Request
        response = requests.get(full_url, timeout=10)

        if response.status_code != 200:
            logger.warning(f"   ⚠️ ThetaData API Error {response.status_code}: {response.text}")
            return None

        # 4. Parse
        df = parse_thetadata_csv(response.text)

        if df.empty:
            return None

        # 5. Filter for Day 0 (Only candles AFTER entry)
        if is_day_0:
            # entry_dt is likely offset-aware (EST). df['timestamp'] might be naive.
            # We assume df['timestamp'] is in EST/Exchange time as per ThetaData defaults.
            # To be safe, we compare naive to naive or aware to aware.

            # Make entry_dt naive for comparison if df is naive
            entry_naive = entry_dt.replace(tzinfo=None)
            df = df[df['timestamp'] >= entry_naive]

            if df.empty:
                logger.warning(f"   ⚠️ Data found, but all before entry time {entry_naive}")
                return None

        # 6. Calculate Stats
        day_high = df['high'].max()
        day_low = df['low'].min()
        day_close = df.iloc[-1]['close']  # Last row close

        # Note: OHLC endpoint often doesn't give OI. We set it to 0 to safely ignore OI checks.
        day_oi = 0

        return {
            "high": float(day_high),
            "low": float(day_low),
            "close": float(day_close),
            "oi": day_oi
        }

    except Exception as e:
        logger.error(f"   ❌ Error fetching market data: {e}")
        return None


async def run_cleanup():
    logger.info("🧹 Starting Cleanup of Expired 'OPEN' Trades (CSV Mode)...")

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
        strike = trade.get('strike', 0)
        right = trade.get('option_type', 'N/A')

        logger.info(f"[{i + 1}/{len(stale_trades)}] Fixing {ticker} {strike}{right} (Exp: {exp_str})...")

        entry_dt = datetime.fromisoformat(trade['discord_timestamp']).astimezone(EST)
        start_date = entry_dt.date()
        exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()

        # Reset High/Low trackers
        trade['highest_price'] = trade['entry_price']
        trade['lowest_price'] = trade['entry_price']

        payload = {}
        stop_triggered = False

        # --- VALIDATE DATES ---
        if not strike or strike == 0:
            logger.error(f"   ⛔ SKIPPING: Invalid Strike for {ticker}: {strike}")
            continue

        valid_days = cboe.valid_days(start_date=start_date, end_date=exp_date)
        last_check_date = start_date

        if len(valid_days) == 0:
            logger.warning(f"⚠️ No valid market days found.")
            # If entry and exp are same day and it's a weekend (rare), force check on exp date
            if start_date == exp_date:
                valid_days = [pd.Timestamp(exp_date)]

        # --- SIMULATION LOOP ---
        for timestamp in valid_days:
            check_date = timestamp.date()
            last_check_date = check_date

            date_int = get_theta_date_int(check_date)
            is_day_0 = (check_date == start_date)

            # --- FETCH DATA LOCALLY ---
            market_data = fetch_market_data_csv(
                ticker=ticker,
                exp_date=exp_str,
                strike=strike,
                right=right,
                date_int=date_int,
                is_day_0=is_day_0,
                entry_dt=entry_dt
            )

            if not market_data:
                # If we miss data, we just skip this day.
                # If it's expiration day and we have no data, we might assume expired worthless
                # but better to leave it open than guess wrong.
                continue

            # Process State (Stop Loss / Profit target checks)
            new_status, new_payload = process_trade_state(
                trade,
                market_data['high'],
                market_data['low'],
                market_data['close'],
                market_data['oi'],
                trade['status'],
                trade['expiration_date'],
                current_date=check_date
            )

            # Merge updates into our running trade object so next day's check is accurate
            trade.update(new_payload)
            # Merge updates into final payload to be sent to DB
            payload.update(new_payload)

            logger.info(
                f"   🗓️ {check_date}: High ${market_data['high']:.2f} | Close ${market_data['close']:.2f} | Status: {new_status}")

            if new_status in ["SCALED_EXP", "EXPIRED", "STOP_OI", "STOP_LOSS", "TAKE_PROFIT"]:
                payload['status'] = new_status
                stop_triggered = True
                break

        # --- FINAL EXPIRATION CHECK ---
        # If we finished the loop, reached expiration date, and it's still OPEN
        if not stop_triggered and trade['status'] == "OPEN" and last_check_date >= exp_date:
            trade['status'] = "EXPIRED"
            payload['status'] = "EXPIRED"
            logger.info("   👉 Reached end of valid days. Closing as EXPIRED.")

        # --- DB UPDATE ---
        if payload:
            supabase.table("whale_alerts").update(payload).eq("id", trade['id']).execute()
            logger.info(f"   ✅ Database Updated for {ticker}")
        else:
            logger.info(f"   ⏭️ No changes needed for {ticker}")

        await asyncio.sleep(0.5)


if __name__ == "__main__":
    asyncio.run(run_cleanup())