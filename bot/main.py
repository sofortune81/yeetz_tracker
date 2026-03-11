import asyncio
import os
import sys
import discord
from discord.ext import commands
from dotenv import load_dotenv
from supabase import create_client

# Add project root to sys.path to allow imports from Pythonutils and root modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from Pythonutils.master_logger import setup_logger
except ImportError:
    # Fallback if Pythonutils is not found or structured differently
    import logging
    def setup_logger(name, log_filename):
        logger = logging.getLogger(name)
        logging.basicConfig(level=logging.INFO)
        return logger

# Configuration
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Setup Logger
logger = setup_logger(name="bot_main", log_filename="yeetz_bot.log")

class YeetzBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        
        # Supabase Client
        self.supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    async def setup_hook(self):
        # Load Cogs
        await self.load_extension("bot.cogs.listener")
        await self.load_extension("bot.cogs.order_entry")
        
        # Sync Slash Commands
        try:
            synced = await self.tree.sync()
            logger.info(f"✅ Synced {len(synced)} command(s)")
        except Exception as e:
            logger.error(f"❌ Failed to sync commands: {e}")

    async def on_ready(self):
        logger.info(f"✅ Logged in as {self.user} (ID: {self.user.id})")
        print(f"✅ Bot is Online. Logged in as {self.user}")

async def main():
    if not DISCORD_TOKEN:
        logger.error("❌ DISCORD_TOKEN not found in .env")
        return
    
    bot = YeetzBot()
    async with bot:
        await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
