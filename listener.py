import asyncio
import os
import discord
import re
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client
import pytz
from parser import parse_yeetz_alert
from theta_api_client import get_theta_date_int # <-- NEW IMPORT


# 1. Configuration
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
# Add the specific Channel ID here (Right-click channel in Discord -> Copy ID)
TARGET_CHANNEL_ID = 1405240666337841152 # Assuming this is correct

# Supabase Setup
url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")
supabase = create_client(url, key)


class YeetzListener(discord.Client):
    async def on_ready(self):
        print(f"✅ Logged in as {self.user}")
        print(f"👂 Listening to Channel ID: {TARGET_CHANNEL_ID}")

    async def on_message(self, message):
        # Ignore own messages and wrong channels
        if message.author == self.user:
            return
        if message.channel.id != TARGET_CHANNEL_ID:
            return

        # Check for Embeds
        if message.embeds:
            for embed in message.embeds:
                await self.process_embed(embed, message)

    async def process_embed(self, embed, message):
        utc_time = message.created_at
        est = pytz.timezone('US/Eastern')
        est_time = utc_time.astimezone(est)

        # 1. Use the new parser
        parsed_data = parse_yeetz_alert(embed)
        if not parsed_data:
            return

        ticker = parsed_data['ticker']
        strike = parsed_data['strike']
        opt_type = parsed_data['option_type']
        exp_date = parsed_data['expiration_date']
        avg_fill = parsed_data['entry_price']
        interval_vol = parsed_data['entry_size']
        embed_oi = parsed_data['entry_oi']

        # NEW: Calculate derived fields
        # Vol/OI Ratio: Use 0 if OI is 0 to prevent division by zero
        vol_oi_ratio = round((interval_vol / embed_oi) if embed_oi > 0 else 0.0, 2)

        # Premium: Price * Quantity * Contract Multiplier (100)
        premium_usd = avg_fill * interval_vol * 100

        # 2. Calculate Logic (same as before)
        # Win = 20% gain
        profit_target = avg_fill * 1.20
        # Stop Tracking = OI drops below 80% of the INITIAL Interval Volume
        stop_oi_level = int(interval_vol * 0.20)

        print(
            f"🐳 YEETZ ALERT: {ticker} {strike}{opt_type} @ ${avg_fill} | Vol: {interval_vol} | Target: ${profit_target:.2f}")

        # 3. Save to Supabase
        data = {
            "ticker": ticker,
            "strike": strike,
            "option_type": opt_type,
            "expiration_date": exp_date.isoformat(),
            "entry_price": avg_fill,
            "entry_size": interval_vol,
            "entry_interval_vol": interval_vol,  # New column for clarity
            "entry_oi": embed_oi,
            "entry_vol_oi_ratio": vol_oi_ratio,  # NEW: Calculated
            "entry_premium": premium_usd,  # NEW: Calculated
            "profit_target": profit_target,
            "stop_oi_level": stop_oi_level,
            "discord_timestamp": est_time.isoformat(),
            "discord_message_id": str(message.id),
            "status": "OPEN"
        }

        await self.save_trade(data)

    async def save_trade(self, data):
        # Use asyncio.to_thread to run the blocking synchronous database call
        # in a separate thread, which prevents the main async loop from freezing.
        try:
            await asyncio.to_thread(
                supabase.table("whale_alerts").upsert(data, on_conflict="discord_message_id").execute
            )
            print("💾 Saved to DB successfully.")
        except Exception as e:
            print(f"❌ Database Error: {e}")


# Run Bot
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("❌ Error: DISCORD_TOKEN not found in .env")
    else:
        # ADD THESE TWO LINES
        intents = discord.Intents.default()
        intents.message_content = True
        client = YeetzListener(intents=intents)
        client.run(DISCORD_TOKEN)