-- GENERATED FULL CONTEXT DUMP

-- Generated: 2026-01-04 23:52:56 HKT
-- ------------------------------

------------------------------
-- 1. CRON JOBS --
------------------------------
-- ID: 19 [ACTIVE] Schedule: 0 9 * * *

    -- Clean flow tables using native DATE logic
    DELETE FROM public.flow_1min WHERE expiration < (now() at time zone 'America/New_York' - interval '1 day')::date;
    DELETE FROM public.flow_5min WHERE expiration < (now() at time zone 'America/New_York' - interval '1 day')::date;
    
    -- Clean worker/snapshot tables
    DELETE FROM public.options_data WHERE expiration < (now() at time zone 'America/New_York' - interval '1 day')::date;
    DELETE FROM public.gex_data WHERE expiration < (now() at time zone 'America/New_York' - interval '1 day')::date;

    -- Standard timestamp-based cleanup
    DELETE FROM public.flow_1min WHERE timestamp < now() - interval '3 days';
    DELETE FROM public.flow_5min WHERE bucket < now() - interval '5 days';
    DELETE FROM public.intraday_flow WHERE updated_at < now() - interval '1 day';
    ;

-- ID: 9 [ACTIVE] Schedule: 0 */6 * * *

    SELECT
        net.http_get('https://sofortunegex.streamlit.app'),
        net.http_get('https://sofortuneyeetz.streamlit.app');
    ;

-- ID: 16 [ACTIVE] Schedule: */5 * * * *
SELECT public.snapshot_intraday_flow();

-- ID: 17 [ACTIVE] Schedule: 0 2 1 * *
SELECT prune_yeetz_history();;

------------------------------
-- 2. TABLES --
------------------------------
CREATE TABLE flow_1min (
    id bigint,
    timestamp timestamp with time zone,
    ticker text,
    strike numeric,
    right text,
    volume integer,
    net_premium_flow numeric,
    gamma_snapshot numeric,
    gex_impact numeric,
    net_quantity bigint,
    expiration date);

CREATE TABLE flow_5min (
    bucket timestamp with time zone,
    ticker text,
    strike numeric,
    gex_impact_sum numeric,
    net_quantity_sum numeric,
    expiration date);

CREATE TABLE flow_snapshots (
    id bigint,
    timestamp timestamp with time zone,
    ticker text,
    strike numeric,
    expiration date,
    gex_value numeric);

CREATE TABLE gamma_profiles (
    id integer,
    ticker text,
    days integer,
    profile_data jsonb,
    timestamp timestamp without time zone,
    profile jsonb);

CREATE TABLE gex_data (
    id integer,
    ticker text,
    expiration date,
    strike double precision,
    gex double precision,
    timestamp timestamp without time zone);

CREATE TABLE gex_levels (
    id integer,
    ticker text,
    strike double precision,
    gex_per_strike double precision,
    timestamp timestamp without time zone,
    volume numeric,
    open_interest numeric);

CREATE TABLE intraday_flow (
    ticker text,
    strike numeric,
    expiration date,
    net_flow numeric,
    updated_at timestamp with time zone,
    net_gamma_flow double precision,
    net_quantity numeric,
    net_flow_spot numeric,
    option_right text);

CREATE TABLE metadata (
    id integer,
    ticker text,
    spot numeric,
    atm_strike numeric,
    spot_source text,
    spot_fetch_time text,
    options_fetch_time text,
    total_contracts integer,
    strikes_above integer,
    strikes_below integer,
    updated_at timestamp without time zone);

CREATE TABLE options_data (
    id integer,
    ticker text,
    expiration date,
    strike numeric,
    type text,
    bid numeric,
    ask numeric,
    volume integer,
    open_interest integer,
    gamma numeric,
    gex_raw numeric,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    iv numeric,
    delta numeric,
    theta numeric,
    vega numeric,
    rho numeric,
    iv_source text);

CREATE TABLE risk_free_rates (
    id uuid,
    date text,
    rate double precision,
    created_at timestamp with time zone,
    source text,
    fetched_at timestamp with time zone);

CREATE TABLE vanna_adjusted_gex (
    id integer,
    ticker text,
    strike double precision,
    adjusted_gex double precision,
    timestamp timestamp without time zone);

CREATE TABLE vanna_exposure (
    id integer,
    ticker text,
    strike double precision,
    vanna_exposure double precision,
    timestamp timestamp without time zone);

CREATE TABLE view_anchored_markers (
    ticker text,
    strike numeric,
    live_gex numeric,
    gex_15m numeric,
    gex_30m numeric,
    gex_60m numeric,
    last_anchor_update timestamp with time zone);

CREATE TABLE view_position_matrix (
    ticker text,
    strike numeric,
    expiration date,
    net_call_vol numeric,
    net_put_vol numeric,
    total_activity numeric);

CREATE TABLE whale_alerts (
    id bigint,
    ticker text,
    strike numeric,
    option_type text,
    expiration_date date,
    entry_price numeric,
    entry_size integer,
    entry_oi integer,
    profit_target numeric,
    stop_oi_level integer,
    status text,
    win_timestamp timestamp with time zone,
    highest_price numeric,
    discord_timestamp timestamp with time zone,
    created_at timestamp with time zone,
    entry_iv numeric,
    entry_delta numeric,
    risk_pct_used numeric,
    equity_at_entry numeric,
    position_size_dollars numeric,
    tp_hit_date timestamp with time zone,
    tp_hit_price numeric,
    realized_scale_pnl numeric,
    moonshot_cost_basis numeric,
    realized_moonshot_pnl numeric,
    close_date timestamp with time zone,
    close_price numeric,
    close_reason text,
    last_price numeric,
    last_iv numeric,
    last_oi numeric,
    discord_message_id text,
    entry_vol_oi_ratio numeric,
    entry_interval_vol integer,
    entry_premium numeric,
    final_sim_pnl_pct real,
    final_tp_pnl_pct real,
    lowest_price numeric,
    final_dbap_pnl_pct double precision);

CREATE TABLE whale_performance (
    id bigint,
    alert_id bigint,
    date date,
    price_high numeric,
    price_close numeric,
    current_oi integer,
    is_win boolean,
    created_at timestamp with time zone,
    implied_volatility numeric,
    price_low numeric);

CREATE TABLE worker_status (
    id integer,
    last_run text,
    status text
);

------------------------------
-- 3. VIEWS --
------------------------------
-- View: view_anchored_markers
CREATE OR REPLACE VIEW view_anchored_markers AS
 WITH anchor_calc AS (
         SELECT (date_trunc('hour'::text, now()) + ('00:15:00'::interval * floor((date_part('minute'::text, now()) / (15)::double precision)))) AS anchor_time
        ), targets AS (
         SELECT a.anchor_time,
            (a.anchor_time - '00:15:00'::interval) AS t15,
            (a.anchor_time - '00:30:00'::interval) AS t30,
            (a.anchor_time - '01:00:00'::interval) AS t60
           FROM anchor_calc a
        )
 SELECT live.ticker,
    live.strike,
    live.net_flow AS live_gex,
    COALESCE(( SELECT s.gex_value
           FROM flow_snapshots s
          WHERE ((s.ticker = live.ticker) AND (s.strike = live.strike) AND ((s."timestamp" >= (t.t15 - '00:02:00'::interval)) AND (s."timestamp" <= (t.t15 + '00:02:00'::interval))))
         LIMIT 1), (0)::numeric) AS gex_15m,
    COALESCE(( SELECT s.gex_value
           FROM flow_snapshots s
          WHERE ((s.ticker = live.ticker) AND (s.strike = live.strike) AND ((s."timestamp" >= (t.t30 - '00:02:00'::interval)) AND (s."timestamp" <= (t.t30 + '00:02:00'::interval))))
         LIMIT 1), (0)::numeric) AS gex_30m,
    COALESCE(( SELECT s.gex_value
           FROM flow_snapshots s
          WHERE ((s.ticker = live.ticker) AND (s.strike = live.strike) AND ((s."timestamp" >= (t.t60 - '00:02:00'::interval)) AND (s."timestamp" <= (t.t60 + '00:02:00'::interval))))
         LIMIT 1), (0)::numeric) AS gex_60m,
    t.anchor_time AS last_anchor_update
   FROM (intraday_flow live
     CROSS JOIN targets t);

-- View: view_position_matrix
CREATE OR REPLACE VIEW view_position_matrix AS
 SELECT ticker,
    strike,
    expiration,
    sum(
        CASE
            WHEN (option_right = 'CALL'::text) THEN net_quantity
            ELSE (0)::numeric
        END) AS net_call_vol,
    sum(
        CASE
            WHEN (option_right = 'PUT'::text) THEN net_quantity
            ELSE (0)::numeric
        END) AS net_put_vol,
    sum(abs(net_quantity)) AS total_activity
   FROM intraday_flow
  GROUP BY ticker, strike, expiration;

------------------------------
-- 4. FUNCTIONS --
------------------------------
-- Function: prune_yeetz_history
CREATE OR REPLACE FUNCTION prune_yeetz_history AS $$

BEGIN
  -- Keep only 180 days of daily performance data for historical audits
  DELETE FROM public.whale_performance 
  WHERE date < NOW() - INTERVAL '180 days';

  -- Optional: Clean up older alerts if you only want a rolling 1-year record
  -- DELETE FROM public.whale_alerts WHERE discord_timestamp < NOW() - INTERVAL '365 days';
END;

$$;

-- Function: snapshot_intraday_flow
CREATE OR REPLACE FUNCTION snapshot_intraday_flow AS $$

BEGIN
    -- 1. Insert the new snapshot (Existing Logic)
    INSERT INTO flow_snapshots (timestamp, ticker, strike, expiration, gex_value)
    SELECT 
        date_trunc('minute', NOW()) as timestamp, 
        ticker,
        strike,
        expiration, 
        net_flow
    FROM intraday_flow;

    -- 2. Cleanup: Delete snapshots older than 24 hours (New Logic)
    -- This keeps the table light, holding only what is needed for the 
    -- 15m/30m/60m comparison view.
    DELETE FROM flow_snapshots 
    WHERE timestamp < (now() - interval '24 hours');
END;

$$;

-- Function: capture_5min_snapshot
CREATE OR REPLACE FUNCTION capture_5min_snapshot AS $$

BEGIN
    INSERT INTO flow_snapshots (snapshot_time, ticker, strike, net_flow)
    SELECT 
        -- Round current time down to nearest 5 minutes
        date_bin('5 minutes', now(), TIMESTAMP '2000-01-01'), 
        ticker, 
        strike, 
        net_flow -- Capturing the LIVE TOTAL
    FROM intraday_flow
    ON CONFLICT (snapshot_time, ticker, strike) 
    DO UPDATE SET net_flow = EXCLUDED.net_flow; 
    
    -- Cleanup: Keep only last 24 hours of snapshots to keep table light
    DELETE FROM flow_snapshots 
    WHERE snapshot_time < (now() - interval '24 hours');
END;

$$;

-- Function: get_tables
CREATE OR REPLACE FUNCTION get_tables AS $$

SELECT json_agg(row_to_json(t)) FROM (
  SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'
) t;

$$;

-- Function: update_updated_at_column
CREATE OR REPLACE FUNCTION update_updated_at_column AS $$

BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;

$$;

