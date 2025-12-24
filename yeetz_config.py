# --- STRATEGY CONSTANTS ---
# Single Source of Truth for both Local Backend and Cloud Dashboard

TP_PCT = 20.0       # Take Profit target (+20%)
SCALE_PCT = 80.0    # Percentage of position to sell at TP (80%)
MOON_PCT = 100.0 - SCALE_PCT
STOP_OI_PCT = 20.0  # Stop loss if OI drops below 20% of entry