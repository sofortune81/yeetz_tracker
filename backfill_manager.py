import os
import sys
import discord
import asyncio
from datetime import datetime, timedelta
from dotenv import load_dotenv
from supabase import create_client
import pytz

# Shared Project Imports
from parser import parse_yeetz_alert
from daily_tracker import calculate_trade_pnl_percentages, get_market_data, fetch_open_interest
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


class BackfillBot(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.start_scan = None

    async def on_ready(self):
        print(f"✅ Backfill Bot Logged in as {self.user}")
        await self.run_backfill()
        await self.close()

    async def run_backfill(self):
        print("⏳ Starting Intelligent Backfill...")
        channel = self.get_channel(TARGET_CHANNEL_ID)

        # 1. Determine Scan Start (CLI Arguments or DB Lookup)
        try:
            days_lookback = int(sys.argv[1]) if len(sys.argv) > 1 else 30
            prompt_full_repop = sys.argv[2].lower() if len(sys.argv) > 2 else 'no'
        except ValueError:
            days_lookback = 30
            prompt_full_repop = 'no'

        if prompt_full_repop == 'yes':
            print("\n🛑 FULL BACKFILL SELECTED: Deleting all existing trade data...")

            def delete_db_data():
                supabase.table("whale_performance").delete().neq("alert_id", "0").execute()
                supabase.table("whale_alerts").delete().neq("id", "0").execute()

            await asyncio.to_thread(delete_db_data)
            self.start_scan = datetime.now(pytz.utc) - timedelta(days=days_lookback)
            print(f"✅ Database cleared. Scanning the last {days_lookback} days.")
        else:
            is_incremental = await self.check_and_set_incremental_start()
            if not is_incremental:
                self.start_scan = datetime.now(pytz.utc) - timedelta(days=days_lookback)
                print(f"🔎 Empty DB. Scanning the last {days_lookback} days.")

        # 2. Fetch Discord History
        messages = []
        async for message in channel.history(after=self.start_scan, limit=None, oldest_first=True):
            if message.embeds:
                messages.append(message)

        print(f"📥 Found {len(messages)} alerts to process.")

        # 3. Process Each Alert (The Time Machine)
        for msg in messages:
            await self.process_and_simulate(msg)
            await asyncio.sleep(1.5)

    async def process_and_simulate(self, message):
        print(f"\n-> START Processing Message ID: {message.id}")
        parsed = parse_yeetz_alert(message.embeds[0])
        if not parsed:
            print(f"   - SKIPPING {message.id}: Failed to parse alert details.")
            return

        alert_dt = message.created_at.astimezone(EST)
        exp_date = parsed['expiration_date']
        ticker = parsed['ticker']

        print(f"⚡ Processing: {ticker} {parsed['strike']}{parsed['option_type']} (Alert: {alert_dt.date()})")

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
        print(f"   ✅ Saved alert. Trade ID: {trade_id}")

        # Simulation Loop (Alert Day -> Today)
        check_date = alert_dt.date()
        today = datetime.now(EST).date()
        is_closed = False

        while check_date <= today and not is_closed:
            # 1. Define date_int and skip weekends
            date_int = get_theta_date_int(check_date)

            if check_date.weekday() >= 5:
                day_name = "Saturday" if check_date.weekday() == 5 else "Sunday"
                print(f"      ☕ {check_date} is {day_name}. Skipping.")
                check_date += timedelta(days=1)
                continue

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
                print(f"      EOD {date_int}: No data. Skipping.")
                check_date += timedelta(days=1)
                continue

            # 4. Extract standard variables
            day_high = market_data['high']
            day_low = market_data['low']
            day_close = market_data['close']
            day_oi = market_data['oi']

            # Update Simulation Local State
            trade['highest_price'] = max(trade['highest_price'], day_high)
            if trade['status'] == "OPEN" and day_low > 0:
                trade['lowest_price'] = min(trade['lowest_price'], day_low)


            update_payload = {
                "highest_price": trade['highest_price'],
                "lowest_price": trade['lowest_price'],
                "last_price": day_close,
                "last_oi": day_oi
            }

            # 1. Memorialize the Win (Baseline Curve)
            if trade['status'] == "OPEN" and trade['highest_price'] >= trade['profit_target']:
                print(f"🎉 WIN LOCKED: Memorializing 20% Baseline for {ticker}")
                trade['status'] = "SCALED"

                # Calculate initial values. Baseline PnL (tp_pnl) is now 20.0 and LOCKED.
                sim_pnl, tp_pnl = calculate_trade_pnl_percentages(trade, trade['highest_price'], trade['profit_target'])

                update_payload.update({
                    "status": "SCALED",
                    "tp_hit_date": check_date.isoformat(),
                    "tp_hit_price": trade['profit_target'],
                    "final_tp_pnl_pct": 20.0,  # LOCKED Curve 1
                    "final_sim_pnl_pct": sim_pnl  # Initial Curve 2
                })

            # 2. Update Peak for SCALED trades (Moonshot growth)
            elif trade['status'] == "SCALED":
                sim_pnl, tp_pnl = calculate_trade_pnl_percentages(trade, trade['highest_price'], day_close)
                update_payload.update({
                    "final_sim_pnl_pct": sim_pnl  # Curve 2 grows as Peak High grows
                })

            # 3. Finalize on Stop OI or Expiration
            if (check_date > alert_dt.date() and day_oi < trade['stop_oi_level'] and day_oi > 0) or (
                    check_date >= exp_date):
                reason = "stop_oi" if check_date < exp_date else "expiration"
                print(f"   🛑 Finalizing Trade: {reason}")
                is_closed = True

                # Final check of PnL at close
                sim_pnl, tp_pnl = calculate_trade_pnl_percentages(trade, trade['highest_price'], day_close)
                update_payload.update({
                    "status": "STOP_OI" if reason == "stop_oi" else "EXPIRED",
                    "close_date": check_date.isoformat(),
                    "close_price": day_close,
                    "close_reason": reason,
                    "final_sim_pnl_pct": sim_pnl,
                    "final_tp_pnl_pct": tp_pnl
                })

            # Update Database
            supabase.table("whale_alerts").update(update_payload).eq("id", trade_id).execute()

            # Save History Snapshot
            supabase.table("whale_performance").upsert({
                "alert_id": trade_id, "date": check_date.isoformat(),
                "price_high": day_high, "price_low": day_low, "price_close": day_close, "current_oi": day_oi
            }, on_conflict="alert_id, date").execute()
            print(f"   ☑️ Saved history snapshot for {check_date} (High: {day_high:.2f})")

            check_date += timedelta(days=1)

    async def check_and_set_incremental_start(self):
        def fetch_last():
            return supabase.table("whale_alerts").select("discord_timestamp").order("discord_timestamp",
                                                                                    desc=True).limit(1).execute()

        res = await asyncio.to_thread(fetch_last)
        if res.data:
            self.start_scan = datetime.fromisoformat(res.data[0]['discord_timestamp']).astimezone(pytz.utc)
            print(f"📅 Resuming from DB timestamp: {self.start_scan}")
            return True
        return False


if __name__ == "__main__":
    intents = discord.Intents.default()
    intents.message_content = True
    BackfillBot(intents=intents).run(DISCORD_TOKEN)