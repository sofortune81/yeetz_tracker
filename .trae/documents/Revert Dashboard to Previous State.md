I will restore `dashboard.py` to its previous state.

### Plan
1.  **Revert Sidebar Active Pulse:** I will re-add the "Active Pulse" section to the sidebar, which calculated the Green/Red split of active trades based on their PnL.
2.  **Revert Open Positions Display:** I will revert the "Active Positions" metric in the main dashboard back to "Open Positions" and remove the split between "Scaled" and "Hunting".
3.  **Restore Logic:** The logic for calculating `wins`, `losses`, and `active_now` will be restored to exactly how it was before the last edit.

This will return the dashboard to the state where the Active Pulse was in the sidebar and the Open Positions metric was simpler.