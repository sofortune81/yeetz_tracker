I will fix the expiration logic for both **OPEN** and **SCALED** trades, and update the dashboard to recognize the new expired-win status.

### **1. Fix "Stuck" OPEN Trades (Jan 9 Expirations)**
*   **Problem:** The current script skips trades if the API returns "No Data" (common for expired contracts). This prevented yesterday's Jan 9 expirations from closing.
*   **Fix:** Modify `daily_tracker.py` to force an expiration check even when market data is missing. If `current_date >= expiration_date`, it will mark them as `EXPIRED`.

### **2. Implement SCALED Trade Expiration**
*   **Problem:** Wins currently run forever. You requested they expire but keep their Moonshot Peak value.
*   **Fix:**
    *   Create a new status `SCALED_EXP` for scaled trades that pass their expiration date.
    *   Modify `daily_tracker.py` to transition `SCALED` -> `SCALED_EXP` on expiration.
    *   This stops them from being queried daily (saving API calls) while preserving their "Highest Price" data.

### **3. Update Dashboard Logic**
*   **Problem:** The dashboard only counts `SCALED` as winners.
*   **Fix:** Update `dashboard.py` to treat `SCALED_EXP` exactly like `SCALED` (as a Win, using Peak PnL for Moonshot).

### **Execution**
1.  **Modify `daily_tracker.py`**: Add the "No Data" fallback and the `SCALED_EXP` transition.
2.  **Modify `dashboard.py`**: Update PnL/Win masking to include `SCALED_EXP`.
3.  **Recommendation**: After these changes, you should re-run the daily update script manually (or wait for the scheduled run) to clean up the currently stuck Jan 9 trades.
