import os
import discord
import asyncio
from datetime import datetime, timedelta, date
from dotenv import load_dotenv
from supabase import create_client
import pytz
from parser import parse_yeetz_alert
from theta_api_client import get_theta_date_int, get_option_root_params, fetch_trade_quote_data, fetch_eod_data, filter_and_get_post_alert_high, filter_and_get_post_alert_low # <-- ADDED filter_and_get_post_alert_low
from daily_tracker import calculate_trade_pnl_percentages # <-- NEW IMPORT

# --- CONFIGURATION ---
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
TARGET_CHANNEL_ID = int(os.getenv("TARGET_CHANNEL_ID"))
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
# THETA_HTTP_URL is now defined in theta_api_client.py
EST = pytz.timezone('US/Eastern')

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


class BackfillBot(discord.Client):
    async def on_ready(self):
        print(f"✅ Backfill Bot Logged in as {self.user}")
        await self.run_backfill()
        await self.close()

    async def run_backfill(self):
        print("⏳ Starting Intelligent Backfill...")
        channel = self.get_channel(TARGET_CHANNEL_ID)

        # 1. Determine Gap
        # Find the most recent trade in DB to know where to start
        last_db_entry = supabase.table("whale_alerts").select("discord_timestamp").order("discord_timestamp",
                                                                                         desc=True).limit(1).execute()

        if last_db_entry.data:
            last_dt = datetime.fromisoformat(last_db_entry.data[0]['discord_timestamp'])
            start_scan = last_dt.astimezone(pytz.utc)
            print(f"📅 Database has data up to {last_dt}. Scanning after this...")
        else:
            # Default to 30 days ago if empty
            start_scan = datetime.now(pytz.utc) - timedelta(days=30)
            print(f"⚠️ Database empty. Scanning last 30 days from {start_scan}...")

        # 2. Fetch Messages
        messages_to_process = []
        async for message in channel.history(after=start_scan, limit=None, oldest_first=True):
            if message.author == self.user: continue
            if message.embeds:
                messages_to_process.append(message)

        print(f"📥 Found {len(messages_to_process)} alerts to process.")

        # 3. Process Each Alert (The Time Machine)
        for msg in messages_to_process:
            for embed in msg.embeds:
                await self.process_and_simulate(embed, msg)

            await asyncio.sleep(2.0)

    async def process_and_simulate(self, embed, message):
        # --- DEBUG: Immediately confirm entry into the function ---
        print(f"-> START Processing Message ID: {message.id}")

        try:
            # --- PARSE (Use Shared Logic) ---
            parsed_data = parse_yeetz_alert(embed)

            # 1. Check if parsing was successful
            if not parsed_data:
                print(f"   - SKIPPING {message.id}: Failed to parse alert details.")
                return

            ticker = parsed_data['ticker']
            strike = parsed_data['strike']
            opt_type = parsed_data['option_type']
            exp_date = parsed_data['expiration_date']
            entry_price = parsed_data['entry_price']
            entry_size = parsed_data['entry_size']
            entry_oi = parsed_data['entry_oi']
            raw_title = parsed_data['raw_title']

            # NEW: Calculate derived fields
            # Vol/OI Ratio: Use 0 if OI is 0 to prevent division by zero
            vol_oi_ratio = round((entry_size / entry_oi) if entry_oi > 0 else 0.0, 2)

            # Premium: Price * Quantity * Contract Multiplier (100)
            premium_usd = entry_price * entry_size * 100

            # Print the found alert title for debugging
            print(f"\n⚡ Processing ALERT: {raw_title} (Discord ID: {message.id})")

            # Metadata
            utc_time = message.created_at
            alert_dt = utc_time.astimezone(EST)
            alert_date_int = int(alert_dt.strftime('%Y%m%d'))

            print(f"\n⚡ Processing: {ticker} {strike}{opt_type} (Alert: {alert_dt.date()})")

            # --- ENDPOINT 1: FETCH ENTRY IV (Greeks Snapshot) ---
            entry_iv = 0.0  # IV is permanently skipped

            # --- SIMULATION ENGINE ---
            # Setup Trade Object
            trade = {
                "discord_message_id": str(message.id),
                "ticker": ticker,
                "strike": strike,
                "option_type": opt_type,
                "expiration_date": exp_date.isoformat(),
                "entry_price": entry_price,
                "entry_size": entry_size,
                "entry_interval_vol": entry_size,
                "entry_oi": entry_oi,
                "entry_vol_oi_ratio": vol_oi_ratio,  # NEW: Calculated
                "entry_premium": premium_usd,  # NEW: Calculated
                "entry_iv": entry_iv,
                "profit_target": entry_price * 1.20,
                "stop_oi_level": int(entry_size * 0.20),
                "discord_timestamp": alert_dt.isoformat(),
                "status": "OPEN",
                "highest_price": 0,
                "lowest_price": 99999,
                "risk_pct_used": 1.0
            }

            # Insert Initial "OPEN" Record so we have an ID to link history to
            # We use upsert to avoid duplicates
            db_res = supabase.table("whale_alerts").upsert(trade, on_conflict="discord_message_id").execute()

            if db_res.data:
                trade_id = db_res.data[0]['id']
                print(f"   ✅ Saved alert. Trade ID: {trade_id}")
            else:
                print(f"   ❌ ERROR: Supabase upsert failed. Response: {db_res}")
                return  # Stop processing this trade if the main record failed to save

            # Generate list of dates to check (Alert Date -> Today)
            today = datetime.now(EST).date()
            check_date = alert_dt.date()

            is_closed = False

            while check_date <= today and not is_closed:
                # Loop control
                check_date_int = int(check_date.strftime('%Y%m%d'))

                # Check Expiration
                if check_date > exp_date:
                    print(f"   💀 Expired on {check_date}")
                    high_price = trade['highest_price']
                    exit_price = 0.0  # Assumed worthless at expiration

                    final_sim_pnl_pct, final_tp_pnl_pct = calculate_trade_pnl_percentages(
                        trade, high_price, exit_price
                    )

                    supabase.table("whale_alerts").update({
                        "status": "EXPIRED",
                        "close_date": check_date.isoformat(),
                        "close_price": 0,
                        "close_reason": "expiration",
                        "final_sim_pnl_pct": final_sim_pnl_pct,  # <-- SAVE PNL
                        "final_tp_pnl_pct": final_tp_pnl_pct  # <-- SAVE PNL
                    }).eq("id", trade_id).execute()
                    is_closed = True
                    break

                # DATA FETCHING
                day_high = 0
                day_close = 0
                day_low = 0  # <-- NEW
                day_oi = 0
                day_iv = 0

                # --- ENDPOINT 1 (Day 0): Trade Quote (Intraday) ---
                if check_date == alert_dt.date():
                    # Special logic for Day 0: Only look at price AFTER alert time
                    alert_ms = (alert_dt.hour * 3600 + alert_dt.minute * 60 + alert_dt.second) * 1000
                    day_high, day_close, day_low, day_oi, day_iv = self.get_day_0_stats(  # <-- ADD day_low
                        ticker, strike, opt_type, exp_date, check_date_int, alert_dt, entry_oi
                    )

                # --- ENDPOINT 2 & 3 (Day 1+): EOD & Greeks ---
                else:
                    day_high, day_close, day_low, day_oi, day_iv = self.get_eod_stats(  # <-- ADD day_low
                        ticker, strike, opt_type, exp_date, check_date_int
                    )

                    # The lines before the block to replace (line 153):
                    if day_close == 0 and day_high == 0:
                        # The block to replace:
                        # No data for this day (weekend/holiday), skip
                        check_date += timedelta(days=1)
                        continue

                # --- UPDATE LOWEST PRICE SEEN ---
                current_lowest = trade['lowest_price']
                # Only update if day_low is positive and lower than the current lowest
                if day_low > 0 and day_low < current_lowest:
                    current_lowest = day_low
                    supabase.table("whale_alerts").update({"lowest_price": current_lowest}).eq("id",
                                                                                               trade_id).execute()
                    trade['lowest_price'] = current_lowest

                if day_close > 0:
                    supabase.table("whale_alerts").update({"last_price": day_close}).eq("id", trade_id).execute()
                # --- CHECK WIN/LOSS LOGIC ---

                # Update Highest Price Seen
                current_highest = trade['highest_price']
                if day_high > current_highest:
                    current_highest = day_high
                    supabase.table("whale_alerts").update({"highest_price": current_highest}).eq("id",
                                                                                                 trade_id).execute()
                    trade['highest_price'] = current_highest

                    # --- Replacement Block ---
                    # 1. Check Win (TP) - Transition from OPEN to SCALED
                    # The lines after the block to replace:
                    if day_high >= trade['profit_target'] and trade['status'] == "OPEN":
                        print(f"   🎉 Backfill SCALE OUT HIT: Hit {trade['profit_target']:.2f} on {check_date}")
                        supabase.table("whale_alerts").update({
                            "status": "SCALED",
                            "tp_hit_date": check_date.isoformat(),
                            "tp_hit_price": trade['profit_target'],
                        }).eq("id", trade_id).execute()

                        trade['status'] = "SCALED"
                        trade['tp_hit_date'] = check_date.isoformat()

                        # --- START INSERTION BLOCK (Save PnL on Scale) ---
                        # Calculate and save the PnL at the point of scaling
                        high_price = trade['highest_price']
                        exit_price = trade['profit_target']

                        # We use profit_target as the exit price signal since TP was hit
                        final_sim_pnl_pct, final_tp_pnl_pct = calculate_trade_pnl_percentages(
                            trade, high_price, exit_price
                        )

                        supabase.table("whale_alerts").update({
                            "final_sim_pnl_pct": final_sim_pnl_pct,
                            "final_tp_pnl_pct": final_tp_pnl_pct
                        }).eq("id", trade_id).execute()

                        # --- Run Daily OI Stop Check (Always on Day 1+) ---
                    if check_date > alert_dt.date() and trade['status'] not in ["STOP_OI", "EXPIRED"]:
                        stop_oi = int(trade['entry_size'] * 0.20)  # Use the corrected 20% stop

                        # Assuming Stop OI kills the moonshot if status is SCALED, or the whole trade if OPEN
                        if day_oi < stop_oi and day_oi > 0:
                            print(f"   🛑 Backfill Stop: OI {day_oi} < {stop_oi} on {check_date}")
                            high_price = trade['highest_price']
                            exit_price = day_close  # Exit price is the day's closing price

                            final_sim_pnl_pct, final_tp_pnl_pct = calculate_trade_pnl_percentages(
                                trade, high_price, exit_price
                            )

                            supabase.table("whale_alerts").update({
                                "status": "STOP_OI",
                                "close_date": check_date.isoformat(),
                                "close_price": day_close,
                                "close_reason": "stop_oi",
                                "final_sim_pnl_pct": final_sim_pnl_pct,  # <-- SAVE PNL
                                "final_tp_pnl_pct": final_tp_pnl_pct  # <-- SAVE PNL
                            }).eq("id", trade_id).execute()
                            is_closed = True  # Stop tracking

                # Save History Snapshot
                supabase.table("whale_performance").insert({
                    "alert_id": trade_id,
                    "date": check_date.isoformat(),
                    "price_high": day_high,
                    "price_close": day_close,
                    "price_low": day_low,  # <-- NEW: Save Daily Low
                    "current_oi": day_oi,
                    "implied_volatility": day_iv
                }).execute()
                print(f"   ☑️ Saved history snapshot for {check_date} (High: {day_high:.2f})")
                # Move to next day
                check_date += timedelta(days=1)

        except Exception as e:
            print(f"❌ Error processing {message.id}: {e}")

    def get_day_0_stats(self, ticker, strike, opt_type_char, exp_date, date_int, alert_dt, entry_oi):
        """Endpoint 1: Trade Quote (Intraday) - Fetches high/close post-alert using the API client."""

        # NOTE: alert_dt is the full EST datetime object passed from the caller.

        # Fetch trade/quote data using the refactored client
        data_list = fetch_trade_quote_data(ticker, strike, opt_type_char, exp_date, date_int)

        # 1. Get the high price achieved AFTER the alert time using the helper
        high = filter_and_get_post_alert_high(data_list, alert_dt)

        # 2. Get the low price achieved AFTER the alert time using the new helper
        low = filter_and_get_post_alert_low(data_list, alert_dt)  # <-- NEW: Fetch true low

        # 3. Estimate Close Price (Last trade after alert)
        # The block to replace:
        # last element is the last trade of the day, but it's the best data available.
        close = 0.0
        if data_list:
            # Re-run the list to find the absolute last price *after* the alert
            last_valid_trade = 0.0
            alert_time_str = alert_dt.strftime('%H:%M:%S.%f')[:-3]

            for tick in reversed(data_list):
                try:
                    trade_timestamp = tick.get('trade_timestamp')
                    price = float(tick.get('price', 0.0))

                    if not trade_timestamp: continue

                    # Convert UTC tick time to EST for comparison
                    dt_utc = datetime.fromisoformat(trade_timestamp.replace('Z', '+00:00'))
                    dt_est = dt_utc.astimezone(EST)
                    trade_time_str = dt_est.strftime('%H:%M:%S.%f')[:-3]

                    if trade_time_str >= alert_time_str and price > 0:
                        last_valid_trade = price
                        break  # Found the last trade after alert

                except (KeyError, ValueError, IndexError, AttributeError):
                    continue

            close = last_valid_trade

        if high == 0.0:
            print(f"   ❌ Trade Quote error for {ticker}: No post-alert data returned.")

        # OI is assumed to be entry_oi for Day 0 logic. IV is skipped.
        return high, close, low, entry_oi, 0.0  # <-- RETURN the correct 'low'

    def get_eod_stats(self, ticker, strike, opt_type_char, exp_date, date_int):
        """Endpoint 2: Standard EOD - Fetches high, close, and OI using the API client."""

        eod_data = fetch_eod_data(ticker, strike, opt_type_char, exp_date, date_int)

        if eod_data:
            high = eod_data['high']
            close = eod_data['close']
            low = eod_data['low']  # <-- NEW: Extract Low
            oi = eod_data['oi']
            return high, close, low, oi, 0.0  # IV is permanently 0.0

        print(f"      EOD {date_int}: Failed to fetch or parse EOD data. Returning 0s.")
        # FIX CONFIRMED: Ensures 5 values are returned on failure: (high, close, low, oi, iv)
        return 0.0, 0.0, 0.0, 0, 0.0


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("❌ Error: DISCORD_TOKEN not found.")
    else:
        intents = discord.Intents.default()
        intents.message_content = True
        client = BackfillBot(intents=intents)
        client.run(DISCORD_TOKEN)