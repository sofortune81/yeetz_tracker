I will modify `dashboard.py` to address your three requests.

### 1. Fix Filter Refresh
**Solution:** Use `st.fragment`.
*   I will add the `@st.fragment` decorator to the `render_trade_finder` function.
*   **Effect:** Interacting with filters (Ticker, Status, etc.) will only rerun this specific component, preventing the entire page from refreshing and keeping you on the "Finder" tab.

### 2. Add "Max Gain (or Loss)" Column
**Solution:** Calculate and display a new metric.
*   I will create a logic that shows the **Peak Return %** if the trade went positive.
*   If the trade never went positive (Peak Return is 0%), it will display the **Max Drawdown %** instead.
*   This column will be added to the table as "Max Gain/Loss".

### 3. Global Sorting with Pagination
**Solution:** Server-side sorting controls.
*   The current issue is that `st.dataframe` sorting only affects the 50 visible rows because of how pagination is implemented.
*   To fix this while **maintaining pagination**, I will add **Sort By** and **Order** (Ascending/Descending) controls above the table.
*   The sorting will be applied to the full dataset *before* it is split into pages, ensuring that "Top Gains" or "Newest" brings the correct rows to Page 1.

### **Verification Plan**
1.  **Refresh:** I will verify the code change includes the decorator. (You can test interactively that changing a filter doesn't reset the tab).
2.  **Column:** I will check that the new column appears and shows positive values for winners and negative values (drawdown) for straight losers.
3.  **Sorting:** I will verify that the new sort controls reorder the data globally (e.g., sorting by "Total PnL" brings the biggest winners to Page 1).
