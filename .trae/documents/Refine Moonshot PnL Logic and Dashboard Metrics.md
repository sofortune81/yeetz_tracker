Yes, I will refine the logic and check the dashboard metrics.

### **1. Logic Update: Correctly Calculating SCALED\_EXP PnL**

* **The Problem:** Currently, the PnL logic for `SCALED_EXP` (expired winners) isn't explicitly defined, so it might be falling back to incorrect defaults or using the Peak value erroneously.

* **The Fix in** **`daily_tracker.py`**:

  * **Baseline (100% TP):** Remains +20% (since we assume we sold it all).

  * **Strategy (80/20):** 80% sold at +20%, but the remaining 20% (Moonshot) is now worthless (-100%).

    * Formula: `(0.8 * 20) + (0.2 * -100) = 16 - 20 = -4%` Net Result.

  * **DBAP (Max Potential):** Keeps the Peak High (since "Don't Be A Pussy" implies you held perfectly to the top, but technically if you held to expiration you lost it all... however, usually DBAP tracks the *Max* potential, so I will keep it as Peak High unless you want it to reflect "Diamond Hands to Zero"). *Assumption: DBAP tracks Peak High.*

### **2. Dashboard Improvements (`dashboard.py`)**

* **Moonshot PnL Logic:**

  * **Current:** Uses `peak_ret_pct` for all `SCALED` trades.

  * **New:** If status is `SCALED_EXP`, the Moonshot portion return is **-100.0%**.

  * This ensures the "Moonshot PnL" column reflects that the runner went to zero.

* **Metric Additions:**

  * **Realized vs Unrealized:** Split the PnL metrics to show what is banked vs what is paper profit.

  * **Average Win/Loss:** Add average dollar win vs average dollar loss.

  * **Moonshot Contribution:** Show exactly how much the "Moonshot" strategy adds (or subtracts) compared to the Baseline.

### **Execution Plan**

1. **Modify** **`daily_tracker.py`**: Implement the specific -100% logic for the moonshot portion of `SCALED_EXP` trades.
2. **Modify** **`dashboard.py`**: Update the simulation engine to penalize `SCALED_EXP` moonshots with a -100% return.
3. **Enhance Dashboard**: Add the requested "Average Win/Loss" and "Moonshot Contribution" metrics.

