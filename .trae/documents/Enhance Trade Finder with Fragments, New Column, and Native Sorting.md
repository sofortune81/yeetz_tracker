I will modify `dashboard.py` to address your three requests.

1. **Fix Page Refresh (Filter Issue):**

   * I will use the `st.fragment` decorator on the `render_trade_finder` function. This isolates the component so that interacting with filters (changing ticker, status, etc.) only reruns that specific section of the UI instead of reloading the entire page.

2. **Add "Max Gain %" Column:**

   * I will add the `peak_ret_pct` column to the display dataframe.

   * I will label it "Max Gain %" in the column configuration and format it as a percentage. This uses the existing calculation: `((highest_price - entry_price) / entry_price) * 100`.

3. **Fix Sorting (Pagination Issue):**

   * I will **remove the manual pagination logic** (the "Page" selector and slicing).

   * Instead, I will pass the filtered dataset directly to `st.dataframe`. Streamlit's modern dataframe component handles large datasets efficiently with virtual scrolling. This automatically solves the sorting problem: when you click a column header to sort, it will now sort the **entire dataset** because the frontend has access to all rows, not just the current page.

**Technical Changes in** **`dashboard.py`:**

* Import `st.fragment` (or use via `st`).

* Decorate `render_trade_finder` with `@st.fragment`.

* Remove lines 259-278 (Pagination logic).

* Update `st.dataframe` call to use `filt` instead of `page_df`.

* Add `peak_ret_pct` to the column list and config.

