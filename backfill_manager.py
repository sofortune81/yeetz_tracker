import os
import sys
import discord
import asyncio
from datetime import datetime, timedelta
from dotenv import load_dotenv
from supabase import create_client
import pytz
from master_logger import setup_logger

# Shared Project Imports
from parser import parse_yeetz_alert
# IMPORT THE SHARED LOGIC
from daily_tracker import (
    get_market_data,
    process_trade_state,
    get_theta_date_int,
    EST
)

# --- CONFIGURATION ---
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
TARGET_CHANNEL_ID = int(os.getenv("TARGET_CHANNEL_ID"))
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
log_name = os.path.splitext(os.path.basename(__file__))[0]
logger = setup_logger(name=log_name, log_filename="yeetz.log")


class BackfillBot(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.start_scan = None
        # NEW: Statistics Tracking
        self.stats = {
            "total_found": 0,
            "processed_ok": 0,
            "errors": 0,
            "mode": "Incremental"
        }

    async def on_ready(self):
        logger.info(f"✅ Backfill Bot Logged in as {self.user}")
        asyncio.create_task(self.run_backfill_and_close())

    async def run_backfill_and_close(self):
        try:
            await self.run_backfill()
        except Exception as e:
            logger.exception(f"🔥 Critical Backfill Failure: {e}")
        finally:
            self.log_summary()
            logger.info("👋 Backfill complete. Shutting down bot...")
            await self.close()

    def log_summary(self):
        """Writes a clean summary block to the log file."""
        summary = (
            f"\n"
            f"========================================\n"
            f"   🏁 BACKFILL SUMMARY REPORT 🏁\n"
            f"========================================\n"
            f"   • Mode:        {self.stats['mode']}\n"
            f"   • Start Date:  {self.start_scan}\n"
            f"   • Total Alerts Found: {self.stats['total_found']}\n"
            f"   • Successfully Added: {self.stats['processed_ok']}\n"
            f"   • Errors / Skipped:   {self.stats['errors']}\n"
            f"========================================\n"
        )
        # Log effectively to both file and console (handled by setup_logger)
        logger.info(summary)

    async def run_backfill(self):
        logger.info("⏳ Starting Intelligent Backfill...")
        channel = self.get_channel(TARGET_CHANNEL_ID)

        # 1. Determine Scan Start
        try:
            days_lookback = int(sys.argv[1]) if len(sys.argv) > 1 else 30
            prompt_full_repop = sys.argv[2].lower() if len(sys.argv) > 2 else 'no'
        except ValueError:
            days_lookback = 30
            prompt_full_repop = 'no'

        if prompt_full_repop == 'yes':
            self.stats['mode'] = "FULL REPOPULATE (Wipe & Rebuild)"
            logger.info("🛑 FULL BACKFILL: Clearing Database...")
            await asyncio.to_thread(lambda: supabase.table("whale_performance").delete().neq("alert_id", "0").execute())
            await asyncio.to_thread(lambda: supabase.table("whale_alerts").delete().neq("id", "0").execute())
            self.start_scan = datetime.now(pytz.utc) - timedelta(days=days_lookback)
        else:
            self.stats['mode'] = f"Incremental (Last {days_lookback} days)"
            # Incremental: Find last alert and start from there
            res = await asyncio.to_thread(
                lambda: supabase.table("whale_alerts").select("discord_timestamp").order("discord_timestamp",
                                                                                         desc=True).limit(1).execute())
            if res.data:
                self.start_scan = datetime.fromisoformat(res.data[0]['discord_timestamp']).astimezone(pytz.utc)
                logger.info(f"📅 Incremental: Resuming from {self.start_scan}")
            else:
                self.start_scan = datetime.now(pytz.utc) - timedelta(days=days_lookback)

        # 2. Fetch History
        messages = []
        async for message in channel.history(after=self.start_scan, limit=None, oldest_first=True):
            if message.embeds:
                messages.append(message)

        self.stats['total_found'] = len(messages)
        logger.info(f"📥 Found {len(messages)} alerts to process...")

        # 3. Process
        for msg in messages:
            try:
                success = await self.process_and_simulate(msg)
                if success:
                    self.stats['processed_ok'] += 1
                else:
                    self.stats['errors'] += 1
            except Exception as e:
                self.stats['errors'] += 1
                logger.error(f"❌ Error processing message {msg.id}: {e}")

            await asyncio.sleep(1.0)  # Rate limit protection

    async def process_and_simulate(self, message):
        """Returns True if successful, False if skipped/failed."""
        parsed = parse_yeetz_alert(message.embeds[0])
        if not parsed:
            return False

        alert_dt = message.created_at.astimezone(EST)
        logger.info(f"⚡ Parsing: {parsed['ticker']} {parsed['strike']} ({alert_dt.date()})")

        # Initial Trade Object
        trade = {
            "discord_message_id": str(message.id),
            "ticker": parsed['ticker'],
            "strike": parsed['strike'],
            "option_type": parsed['option_type'],
            "expiration_date": parsed['expiration_date'].isoformat(),
            "entry_price": parsed['entry_price'],
            "entry_size": parsed['entry_size'],
            "entry_oi": parsed['entry_oi'],
            "profit_target": parsed['entry_price'] * 1.20,
            "stop_oi_level": int(parsed['entry_size'] * 0.20),
            "discord_timestamp": alert_dt.isoformat(),
            "status": "OPEN",
            "highest_price": parsed['entry_price'],
            "lowest_price": parsed['entry_price']
        }

        # Save Entry
        res = supabase.table("whale_alerts").upsert(trade, on_conflict="discord_message_id").execute()
        if not res.data:
            logger.warning(f"   ⚠️ DB Insert failed for {parsed['ticker']}")
            return False

        trade['id'] = res.data[0]['id']  # Get ID for performance table

        # Simulate Loop (Alert Day -> Today)
        check_date = alert_dt.date()
        today = datetime.now(EST).date()

        while check_date <= today:
            # Weekend Check
            if check_date.weekday() >= 5:
                check_date += timedelta(days=1)
                continue

            # Future check
            if check_date == today and datetime.now(EST).hour < 17:
                break

            date_int = get_theta_date_int(check_date)

            # 1. Fetch Data
            market_data = get_market_data(trade, date_int, (check_date == alert_dt.date()), alert_dt)

            if not market_data:
                check_date += timedelta(days=1)
                continue

            # 2. Process State
            new_status, update_payload = process_trade_state(
                trade,
                market_data['high'],
                market_data['low'],
                market_data['close'],
                market_data['oi'],
                trade['status'],
                trade['expiration_date'],
                current_date=check_date
            )

            # Update local object
            trade.update(update_payload)

            # Update DB (Alerts Table)
            supabase.table("whale_alerts").update(update_payload).eq("id", trade['id']).execute()

            # Save Daily Snapshot (Performance Table)
            supabase.table("whale_performance").upsert({
                "alert_id": trade['id'],
                "date": check_date.isoformat(),
                "price_high": market_data['high'],
                "price_low": market_data['low'],
                "price_close": market_data['close'],
                "current_oi": market_data['oi']
            }, on_conflict="alert_id, date").execute()

            logger.info(f"   Simulated {check_date}: Status={new_status} | High=${market_data['high']:.2f}")

            if new_status in ["EXPIRED", "STOP_OI"]:
                break

            check_date += timedelta(days=1)
            await asyncio.sleep(0.05)

        return True


if __name__ == "__main__":
    intents = discord.Intents.default()
    intents.message_content = True
    BackfillBot(intents=intents).run(DISCORD_TOKEN)