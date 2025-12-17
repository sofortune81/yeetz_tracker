import os
import sys
import discord
import asyncio
from datetime import datetime, timedelta
from dotenv import load_dotenv
from supabase import create_client
import pytz
from logger import setup_logger
# Shared Project Imports
from parser import parse_yeetz_alert
from daily_tracker import calculate_trade_pnl_percentages, get_market_data, fetch_open_interest, process_trade_state
from theta_api_client import (
    get_theta_date_int,
    get_intraday_performance,
    fetch_eod_data,
    EST
)

# --- CONFIGURATION ---
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
TARGET_CHANNEL_ID = int(os.getenv("TARGET_CHANNEL_ID"))
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
logger = setup_logger(name="backfill_manager", log_filename="backfill.log")

class BackfillBot(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.start_scan = None

    async def on_ready(self):
        logger.info(f"✅ Backfill Bot Logged in as {self.user}")
        await self.run_backfill()
        await self.close()

    async def run_backfill(self):
        logger.info("⏳ Starting Intelligent Backfill...")
        channel = self.get_channel(TARGET_CHANNEL_ID)

        # 1. Determine Scan Start (CLI Arguments or DB Lookup)
        try:
            days_lookback = int(sys.argv[1]) if len(sys.argv) > 1 else 30
            prompt_full_repop = sys.argv[2].lower() if len(sys.argv) > 2 else 'no'
        except ValueError:
            days_lookback = 30
            prompt_full_repop = 'no'

        if prompt_full_repop == 'yes':
            logger.info("\n🛑 FULL BACKFILL SELECTED: Deleting all existing trade data...")

            def delete_db_data():
                supabase.table("whale_performance").delete().neq("alert_id", "0").execute()
                supabase.table("whale_alerts").delete().neq("id", "0").execute()

            await asyncio.to_thread(delete_db_data)
            self.start_scan = datetime.now(pytz.utc) - timedelta(days=days_lookback)
            logger.info(f"✅ Database cleared. Scanning the last {days_lookback} days.")
        else:
            is_incremental = await self.check_and_set_incremental_start()
            if not is_incremental:
                self.start_scan = datetime.now(pytz.utc) - timedelta(days=days_lookback)
                logger.info(f"🔎 Empty DB. Scanning the last {days_lookback} days.")

        # 2. Fetch Discord History
        messages = []
        async for message in channel.history(after=self.start_scan, limit=None, oldest_first=True):
            if message.embeds:
                messages.append(message)

        logger.info(f"📥 Found {len(messages)} alerts to process.")

        # 3. Process Each Alert (The Time Machine)
        for msg in messages:
            await self.process_and_simulate(msg)
            await asyncio.sleep(1.5)

    async def process_and_simulate(self, message):
        logger.info(f"\n-> START Processing Message ID: {message.id}")
        parsed = parse_yeetz_alert(message.embeds[0])
        if not parsed:
            logger.info(f"   - SKIPPING {message.id}: Failed to parse alert details.")
            return

        alert_dt = message.created_at.astimezone(EST)
        exp_date = parsed['expiration_date']
        ticker = parsed['ticker']

        logger.info(f"⚡ Processing: {ticker} {parsed['strike']}{parsed['option_type']} (Alert: {alert_dt.date()})")

        # Initial Trade Record
        trade = {
            "discord_message_id": str(message.id),
            "ticker": ticker,
            "strike": parsed['strike'],
            "option_type": parsed['option_type'],
            "expiration_date": exp_date.isoformat(),
            "entry_price": parsed['entry_price'],
            "entry_size": parsed['entry_size'],
            "entry_oi": parsed['entry_oi'],
            "profit_target": parsed['entry_price'] * 1.20,
            "stop_oi_level": int(parsed['entry_size'] * 0.20),
            "discord_timestamp": alert_dt.isoformat(),
            "status": "OPEN",
            "status": "OPEN",
            "highest_price": parsed['entry_price'],  # Initialize with entry, not 0.0
            "lowest_price": parsed['entry_price']  # Initialize with entry, not 9999
        }

        db_res = supabase.table("whale_alerts").upsert(trade, on_conflict="discord_message_id").execute()
        if not db_res.data: return
        trade_id = db_res.data[0]['id']
        logger.info(f"   ✅ Saved alert. Trade ID: {trade_id}")
        logger.info(f"      📍 Contract Details: Exp={trade['expiration_date']} | Entry=${trade['entry_price']:.2f}")

        # Simulation Loop (Alert Day -> Today)
        check_date = alert_dt.date()
        today = datetime.now(EST).date()
        is_closed = False

        while check_date <= today and not is_closed:
            # 1. Define date_int and skip weekends
            date_int = get_theta_date_int(check_date)

            # 2. Now skip the API calls if it's a weekend
            if check_date.weekday() >= 5:
                check_date += timedelta(days=1)
                continue

            # 2. Skip today's EOD if market is still open
            now_est = datetime.now(EST)
            if check_date == today and now_est.hour < 17:
                if check_date != alert_dt.date():
                    check_date += timedelta(days=1)
                    continue

            # 3. Use the unified get_market_data (Handles Day 0 and EOD)
            market_data = get_market_data(trade, date_int, (check_date == alert_dt.date()), alert_dt)

            if not market_data or market_data['close'] == 0:
                logger.info(f"      EOD {date_int}: No data. Skipping.")
                check_date += timedelta(days=1)
                continue

            # 4. Use the Shared State Machine (Source of Truth)
            new_status, update_payload = process_trade_state(
                trade,
                market_data['high'],
                market_data['low'],
                market_data['close'],
                market_data['oi'],
                trade['status'],
                trade['expiration_date'],
                current_date=check_date  # <--- CRITICAL: Pass the simulated date
            )

            # Update local trade object for the next loop iteration
            trade.update(update_payload)

            if new_status in ["STOP_OI", "EXPIRED"]:
                is_closed = True

            # Update Database - Single Point of Entry
            supabase.table("whale_alerts").update(update_payload).eq("id", trade_id).execute()
            logger.info(
                f"      💾 Saving Snapshot: High=${market_data['high']:.2f} | Close=${market_data['close']:.2f} | Low=${market_data['low']:.2f} | OI={market_data['oi']}")
            # Save History Snapshot
            supabase.table("whale_performance").upsert({
                "alert_id": trade_id,
                "date": check_date.isoformat(),
                "price_high": market_data['high'],
                "price_low": market_data['low'],
                "price_close": market_data['close'],
                "current_oi": market_data['oi']
            }, on_conflict="alert_id, date").execute()

            logger.info(f"   ☑️ Saved snapshot for {check_date} (Status: {new_status})")
            check_date += timedelta(days=1)

    async def check_and_set_incremental_start(self):
        def fetch_last():
            return supabase.table("whale_alerts").select("discord_timestamp").order("discord_timestamp",
                                                                                    desc=True).limit(1).execute()

        res = await asyncio.to_thread(fetch_last)
        if res.data:
            self.start_scan = datetime.fromisoformat(res.data[0]['discord_timestamp']).astimezone(pytz.utc)
            logger.info(f"📅 Resuming from DB timestamp: {self.start_scan}")
            return True
        return False


if __name__ == "__main__":
    intents = discord.Intents.default()
    intents.message_content = True
    BackfillBot(intents=intents).run(DISCORD_TOKEN)