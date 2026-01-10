import os
import asyncio
from datetime import datetime, timedelta, date
from dotenv import load_dotenv
from supabase import create_client
import pytz
from master_logger import setup_logger

# Reuse existing logic to ensure consistency
from theta_api_client import get_theta_date_int
from daily_tracker import (
    get_market_data,
    process_trade_state,
    EST
)

# --- CONFIG ---
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

logger = setup_logger(name="cleanup_script", log_filename="cleanup.log")

async def run_cleanup():
    print("🧹 Starting Cleanup of Expired 'OPEN' Trades...")
    
    today = datetime.now(EST).date()
    
    # 1. Fetch Stale Trades: Status is OPEN but Expiration is in the past
    response = supabase.table("whale_alerts") \
        .select("*") \
        .eq("status", "OPEN") \
        .lt("expiration_date", today.isoformat()) \
        .execute()
        
    stale_trades = response.data
    
    if not stale_trades:
        print("✅ No stale trades found. Data is clean!")
        return

    print(f"📉 Found {len(stale_trades)} stale trades to process.")
    
    for i, trade in enumerate(stale_trades):
        ticker = trade['ticker']
        exp_str = trade['expiration_date']
        print(f"\n[{i+1}/{len(stale_trades)}] Fixing {ticker} (Exp: {exp_str})...")

        # Setup Timeline
        # We start checking from the day AFTER the discord timestamp (or same day if day 0)
        entry_dt = datetime.fromisoformat(trade['discord_timestamp']).astimezone(EST)
        start_date = entry_dt.date()
        exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
        
        # We assume the DB has the correct entry data, but we reset High/Low for the simulation
        # to ensure the calculation is pure based on history.
        trade['highest_price'] = trade['entry_price']
        trade['lowest_price'] = trade['entry_price']
        
        check_date = start_date
        
        # --- SIMULATION LOOP (Replaying History) ---
        while check_date <= exp_date:
            # Skip weekends
            if check_date.weekday() >= 5:
                check_date += timedelta(days=1)
                continue
            
            # Prepare Dates
            date_int = get_theta_date_int(check_date)
            is_day_0 = (check_date == start_date)
            
            # Fetch Data
            market_data = get_market_data(trade, date_int, is_day_0, entry_dt)
            
            if not market_data:
                logger.warning(f"   ⚠️ No data for {ticker} on {check_date}. Skipping day.")
                check_date += timedelta(days=1)
                continue

            # Process State (Calculates Stops, Targets, Moonshots)
            new_status, payload = process_trade_state(
                trade,
                market_data['high'],
                market_data['low'],
                market_data['close'],
                market_data['oi'],
                trade['status'],
                trade['expiration_date'],
                current_date=check_date
            )
            
            # Update local trade object for the next loop iteration
            trade.update(payload)
            
            print(f"   🗓️ {check_date}: High ${market_data['high']:.2f} | Status: {new_status}")
            
            # If trade closed or expired during simulation, we are done with this trade
            if new_status in ["SCALED_EXP", "EXPIRED", "STOP_OI"]:
                break
                
            check_date += timedelta(days=1)
        
        # --- FINAL DB UPDATE ---
        # If loop finished and it's still OPEN but past expiration, force EXPIRED
        if trade['status'] == "OPEN" and check_date >= exp_date:
            trade['status'] = "EXPIRED"
            payload['status'] = "EXPIRED"
            print("   👉 Force closing as EXPIRED (reached end of loop)")

        # Update Supabase with the final calculated result
        supabase.table("whale_alerts").update(payload).eq("id", trade['id']).execute()
        
        # Optional: Generate Performance row for the final day if needed
        # (Omitting for brevity, but the Alert status is the priority)
        
        print(f"   ✅ Fixed: {ticker} -> {trade['status']}")
        await asyncio.sleep(0.5) # Rate limit safety

if __name__ == "__main__":
    asyncio.run(run_cleanup())