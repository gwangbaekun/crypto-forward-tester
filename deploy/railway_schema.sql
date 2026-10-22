--
-- PostgreSQL database dump
--

\restrict tl7HxO0kHlVdhU8Et2WBdGgOAUBLZ4uQGKwWd5a5yPoUzjSHWTRrcbm3chCqYWl

-- Dumped from database version 18.6 (Debian 18.6-1.pgdg13+2)
-- Dumped by pg_dump version 18.6 (Debian 18.6-1.pgdg13+2)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: access_passes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.access_passes (
    id integer NOT NULL,
    code_hash character varying(64) NOT NULL,
    code_hint character varying(16) NOT NULL,
    label character varying(120) NOT NULL,
    created_at timestamp without time zone NOT NULL,
    expires_at timestamp without time zone NOT NULL,
    revoked_at timestamp without time zone,
    first_used_at timestamp without time zone,
    last_used_at timestamp without time zone,
    use_count integer NOT NULL,
    sliding_hours integer,
    max_expires_at timestamp without time zone,
    renew_count integer NOT NULL
);


--
-- Name: access_passes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.access_passes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: access_passes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.access_passes_id_seq OWNED BY public.access_passes.id;


--
-- Name: ctrader_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ctrader_tokens (
    key character varying(32) NOT NULL,
    access_token text NOT NULL,
    refresh_token text,
    updated_at timestamp without time zone NOT NULL
);


--
-- Name: drawdown_ledger; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.drawdown_ledger (
    id integer NOT NULL,
    payload_json text NOT NULL,
    updated_at timestamp without time zone NOT NULL
);


--
-- Name: drawdown_ledger_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.drawdown_ledger_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: drawdown_ledger_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.drawdown_ledger_id_seq OWNED BY public.drawdown_ledger.id;


--
-- Name: forward_trades; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.forward_trades (
    id integer NOT NULL,
    symbol character varying(32) NOT NULL,
    side character varying(16) NOT NULL,
    entry_price double precision NOT NULL,
    opened_at timestamp without time zone NOT NULL,
    entry_state text,
    entry_report_text text,
    trigger_tfs character varying(64) NOT NULL,
    confidence integer NOT NULL,
    direction_detail character varying(128),
    entry_source character varying(16),
    sl_price double precision,
    tp1_price double precision,
    tp2_price double precision,
    sl_history text,
    tp1_history text,
    position_meta text,
    status character varying(32) NOT NULL,
    exit_price double precision,
    pnl_pct double precision,
    pnl_pct_net double precision,
    closed_at timestamp without time zone,
    duration_min double precision,
    close_note character varying(256),
    strategy character varying(32)
);


--
-- Name: forward_trades_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.forward_trades_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: forward_trades_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.forward_trades_id_seq OWNED BY public.forward_trades.id;


--
-- Name: gamma_wall_ledger; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.gamma_wall_ledger (
    id integer NOT NULL,
    blob jsonb NOT NULL,
    updated_at timestamp with time zone
);


--
-- Name: polymarket_fade_curves; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.polymarket_fade_curves (
    condition_id character varying(128) NOT NULL,
    pts_json text NOT NULL,
    chart_json text,
    fetched_at timestamp without time zone NOT NULL
);


--
-- Name: polymarket_fade_positions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.polymarket_fade_positions (
    id integer NOT NULL,
    condition_id character varying(128) NOT NULL,
    question character varying(512),
    no_token_id character varying(128),
    p0 double precision NOT NULL,
    entry_px double precision NOT NULL,
    target_px double precision NOT NULL,
    stop_px double precision NOT NULL,
    entry_ts integer NOT NULL,
    timeout_ts integer NOT NULL,
    status character varying(16) NOT NULL,
    exit_px double precision,
    exit_ts integer,
    exit_reason character varying(16),
    ret_pct double precision,
    shares double precision,
    entry_usd double precision,
    order_id character varying(128),
    order_status character varying(16),
    addon_count integer NOT NULL,
    last_leg_px double precision,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: polymarket_fade_positions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.polymarket_fade_positions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: polymarket_fade_positions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.polymarket_fade_positions_id_seq OWNED BY public.polymarket_fade_positions.id;


--
-- Name: polymarket_fade_watch; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.polymarket_fade_watch (
    condition_id character varying(128) NOT NULL,
    question character varying(512),
    yes_token_id character varying(128),
    no_token_id character varying(128),
    volume_usd double precision,
    start_ts integer,
    end_ts integer,
    status character varying(16) NOT NULL,
    added_at timestamp without time zone NOT NULL
);


--
-- Name: polymarket_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.polymarket_jobs (
    id integer NOT NULL,
    job_type character varying(64) NOT NULL,
    status character varying(16) NOT NULL,
    payload text,
    result text,
    error text,
    created_at timestamp without time zone NOT NULL,
    started_at timestamp without time zone,
    finished_at timestamp without time zone
);


--
-- Name: polymarket_jobs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.polymarket_jobs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: polymarket_jobs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.polymarket_jobs_id_seq OWNED BY public.polymarket_jobs.id;


--
-- Name: polymarket_live_markets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.polymarket_live_markets (
    condition_id character varying(128) NOT NULL,
    strategy character varying(16) NOT NULL,
    question character varying(512),
    slug character varying(256),
    yes_price double precision,
    no_price double precision,
    volume_usd double precision,
    end_ts integer,
    yes_token_id character varying(128),
    no_token_id character varying(128),
    updated_at timestamp without time zone NOT NULL
);


--
-- Name: polymarket_signals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.polymarket_signals (
    id integer NOT NULL,
    strategy character varying(32) NOT NULL,
    condition_id character varying(128),
    question character varying(512),
    signal_type character varying(32),
    yes_price double precision,
    no_price double precision,
    pair_cost double precision,
    divergence double precision,
    side character varying(8),
    volume_usd double precision,
    hours_to_end double precision,
    yes_token_id character varying(128),
    no_token_id character varying(128),
    event_end_ts integer,
    is_resolved integer NOT NULL,
    actual_outcome character varying(8),
    actual_pnl double precision,
    resolved_at timestamp without time zone,
    created_at timestamp without time zone NOT NULL,
    poly_order_id character varying(128),
    order_status character varying(16),
    order_error character varying(256)
);


--
-- Name: polymarket_signals_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.polymarket_signals_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: polymarket_signals_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.polymarket_signals_id_seq OWNED BY public.polymarket_signals.id;


--
-- Name: us_etf_daily; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.us_etf_daily (
    date date,
    open double precision,
    high double precision,
    low double precision,
    close double precision,
    adj_close double precision,
    volume bigint,
    symbol text
);


--
-- Name: us_options_chain; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.us_options_chain (
    option text CONSTRAINT us_options_chain_latest_option_not_null NOT NULL,
    bid double precision,
    ask double precision,
    last_trade_price double precision,
    iv double precision,
    delta double precision,
    gamma double precision,
    open_interest double precision,
    volume double precision,
    underlying_price double precision,
    expiry date,
    strike double precision,
    option_type text,
    underlying text CONSTRAINT us_options_chain_latest_underlying_not_null NOT NULL,
    snapshot_ts timestamp with time zone CONSTRAINT us_options_chain_latest_snapshot_ts_not_null NOT NULL
);


--
-- Name: us_options_chain_pending; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.us_options_chain_pending (
    session date NOT NULL,
    option text NOT NULL,
    snapshot_ts timestamp with time zone NOT NULL,
    expiry date NOT NULL,
    strike double precision,
    option_type text,
    open_interest double precision,
    gamma double precision,
    underlying_price double precision
);


--
-- Name: us_options_chain_pending_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.us_options_chain_pending_snapshots (
    session date NOT NULL,
    snapshot_ts timestamp with time zone NOT NULL,
    underlying_price double precision
);


--
-- Name: value_scan_closed_lots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.value_scan_closed_lots (
    id integer NOT NULL,
    trade_id integer NOT NULL,
    lot_date character varying(10) NOT NULL,
    price double precision,
    unit_usd double precision
);


--
-- Name: value_scan_closed_lots_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.value_scan_closed_lots_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: value_scan_closed_lots_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.value_scan_closed_lots_id_seq OWNED BY public.value_scan_closed_lots.id;


--
-- Name: value_scan_closed_trades; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.value_scan_closed_trades (
    id integer NOT NULL,
    market character varying(16) NOT NULL,
    symbol character varying(32) NOT NULL,
    name character varying(256) NOT NULL,
    sector character varying(128),
    first_entry_date character varying(10) NOT NULL,
    exit_date character varying(10) NOT NULL,
    exit_price double precision,
    exit_reason character varying(32),
    invested_usd double precision,
    pnl_pct double precision,
    pnl_usd double precision,
    hold_days integer,
    per double precision,
    sector_median double precision,
    eps double precision,
    fwd_eps double precision,
    pbr double precision,
    closed_at timestamp without time zone NOT NULL
);


--
-- Name: value_scan_closed_trades_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.value_scan_closed_trades_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: value_scan_closed_trades_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.value_scan_closed_trades_id_seq OWNED BY public.value_scan_closed_trades.id;


--
-- Name: value_scan_lots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.value_scan_lots (
    id integer NOT NULL,
    position_id integer NOT NULL,
    lot_date character varying(10) NOT NULL,
    price double precision,
    unit_usd double precision NOT NULL
);


--
-- Name: value_scan_lots_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.value_scan_lots_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: value_scan_lots_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.value_scan_lots_id_seq OWNED BY public.value_scan_lots.id;


--
-- Name: value_scan_market_meta; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.value_scan_market_meta (
    market character varying(16) NOT NULL,
    last_scan_at timestamp without time zone,
    last_trading_date character varying(10),
    last_scanned integer,
    last_buy integer,
    last_sell integer,
    last_new_entries integer,
    last_new_exits integer
);


--
-- Name: value_scan_positions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.value_scan_positions (
    id integer NOT NULL,
    market character varying(16) NOT NULL,
    symbol character varying(32) NOT NULL,
    name character varying(256) NOT NULL,
    sector character varying(128),
    first_entry_date character varying(10) NOT NULL,
    invested_usd double precision NOT NULL,
    per double precision,
    sector_median double precision,
    eps double precision,
    fwd_eps double precision,
    pbr double precision,
    updated_at timestamp without time zone NOT NULL
);


--
-- Name: value_scan_positions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.value_scan_positions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: value_scan_positions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.value_scan_positions_id_seq OWNED BY public.value_scan_positions.id;


--
-- Name: value_scan_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.value_scan_snapshots (
    id integer NOT NULL,
    date character varying(10) NOT NULL,
    market character varying(16) NOT NULL,
    rows_json text NOT NULL,
    saved_at timestamp without time zone NOT NULL
);


--
-- Name: value_scan_snapshots_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.value_scan_snapshots_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: value_scan_snapshots_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.value_scan_snapshots_id_seq OWNED BY public.value_scan_snapshots.id;


--
-- Name: access_passes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.access_passes ALTER COLUMN id SET DEFAULT nextval('public.access_passes_id_seq'::regclass);


--
-- Name: drawdown_ledger id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.drawdown_ledger ALTER COLUMN id SET DEFAULT nextval('public.drawdown_ledger_id_seq'::regclass);


--
-- Name: forward_trades id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.forward_trades ALTER COLUMN id SET DEFAULT nextval('public.forward_trades_id_seq'::regclass);


--
-- Name: polymarket_fade_positions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.polymarket_fade_positions ALTER COLUMN id SET DEFAULT nextval('public.polymarket_fade_positions_id_seq'::regclass);


--
-- Name: polymarket_jobs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.polymarket_jobs ALTER COLUMN id SET DEFAULT nextval('public.polymarket_jobs_id_seq'::regclass);


--
-- Name: polymarket_signals id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.polymarket_signals ALTER COLUMN id SET DEFAULT nextval('public.polymarket_signals_id_seq'::regclass);


--
-- Name: value_scan_closed_lots id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.value_scan_closed_lots ALTER COLUMN id SET DEFAULT nextval('public.value_scan_closed_lots_id_seq'::regclass);


--
-- Name: value_scan_closed_trades id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.value_scan_closed_trades ALTER COLUMN id SET DEFAULT nextval('public.value_scan_closed_trades_id_seq'::regclass);


--
-- Name: value_scan_lots id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.value_scan_lots ALTER COLUMN id SET DEFAULT nextval('public.value_scan_lots_id_seq'::regclass);


--
-- Name: value_scan_positions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.value_scan_positions ALTER COLUMN id SET DEFAULT nextval('public.value_scan_positions_id_seq'::regclass);


--
-- Name: value_scan_snapshots id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.value_scan_snapshots ALTER COLUMN id SET DEFAULT nextval('public.value_scan_snapshots_id_seq'::regclass);


--
-- Name: access_passes access_passes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.access_passes
    ADD CONSTRAINT access_passes_pkey PRIMARY KEY (id);


--
-- Name: ctrader_tokens ctrader_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ctrader_tokens
    ADD CONSTRAINT ctrader_tokens_pkey PRIMARY KEY (key);


--
-- Name: drawdown_ledger drawdown_ledger_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.drawdown_ledger
    ADD CONSTRAINT drawdown_ledger_pkey PRIMARY KEY (id);


--
-- Name: forward_trades forward_trades_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.forward_trades
    ADD CONSTRAINT forward_trades_pkey PRIMARY KEY (id);


--
-- Name: gamma_wall_ledger gamma_wall_ledger_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gamma_wall_ledger
    ADD CONSTRAINT gamma_wall_ledger_pkey PRIMARY KEY (id);


--
-- Name: polymarket_fade_curves polymarket_fade_curves_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.polymarket_fade_curves
    ADD CONSTRAINT polymarket_fade_curves_pkey PRIMARY KEY (condition_id);


--
-- Name: polymarket_fade_positions polymarket_fade_positions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.polymarket_fade_positions
    ADD CONSTRAINT polymarket_fade_positions_pkey PRIMARY KEY (id);


--
-- Name: polymarket_fade_watch polymarket_fade_watch_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.polymarket_fade_watch
    ADD CONSTRAINT polymarket_fade_watch_pkey PRIMARY KEY (condition_id);


--
-- Name: polymarket_jobs polymarket_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.polymarket_jobs
    ADD CONSTRAINT polymarket_jobs_pkey PRIMARY KEY (id);


--
-- Name: polymarket_live_markets polymarket_live_markets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.polymarket_live_markets
    ADD CONSTRAINT polymarket_live_markets_pkey PRIMARY KEY (condition_id);


--
-- Name: polymarket_signals polymarket_signals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.polymarket_signals
    ADD CONSTRAINT polymarket_signals_pkey PRIMARY KEY (id);


--
-- Name: value_scan_positions uq_value_scan_pos_market_symbol; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.value_scan_positions
    ADD CONSTRAINT uq_value_scan_pos_market_symbol UNIQUE (market, symbol);


--
-- Name: value_scan_snapshots uq_vs_snapshot_date_market; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.value_scan_snapshots
    ADD CONSTRAINT uq_vs_snapshot_date_market UNIQUE (date, market);


--
-- Name: us_options_chain us_options_chain_latest_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.us_options_chain
    ADD CONSTRAINT us_options_chain_latest_pkey PRIMARY KEY (option);


--
-- Name: us_options_chain_pending us_options_chain_pending_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.us_options_chain_pending
    ADD CONSTRAINT us_options_chain_pending_pkey PRIMARY KEY (session, option);


--
-- Name: us_options_chain_pending_snapshots us_options_chain_pending_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.us_options_chain_pending_snapshots
    ADD CONSTRAINT us_options_chain_pending_snapshots_pkey PRIMARY KEY (session);


--
-- Name: value_scan_closed_lots value_scan_closed_lots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.value_scan_closed_lots
    ADD CONSTRAINT value_scan_closed_lots_pkey PRIMARY KEY (id);


--
-- Name: value_scan_closed_trades value_scan_closed_trades_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.value_scan_closed_trades
    ADD CONSTRAINT value_scan_closed_trades_pkey PRIMARY KEY (id);


--
-- Name: value_scan_lots value_scan_lots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.value_scan_lots
    ADD CONSTRAINT value_scan_lots_pkey PRIMARY KEY (id);


--
-- Name: value_scan_market_meta value_scan_market_meta_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.value_scan_market_meta
    ADD CONSTRAINT value_scan_market_meta_pkey PRIMARY KEY (market);


--
-- Name: value_scan_positions value_scan_positions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.value_scan_positions
    ADD CONSTRAINT value_scan_positions_pkey PRIMARY KEY (id);


--
-- Name: value_scan_snapshots value_scan_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.value_scan_snapshots
    ADD CONSTRAINT value_scan_snapshots_pkey PRIMARY KEY (id);


--
-- Name: ix_access_passes_code_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_access_passes_code_hash ON public.access_passes USING btree (code_hash);


--
-- Name: ix_access_passes_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_access_passes_created_at ON public.access_passes USING btree (created_at);


--
-- Name: ix_access_passes_expires_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_access_passes_expires_at ON public.access_passes USING btree (expires_at);


--
-- Name: ix_forward_trades_strategy; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_forward_trades_strategy ON public.forward_trades USING btree (strategy);


--
-- Name: ix_forward_trades_symbol; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_forward_trades_symbol ON public.forward_trades USING btree (symbol);


--
-- Name: ix_polymarket_fade_positions_condition_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_polymarket_fade_positions_condition_id ON public.polymarket_fade_positions USING btree (condition_id);


--
-- Name: ix_polymarket_fade_positions_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_polymarket_fade_positions_status ON public.polymarket_fade_positions USING btree (status);


--
-- Name: ix_polymarket_jobs_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_polymarket_jobs_created_at ON public.polymarket_jobs USING btree (created_at);


--
-- Name: ix_polymarket_jobs_job_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_polymarket_jobs_job_type ON public.polymarket_jobs USING btree (job_type);


--
-- Name: ix_polymarket_jobs_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_polymarket_jobs_status ON public.polymarket_jobs USING btree (status);


--
-- Name: ix_polymarket_signals_condition_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_polymarket_signals_condition_id ON public.polymarket_signals USING btree (condition_id);


--
-- Name: ix_polymarket_signals_strategy; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_polymarket_signals_strategy ON public.polymarket_signals USING btree (strategy);


--
-- Name: ix_value_scan_closed_lots_trade_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_value_scan_closed_lots_trade_id ON public.value_scan_closed_lots USING btree (trade_id);


--
-- Name: ix_value_scan_closed_trades_exit_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_value_scan_closed_trades_exit_date ON public.value_scan_closed_trades USING btree (exit_date);


--
-- Name: ix_value_scan_closed_trades_market; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_value_scan_closed_trades_market ON public.value_scan_closed_trades USING btree (market);


--
-- Name: ix_value_scan_closed_trades_symbol; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_value_scan_closed_trades_symbol ON public.value_scan_closed_trades USING btree (symbol);


--
-- Name: ix_value_scan_lots_position_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_value_scan_lots_position_id ON public.value_scan_lots USING btree (position_id);


--
-- Name: ix_value_scan_market_meta_last_trading_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_value_scan_market_meta_last_trading_date ON public.value_scan_market_meta USING btree (last_trading_date);


--
-- Name: ix_value_scan_positions_market; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_value_scan_positions_market ON public.value_scan_positions USING btree (market);


--
-- Name: ix_value_scan_positions_symbol; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_value_scan_positions_symbol ON public.value_scan_positions USING btree (symbol);


--
-- Name: ix_value_scan_snapshots_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_value_scan_snapshots_date ON public.value_scan_snapshots USING btree (date);


--
-- Name: ix_value_scan_snapshots_market; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_value_scan_snapshots_market ON public.value_scan_snapshots USING btree (market);


--
-- Name: us_etf_daily_natkey; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX us_etf_daily_natkey ON public.us_etf_daily USING btree (symbol, date);


--
-- Name: us_options_chain_underlying_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX us_options_chain_underlying_ts ON public.us_options_chain USING btree (underlying, snapshot_ts DESC);


--
-- Name: us_options_chain_pending us_options_chain_pending_session_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.us_options_chain_pending
    ADD CONSTRAINT us_options_chain_pending_session_fkey FOREIGN KEY (session) REFERENCES public.us_options_chain_pending_snapshots(session) ON DELETE CASCADE;


--
-- Name: value_scan_closed_lots value_scan_closed_lots_trade_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.value_scan_closed_lots
    ADD CONSTRAINT value_scan_closed_lots_trade_id_fkey FOREIGN KEY (trade_id) REFERENCES public.value_scan_closed_trades(id) ON DELETE CASCADE;


--
-- Name: value_scan_lots value_scan_lots_position_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.value_scan_lots
    ADD CONSTRAINT value_scan_lots_position_id_fkey FOREIGN KEY (position_id) REFERENCES public.value_scan_positions(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict tl7HxO0kHlVdhU8Et2WBdGgOAUBLZ4uQGKwWd5a5yPoUzjSHWTRrcbm3chCqYWl

