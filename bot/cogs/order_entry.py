import discord
from discord import app_commands
from discord.ext import commands
import os
import sys
from datetime import datetime, date

# Ensure root is in path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

try:
    from Pythonutils.master_logger import setup_logger
except ImportError:
    pass

logger = setup_logger(name="order_entry_cog", log_filename="yeetz_bot.log")

class OrderEntryCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.supabase = bot.supabase
        env_channel_id = os.getenv("ORDER_ENTRY_CHANNEL_ID")
        # Default to same as listener if not set, or handle gracefully. 
        # Ideally user sets this in .env. 
        self.target_channel_id = int(env_channel_id) if env_channel_id else None

    def generate_unusual_whales_link(self, ticker: str, expiration: date, option_type: str, strike: float) -> str:
        # Format: TICKER + YYMMDD + TYPE + STRIKE_PADDED
        # Strike is multiplied by 1000 and padded to 8 digits
        
        # 1. Ticker
        ticker_str = ticker.upper()
        
        # 2. Date YYMMDD
        date_str = expiration.strftime('%y%m%d')
        
        # 3. Type
        type_str = 'C' if option_type.upper().startswith('C') else 'P'
        
        # 4. Strike
        # OCC standard: Strike * 1000, 8 digits
        strike_val = int(strike * 1000)
        strike_str = f"{strike_val:08d}"
        
        chain_str = f"{ticker_str}{date_str}{type_str}{strike_str}"
        return f"https://unusualwhales.com/flow/option_chains?chain={chain_str}"

    @app_commands.command(name="trade", description="Log a new trade and generate tracking link")
    @app_commands.describe(
        ticker="Stock Ticker (e.g. NRG)",
        strike="Strike Price (e.g. 16.0)",
        expiration="Expiration Date (YYYY-MM-DD)",
        entry_price="Entry Price per contract",
        option_type="Call or Put"
    )
    async def trade(self, interaction: discord.Interaction, ticker: str, strike: float, expiration: str, entry_price: float, option_type: str):
        # 1. Channel Check (Optional, but requested)
        if self.target_channel_id and interaction.channel_id != self.target_channel_id:
            await interaction.response.send_message(f"⚠️ This command is only available in <#{self.target_channel_id}>", ephemeral=True)
            return

        # 2. Validation
        try:
            exp_date = datetime.strptime(expiration, "%Y-%m-%d").date()
        except ValueError:
            await interaction.response.send_message("❌ Invalid Date Format. Please use YYYY-MM-DD (e.g. 2026-03-20)", ephemeral=True)
            return

        if option_type.upper() not in ['C', 'P', 'CALL', 'PUT']:
            await interaction.response.send_message("❌ Invalid Option Type. Use 'Call' or 'Put' (or C/P)", ephemeral=True)
            return
        
        norm_type = 'C' if option_type.upper().startswith('C') else 'P'

        # 3. DB Insert
        try:
            trade_data = {
                "user_id": str(interaction.user.id),
                "ticker": ticker.upper(),
                "strike": strike,
                "option_type": norm_type,
                "expiration_date": exp_date.isoformat(),
                "entry_price": entry_price,
                "entry_size": 1, # Default
                "profit_target": entry_price * 1.20, # Default 20%
                "status": "OPEN",
                "discord_timestamp": datetime.now().isoformat()
            }
            
            response = self.supabase.schema("flow_life").table("trades").insert(trade_data).execute()
            # Note: We are using "trades" table in "flow_life" schema?
            # Supabase client usually defaults to "public".
            # We need to specify schema or use table name "flow_life.trades" if user/role has access?
            # Standard supabase client: .table('table_name')... defaults to public.
            # To use schema: .schema('flow_life').table('trades')...
            # I need to check if self.supabase supports schema().
            # Usually supabase-py supports `supabase.schema('flow_life').table('trades')`.
            
        except Exception as e:
            logger.error(f"DB Insert Error: {e}")
            await interaction.response.send_message(f"❌ Database Error: {str(e)}", ephemeral=True)
            return

        # 4. Generate Link
        link = self.generate_unusual_whales_link(ticker, exp_date, norm_type, strike)
        
        # 5. Respond
        embed = discord.Embed(title="✅ Trade Logged", color=discord.Color.green())
        embed.add_field(name="Ticker", value=ticker.upper(), inline=True)
        embed.add_field(name="Strike", value=str(strike), inline=True)
        embed.add_field(name="Type", value=norm_type, inline=True)
        embed.add_field(name="Exp", value=expiration, inline=True)
        embed.add_field(name="Entry", value=f"${entry_price}", inline=True)
        embed.add_field(name="Link", value=f"[Unusual Whales Chain]({link})", inline=False)
        embed.set_footer(text=f"Logged by {interaction.user.display_name}")
        
        await interaction.response.send_message(embed=embed)
        
        # Try to update DB with message ID if needed (for tracking updates later)
        # But we responded with interaction... we can get the message
        try:
            msg = await interaction.original_response()
            # Update DB with msg.id?
            # We need the ID of the inserted row to update it.
            # Response.data should have it.
            if response.data:
                row_id = response.data[0]['id']
                self.supabase.schema('flow_life').table('trades').update({"discord_message_id": str(msg.id)}).eq("id", row_id).execute()
        except Exception as e:
            logger.error(f"Failed to update msg id: {e}")

async def setup(bot):
    await bot.add_cog(OrderEntryCog(bot))
