from datetime import datetime
import pytz
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from supabase import create_client
import numpy as np
import calendar

# --- IMPORT CONFIG ---
try:
    from yeetz_config import TP_PCT, SCALE_PCT, STOP_OI_PCT
except ImportError:
    # Defaults if config not found
    TP_PCT = 20.0
    SCALE_PCT = 80.0
    STOP_OI_PCT = 80.0

# --- CONFIG ---
st.set_page_config(
    page_title="Flow-Life Tracker",
    layout="wide",
    page_icon="🌊",
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
    # UPDATED: Query the flow_life schema
    resp = supabase.schema("flow_life").table("trades").select("*").execute()
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
    # Note: final_dbap_pnl_pct might be missing in older rows if we just added it
    if 'final_dbap_pnl_pct' not in sim_df.columns:
         sim_df['final_dbap_pnl_pct'] = np.nan
         
    sim_df['dbap_ret_pct'] = sim_df['final_dbap_pnl_pct'].fillna(
        pd.Series(np.where(sim_df['status'].isin(['SCALED', 'SCALED_EXP']), sim_df['peak_ret_pct'], sim_df['sim_ret_pct']),
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

    mask_win = sim_df['status'].isin(['SCALED', 'SCALED_EXP'])

    # Winners: Split PnL
    sim_df.loc[mask_win, 'scale_pnl_dollars'] = (sim_df.loc[mask_win, 'pos_size_dollars'] * portion_scale) * (
                TP_PCT / 100.0)
    sim_df.loc[mask_win, 'moonshot_pnl_dollars'] = (sim_df.loc[mask_win, 'pos_size_dollars'] * portion_moon) * (
                sim_df.loc[mask_win, 'peak_ret_pct'] / 100.0)

    # Losers/Open: All to Scale
    sim_df.loc[~mask_win, 'scale_pnl_dollars'] = sim_df.loc[~mask_win, 'sim_pnl']
    sim_df.loc[~mask_win, 'moonshot_pnl_dollars'] = 0.0


    # 6. Win/Loss Label
    conditions = [
        sim_df['status'] == 'OPEN',
        sim_df['sim_pnl'] > 0,
        sim_df['sim_pnl'] < 0
    ]
    choices = ['PENDING', 'WIN', 'LOSS']
    sim_df['win_loss'] = np.select(conditions, choices, default='EVEN')

    safe_low = sim_df['lowest_price'].replace(0, np.nan).fillna(sim_df['entry_price'])

    sim_df['scale_pnl_pct'] = np.where(mask_win, TP_PCT, sim_df['sim_ret_pct'])
    sim_df['moonshot_pnl_pct'] = np.where(mask_win, sim_df['peak_ret_pct'], 0.0)

    sim_df['max_drawdown_pct'] = ((safe_low - sim_df['entry_price']) / sim_df['entry_price']) * 100.0
    sim_df['max_drawdown_pct'] = sim_df['max_drawdown_pct'].clip(upper=0.0)

    # 7. Realized vs Unrealized Split
    sim_df['realized_pnl_dollars'] = sim_df['sim_pnl']
    sim_df['unrealized_pnl_dollars'] = 0.0

    today_date = datetime.now().date()
    entry_dates = sim_df['discord_timestamp'].dt.date
    actual_close_dates = sim_df['close_date'].dt.date
    final_close_dates = actual_close_dates.fillna(today_date)

    sim_df['days_held'] = (final_close_dates - entry_dates).apply(lambda x: x.days)
    sim_df['days_held'] = sim_df['days_held'].clip(lower=1)

    mask_open = sim_df['status'] == 'OPEN'
    sim_df.loc[mask_open, 'unrealized_pnl_dollars'] = sim_df.loc[mask_open, 'sim_pnl']
    sim_df.loc[mask_open, 'realized_pnl_dollars'] = 0.0
    
    mask_active_scaled = sim_df['status'] == 'SCALED'
    sim_df.loc[mask_active_scaled, 'realized_pnl_dollars'] = sim_df.loc[mask_active_scaled, 'scale_pnl_dollars']
    sim_df.loc[mask_active_scaled, 'unrealized_pnl_dollars'] = sim_df.loc[mask_active_scaled, 'moonshot_pnl_dollars']

    # 8. Moonshot Contribution
    sim_df['moonshot_contribution'] = sim_df['sim_pnl'] - sim_df['tp_exit_pnl']

    # 9. Equity Curves
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

    st.subheader(f"🔔 Today's Flow ({today_est})")
    if todays.empty:
        st.info("No flow yet today.")
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
                             name="Max Potential", line=dict(color='#FF5733', dash='dash')))

    fig.update_layout(title="Equity Curves", template="plotly_dark", hovermode="x unified",
                      margin=dict(t=30, b=0, l=0, r=0))
    st.plotly_chart(fig, key="equity_chart", width="stretch")


@st.fragment
def render_trade_finder(df, key_suffix=""):
    st.header("🔎 Flow Finder")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        f_tick = st.multiselect("Ticker", df['ticker'].unique(), key=f"tick_{key_suffix}")
    with c2:
        f_stat = st.multiselect("Status", df['status'].unique(), key=f"stat_{key_suffix}")
    with c3:
        f_month = st.multiselect("Month", df['Month'].unique(), key=f"mth_{key_suffix}")
    with c4:
        f_win = st.checkbox("Winners Only", key=f"win_{key_suffix}")

    filt = df.copy()
    if f_tick: filt = filt[filt['ticker'].isin(f_tick)]
    if f_stat: filt = filt[filt['status'].isin(f_stat)]
    if f_month: filt = filt[filt['Month'].isin(f_month)]
    if f_win: filt = filt[filt['sim_pnl'] > 0]

    st.dataframe(
        filt[[
            'discord_timestamp', 'ticker', 'expiration_date', 'strike', 'option_type',
            'entry_price', 'last_price', 'highest_price', 'lowest_price',
            'status', 'win_loss',
            'scale_pnl_dollars', 'moonshot_pnl_dollars',
            'sim_pnl', 'sim_ret_pct', 'peak_ret_pct', 'max_drawdown_pct'
        ]],
        hide_index=True,
        use_container_width=True,
        column_config={
            "discord_timestamp": st.column_config.DatetimeColumn("Date", format="MM-DD HH:mm"),
            "entry_price": st.column_config.NumberColumn("Entry", format="$%.2f"),
            "highest_price": st.column_config.NumberColumn("High", format="$%.2f"),
            "lowest_price": st.column_config.NumberColumn("Low", format="$%.2f"),
            "scale_pnl_dollars": st.column_config.NumberColumn("Scale $", format="$%.0f"),
            "moonshot_pnl_dollars": st.column_config.NumberColumn("Moon $", format="$%.0f"),
            "sim_pnl": st.column_config.NumberColumn("Total PnL", format="$%.0f"),
            "sim_ret_pct": st.column_config.NumberColumn("Total %", format="%.1f%%"),
            "peak_ret_pct": st.column_config.NumberColumn("Max Gain %", format="%.1f%%"),
            "max_drawdown_pct": st.column_config.NumberColumn("Max DD", format="%.1f%%"),
        }
    )
    st.caption(f"Showing {len(filt)} trades")


def main():
    with st.sidebar:
        st.title("🌊 Flow-Life")
        cap = st.number_input("Capital", value=100000)
        risk = st.slider("Risk %", 0.5, 5.0, 1.0)
        
        st.divider()
        st.subheader("📅 Time Filter")
        time_filter = st.selectbox(
            "Period",
            ["All Time", "Year to Date", "Last 30 Days", "Last 90 Days", "Custom Range"]
        )
        
        custom_start = None
        custom_end = None
        if time_filter == "Custom Range":
            c1, c2 = st.columns(2)
            custom_start = c1.date_input("Start", value=datetime.now().date())
            custom_end = c2.date_input("End", value=datetime.now().date())
        
        st.divider()
        if st.button("Refresh"): st.cache_data.clear()

    raw_df = fetch_data()
    if raw_df.empty:
        st.warning("No Flow Data Found in 'flow_life' schema.")
        return

    sim_df = run_simulation(raw_df, cap, risk)
    
    # Apply Time Filters
    if time_filter != "All Time":
        sim_df['date_only'] = sim_df['discord_timestamp'].dt.date
        today = datetime.now().date()
        if time_filter == "Year to Date":
            start_date = datetime(today.year, 1, 1).date()
            sim_df = sim_df[sim_df['date_only'] >= start_date]
        elif time_filter == "Last 30 Days":
            start_date = today - pd.Timedelta(days=30)
            sim_df = sim_df[sim_df['date_only'] >= start_date]
        elif time_filter == "Last 90 Days":
            start_date = today - pd.Timedelta(days=90)
            sim_df = sim_df[sim_df['date_only'] >= start_date]
        elif time_filter == "Custom Range" and custom_start and custom_end:
            sim_df = sim_df[(sim_df['date_only'] >= custom_start) & (sim_df['date_only'] <= custom_end)]

    render_todays_activity(sim_df)

    tab1, tab2, tab3 = st.tabs(["📊 Portfolio", "🗓️ Monthly", "🔍 Finder"])

    with tab1:
        st.caption(f"📈 Tracking flow since: **{raw_df['discord_timestamp'].min().strftime('%B %d, %Y')}**")
        
        m1, m2, m3, m4 = st.columns(4)
        total_trades = len(sim_df)
        wins_df = sim_df[sim_df['win_loss'] == 'WIN']
        losses_df = sim_df[sim_df['win_loss'] == 'LOSS']
        wr = (len(wins_df) / total_trades * 100) if total_trades > 0 else 0

        m1.metric("Portfolio Value", f"${sim_df['equity_curve_scaled'].iloc[0]:,.0f}")
        m2.metric("Total Trades", total_trades)
        m3.metric("Win Rate", f"{wr:.1f}%")
        
        total_win_val = wins_df['sim_pnl'].sum()
        total_loss_val = abs(losses_df['sim_pnl'].sum())
        pf = (total_win_val / total_loss_val) if total_loss_val > 0 else 0
        m4.metric("Profit Factor", f"{pf:.2f}")
        
        st.divider()
        render_equity_chart(sim_df)

    with tab2:
        st.header("🗓️ Monthly Performance")
        monthly = sim_df.groupby('Month').agg({
            'ticker': 'count',
            'sim_pnl': 'sum',
            'win_loss': lambda x: (x == 'WIN').sum(),
            'days_held': 'mean',
            'max_drawdown_pct': 'mean'
        }).rename(columns={'ticker': 'Count', 'sim_pnl': 'PnL', 'days_held': 'Hold', 'max_drawdown_pct': 'DD'})
        
        monthly['Win Rate'] = (monthly['win_loss'] / monthly['Count']) * 100
        monthly = monthly.drop(columns=['win_loss'])
        monthly = monthly.sort_index(ascending=False)
        
        st.dataframe(monthly.style.background_gradient(subset=['PnL'], cmap='RdYlGn'), use_container_width=True)

    with tab3:
        render_trade_finder(sim_df, key_suffix="tab3")

if __name__ == "__main__":
    main()
