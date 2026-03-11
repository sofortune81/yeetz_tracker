import discord
from discord.ext import commands
import pytz
import asyncio
import os
import sys

# Ensure root is in path (redundant if loaded from main, but good for linting/standalone)
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

try:
    from Pythonutils.master_logger import setup_logger
    from parser import parse_yeetz_alert
except ImportError:
    # Fallback or local import if running differently
    pass

logger = setup_logger(name="listener_cog", log_filename="yeetz_bot.log")

class ListenerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        env_channel_id = os.getenv("TARGET_CHANNEL_ID")
        self.target_channel_id = int(env_channel_id) if env_channel_id else 1405240666337841152
        self.supabase = bot.supabase

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author == self.bot.user:
            return

        # Check if this is our Target Channel
        if message.channel.id != self.target_channel_id:
            return

        channel_name = getattr(message.channel, 'name', str(message.channel.id))
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
                self.supabase.table("whale_alerts").upsert(data, on_conflict="discord_message_id").execute
            )

            # 5. Confirm Write
            if response.data:
                logger.info(f"   ✅ 💾 DATABASE SUCCESS: Saved {data['ticker']} (ID: {response.data[0]['id']})")
            else:
                logger.warning(f"   ⚠️ Database write happened but returned no data? Response: {response}")

        except Exception as e:
            logger.error(f"   ❌ DATABASE ERROR: {e}")

async def setup(bot):
    await bot.add_cog(ListenerCog(bot))
