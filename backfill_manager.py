# --- IMPORTS ---
import os
import sys
import discord
import asyncio
from datetime import datetime, timedelta
from dotenv import load_dotenv
from supabase import create_client
import pytz

from logger import setup_logger
from parser import parse_yeetz_alert
from daily_tracker import calculate_trade_pnl_percentages  #

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

logger = setup_logger(name="backfill", log_filename="backfill.log")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


class BackfillBot(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Initialize the attribute to prevent AttributeError
        self.start_scan = datetime.now(pytz.utc) - timedelta(days=30)

    async def on_ready(self):
        logger.info(f"✅ Backfill Bot Logged in as {self.user}")
        await self.run_backfill()
        await self.close()

    async def run_backfill(self):
        logger.info("⏳ Starting Intelligent Backfill...")
        channel = self.get_channel(TARGET_CHANNEL_ID)

        try:
            days_lookback = int(sys.argv[1]) if len(sys.argv) > 1 else 30
            prompt_full_repop = sys.argv[2].lower() if len(sys.argv) > 2 else 'no'
        except ValueError:
            days_lookback = 30
            prompt_full_repop = 'no'

        # --- 2. Handle Full Repopulation ---
        if prompt_full_repop == 'yes':
            logger.warning("🛑 FULL BACKFILL SELECTED: Clearing existing data...")

            def delete_db_data():
                supabase.table("whale_performance").delete().neq("alert_id", "0").execute()
                supabase.table("whale_alerts").delete().neq("id", "0").execute()

            await asyncio.to_thread(delete_db_data)

            # Use self. to ensure the attribute is set correctly
            self.start_scan = datetime.now(pytz.utc) - timedelta(days=days_lookback)
            logger.info(f"✅ DB Cleared. Scanning last {days_lookback} days.")
        else:
            # --- 3. Incremental Backfill ---
            is_incremental = await self.check_and_set_incremental_start()
            if not is_incremental:
                self.start_scan = datetime.now(pytz.utc) - timedelta(days=days_lookback)
                logger.info(f"🔎 Empty DB. Scanning last {days_lookback} days.")

        # --- 4. Fetch and Process ---
        messages_to_process = []
        async for message in channel.history(after=self.start_scan, limit=None, oldest_first=True):
            if message.author == self.user: continue
            if message.embeds:
                messages_to_process.append(message)

        logger.info(f"📥 Found {len(messages_to_process)} alerts to process.")

        for msg in messages_to_process:
            await self.process_and_simulate(msg)
            await asyncio.sleep(1.0)

    async def process_and_simulate(self, message):
        """Re-implementing the Simulation Time Machine"""
        parsed_data = parse_yeetz_alert(message.embeds[0])
        if not parsed_data: return

        alert_dt = message.created_at.astimezone(EST)
        ticker = parsed_data['ticker']

        # Initial Trade Setup
        trade = {
            "discord_message_id": str(message.id),
            "ticker": ticker,
            "strike": parsed_data['strike'],
            "option_type": parsed_data['option_type'],
            "expiration_date": parsed_data['expiration_date'].isoformat(),
            "entry_price": parsed_data['entry_price'],
            "entry_size": parsed_data['entry_size'],
            "entry_oi": parsed_data['entry_oi'],
            "profit_target": parsed_data['entry_price'] * 1.20,
            "stop_oi_level": int(parsed_data['entry_size'] * 0.20),
            "discord_timestamp": alert_dt.isoformat(),
            "status": "OPEN",
            "highest_price": 0.0,
            "lowest_price": 9999.0
        }

        # Save record to get Trade ID
        db_res = supabase.table("whale_alerts").upsert(trade, on_conflict="discord_message_id").execute()
        if not db_res.data: return
        trade_id = db_res.data[0]['id']

        # Simulation Loop (Alert Day -> Today)
        check_date = alert_dt.date()
        today = datetime.now(EST).date()
        is_closed = False

        while check_date <= today and not is_closed:
            date_int = get_theta_date_int(check_date)
            if check_date.weekday() >= 5:  # Weekend skip
                check_date += timedelta(days=1)
                continue

            # FETCH CORRECT DATA (Day 0 Ticks vs EOD)
            if check_date == alert_dt.date():
                perf = get_intraday_performance(
                    ticker, trade['strike'], trade['option_type'][0],
                    parsed_data['expiration_date'], date_int, alert_dt
                )
                day_high, day_low, day_close = perf['high'], perf['low'], trade['entry_price']
                day_oi = trade['entry_oi']
            else:
                eod = fetch_eod_data(
                    ticker, trade['strike'], trade['option_type'][0],
                    parsed_data['expiration_date'], date_int
                )
                if not eod:
                    check_date += timedelta(days=1)
                    continue
                day_high, day_low, day_close, day_oi = eod['high'], eod['low'], eod['close'], eod['oi']

            # Update Simulation State
            trade['highest_price'] = max(trade['highest_price'], day_high)
            if trade['status'] == "OPEN" and day_low > 0:
                trade['lowest_price'] = min(trade['lowest_price'], day_low)

            # Strategy Triggers
            update_payload = {"highest_price": trade['highest_price'], "lowest_price": trade['lowest_price'],
                              "last_price": day_close, "last_oi": day_oi}

            # Check Scale Out (TP)
            if trade['status'] == "OPEN" and day_high >= trade['profit_target']:
                trade['status'] = "SCALED"
                sim_pnl, tp_pnl = calculate_trade_pnl_percentages(trade, trade['highest_price'], trade['profit_target'])
                update_payload.update({"status": "SCALED", "final_sim_pnl_pct": sim_pnl, "final_tp_pnl_pct": tp_pnl})

            # Check Stop OI (Day 1+)
            if check_date > alert_dt.date() and day_oi < trade['stop_oi_level'] and day_oi > 0:
                is_closed = True
                sim_pnl, tp_pnl = calculate_trade_pnl_percentages(trade, trade['highest_price'], day_close)
                update_payload.update({
                    "status": "STOP_OI", "close_date": check_date.isoformat(), "close_price": day_close,
                    "close_reason": "stop_oi", "final_sim_pnl_pct": sim_pnl, "final_tp_pnl_pct": tp_pnl
                })

            # Update Main Record
            supabase.table("whale_alerts").update(update_payload).eq("id", trade_id).execute()

            # Save Snapshot
            supabase.table("whale_performance").upsert({
                "alert_id": trade_id, "date": check_date.isoformat(),
                "price_high": day_high, "price_low": day_low, "price_close": day_close, "current_oi": day_oi
            }, on_conflict="alert_id, date").execute()

            check_date += timedelta(days=1)

    async def check_and_set_incremental_start(self):
        """Checks DB for last entry and sets self.start_scan."""

        def fetch_last():
            return supabase.table("whale_alerts").select("discord_timestamp").order("discord_timestamp",
                                                                                    desc=True).limit(1).execute()

        last_db_entry = await asyncio.to_thread(fetch_last)
        if last_db_entry.data:
            last_dt = datetime.fromisoformat(last_db_entry.data[0]['discord_timestamp'])
            self.start_scan = last_dt.astimezone(pytz.utc)
            logger.info(f"📅 Resuming from DB timestamp: {last_dt}")
            return True
        return False


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        logger.error("❌ DISCORD_TOKEN not found.")
    else:
        intents = discord.Intents.default()
        intents.message_content = True
        client = BackfillBot(intents=intents)
        client.run(DISCORD_TOKEN)