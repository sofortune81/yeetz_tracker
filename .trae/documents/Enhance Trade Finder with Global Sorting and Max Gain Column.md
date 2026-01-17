I will address the requirements in two phases, starting with a deep-dive audit as requested.

### Phase 1: Audit & Expiration Logic Verification (Backend)

1. **Diagnostic Audit**:

   * I will create and run a temporary diagnostic script `scripts/audit_expired_logic.py` to simulate the `daily_tracker.py` logic on the specific reference trade `IRBT3C20251219` (ID 1878).

   * **Goal**: Determine *precisely* why the current logic failed to catch this expiration. (e.g., Was the cron job missed? Did the API return partial data that bypassed the "no data" check? Did the date comparison logic fail?)

   * **Deliverable**: A summary of the "Data Hygiene Issues" and "Root Cause" as requested.
2. **Implement "Safe" Cleanup**:

   * Create `scripts/fix_expired_status_verified.py`.

   * **Verification Step**: Before marking as `EXPIRED` (0.00), the script will attempt to fetch the final EOD price from ThetaData for the expiration date.

     * If Price > 0: Mark as `CLOSED` (or appropriate status) with the actual value.

     * If Price == 0 or No Data (and date > expiration): Mark as `EXPIRED` (0.00) with `close_reason="expiration_cleanup"`.

   * **Logging**: Log every change to `logs/cleanup_audit.log` for the user to review.

### Phase 2: Expiration Column Format (Frontend)

1. **Update Dashboard (`dashboard.py`)**:

   * Modify `render_trade_finder` to format the expiration column.

   * **Implementation**: Create a display-only column `Expiration (YYYYMMDD)` derived from `expiration_date`.

     * Format: `2025-12-19` -> `20251219`.

   * **Validation**: Ensure this column is sortable and handles `None` values gracefully (displaying as empty or "N/A").

   * **Preservation**: The original `expiration_date` object will remain in the dataframe for backend logic/filtering, but hidden or replaced in the view.

### Verification Plan

* **Audit**: Confirm `IRBT` status is corrected.

* **Frontend**: Open the dashboard (via `OpenPreview` if available, or just code verification) to check the `YYYYMMDD` format.

* **Report**: Provide the "Data Hygiene" report requested.

