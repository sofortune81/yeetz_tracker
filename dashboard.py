from datetime import datetime
import pytz
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from supabase import create_client
import numpy as np
from daily_tracker import TP_PCT, SCALE_PCT, STOP_OI_PCT

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
@st.cache_data(ttl=600) # Cache the entire simulation result for 10 minutes (or until params change)
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

        # --- 1. If Trade has Final PnL saved (Realized PnL from DB: STOP_OI, EXPIRED, SCALED) ---
        if row['final_sim_pnl_pct'] is not None:
            final_ret_pct = row['final_sim_pnl_pct']
            final_tp_ret_pct = row['final_tp_pnl_pct']

            pnl = row['pos_size_dollars'] * (final_ret_pct / 100.0)
            tp_exit_pnl = row['pos_size_dollars'] * (final_tp_ret_pct / 100.0)
            status = row['status']
            left_on_table = 0.0  # Already finalized/realized
            is_winner = pnl > 0  # A win is defined by a positive PnL

            # --- CORRECTED PNL BREAKDOWN (Calculate Components & Set Total PnL to Sum) ---
            if row['status'] == 'SCALED' or row['final_tp_pnl_pct'] == TP_PCT:
                # 1. Calculate Scale Component PnL
                scale_pct_amount = SCALE_PCT / 100.0
                scale_pnl_dollars = row['pos_size_dollars'] * scale_pct_amount * (TP_PCT / 100.0)
                scale_ret_pct = TP_PCT

                # 2. Calculate Moonshot Component PnL
                moon_pct_amount = (100 - SCALE_PCT) / 100.0
                moonshot_ret_pct = ((high - entry) / entry) * 100.0
                moonshot_pnl_dollars = row['pos_size_dollars'] * moon_pct_amount * (moonshot_ret_pct / 100.0)

                # 3. SET FINAL VALUES
                # The Total PnL MUST be the sum of the components, overriding the database's 'pnl'
                pnl = scale_pnl_dollars + moonshot_pnl_dollars

                scale_pnl = scale_pnl_dollars
                moonshot_pnl = moonshot_pnl_dollars
                is_winner = True  # <-- ADDED: SCALED with final PnL is always a winner

            else:
                # For STOP_OI or EXPIRED without TP hit: Display the total realized PnL as the 'scale_pnl'.
                # pnl is already set based on final_ret_pct from the DB.
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
                    scale_pos_size = row['pos_size_dollars'] * (SCALE_PCT / 100.0)
                    scale_pnl = scale_pos_size * (TP_PCT / 100.0)
                    scale_ret_pct = TP_PCT

                    # 2. Moonshot PnL (20% at Peak High)
                    moon_pos_size = row['pos_size_dollars'] * ((100 - SCALE_PCT) / 100.0)
                    moonshot_ret_pct = ((high - entry) / entry) * 100.0
                    moonshot_pnl = moon_pos_size * (moonshot_ret_pct / 100.0)

                    pnl = scale_pnl + moonshot_pnl  # <-- Total PnL is scale + moonshot peak
                    tp_exit_pnl = row['pos_size_dollars'] * (TP_PCT / 100.0)

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
        win_loss_char = ""  # Default to blank (for OPEN trades)

        # Determine W/L for Realized Trades
        if status in ['STOP_OI', 'EXPIRED']:
            # Closed trade: Determine W/L based on final sim_pnl
            win_loss_char = "W" if pnl > 0.0 else "L"
        elif status == 'SCALED' and row['final_sim_pnl_pct'] is not None:
            # SCALED with final PnL from DB (Always W, as PnL >= Scale-out PnL)
            win_loss_char = "W"
        elif status == 'SCALED' and row['final_sim_pnl_pct'] is None:
            # SCALED without final PnL yet (current day): Assume W for the dashboard's W/L count
            win_loss_char = "W"
        # If status is 'OPEN' or 'SCALED' without final PnL, win_loss_char remains ""
            # --- Max Drawdown Calculation (Robust against 99999 placeholder) ---
        dd_pct = 0.0
        lowest_price_float = float(row['lowest_price'])

        if entry != 0 and lowest_price_float < 99999:
            dd_pct = min(0, ((lowest_price_float - entry) / entry) * 100.0)

        results.append({
            "id": row['id'],
            "sim_pnl": pnl,
            "tp_exit_pnl": tp_exit_pnl,
            "max_drawdown_pct": dd_pct,  # <-- Use the robustly calculated DD
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
    # Drop any stale 'sim_pnl' or 'tp_exit_pnl' columns from the original trade data
    # to ensure we only use the freshly calculated values from results.
    cols_to_drop = [col for col in ['sim_pnl', 'tp_exit_pnl', 'max_drawdown_pct'] if col in sim_df.columns]

    # Merge the original trade data with the new simulation results
    final_df = sim_df.drop(columns=cols_to_drop, errors='ignore').merge(res_df, on='id')
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
    # FIX: Ensure PnL columns are never NaN before cumulative sum to prevent 'nan' in metric cards
    final_df['sim_pnl'] = final_df['sim_pnl'].fillna(0.0)
    final_df['tp_exit_pnl'] = final_df['tp_exit_pnl'].fillna(0.0)

    final_df['equity_curve_scaled'] = initial_capital + final_df['sim_pnl'].cumsum()
    final_df['equity_curve_tp_exit'] = initial_capital + final_df['tp_exit_pnl'].cumsum()

    return final_df

# --- 4. VISUALIZATION COMPONENTS ---

def render_todays_activity(df):
    """
    Renders a dedicated section for trades alerted 'Today' (EST).
    Handles the HKT vs EST timezone difference correctly.
    """
    # 1. Get Today's Date in EST (regardless of User's System Time)
    est = pytz.timezone('US/Eastern')
    today_est = datetime.now(est).date()

    # 2. Filter DF for Today's Trades (using the EST 'Day' column we made earlier)
    todays_trades = df[df['Day'] == today_est].copy()

    st.subheader(f"🔔 Today's Live Alerts ({today_est})")

    if todays_trades.empty:
        st.info("No alerts found for the current EST trading session yet.")
        return

    # 3. Calculate Quick Stats for Today
    count = len(todays_trades)
    winners = len(todays_trades[todays_trades['sim_pnl'] > 0])
    # Assume unrealized PnL + realized PnL for today's net
    net_pnl_today = todays_trades['sim_pnl'].sum() + todays_trades['unrealized_pnl_dollars'].sum()

    # 4. Metrics
    c1, c2, c3 = st.columns(3)
    c1.metric("Alerts Today", count)
    c2.metric("Today's PnL (Sim)", f"${net_pnl_today:,.2f}", help="Realized + Unrealized PnL for trades alerted today.")
    c3.metric("Strike Rate", f"{winners}/{count}")

    # 5. Simple Dataframe
    st.dataframe(
        todays_trades[['discord_timestamp', 'ticker', 'strike', 'option_type', 'entry_price', 'last_price', 'sim_status', 'unrealized_pnl_pct']],
        hide_index=True,
        use_container_width=True,
        column_config={
            "discord_timestamp": st.column_config.DatetimeColumn("Time (EST)", format="HH:mm"),
            "entry_price": st.column_config.NumberColumn("Entry", format="$%.2f"),
            "last_price": st.column_config.NumberColumn("Current", format="$%.2f"),
            "unrealized_pnl_pct": st.column_config.NumberColumn("Unrealized %", format="%.2f%%"),
        }
    )
    st.divider()

def render_equity_chart(df):
    # Combine the min/max of both curves for padding calculation
    min_eq = min(df['equity_curve_scaled'].min(), df['equity_curve_tp_exit'].min())
    max_eq = max(df['equity_curve_scaled'].max(), df['equity_curve_tp_exit'].max())
    buffer = (max_eq - min_eq) * 0.15  # 15% buffer

    fig = go.Figure()
    # 1. Max Potential (80% @ TP + 20% @ Peak High)
    fig.add_trace(go.Scatter(x=df['discord_timestamp'], y=df['equity_curve_scaled'],
                             mode='lines',
                             name='Max Potential PnL (Scaled & Moonshot)',
                             line=dict(color='#00CC96', width=3)))  # Green

    # 2. Baseline TP (100% @ TP)
    fig.add_trace(go.Scatter(x=df['discord_timestamp'], y=df['equity_curve_tp_exit'],
                             mode='lines',
                             name='Baseline PnL (100% @ TP)',
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
    st.subheader("🗓️ Monthly Performance (Last 12 Months)")
    st.info("The PnL for each month is based on the **trade entry date** (Discord Timestamp).")

    # 1. Prepare 12-Month Calendar Range
    current_month_str = pd.Timestamp.now(tz='US/Eastern').strftime('%Y-%m')
    today = pd.Period(current_month_str, freq='M')
    # Generate the 12 months from 11 months ago up to the current month
    month_range = pd.period_range(end=today, periods=12, freq='M')
    month_names = [m.strftime('%Y-%m') for m in month_range]

    # 2. Group PnL by Calendar Month
    # Ensure Month column exists and is correctly formatted for grouping
    df['Month'] = df['discord_timestamp'].dt.strftime('%Y-%m')

    m_df = df.groupby('Month').agg({
        'sim_pnl': 'sum',
        'id': 'count',
        'sim_ret_pct': 'mean'
    }).reset_index()

    # 3. Merge with 12-Month Range and Fill Missing PnL with 0
    full_m_df = pd.DataFrame(month_names, columns=['Month'])
    m_df = full_m_df.merge(m_df, on='Month', how='left').fillna({
        'sim_pnl': 0,
        'id': 0,
        'sim_ret_pct': 0
    })

    # 4. Interactive Bar Chart (Simple Green/Red for PnL)
    m_df['PnL Sign'] = m_df['sim_pnl'].apply(lambda x: 'Profit' if x > 0 else 'Loss')

    # Ensure correct chronological order on the X-axis
    fig = px.bar(m_df, x='Month', y='sim_pnl', color='PnL Sign',
                 color_discrete_map={'Profit': '#00CC96', 'Loss': '#EF553B'},
                 category_orders={"Month": month_names}, # Ensures chronological ordering
                 title="Monthly Net PnL (Scaled & Moonshot)",
                 height=350)

    # 5. Update Layout - Restructured for robustness
    fig.update_layout(
        margin=dict(t=50, b=0, l=0, r=0),
        xaxis_title="Calendar Month (YYYY-MM)",
        yaxis_title="Net PnL ($)",
        bargap=0.1,  # Use bargap to control the space between bars/bar groups
        showlegend=False
    )

    # 6. Render Chart
    st_chart = st.plotly_chart(fig, width='stretch', key="monthly_pnl_chart")

    st.markdown("""
        To drill down, find the desired month in the **Trade Finder** tab and use the 'Month' filter.
    """)


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
    # --- INTERACTIVE TABLE ---
    # Configure grid options
    st.info("👆 Click on any row to open the Deep Dive Chart below.")

    event = st.dataframe(
        filtered[['discord_timestamp', 'ticker', 'expiration_date', 'strike', 'option_type', 'entry_price',
                  'last_price', 'highest_price', 'lowest_price',
                  'sim_status', 'win_loss',
                  'scale_pnl_dollars', 'scale_pnl_pct', 'moonshot_pnl_dollars', 'moonshot_pnl_pct',
                  'sim_pnl', 'sim_ret_pct', 'max_drawdown_pct',
                  'unrealized_pnl_dollars', 'unrealized_pnl_pct']],
        width='stretch',
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            # --- NEW: Explicit Timestamp Formatting ---
            "discord_timestamp": st.column_config.DatetimeColumn(
                "Alert Time (EST)",
                format="YYYY-MM-DD HH:mm"
            ),
            # ------------------------------------------
            "last_price": st.column_config.NumberColumn("Last Price ($)", format="$%.2f",
                                                        help="Most recent price fetched for this contract."),
            "win_loss": st.column_config.TextColumn("W/L"),
            "entry_price": st.column_config.NumberColumn("Entry ($)", format="$%.2f"),
            "highest_price": st.column_config.NumberColumn("High ($)", format="$%.2f"),
            "lowest_price": st.column_config.NumberColumn("Low ($)", format="$%.2f"),
            "expiration_date": st.column_config.DateColumn("Exp. Date", format="YYYY-MM-DD"),

            "scale_pnl_dollars": st.column_config.NumberColumn("Scale PnL ($)", format="$%.2f",
                                                               help="PnL of the 80% position at TP (if hit)."),
            "scale_pnl_pct": st.column_config.NumberColumn("Scale Ret (%)", format="%.2f%%"),

            "moonshot_pnl_dollars": st.column_config.NumberColumn("Moonshot PnL ($)", format="$%.2f",
                                                                  help="PnL of the 20% position at highest price reached."),
            "moonshot_pnl_pct": st.column_config.NumberColumn("Moonshot Ret (%)", format="%.2f%%"),

            "sim_pnl": st.column_config.NumberColumn("TOTAL PnL ($)", format="$%.2f"),
            "sim_ret_pct": st.column_config.NumberColumn("TOTAL Ret (%)", format="%.2f%%"),
            "max_drawdown_pct": st.column_config.NumberColumn("Max DD (%)", format="%.2f%%",
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

        # User only controls Capital and Risk %
        cap = st.number_input("Capital", 10000, 1000000, 100000)
        risk = st.slider("Risk % per Trade", 0.5, 5.0, 1.0)

        # Hardcoded strategy parameters (since back-end locks realized PnL)
        # These are used for calculating PnL of OPEN/SCALED trades only.
        tp_fixed = 20
        scale_fixed = 80
        stop_fixed = 0

        st.divider()

        st.info(
            f"""
                    **📝 Key Strategy Assumptions:**
                    * **Take Profit (TP):** +{TP_PCT:.0f}%
                    * **Scale Out:** Sell {SCALE_PCT:.0f}% at TP.
                    * **Moonshot:** Hold remaining {100 - SCALE_PCT:.0f}% to Peak High.
                    * **Stop Loss:** Triggered if Open Interest drops > {100 - STOP_OI_PCT:.0f}%.
                    * **Other assumptions:** Assumes fill same as the flow alert fill which is unlikely
                    """
        )

        st.button("🔄 Refresh DB", on_click=st.cache_data.clear)

    # Run Sim (using hardcoded strategy parameters)
    df = run_simulation(raw_df, cap, risk, tp_fixed, scale_fixed, stop_fixed)

    render_todays_activity(df)
    # --- DASHBOARD LAYOUT ---
    tab1, tab2 = st.tabs(["📊 Portfolio Overview", "🔎 Trade Finder"])

    with tab1:
        st.header("📈 Equity Curve & Performance Metrics")

        # --- Calculations (Corrected) ---
        realized_df = df[df['status'].isin(['STOP_OI', 'EXPIRED', 'SCALED']) & df['final_sim_pnl_pct'].notna()]

        total_trades = len(df)

        # Realized PnL
        total_realized_pnl = realized_df['sim_pnl'].sum()
        total_realized_wins = realized_df[realized_df['sim_pnl'] > 0]['sim_pnl'].sum()
        total_realized_losses = realized_df[realized_df['sim_pnl'] <= 0]['sim_pnl'].sum()  # Includes zero PnL
        realized_win_rate = (len(realized_df[realized_df['sim_pnl'] > 0]) / len(
            realized_df) * 100) if not realized_df.empty else 0

        # Unrealized Metrics (Current Open/Scaled positions without final PnL)
        total_unrealized_pnl = df['unrealized_pnl_dollars'].sum()
        num_unrealized_open = len(df[df['status'].isin(['OPEN', 'SCALED']) & df['final_sim_pnl_pct'].isna()])

        # Risk Metric (Max DD)
        avg_max_drawdown = df['max_drawdown_pct'].mean()
        profit_factor = total_realized_wins / abs(total_realized_losses) if total_realized_losses < 0 else (
            total_realized_wins / 1 if total_realized_wins > 0 else 0)

        # --- 1. Key Metrics (3 Columns) ---
        st.subheader("Key Metrics")
        col_m1, col_m2, col_m3 = st.columns(3)

        col_m1.metric("Net Portfolio Value", f"${df['equity_curve_scaled'].iloc[-1]:,.0f}", f"Initial: ${cap:,.0f}")
        col_m2.metric("Total Trades", f"{total_trades:,}")
        col_m3.metric("Realized Win Rate", f"{realized_win_rate:.1f}%", f"Avg DD: {avg_max_drawdown:.1f}%")

        st.divider()

        # --- 2. Realized PnL Breakdown (5 Columns) ---
        st.subheader("Realized Performance (Closed Trades)")
        col_r1, col_r2, col_r3, col_r4, col_r5 = st.columns(5)

        # Calculate the sum of the 100% exit PnL column
        total_baseline_pnl = realized_df['tp_exit_pnl'].sum()

        col_r1.metric("Net Realized PnL", f"${total_realized_pnl:,.0f}",
                      help="Total PnL from trades with a final status (SCALED + Moonshot).")
        col_r2.metric("Total Win PnL", f"${total_realized_wins:,.0f}",
                      help="Sum of PnL from all trades with positive final PnL.")
        col_r3.metric("Total Loss PnL", f"${total_realized_losses:,.0f}",
                      help="Sum of PnL from all trades with negative/zero final PnL.")
        col_r4.metric("Profit Factor", f"{profit_factor:.2f}", help="Total Win PnL / Total Loss PnL.")
        col_r5.metric("Baseline (100% @ 20% TP)", f"${total_baseline_pnl:,.0f}",
                      help="Total PnL if 100% of the position had been closed at the 20% profit target.")

        st.divider()

        # --- 3. Unrealized Snapshot (3 Columns) ---
        st.subheader("Current Market Exposure (Open Trades)")
        col_u1, col_u2, col_u3 = st.columns(3)

        col_u1.metric("Total Unrealized PnL", f"${total_unrealized_pnl:,.0f}",
                      help="Sum of PnL for trades currently in OPEN/SCALED status.")
        col_u2.metric("Open Positions", f"{num_unrealized_open:,}")
        potential_winners = df[df['highest_price'] > df['entry_price']]
        max_possible_pnl = (
                ((potential_winners['highest_price'] - potential_winners['entry_price']) / potential_winners[
                    'entry_price'])
                * potential_winners['pos_size_dollars']
        ).sum()
        col_u3.metric("Max Potential Return", f"${max_possible_pnl:,.0f}",
                      help="Total PnL if of all trades that hit at least 20% profit if you weren't such a pussy and held 100% of the entire position to the absolute highest price.")

        st.divider()

        # --- 4. Equity Curve ---
        render_equity_chart(df)

        # --- 5. Monthly Drill Down ---
        render_monthly_drilldown(df)

    with tab2:
        # 4. Finder & Deep Dive
        render_trade_finder(df)

if __name__ == "__main__":
    main()