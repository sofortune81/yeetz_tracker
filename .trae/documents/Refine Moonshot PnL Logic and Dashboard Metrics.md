I have carefully reviewed your detailed requirements for the Moonshot and DBAP curves. I will revise the logic in `daily_tracker.py` to strictly adhere to these rules.

### **Revised Logic Implementation**

#### **1. Moonshot Curve Logic**
*   **Rule:** Only triggers on `SCALED` (Win) trades.
*   **Rule:** Never falls below +20% (since 80% is sold at +20%, and the remaining 20% is risk-free).
*   **Rule:** Tracks Peak High. Even if the moonbag goes to zero, the *Moonshot Curve* records the *Peak* value it achieved.
*   **Losses:** For trades that never win (Loss/Open), the Strategy Curve (which contains the Moonshot component) naturally tracks the current/loss value.

#### **2. DBAP Curve Logic**
*   **Rule:** Tracks Peak High for 100% of the position if the trade is a **WIN** (`SCALED` or `SCALED_EXP`).
*   **Rule:** Never draws down for Wins.
*   **Rule:** **INCLUDES LOSSES** for trades that never hit scale. If a trade expires worthless or hits a stop without scaling, DBAP takes that loss (likely -100% or stop loss %). This was the missing piece in my previous assumption.

### **Execution Plan**

1.  **Modify `calculate_pnl_snapshots` in `daily_tracker.py`**:
    *   **Strategy Curve:**
        *   If `SCALED/SCALED_EXP`: `(0.8 * 20%) + (0.2 * Peak_Return_Pct)`. This ensures it never drops below +16% (math: 16% + 20% of peak), effectively locking in the win.
        *   If `OPEN/LOSS`: `Current_Return_Pct`.
    *   **DBAP Curve:**
        *   If `SCALED/SCALED_EXP`: `Peak_Return_Pct` (Max Potential, never down).
        *   If `OPEN/LOSS`: `Current_Return_Pct` (Takes the full heat/loss).

2.  **Update `dashboard.py`**:
    *   Ensure the dashboard visualization uses these new computed columns directly without re-interpreting them incorrectly.

3.  **Comprehensive Testing (`test_logic.py`)**:
    *   **Test 1 (Win -> Expired):** Verify Strategy stays high and DBAP stays at peak, even after expiration.
    *   **Test 2 (Loss -> Expired):** Verify DBAP takes the -100% loss.
    *   **Test 3 (Active Win):** Verify upward-only trajectory.

I will now proceed to implement these strict logic changes and verify them.
