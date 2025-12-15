import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from supabase import create_client
import os
import numpy as np

# --- 1. SETUP & CONFIG ---
st.set_page_config(
    page_title="WhaleTracker Pro",
    layout="wide",
    page_icon="🐋",
    initial_sidebar_state="collapsed"
)

# --- CUSTOM CSS FOR "PIZZAZZ" ---
st.markdown("""
<style>
    /* Metric Card Styling */
    div[data-testid="stMetric"] {
        background-color: #1E1E1E;
        padding: 15px;
        border-radius: 10px;
        border: 1px solid #333;
        transition: transform 0.2s;
    }
    div[data-testid="stMetric"]:hover {
        transform: scale(1.02);
        border-color: #00CC96;
    }

    /* Headers */
    h1, h2, h3 {
        font-family: 'Helvetica Neue', sans-serif;
        font-weight: 700;
    }

    /* Custom Dividers */
    hr {
        border-color: #444;
    }
</style>
""", unsafe_allow_html=True)


@st.cache_resource
def init_supabase():
    # Adjusted to use the nested format: [supabase] url = ...
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["key"]
    return create_client(url, key)

supabase = init_supabase()


# --- 2. DATA ENGINE ---
@st.cache_data(ttl=300)
def fetch_data():
    resp = supabase.table("whale_alerts").select("*").execute()
    df = pd.DataFrame(resp.data)

    # FIX: Changed to return a single empty DataFrame
    if df.empty: return df

    date_cols = ['discord_timestamp', 'expiration_date', 'tp_hit_date', 'close_date']
    for col in date_cols:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], utc=True).dt.tz_convert('US/Eastern')

    df['entry_iv'] = df['entry_iv'].fillna(0)
    df['highest_price'] = df['highest_price'].fillna(df['entry_price'])

    # FIX: Initialize and fill 'lowest_price' to prevent KeyError if the column is new/missing
    if 'lowest_price' not in df.columns:
        df['lowest_price'] = df['entry_price']
    else:
        df['lowest_price'] = df['lowest_price'].fillna(df['entry_price'])


    # Extract useful date parts for filtering
    df['Month'] = df['discord_timestamp'].dt.strftime('%Y-%m')
    df['Day'] = df['discord_timestamp'].dt.date

    return df

@st.cache_data(ttl=300)
def fetch_trade_history(alert_id):
    resp = supabase.table("whale_performance").select("*").eq("alert_id", alert_id).order("date").execute()
    df_hist = pd.DataFrame(resp.data)
    if not df_hist.empty:
        df_hist['date'] = pd.to_datetime(df_hist['date'])
    return df_hist


# --- 3. SIMULATION ENGINE ---
def run_simulation(df_trades, initial_capital, risk_pct, tp_pct, scale_pct, stop_loss_pct):
    sim_df = df_trades.copy()
    sim_df['pos_size_dollars'] = initial_capital * (risk_pct / 100.0)

    results = []

    for _, row in sim_df.iterrows():
        entry = float(row['entry_price'])
        high = float(row['highest_price'])
        curr_price = float(row['last_price'] or entry)
        target_price = entry * (1 + tp_pct / 100.0)

        hit_tp = high >= target_price
        pnl = 0.0
        tp_exit_pnl = 0.0
        status = "OPEN"
        left_on_table = 0.0

        # PnL Breakdown Initialization
        scale_pnl = 0.0
        scale_ret_pct = 0.0
        moonshot_pnl = 0.0
        moonshot_ret_pct = 0.0
        is_winner = False

        # Use hardcoded back-end rules for calculating PnL breakdown/realization
        # Note: The back-end hardcodes 20% TP and 80% Scale-Out.
        BACKEND_TP_PCT = 20.0
        BACKEND_SCALE_PCT = 80.0

        # --- 1. If Trade has Final PnL saved (Realized PnL from DB: STOP_OI, EXPIRED, SCALED) ---
        if row['final_sim_pnl_pct'] is not None:
            final_ret_pct = row['final_sim_pnl_pct']
            final_tp_ret_pct = row['final_tp_pnl_pct']

            pnl = row['pos_size_dollars'] * (final_ret_pct / 100.0)
            tp_exit_pnl = row['pos_size_dollars'] * (final_tp_ret_pct / 100.0)
            status = row['status']
            left_on_table = 0.0  # Already finalized/realized
            is_winner = pnl > 0  # A win is defined by a positive PnL

            # Back-calculate PnL Breakdown (always calculate if PnL is saved, using high for moonshot)
            if entry != 0 and row['pos_size_dollars'] != 0 and row['status'] in ['SCALED', 'EXPIRED', 'STOP_OI']:
                # PnL Breakdown ONLY happens if TP was hit. If TP was not hit (STOP_OI/EXPIRED)
                # the breakdown is just the final realized PnL on 100% of the position.

                if row['status'] == 'SCALED' or row['final_tp_pnl_pct'] == BACKEND_TP_PCT:  # Check if TP was hit
                    # Scale Out PnL (80% at 20% gain)
                    scale_pos_size = row['pos_size_dollars'] * (BACKEND_SCALE_PCT / 100.0)
                    scale_pnl = scale_pos_size * (BACKEND_TP_PCT / 100.0)
                    scale_ret_pct = BACKEND_TP_PCT

                    # Moonshot PnL (20% at Highest Price achieved)
                    moon_pos_size = row['pos_size_dollars'] * ((100 - BACKEND_SCALE_PCT) / 100.0)
                    moonshot_ret_pct = ((high - entry) / entry) * 100.0
                    moonshot_pnl = moon_pos_size * (moonshot_ret_pct / 100.0)
                else:
                    # For STOP_OI or EXPIRED without TP hit: 100% was closed at exit_price/0.
                    # Display the total PnL as the 'scale_pnl' since no scaling occurred.
                    scale_pnl = pnl
                    scale_ret_pct = final_ret_pct
                    moonshot_pnl = 0.0
                    moonshot_ret_pct = 0.0
            # Else: breakdown stays 0.0

        # --- 2. If PnL is NOT saved (OPEN/SCALED without final PnL, e.g., current day trade) ---
        else:
            exit_price = float(row['close_price'] or curr_price)

            if entry != 0:
                if hit_tp or row['status'] == 'SCALED':
                    # Case: TP hit (SCALED) - Calculate full potential PnL (as if closed today at high/TP)
                    is_winner = True

                    # 1. Scale Out PnL (80% at 20% gain)
                    scale_pos_size = row['pos_size_dollars'] * (BACKEND_SCALE_PCT / 100.0)
                    scale_pnl = scale_pos_size * (BACKEND_TP_PCT / 100.0)
                    scale_ret_pct = BACKEND_TP_PCT

                    # 2. Moonshot PnL (20% at Peak High)
                    moon_pos_size = row['pos_size_dollars'] * ((100 - BACKEND_SCALE_PCT) / 100.0)
                    moonshot_ret_pct = ((high - entry) / entry) * 100.0
                    moonshot_pnl = moon_pos_size * (moonshot_ret_pct / 100.0)

                    pnl = scale_pnl + moonshot_pnl  # <-- Total PnL is scale + moonshot peak
                    tp_exit_pnl = row['pos_size_dollars'] * (BACKEND_TP_PCT / 100.0)

                    # Left on Table
                    max_ret_pct = ((high - entry) / entry) * 100.0
                    potential_max_pnl = row['pos_size_dollars'] * (max_ret_pct / 100.0)
                    left_on_table = potential_max_pnl - pnl
                    status = row['status']  # Keep OPEN or SCALED
                else:
                    # Case: No TP hit (OPEN/DRAWDOWN) - PnL at current price
                    is_winner = False
                    ret = (exit_price - entry) / entry
                    pnl = row['pos_size_dollars'] * ret
                    tp_exit_pnl = pnl
                    left_on_table = 0.0
                    status = row['status']
                    # PnL Breakdown stays 0.0
            else:
                status = "INVALID_ENTRY"

        # --- Collect Results ---
        win_loss_char = ""  # Default to blank (for OPEN, SCALED without final PnL)

        if status in ['STOP_OI', 'EXPIRED']:
            # Closed trade: Determine W/L based on PnL
            win_loss_char = "W" if pnl > 0.0 else "L"
        elif status == 'SCALED':
            # Scaled trade: Always a Win, regardless of final realized status (which may not be set yet)
            win_loss_char = "W"
        # If status is 'OPEN' or 'SCALED' without final PnL, win_loss_char remains ""

        results.append({
            "id": row['id'],
            "sim_pnl": pnl,
            "tp_exit_pnl": tp_exit_pnl,
            "max_drawdown_pct": min(0, ((float(row['lowest_price']) - entry) / entry) * 100.0) if entry != 0 else 0.0,
            "sim_status": status,
            "sim_ret_pct": (pnl / row['pos_size_dollars']) * 100 if row['pos_size_dollars'] != 0 else 0.0,
            "left_on_table": max(0, left_on_table),
            "scale_pnl_dollars": scale_pnl,
            "scale_pnl_pct": scale_ret_pct,
            "moonshot_pnl_dollars": moonshot_pnl,
            "moonshot_pnl_pct": moonshot_ret_pct,
            "is_winner": is_winner,
            "win_loss": win_loss_char  # This is the desired W/L/"" column
        })

    res_df = pd.DataFrame(results)
    final_df = sim_df.merge(res_df, on='id')
    final_df = final_df.sort_values('discord_timestamp')

    # --- PnL and Equity Curve Calculations (Unrealized PnL is calculated here) ---

    # Determine if trade is not finalized (OPEN, or SCALED status where final_sim_pnl_pct is still None)
    # A trade is "unrealized" if its status is OPEN or SCALED AND it has no final PnL recorded yet.
    is_unfinalized = final_df['final_sim_pnl_pct'].isna() & final_df['status'].isin(['OPEN', 'SCALED'])

    # Calculate Unrealized PnL Dollars (using the current 'last_price')
    # Use last_price (or entry_price if last_price is null) for the calculation.
    last_price = final_df['last_price'].fillna(final_df['entry_price'])
    entry_price = final_df['entry_price']

    final_df['unrealized_pnl_dollars'] = np.where(
        is_unfinalized,
        (last_price - entry_price) / entry_price * final_df['pos_size_dollars'],
        0.0  # Finalized trades or invalid entries have 0 unrealized PnL here
    )

    # Calculate Unrealized PnL Percentage (Dollars / Position Size)
    final_df['unrealized_pnl_pct'] = np.where(
        (final_df['pos_size_dollars'] != 0) & is_unfinalized,
        (final_df['unrealized_pnl_dollars'] / final_df['pos_size_dollars']) * 100,
        0.0
    )

    # Categorize Unrealized PnL (for metric cards) - This part is fine.
    final_df['unrealized_profit'] = np.where(
        (final_df['unrealized_pnl_dollars'] > 0) & is_unfinalized,
        final_df['unrealized_pnl_dollars'],
        0.0
    )
    final_df['unrealized_drawdown'] = np.where(
        (final_df['unrealized_pnl_dollars'] < 0) & is_unfinalized,
        final_df['unrealized_pnl_dollars'],
        0.0
    )

    # --- Corrected Equity Curve Calculations ---
    final_df['equity_curve_scaled'] = initial_capital + final_df['sim_pnl'].cumsum()
    final_df['equity_curve_tp_exit'] = initial_capital + final_df['tp_exit_pnl'].cumsum()

    return final_df

# --- 4. VISUALIZATION COMPONENTS ---

def render_equity_chart(df):
    # Combine the min/max of both curves for padding calculation
    min_eq = min(df['equity_curve_scaled'].min(), df['equity_curve_tp_exit'].min())
    max_eq = max(df['equity_curve_scaled'].max(), df['equity_curve_tp_exit'].max())
    buffer = (max_eq - min_eq) * 0.15  # 15% buffer

    fig = go.Figure()

    # 1. Scaled & Moonshot (Primary)
    fig.add_trace(go.Scatter(x=df['discord_timestamp'], y=df['equity_curve_scaled'],
                             mode='lines',
                             name='Scaled & Moonshot',
                             line=dict(color='#00CC96', width=3)))  # Green

    # 2. Full TP Exit (Comparison)
    fig.add_trace(go.Scatter(x=df['discord_timestamp'], y=df['equity_curve_tp_exit'],
                             mode='lines',
                             name='Full TP Exit (100% @ TP)',
                             line=dict(color='#636EFA', width=1.5, dash='dot')))  # Blue/Grey dot

    fig.update_layout(
        title="<b>📈 Portfolio Velocity (Scaled vs. Full TP Exit)</b>",
        yaxis=dict(range=[min_eq - buffer, max_eq + buffer], title="Equity ($)"),
        xaxis_title="Time",
        hovermode="x unified",
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01),
        margin=dict(t=50, b=0, l=0, r=0)
    )
    st.plotly_chart(fig, width='stretch')


def render_calendar_view(df):
    st.subheader("📅 Daily Heatmap")
    # Group by Day
    daily = df.groupby('Day')['sim_pnl'].sum().reset_index()
    daily['color'] = np.where(daily['sim_pnl'] > 0, 'green', 'red')

    # We use a simple Bar here for clarity, but a Github Style heatmap
    # requires 'plotly_calplot' or complex rects.
    # Let's do a stylish Bar chart colored by PnL

    fig = px.bar(daily, x='Day', y='sim_pnl', color='sim_pnl',
                 title="Daily Net PnL",
                 color_continuous_scale="RdYlGn")
    fig.update_layout(height=250)
    st.plotly_chart(fig, width='stretch')


def render_monthly_drilldown(df):
    st.subheader("🗓️ Monthly Analysis")

    # 1. Prepare Monthly Data
    # Ensure Month column exists
    if 'Month' not in df.columns:
        df['Month'] = df['discord_timestamp'].dt.strftime('%Y-%m')

    m_df = df.groupby('Month').agg({
        'sim_pnl': 'sum',
        'id': 'count',
        'sim_ret_pct': 'mean'
    }).reset_index()

    if m_df.empty:
        st.info("No monthly data available yet.")
        return

    # 2. Interactive Bar Chart
    fig = px.bar(m_df, x='Month', y='sim_pnl', color='sim_pnl',
                 color_continuous_scale="RdYlGn",
                 title="Click a Month to Inspect Stats")

    # Enable selection
    selected_points = st.plotly_chart(fig, width='stretch', on_select="rerun")

    # 2. Interactive Bar Chart
    # Use a unique key for the chart to allow selection to persist/be accessed
    selected_points = st.plotly_chart(fig, width='stretch', key="monthly_pnl_chart")

    # 3. Drill Down Logic: Use session state to link selection to Trade Finder filter
    selected_month = None
    if st.session_state.get('monthly_pnl_chart_select_data') and st.session_state['monthly_pnl_chart_select_data'][
        'points']:
        selected_month = st.session_state['monthly_pnl_chart_select_data']['points'][0]['x']

    # CRITICAL: We save the selected month to session state so render_trade_finder can pick it up.
    # If no selection, default to None (no filter)
    if selected_month:
        st.session_state['trade_finder_month_filter'] = [selected_month]
        st.info(
            f"Trades list below is filtered for **{selected_month}**. Click a different month or clear the filter to see all.")
    else:
        st.session_state['trade_finder_month_filter'] = []

    # Show Summary Stats for the whole period (or remove this section if you want a clean funnel)
    st.markdown("### 📊 Overall Performance Summary")

    c1, c2, c3, c4 = st.columns(4)

    wins = df[df['sim_pnl'] > 0]
    losses = df[df['sim_pnl'] <= 0]
    total_trades = len(df)
    win_rate = (len(wins) / total_trades * 100) if total_trades > 0 else 0

    c1.metric("Net PnL", f"${df['sim_pnl'].sum():,.0f}", f"{total_trades} Trades")
    c2.metric("Win Rate", f"{win_rate:.0f}%", f"{len(wins)}W / {len(losses)}L")

    # Display best/worst of the *entire* period for high-level summary
    best_trade = df.loc[df['sim_pnl'].idxmax()] if not df.empty else None
    worst_trade = df.loc[df['sim_pnl'].idxmin()] if not df.empty else None

    if best_trade is not None:
        c3.metric("🏆 Biggest Win", f"${best_trade['sim_pnl']:.0f}", f"{best_trade['ticker']}")

    if worst_trade is not None:
        c4.metric("💀 Biggest Loss", f"${worst_trade['sim_pnl']:.0f}", f"{worst_trade['ticker']}")


def render_trade_finder(df):
    st.divider()
    st.header("🔎 Smart Trade Finder")

    # --- FILTERS ---
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        filter_ticker = st.multiselect("Ticker", options=df['ticker'].unique())
    with c2:
        filter_status = st.multiselect("Status", options=df['sim_status'].unique())
    with c3:
        # Use a default from session state if set by the monthly chart
        default_month_filter = st.session_state.get('trade_finder_month_filter', [])
        filter_month = st.multiselect("Month", options=df['Month'].unique(), default=default_month_filter)
    with c4:
        show_only_winners = st.checkbox("Only Winners")

    # Apply Filters
    filtered = df.copy()
    if filter_ticker: filtered = filtered[filtered['ticker'].isin(filter_ticker)]
    if filter_status: filtered = filtered[filtered['sim_status'].isin(filter_status)]
    # Apply Month Filter (now correctly handles the default from session state)
    if filter_month: filtered = filtered[filtered['Month'].isin(filter_month)]
    if show_only_winners: filtered = filtered[filtered['sim_pnl'] > 0]

    filtered = filtered.sort_values("discord_timestamp", ascending=False)

    # --- INTERACTIVE TABLE ---
    # Configure grid options
    st.info("👆 Click on any row to open the Deep Dive Chart below.")

    event = st.dataframe(
        filtered[['discord_timestamp', 'ticker', 'expiration_date', 'strike', 'option_type', 'entry_price',
                  'last_price', 'highest_price', 'lowest_price',
                  'sim_status', 'win_loss',  # <-- EDITED: Use 'win_loss' instead of 'is_winner'
                  'scale_pnl_dollars', 'scale_pnl_pct', 'moonshot_pnl_dollars', 'moonshot_pnl_pct',
                  'sim_pnl', 'sim_ret_pct', 'max_drawdown_pct',
                  'unrealized_pnl_dollars', 'unrealized_pnl_pct']],
        width='stretch',
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "last_price": st.column_config.NumberColumn("Last Price ($)", format="$%.2f",
                                                        help="Most recent price fetched for this contract."),
            "win_loss": st.column_config.TextColumn("W/L"),  # <-- EDITED: New W/L column config
            "entry_price": st.column_config.NumberColumn("Entry ($)", format="$%.2f"),
            "highest_price": st.column_config.NumberColumn("High ($)", format="$%.2f"),
            "lowest_price": st.column_config.NumberColumn("Low ($)", format="$%.2f"),  # <-- NEW: Lowest Price Seen
            "expiration_date": st.column_config.DateColumn("Exp. Date", format="YYYY-MM-DD"),

            "scale_pnl_dollars": st.column_config.NumberColumn("Scale PnL ($)", format="$%.2f",
                                                               help="PnL of the 80% position at TP (if hit)."),
            "scale_pnl_pct": st.column_config.NumberColumn("Scale Ret (%)", format="%.2f%%"),

            "moonshot_pnl_dollars": st.column_config.NumberColumn("Moonshot PnL ($)", format="$%.2f",
                                                                  help="PnL of the 20% position at highest price reached."),
            "moonshot_pnl_pct": st.column_config.NumberColumn("Moonshot Ret (%)", format="%.2f%%"),

            "sim_pnl": st.column_config.NumberColumn("TOTAL PnL ($)", format="$%.2f"),
            "sim_ret_pct": st.column_config.NumberColumn("TOTAL Ret (%)", format="%.2f%%"),
            "max_drawdown_pct": st.column_config.NumberColumn("Max DD (%)", format="%.2f%%",  # <-- NEW: Max Drawdown
                                                              help="Max drawdown from entry price."),

            "unrealized_pnl_dollars": st.column_config.NumberColumn("Unrealized PnL ($)", format="$%.2f",
                                                                    help="PnL of currently OPEN/SCALED trades at last price."),
            "unrealized_pnl_pct": st.column_config.NumberColumn("Unrealized Ret (%)", format="%.2f%%"),

            "sim_status": st.column_config.TextColumn("Outcome"),
        }
    )

    # --- DEEP DIVE SECTION ---
    if event.selection['rows']:
        idx = event.selection['rows'][0]
        # Get the actual ID from the filtered dataframe using the row index
        selected_id = filtered.iloc[idx]['id']
        render_deep_dive(df, selected_id)


def render_deep_dive(df, trade_id):
    row = df[df['id'] == trade_id].iloc[0]
    hist = fetch_trade_history(trade_id)

    st.markdown(f"## 🔬 Analyzing {row['ticker']} ({row['Month']})")

    # Chart
    fig = go.Figure()

    # 1. Price Action
    if not hist.empty:
        fig.add_trace(go.Scatter(x=hist['date'], y=hist['price_close'], name='Close Price', line=dict(color='#00CC96')))
        fig.add_trace(go.Scatter(x=hist['date'], y=hist['price_high'], name='Daily High', mode='markers',
                                 marker=dict(color='yellow', size=6)))

    # 2. Key Levels
    fig.add_hline(y=row['entry_price'], line_dash="dash", line_color="white", annotation_text="Entry")
    target = row['entry_price'] * (1 + 0.20)  # Assuming 20%
    fig.add_hline(y=target, line_dash="dot", line_color="green", annotation_text="Target")

    fig.update_layout(title="Trade Lifecycle", template="plotly_dark", height=400)
    st.plotly_chart(fig, width='stretch')

    # Stats Row
    c1, c2, c3 = st.columns(3)
    c1.metric("Result", row['sim_status'])
    c2.metric("Realized PnL", f"${row['sim_pnl']:.2f}")
    c3.metric("Money Left on Table", f"${row['left_on_table']:.2f}", help="Difference between Max High and your Exit")


# --- 5. MAIN ---
def main():
    raw_df = fetch_data()
    if raw_df.empty:
        st.error("No trades found. Start the listener!")
        return

    # Sidebar Controls
    with st.sidebar:
        st.header("🎛️ Simulation")
        cap = st.number_input("Capital", 10000, 1000000, 100000)
        risk = st.slider("Risk %", 0.5, 5.0, 1.0)
        tp = st.slider("TP %", 10, 100, 20)
        scale = st.slider("Scale %", 0, 100, 80)
        stop = st.slider("Stop Loss %", -50, 0, 0)

        st.divider()
        st.button("🔄 Refresh DB", on_click=st.cache_data.clear)

    # Run Sim
    df = run_simulation(raw_df, cap, risk, tp, scale, stop)

    # --- DASHBOARD LAYOUT ---

    # 1. Top Cards (Colored)
    # The new metrics require 6 columns, so let's use 3 pairs of 2 columns
    col_r1 = st.columns(2)
    col_r2 = st.columns(2)
    col_r3 = st.columns(2)

    # --- Calculations ---
    total_trades = len(df)
    total_wins_scaled = len(df[df['status'].isin(['SCALED', 'STOP_OI', 'EXPIRED']) & (df['sim_pnl'] > 0)])
    total_expired = len(df[df['status'] == 'EXPIRED'])

    # Unrealized Metrics
    num_unrealized_dd = len(df[df['unrealized_drawdown'] < 0])
    total_unrealized_dd = df['unrealized_drawdown'].sum()
    num_unrealized_profit = len(df[df['unrealized_profit'] > 0])
    total_unrealized_profit = df['unrealized_profit'].sum()
    total_unrealized_pnl = total_unrealized_profit + total_unrealized_dd

    # Realized Metrics
    realized_trades = df[df['status'].isin(['STOP_OI', 'EXPIRED'])]
    total_winner_pnl = realized_trades[realized_trades['sim_pnl'] > 0]['sim_pnl'].sum()
    total_loser_pnl_expired = df[df['status'] == 'EXPIRED']['sim_pnl'].sum()  # PnL of ONLY expired trades
    total_realized_pnl = realized_trades['sim_pnl'].sum()
    total_current_moonshot_pnl = df[df['status'] == 'SCALED']['sim_pnl'].sum() - df[df['status'] == 'SCALED'][
        'tp_exit_pnl'].sum()

    # --- Row 1: High-Level Totals ---
    col_r1[0].metric("Total Trades Taken", f"{total_trades:,}")
    col_r1[1].metric("Total Wins (Realized)", f"{total_wins_scaled:,}",
                     help="Trades that hit TP/SCALED and have a positive PnL.")

    # --- Row 2: Unrealized Snapshot ---
    col_r2[0].metric("Unrealized Drawdown", f"${total_unrealized_dd:,.0f}", f"{num_unrealized_dd} Trades")
    col_r2[1].metric("Net Unrealized PnL", f"${total_unrealized_pnl:,.0f}", delta_color="normal")

    # --- Row 3: Realized / Closed PnL ---
    col_r3[0].metric("Total Realized PnL", f"${total_realized_pnl:,.0f}",
                     help="Total PnL from trades that are STOP_OI or EXPIRED.")
    col_r3[1].metric("Total Moonshot Value", f"${total_current_moonshot_pnl:,.0f}",
                     help="Remaining PnL of SCALED trades (PnL - TP Exit PnL).")

    # The next metrics will replace the old k1-k4 metrics in the layout:
    st.divider()

    col_next = st.columns(4)
    # Re-introducing a simpler metric in the second row for clean display
    col_next[0].metric("Total Winner PnL", f"${total_winner_pnl:,.0f}", help="Total realized PnL from winning trades.")
    col_next[1].metric("Total Loser PnL (Expired)", f"${total_loser_pnl_expired:,.0f}",
                       help="PnL from trades that only expired worthless.")
    col_next[2].metric("Expired Worthless Count", f"{total_expired}", delta_color="inverse")
    col_next[3].metric("Profit Factor",
                       f"{df[df['sim_pnl'] > 0]['sim_pnl'].sum() / abs(df[df['sim_pnl'] < 0]['sim_pnl'].sum() or 1):.2f}")

    st.divider()

    # 2. Main Visuals
    c1, c2 = st.columns([2, 1])

    # 3. Monthly Drill Down
    render_monthly_drilldown(df)

    # 4. Finder & Deep Dive
    render_trade_finder(df)


if __name__ == "__main__":
    main()