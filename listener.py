import asyncio
import os
import discord
import pytz
from dotenv import load_dotenv
from supabase import create_client

# Shared Imports
from master_logger import setup_logger
from parser import parse_yeetz_alert

# 1. Configuration
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

# Target Channel Logic
env_channel_id = os.getenv("TARGET_CHANNEL_ID")
TARGET_CHANNEL_ID = int(env_channel_id) if env_channel_id else 1405240666337841152

# --- SETUP LOGGER (Unified to yeetz.log) ---
logger = setup_logger(name="listener", log_filename="yeetz.log")

# Supabase Setup
url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")
supabase = create_client(url, key)


class YeetzListener(discord.Client):
    async def on_ready(self):
        logger.info(f"✅ Logged in as {self.user}")
        logger.info(f"👂 Listening to Target Channel ID: {TARGET_CHANNEL_ID}")
        print(f"✅ Listener is Online. Logs are being written to logs/yeetz.log")

    async def on_message(self, message):
        if message.author == self.user:
            return

        # Get Channel Name for clearer logs
        channel_name = getattr(message.channel, 'name', str(message.channel.id))

        # 1. Check if this is our Target Channel
        if message.channel.id != TARGET_CHANNEL_ID:
            # OPTIONAL: Comment this out if the logs are too noisy
            # logger.info(f"🔇 Ignored message from #{channel_name} (ID: {message.channel.id})")
            return

        # 2. Log Content from Target Channel
        logger.info(f"🎯 MESSAGE RECEIVED in Target Channel (#{channel_name})")

        if message.embeds:
            logger.info(f"   📄 Found {len(message.embeds)} Embeds. Processing...")
            for i, embed in enumerate(message.embeds):
                logger.info(f"      - Embed {i + 1} Title: {embed.title}")
                await self.process_embed(embed, message)
        else:
            logger.warning(f"   ⚠️ Message has no embeds. Content: '{message.content}'")

    async def process_embed(self, embed, message):
        try:
            utc_time = message.created_at
            est = pytz.timezone('US/Eastern')
            est_time = utc_time.astimezone(est)

            # 1. Parse
            parsed_data = parse_yeetz_alert(embed)

            if not parsed_data:
                # Log the raw title to see why it failed
                logger.error(f"❌ PARSE FAILED. Raw Title: '{embed.title}'")
                return

            # 2. Extract Data
            ticker = parsed_data['ticker']
            strike = parsed_data['strike']
            opt_type = parsed_data['option_type']
            exp_date = parsed_data['expiration_date']
            avg_fill = parsed_data['entry_price']
            interval_vol = parsed_data['entry_size']
            embed_oi = parsed_data['entry_oi']

            # Derived fields
            vol_oi_ratio = round((interval_vol / embed_oi) if embed_oi > 0 else 0.0, 2)
            premium_usd = avg_fill * interval_vol * 100
            profit_target = avg_fill * 1.20
            stop_oi_level = int((embed_oi + interval_vol) * 0.80)

            logger.info(f"   🧩 PARSED OK: {ticker} {strike}{opt_type} Exp:{exp_date} @ ${avg_fill}")

            # 3. Prepare Data
            data = {
                "ticker": ticker,
                "strike": strike,
                "option_type": opt_type,
                "expiration_date": exp_date.isoformat(),
                "entry_price": avg_fill,
                "entry_size": interval_vol,
                "entry_interval_vol": interval_vol,
                "entry_oi": embed_oi,
                "entry_vol_oi_ratio": vol_oi_ratio,
                "entry_premium": premium_usd,
                "profit_target": profit_target,
                "stop_oi_level": stop_oi_level,
                "discord_timestamp": est_time.isoformat(),
                "discord_message_id": str(message.id),
                "status": "OPEN"
            }

            await self.save_trade(data)
        except Exception as e:
            logger.exception(f"🔥 CRITICAL ERROR processing embed: {e}")

    async def save_trade(self, data):
        try:
            # 4. Write to DB
            response = await asyncio.to_thread(
                supabase.table("whale_alerts").upsert(data, on_conflict="discord_message_id").execute
            )

            # 5. Confirm Write
            # Supabase usually returns the data it wrote in response.data
            if response.data:
                logger.info(f"   ✅ 💾 DATABASE SUCCESS: Saved {data['ticker']} (ID: {response.data[0]['id']})")
            else:
                logger.warning(f"   ⚠️ Database write happened but returned no data? Response: {response}")

        except Exception as e:
            logger.error(f"   ❌ DATABASE ERROR: {e}")


# Run Bot
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("❌ Error: DISCORD_TOKEN not found in .env")
    else:
        intents = discord.Intents.default()
        intents.message_content = True
        client = YeetzListener(intents=intents)
        client.run(DISCORD_TOKEN)