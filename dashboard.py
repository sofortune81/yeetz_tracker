from datetime import datetime
import pytz
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from supabase import create_client
import numpy as np

# --- IMPORT CONFIG ---
from yeetz_config import TP_PCT, SCALE_PCT, STOP_OI_PCT

# --- CONFIG ---
st.set_page_config(
    page_title="WhaleTracker Pro",
    layout="wide",
    page_icon="🐋",
    initial_sidebar_state="collapsed"
)

# --- CUSTOM CSS ---
st.markdown("""
<style>
    div[data-testid="stMetric"] { background-color: #1E1E1E; padding: 15px; border-radius: 10px; border: 1px solid #333; }
    div[data-testid="stMetric"]:hover { border-color: #00CC96; }
    h1, h2, h3 { font-family: 'Helvetica Neue', sans-serif; font-weight: 700; }
</style>
""", unsafe_allow_html=True)


@st.cache_resource
def init_supabase():
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["key"]
    return create_client(url, key)


supabase = init_supabase()


# --- DATA ENGINE ---
@st.cache_data(ttl=300)
def fetch_data():
    resp = supabase.table("whale_alerts").select("*").execute()
    df = pd.DataFrame(resp.data)
    if df.empty: return df

    # 1. Date Handling
    date_cols = ['discord_timestamp', 'expiration_date', 'close_date']
    for col in date_cols:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], utc=True, errors='coerce')
            if df[col].notnull().any():
                df[col] = df[col].dt.tz_convert('US/Eastern')

    # 2. Numeric Conversions
    float_cols = ['entry_price', 'highest_price', 'lowest_price', 'last_price', 'profit_target', 'final_sim_pnl_pct',
                  'final_tp_pnl_pct']
    for col in float_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    # 3. Fix Missing Prices
    df['highest_price'] = df['highest_price'].replace(0.0, np.nan).fillna(df['entry_price'])
    df['highest_price'] = np.maximum(df['highest_price'], df['entry_price'])

    df['lowest_price'] = df['lowest_price'].replace(0.0, np.nan).fillna(df['entry_price'])
    df['last_price'] = df['last_price'].fillna(df['entry_price'])

    # 4. Dates
    df['Month'] = df['discord_timestamp'].dt.strftime('%Y-%m')
    df['Day'] = df['discord_timestamp'].dt.date

    # 5. Sort
    df = df.sort_values('discord_timestamp', ascending=False)
    return df


# --- SIMULATION ENGINE ---
def run_simulation(df, initial_capital, risk_pct):
    sim_df = df.copy()

    # 1. Position Size
    sim_df['pos_size_dollars'] = initial_capital * (risk_pct / 100.0)

    # 2. Base Calculations
    sim_df['curr_ret_pct'] = ((sim_df['last_price'] - sim_df['entry_price']) / sim_df['entry_price']) * 100.0
    sim_df['peak_ret_pct'] = ((sim_df['highest_price'] - sim_df['entry_price']) / sim_df['entry_price']) * 100.0

    raw_low = sim_df['lowest_price'].replace(0, np.nan).fillna(sim_df['entry_price'])
    sim_df['max_drawdown_pct'] = ((raw_low - sim_df['entry_price']) / sim_df['entry_price']) * 100.0
    sim_df['max_drawdown_pct'] = sim_df['max_drawdown_pct'].clip(upper=0.0)

    # 3. Retrieve Backend PnL
    sim_df['sim_ret_pct'] = sim_df['final_sim_pnl_pct'].fillna(sim_df['curr_ret_pct'])
    sim_df['tp_exit_ret_pct'] = sim_df['final_tp_pnl_pct'].fillna(sim_df['curr_ret_pct'])

    # Use the stored DBAP value first; if it doesn't exist (live trades), use the logic
    sim_df['dbap_ret_pct'] = sim_df['final_dbap_pnl_pct'].fillna(
        pd.Series(np.where(sim_df['status'] == 'SCALED', sim_df['peak_ret_pct'], sim_df['sim_ret_pct']),
                  index=sim_df.index)
    )

    # 4. Calculate $$$
    sim_df['sim_pnl'] = sim_df['pos_size_dollars'] * (sim_df['sim_ret_pct'] / 100.0)
    sim_df['tp_exit_pnl'] = sim_df['pos_size_dollars'] * (sim_df['tp_exit_ret_pct'] / 100.0)
    sim_df['dbap_pnl'] = sim_df['pos_size_dollars'] * (sim_df['dbap_ret_pct'] / 100.0)

    # 5. Visual Split
    portion_scale = SCALE_PCT / 100.0
    portion_moon = 1.0 - portion_scale

    sim_df['scale_pnl_dollars'] = 0.0
    sim_df['moonshot_pnl_dollars'] = 0.0

    mask_win = sim_df['status'] == 'SCALED'

    # Winners: Split PnL
    sim_df.loc[mask_win, 'scale_pnl_dollars'] = (sim_df.loc[mask_win, 'pos_size_dollars'] * portion_scale) * (
                TP_PCT / 100.0)
    sim_df.loc[mask_win, 'moonshot_pnl_dollars'] = (sim_df.loc[mask_win, 'pos_size_dollars'] * portion_moon) * (
                sim_df.loc[mask_win, 'peak_ret_pct'] / 100.0)

    # Losers/Open: All to Scale
    sim_df.loc[~mask_win, 'scale_pnl_dollars'] = sim_df.loc[~mask_win, 'sim_pnl']
    sim_df.loc[~mask_win, 'moonshot_pnl_dollars'] = 0.0


    # 6. Win/Loss Label
    # 1. Assign WIN/LOSS/PENDING Status
    conditions = [
        sim_df['status'] == 'OPEN',
        sim_df['sim_pnl'] > 0,
        sim_df['sim_pnl'] < 0
    ]
    choices = ['PENDING', 'WIN', 'LOSS']
    sim_df['win_loss'] = np.select(conditions, choices, default='EVEN')

    # 2. Calculate Max Drawdown % (Derived from lowest_price saved by backend)
    # If lowest_price is 0 or NaN, fallback to entry_price to show 0% DD
    safe_low = sim_df['lowest_price'].replace(0, np.nan).fillna(sim_df['entry_price'])

    mask_win = sim_df['status'] == 'SCALED'
    sim_df['scale_pnl_pct'] = np.where(mask_win, TP_PCT, sim_df['sim_ret_pct'])
    sim_df['moonshot_pnl_pct'] = np.where(mask_win, sim_df['peak_ret_pct'], 0.0)

    # This creates the column that was missing!
    sim_df['max_drawdown_pct'] = ((safe_low - sim_df['entry_price']) / sim_df['entry_price']) * 100.0

    # Clip to ensure DD is never positive
    sim_df['max_drawdown_pct'] = sim_df['max_drawdown_pct'].clip(upper=0.0)

    # 7. Unrealized
    mask_open = sim_df['status'] == 'OPEN'
    sim_df['unrealized_pnl_dollars'] = np.where(mask_open, sim_df['sim_pnl'], 0.0)

    # 8. Equity Curves
    sim_df = sim_df.sort_values('discord_timestamp', ascending=True)
    sim_df['equity_curve_scaled'] = initial_capital + sim_df['sim_pnl'].cumsum()
    sim_df['equity_curve_tp_exit'] = initial_capital + sim_df['tp_exit_pnl'].cumsum()
    sim_df['equity_curve_dbap'] = initial_capital + sim_df['dbap_pnl'].cumsum()

    sim_df = sim_df.sort_values('discord_timestamp', ascending=False)
    return sim_df


# --- RENDERERS ---

def render_todays_activity(df):
    est = pytz.timezone('US/Eastern')
    today_est = datetime.now(est).date()
    todays = df[df['Day'] == today_est]

    st.subheader(f"🔔 Today's Live Alerts ({today_est})")
    if todays.empty:
        st.info("No alerts yet today.")
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("Count", len(todays))
    c2.metric("PnL Today", f"${todays['sim_pnl'].sum():,.2f}")
    c3.metric("Winners", len(todays[todays['sim_pnl'] > 0]))

    st.dataframe(todays[['discord_timestamp', 'ticker', 'status', 'last_price', 'sim_pnl']], hide_index=True)
    st.divider()


def render_equity_chart(df):
    plot_df = df.sort_values('discord_timestamp', ascending=True)
    fig = go.Figure()

    fig.add_trace(go.Scatter(x=plot_df['discord_timestamp'], y=plot_df['equity_curve_scaled'],
                             name='Strategy (80/20)', line=dict(color='#00CC96', width=3)))
    fig.add_trace(go.Scatter(x=plot_df['discord_timestamp'], y=plot_df['equity_curve_tp_exit'],
                             name='Baseline (100% TP)', line=dict(color='white', dash='dot')))
    fig.add_trace(go.Scatter(x=plot_df['discord_timestamp'], y=plot_df['equity_curve_dbap'],
                             name="Don't Be A Pussy (Peak)", line=dict(color='#FF5733', dash='dash')))

    fig.update_layout(title="Equity Curves", template="plotly_dark", hovermode="x unified",
                      margin=dict(t=30, b=0, l=0, r=0))
    st.plotly_chart(fig, width='stretch')


def render_trade_finder(df):
    st.header("🔎 Smart Trade Finder")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        f_tick = st.multiselect("Ticker", df['ticker'].unique())
    with c2:
        f_stat = st.multiselect("Status", df['status'].unique())
    with c3:
        f_month = st.multiselect("Month", df['Month'].unique())
    with c4:
        f_win = st.checkbox("Winners Only")

    filt = df.copy()
    if f_tick: filt = filt[filt['ticker'].isin(f_tick)]
    if f_stat: filt = filt[filt['status'].isin(f_stat)]
    if f_month: filt = filt[filt['Month'].isin(f_month)]
    if f_win: filt = filt[filt['sim_pnl'] > 0]

    # --- PAGINATION AFTER FILTERING ---
    rows_per_page = 50  # You can change this number

    total_rows = len(filt)
    total_pages = max(1, (total_rows + rows_per_page - 1) // rows_per_page)

    col_left, col_mid, col_right = st.columns([1, 2, 1])
    with col_mid:
        page_num = st.number_input(
            "Page",
            min_value=1,
            max_value=total_pages,
            value=1,
            step=1,
            key="trade_finder_page"
        )

    start_idx = (page_num - 1) * rows_per_page
    end_idx = start_idx + rows_per_page
    page_df = filt.iloc[start_idx:end_idx]

    st.dataframe(
        page_df[[
            'discord_timestamp', 'ticker', 'expiration_date', 'strike', 'option_type',
            'entry_price', 'last_price', 'highest_price', 'lowest_price',
            'status', 'win_loss',
            'scale_pnl_pct', 'scale_pnl_dollars',
            'moonshot_pnl_pct', 'moonshot_pnl_dollars',
            'sim_pnl', 'sim_ret_pct', 'max_drawdown_pct'
        ]],
        hide_index=True,
        use_container_width=True,
        column_config={
            "discord_timestamp": st.column_config.DatetimeColumn("Date", format="MM-DD HH:mm"),
            "entry_price": st.column_config.NumberColumn("Entry", format="$%.2f"),
            "highest_price": st.column_config.NumberColumn("High", format="$%.2f"),
            "lowest_price": st.column_config.NumberColumn(
                "Pre-Scale Low",
                format="$%.2f",
                help="The lowest price recorded post-entry but BEFORE the trade hit its 20% scale target. This represents the maximum heat the trade took."
            ),
            "scale_pnl_dollars": st.column_config.NumberColumn("Core/Scale $", format="$%.0f"),
            "moonshot_pnl_dollars": st.column_config.NumberColumn("Moon $", format="$%.0f"),
            "sim_pnl": st.column_config.NumberColumn("Total PnL", format="$%.0f"),
            "sim_ret_pct": st.column_config.NumberColumn("Total %", format="%.1f%%"),
            "max_drawdown_pct": st.column_config.NumberColumn(
                "Max DD",
                format="%.1f%%",
                help="The maximum percentage drop from entry price to the Pre-Scale Low."
            ),
        }
    )

    st.caption(f"Showing rows {start_idx + 1}–{min(end_idx, total_rows)} of {total_rows} trades")


def main():
    with st.sidebar:
        st.title("🐋 Config")
        cap = st.number_input("Capital", value=100000)
        risk = st.slider("Risk %", 0.5, 5.0, 1.0)
        if st.button("Refresh"): st.cache_data.clear()

    raw_df = fetch_data()
    if raw_df.empty:
        st.warning("No Data Found.")
        return

    sim_df = run_simulation(raw_df, cap, risk)

    render_todays_activity(sim_df)

    tab1, tab2 = st.tabs(["📊 Portfolio", "🔍 Finder"])
    with tab1:
        # 1. Date Tracking Header
        first_trade_date = sim_df['discord_timestamp'].min().strftime('%B %d, %Y')
        st.caption(f"📈 Tracking performance since: **{first_trade_date}**")

        # 2. Key Metrics Row
        m1, m2, m3, m4 = st.columns(4)
        total_trades = len(sim_df)
        wins_df = sim_df[sim_df['win_loss'] == 'WIN']
        losses_df = sim_df[sim_df['win_loss'] == 'LOSS']
        open_df = sim_df[sim_df['win_loss'] == 'PENDING']

        wr = (len(wins_df) / total_trades * 100) if total_trades > 0 else 0

        m1.metric("Portfolio Value", f"${sim_df['equity_curve_scaled'].iloc[0]:,.0f}")
        m2.metric("Total Trades", total_trades)
        m3.metric("Win Rate", f"{wr:.1f}%")

        # Profit Factor Calculation
        total_win_val = wins_df['sim_pnl'].sum()
        total_loss_val = abs(losses_df['sim_pnl'].sum())

        if total_loss_val == 0:
            if total_win_val > 0:
                pf_display = "∞"  # Infinite (perfect so far)
                pf_help = "No losses recorded yet — infinite profit factor."
            else:
                pf_display = "N/A"
                pf_help = "No wins or losses yet."
        else:
            pf = total_win_val / total_loss_val
            pf_display = f"{pf:.2f}"
            pf_help = "Gross profit from winners divided by gross loss from losers."

        m4.metric("Profit Factor", pf_display, help=pf_help)

        st.divider()

        # 3. Returns Comparison (Strategy vs Baseline vs Max)
        st.subheader("Returns Comparison")
        r1, r2, r3 = st.columns(3)

        strat_total = sim_df['sim_pnl'].sum()
        base_total = sim_df['tp_exit_pnl'].sum()
        # Use the calculated PnL column which now includes the losses
        max_total = sim_df['dbap_pnl'].sum()

        r1.metric("Strategy (80/20)", f"${strat_total:,.0f}", help="Hybrid scaling strategy. Includes all losses.")
        r2.metric("Baseline (100% TP)", f"${base_total:,.0f}", help="Full exit at 20% target. Includes all losses.")
        r3.metric("Max Potential (Peak)", f"${max_total:,.0f}",
                  help="Selling every winner at its absolute peak, while accounting for losses on failed trades.")

        st.divider()

        # 4. Visual Analytics & Deep Dive
        col_chart, col_stats = st.columns([1, 1.5])

        with col_chart:
            # Win/Loss Pie Chart
            counts = sim_df['win_loss'].value_counts()
            fig_pie = px.pie(
                names=counts.index,
                values=counts.values,
                color=counts.index,
                color_discrete_map={'WIN': '#00CC96', 'LOSS': '#EF553B', 'PENDING': '#636EFA', 'EVEN': '#7F7F7F'},
                hole=0.4,
                title="Trade Distribution"
            )
            fig_pie.update_layout(showlegend=True, margin=dict(t=40, b=0, l=0, r=0), height=300)
            st.plotly_chart(fig_pie, use_container_width=True)

        with col_stats:
            st.markdown("### Strategy Deep Dive")

            # Row 1: The "Accounting" (Wins + Losses + Open = Total)
            s1, s2, s3 = st.columns(3)

            # Row 2: The "Why" (Breakdown of outcomes)
            s4, s5, s6 = st.columns(3)

            # Calculations
            runners = sim_df[sim_df['peak_ret_pct'] >= 100]
            runner_pct = (len(runners) / total_trades * 100) if total_trades > 0 else 0

            # Refined Loss Breakdown
            stop_oi_exits = sim_df[sim_df['status'] == 'STOP_OI']
            stop_oi_pct = (len(stop_oi_exits) / total_trades * 100) if total_trades > 0 else 0

            expired_worthless = sim_df[sim_df['status'] == 'EXPIRED']
            expired_pct = (len(expired_worthless) / total_trades * 100) if total_trades > 0 else 0

            open_pct = (len(open_df) / total_trades * 100) if total_trades > 0 else 0

            # Row 1: Totals
            s1.metric("Total Wins", f"{len(wins_df)}", f"{wr:.1f}%")
            s2.metric("Total Losses", f"{len(losses_df)}", f"{(len(losses_df) / total_trades * 100):.1f}%",
                      delta_color="inverse")
            s3.metric("Open Positions", f"{len(open_df)}", f"{open_pct:.1f}%")

            # Row 2: Details
            s4.metric("100%+ Runners", f"{len(runners)}", f"{runner_pct:.1f}% Home Run Rate")
            s5.metric("Stop OI Exits", f"{len(stop_oi_exits)}", f"{stop_oi_pct:.1f}% Risk Cut",
                      help="Trades closed because dropped below 80% of entry OI.")
            s6.metric("Expired Worthless", f"{len(expired_worthless)}", f"{expired_pct:.1f}% Max Loss",
                      delta_color="inverse", help="Trades that reached expiration without hitting the profit target.")

        st.divider()
        render_equity_chart(sim_df)

    with tab2:
        render_trade_finder(sim_df)


if __name__ == "__main__":
    main()