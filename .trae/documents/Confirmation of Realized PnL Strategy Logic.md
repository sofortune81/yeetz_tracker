I will enhance the **Dashboard Sidebar** with the following features to improve clarity and real-time tracking:

### 1. ℹ️ Methodology & Strategy Notes (Collapsible)
A dedicated, toggleable section explaining the core logic:
*   **Strategy Basis:** Explanation of the **Hybrid 80/20** approach (80% Scaled at Target, 20% Moonshot).
*   **PnL Calculations:** Clear definitions of **Realized** (Banked Cash) vs. **Unrealized** (Paper Profit).
*   **DBAP Curve:** Definition of the "Peak Potential" tracking.

### 2. 🟢 Live Position Pulse (Visual Indicator)
A real-time visual breakdown of **Open Positions** in the sidebar:
*   **Win/Loss Split:** Dynamic counters for Open Winners (Green) vs. Open Losers (Red).
*   **Visual Ratio:** A progress bar or metric showing the percentage of open trades that are currently green.
*   **Toggle:** Collapsible to save space.

### 3. 📅 Time-Based Performance Filters
A global filter mechanism in the sidebar to adjust the dashboard view:
*   **Options:** *All Time*, *Year to Date*, *Last 30 Days*, *Last 90 Days*, *Custom Range*.
*   **Impact:** Updates all metrics (PnL, Win Rate, Equity Curves) based on the selected period.

### Implementation Plan
*   **Modify `dashboard.py`**:
    *   Insert the new Sidebar components (`st.sidebar`).
    *   Implement the filtering logic to slice the dataframe based on the selected Time Period.
    *   Calculate "Open Position" stats specifically from the current active trades list.
    *   Add styling and tooltips for better UX.

This ensures you have immediate visibility into active trade health while keeping the detailed methodology accessible but tucked away.