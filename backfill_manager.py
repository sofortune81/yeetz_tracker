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
from theta_api_client import get_theta_date_int
from daily_tracker import (
    get_market_data,
    process_trade_state,
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

try:
    test_path = os.path.join(os.getcwd(), 'logs', 'WRITE_TEST.txt')
    print(f"DEBUG: Attempting to create file at: {test_path}")
    with open(test_path, 'w') as f:
        f.write("If you can read this, Docker volume mapping works!")
    print("DEBUG: ✅ Successfully wrote TEST file.")
except Exception as e:
    print(f"DEBUG: ❌ FAILED to write file. Reason: {e}")

class BackfillBot(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.start_scan = None
        self.stats = {
            "total_found": 0,
            "processed_ok": 0,
            "errors": 0,
            "mode": "Incremental"
        }

    async def on_ready(self):
        print("DEBUG: Bot Ready! Starting scan...")
        logger.info(f"✅ Backfill Bot Logged in as {self.user}")
        asyncio.create_task(self.run_backfill_and_close())

    async def run_backfill_and_close(self):
        try:
            await self.run_backfill()
        except Exception as e:
            print(f"🔥 CRITICAL ERROR: {e}")
            logger.exception(f"🔥 Critical Backfill Failure: {e}")
        finally:
            self.log_summary()
            logger.info("👋 Backfill complete. Shutting down bot...")
            await self.close()

    def log_summary(self):
        total = self.stats['total_found']
        success = self.stats['processed_ok']
        rate = (success / total * 100) if total > 0 else 0

        summary = (
            f"\n"
            f"========================================\n"
            f"   🏁 BACKFILL SUMMARY REPORT 🏁\n"
            f"========================================\n"
            f"   • Mode:             {self.stats['mode']}\n"
            f"   • Start Date:       {self.start_scan}\n"
            f"   • Total Alerts:     {total}\n"
            f"   • Successfully Added: {success} ({rate:.1f}%)\n"
            f"   • Errors / Skipped:   {self.stats['errors']}\n"
            f"========================================\n"
        )
        print(summary)
        logger.info(summary)

    async def run_backfill(self):
        logger.info("⏳ Starting Intelligent Backfill...")
        channel = self.get_channel(TARGET_CHANNEL_ID)

        try:
            days_lookback = int(sys.argv[1]) if len(sys.argv) > 1 else 30
            prompt_full_repop = sys.argv[2].lower() if len(sys.argv) > 2 else 'no'
        except ValueError:
            days_lookback = 30
            prompt_full_repop = 'no'

        if prompt_full_repop == 'yes':
            self.stats['mode'] = "FULL REPOPULATE"
            print("🛑 Clearing Database...")
            await asyncio.to_thread(lambda: supabase.table("whale_performance").delete().neq("alert_id", "0").execute())
            await asyncio.to_thread(lambda: supabase.table("whale_alerts").delete().neq("id", "0").execute())
            self.start_scan = datetime.now(pytz.utc) - timedelta(days=days_lookback)
        else:
            self.stats['mode'] = f"Incremental ({days_lookback} days)"
            res = await asyncio.to_thread(
                lambda: supabase.table("whale_alerts").select("discord_timestamp").order("discord_timestamp",
                                                                                         desc=True).limit(1).execute())
            if res.data:
                self.start_scan = datetime.fromisoformat(res.data[0]['discord_timestamp']).astimezone(pytz.utc)
            else:
                self.start_scan = datetime.now(pytz.utc) - timedelta(days=days_lookback)

        print(f"📥 Fetching messages since {self.start_scan.date()}...")
        messages = []
        async for message in channel.history(after=self.start_scan, limit=None, oldest_first=True):
            if message.embeds:
                messages.append(message)

        self.stats['total_found'] = len(messages)
        print(f"📥 Found {len(messages)} alerts to process.")

        for i, msg in enumerate(messages):
            try:
                # Print NEWLINE to ensure it clears previous line
                print(f"\nProcessing {i + 1}/{len(messages)}...", flush=True)

                success = await self.process_and_simulate(msg)
                if success:
                    self.stats['processed_ok'] += 1
                else:
                    self.stats['errors'] += 1
            except Exception as e:
                self.stats['errors'] += 1
                logger.error(f"❌ Error processing message {msg.id}: {e}")
                print(f"Error: {e}")

            await asyncio.sleep(0.5)

        print("\nDone processing messages.")

    async def process_and_simulate(self, message):
        parsed = parse_yeetz_alert(message.embeds[0])
        if not parsed:
            print("   -> Failed to parse embed.")
            return False

        alert_dt = message.created_at.astimezone(EST)
        print(f"   -> Found: {parsed['ticker']} {parsed['strike']} ({alert_dt.date()})")

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
            "stop_oi_level": int((parsed['entry_oi'] + parsed['entry_size']) * 0.20),
            "discord_timestamp": alert_dt.isoformat(),
            "status": "OPEN",
            "highest_price": parsed['entry_price'],
            "lowest_price": parsed['entry_price']
        }

        res = supabase.table("whale_alerts").upsert(trade, on_conflict="discord_message_id").execute()
        if not res.data:
            print("   -> DB Upsert failed.")
            return False

        trade['id'] = res.data[0]['id']

        check_date = alert_dt.date()
        today = datetime.now(EST).date()

        while check_date <= today:
            # print(f"      -> Simulating {check_date}...", end='\r') # Show progress inline

            if check_date.weekday() >= 5:
                check_date += timedelta(days=1)
                continue

            if check_date == today and datetime.now(EST).hour < 17:
                break

            date_int = get_theta_date_int(check_date)

            # --- DEBUG: Print exactly when we hit the API ---
            # print(f"      -> Fetching API for {check_date}...", end='\r')

            market_data = get_market_data(trade, date_int, (check_date == alert_dt.date()), alert_dt)

            if not market_data:
                check_date += timedelta(days=1)
                continue

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

            trade.update(update_payload)
            supabase.table("whale_alerts").update(update_payload).eq("id", trade['id']).execute()

            supabase.table("whale_performance").upsert({
                "alert_id": trade['id'],
                "date": check_date.isoformat(),
                "price_high": market_data['high'],
                "price_low": market_data['low'],
                "price_close": market_data['close'],
                "current_oi": market_data['oi']
            }, on_conflict="alert_id, date").execute()

            # --- VERBOSE LOG (Detailed Contract View) ---
            # Format: TICKER YYYYMMDD Strike P/C
            contract_str = f"{trade['ticker']} {trade['expiration_date'].replace('-', '')} {trade['strike']} {trade['option_type']}"

            print(
                f"      [{check_date}] {contract_str} | Entry: ${trade['entry_price']:.2f} | High: ${market_data['high']:.2f} | Low: ${market_data['low']:.2f} | Status: {new_status}")

            if new_status in ["EXPIRED", "STOP_OI"]:
                break

            check_date += timedelta(days=1)
            await asyncio.sleep(0.01)

        print("")  # Clear line
        return True


if __name__ == "__main__":
    intents = discord.Intents.default()
    intents.message_content = True
    BackfillBot(intents=intents).run(DISCORD_TOKEN)