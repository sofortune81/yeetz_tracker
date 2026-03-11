I will update `dashboard.py` to correctly classify `SCALED` trades as "Open" (Pending) because they still have an active Moonshot portion running.

1. **Update** **`win_loss`** **Logic (Line 136-142):**

   * I will modify the condition for `'PENDING'` to include both `'OPEN'` and `'SCALED'` statuses.

   * New condition: `sim_df['status'].isin(['OPEN', 'SCALED'])` → `'PENDING'`.

2. **Update "Green/Red" Logic for Open Positions:**

   * Currently, the breakdown logic I added only looks at `open_df` (which previously only included 'OPEN').

   * With the change above, `open_df` will now include `SCALED` trades.

   * **Logic Check:**

     * `OPEN` trades are Green if `sim_pnl > 0`.

     * `SCALED` trades are essentially **always Green** on a net basis (since 80% was sold at +20% profit). Even if the moonshot is currently red, the total trade PnL is usually positive.

     * *Self-Correction:* The user said "unrealized pnl is positive... but 14 open positions shows 0 green". This implies the `OPEN` trades are all Red (current price < entry), but the `SCALED` trades (which have large unrealized gains on the moonshot portion) were missing from the count.

   * By including `SCALED` in `open_df`, the breakdown will naturally include them. `SCALED` trades will likely fall into the "Green" bucket because `sim_pnl` (Total PnL) is positive.

3. **Impact on Win Rate:**

   * **Important Side Effect:** By marking `SCALED` as `'PENDING'`, they will disappear from the "Win Rate" calculation (which currently counts `WIN` vs `LOSS` labels).

   * **Fix:** The user likely considers `SCALED` as a "Win" for win-rate purposes (since we banked profit), but "Open" for position management.

   * *Decision:* I will keep `SCALED` as `'WIN'` in the `win_loss` column to preserve the Win Rate metric.

   * **Alternative Implementation:** instead of changing `win_loss`, I will change the definition of `open_df` in the metrics section.

**Revised Plan:**

1. **Keep** **`win_loss`** **Logic As-Is:** `SCALED` remains `'WIN'` so Win Rate stays accurate.
2. **Redefine** **`open_df`** **(Line 416):**

   * Instead of `sim_df[sim_df['win_loss'] == 'PENDING']`, I will use:
     `open_df = sim_df[sim_df['status'].isin(['OPEN', 'SCALED'])]`
3. **Update Breakdown Logic:**

   * The Green/Red breakdown will now automatically process this new superset.

   * **Green:** `sim_pnl > 0` (includes almost all `SCALED` + winning `OPEN`).

   * **Red:** `sim_pnl <= 0` (losing `OPEN`).

This ensures:

* `SCALED` trades show up in the "Open Positions" count (User's request).

* `SCALED` trades contribute to the "Green" count (User's observation about missing positive PnL sources).

* Win Rate remains high (doesn't drop because `SCALED` trades were moved to "Pending").

**Refined Code Change:**

* Locate line \~416 where `open_df` is defined.

* Change to: `open_df = sim_df[sim_df['status'].isin(['OPEN', 'SCALED'])]`

**Verification:**

* If I do this, `open_df` overlap with `wins_df`. `wins_df` filters for `win_loss == 'WIN'`.

* `SCALED` trades are `WIN`.

* So `total_trades` = `wins` + `losses` + `pending`.

* If I change `open_df` definition locally for the metric, it won't break `total_trades` math if I don't use `open_df` to derive `total_trades`.

* Checking code: `total_trades = len(sim_df)`. Correct.

* The metrics are independent. `m1` (Portfolio), `m2` (Total Trades), `m3` (Win Rate).

* `open_df` is used for `s3` ("Open Positions").

* This is safe and correct.

**One detailed check:**
If a `SCALED` trade is technically a "Win" in the pie chart, but we count it as "Open" in the text metrics, is that confusing?

* User explicitly asked: "actualy 'scaled' postions need to be considered 'open' as well."

* So this is exactly what they want.

**Implementation Step:**

* Modify `dashboard.py` around line 416 to redefine `open_df`.

