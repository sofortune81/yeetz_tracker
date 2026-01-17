I have analyzed the `dashboard.py` file and the current "Trade Finder" implementation. Here is the plan to address all your requirements:

### 1. Architecture & Refactoring (`dashboard.py`)
To enable client-side filtering without page reloads and maintain scroll position, I will use the **Streamlit Fragments** feature (`@st.fragment`).

*   **Logic Extraction**: I will extract the filtering, sorting, and pagination logic into a separate, testable function `process_trade_finder_data` (or similar). This ensures we can unit test the business logic without needing the Streamlit UI context.
*   **Fragment Implementation**: I will wrap the UI rendering part of the Trade Finder in a function decorated with `@st.fragment`. This isolates the re-runs to just the Trade Finder component.
*   **Loading State**: Streamlit fragments automatically handle the loading state UI, but I will ensure explicit feedback (like spinners) is used during heavy data processing if needed.

### 2. Feature Implementation
**A. Filter Behavior**
*   The `st.fragment` implementation will naturally solve the "full page refresh" and "page position" issues.
*   Filters (Ticker, Status, Month, Winners Only) will trigger updates only within the fragment.

**B. New Column: "Max Gain %"**
*   **Data Source**: I identified `peak_ret_pct` in `sim_df` which already holds the "Max Gain" data.
*   **UI Update**: I will add this column to the `st.dataframe` configuration.
*   **Formatting**: Use `st.column_config.NumberColumn` to format as a percentage (e.g., `12.5%`) and add the requested tooltip ("Highest percentage gain achieved...").

**C. Sorting Enhancement**
*   **Current Issue**: Sorting only works on the *displayed* page (client-side).
*   **Solution**: I will add server-side sorting controls *before* pagination.
    *   **New UI Controls**: A "Sort By" dropdown (Date, Total PnL, Max Gain %, etc.) and a "Direction" toggle (Ascending/Descending).
    *   **Logic**: The `sim_df` will be sorted based on these controls *before* the pagination slicing occurs. This ensures the "Top 50" really are the top 50 of the entire dataset.

### 3. Testing (`test_dashboard_finder.py`)
I will create a new test file `test_dashboard_finder.py` to verify the logic.
*   **Unit Tests**: Test the extracted `process_trade_finder_data` function.
    *   Verify filtering by Ticker/Status correctly reduces the dataset.
    *   Verify sorting by "Max Gain %" correctly orders the data.
    *   Verify pagination logic returns the correct slice of data.
*   **Performance**: Add a simple timing check in the test to ensure sorting/filtering 10,000+ rows takes under a reasonable threshold (e.g., 200ms).

### 4. Documentation
*   Update `dashboard.py` docstrings to explain the new fragment behavior and sorting logic.
*   Add comments explaining the "Max Gain %" derivation.

### 5. Execution Steps
1.  **Refactor**: Extract logic from `render_trade_finder` into `filter_and_sort_data`.
2.  **Test**: Create and run `test_dashboard_finder.py` to verify the logic.
3.  **UI Update**: Modify `render_trade_finder` to use `@st.fragment` and include the new Sorting UI and "Max Gain" column configuration.
4.  **Verify**: Ensure the UI works as expected (sorting affects all data, no full reload).
