SET LOCAL lock_timeout = '5s';
LOCK TABLE us_options_chain, gamma_wall_ledger IN ACCESS EXCLUSIVE MODE;

CREATE TEMP TABLE chain_snapshots ON COMMIT DROP AS
SELECT underlying, snapshot_ts,
       (max(last_trade_time::timestamp) FILTER (
           WHERE underlying = 'SPY' AND open_interest > 0 AND gamma > 0
       ))::date AS session,
       count(*) FILTER (
           WHERE underlying = 'SPY' AND open_interest > 0 AND gamma > 0
       ) AS eligible_rows
FROM us_options_chain
GROUP BY underlying, snapshot_ts;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM chain_snapshots) THEN
        RAISE EXCEPTION 'us_options_chain is empty';
    END IF;
    IF EXISTS (
        SELECT 1 FROM chain_snapshots
        WHERE underlying = 'SPY' AND eligible_rows > 0 AND session IS NULL
    ) THEN
        RAISE EXCEPTION 'SPY snapshot session cannot be identified';
    END IF;
    IF EXISTS (
        SELECT 1 FROM gamma_wall_ledger l WHERE l.id = 1
          AND (jsonb_typeof(l.blob) IS DISTINCT FROM 'object'
               OR jsonb_typeof(l.blob->'sessions') IS DISTINCT FROM 'object')
    ) THEN
        RAISE EXCEPTION 'Gamma Wall ledger sessions must be an object';
    END IF;
    IF EXISTS (
        SELECT 1 FROM gamma_wall_ledger l,
        LATERAL jsonb_each(l.blob->'sessions') s
        WHERE l.id = 1 AND (NOT (s.value ? 'snapshot_ts') OR s.value->>'snapshot_ts' IS NULL)
    ) THEN
        RAISE EXCEPTION 'Gamma Wall ledger is missing snapshot_ts';
    END IF;
END $$;

CREATE TABLE us_options_chain_latest AS
SELECT c.option, c.bid, c.ask, c.last_trade_price, c.iv, c.delta, c.gamma,
       c.open_interest, c.volume, c.underlying_price, c.expiry, c.strike,
       c.option_type, c.underlying, c.snapshot_ts
FROM us_options_chain c
JOIN (
    SELECT underlying, max(snapshot_ts) AS snapshot_ts
    FROM chain_snapshots GROUP BY underlying
) latest USING (underlying, snapshot_ts);

ALTER TABLE us_options_chain_latest ADD PRIMARY KEY (option);
ALTER TABLE us_options_chain_latest ALTER COLUMN underlying SET NOT NULL;
ALTER TABLE us_options_chain_latest ALTER COLUMN snapshot_ts SET NOT NULL;

CREATE TABLE us_options_chain_pending_snapshots (
    session date PRIMARY KEY,
    snapshot_ts timestamptz NOT NULL,
    underlying_price double precision
);

CREATE TABLE us_options_chain_pending (
    session date NOT NULL,
    option text NOT NULL,
    snapshot_ts timestamptz NOT NULL,
    expiry date NOT NULL,
    strike double precision,
    option_type text,
    open_interest double precision,
    gamma double precision,
    underlying_price double precision,
    PRIMARY KEY (session, option),
    FOREIGN KEY (session) REFERENCES us_options_chain_pending_snapshots (session) ON DELETE CASCADE
);

INSERT INTO us_options_chain_pending_snapshots
SELECT DISTINCT pending.session, pending.snapshot_ts, c.underlying_price
FROM (
    SELECT session, max(snapshot_ts) AS snapshot_ts
    FROM chain_snapshots
    WHERE underlying = 'SPY' AND eligible_rows > 0
    GROUP BY session
) pending
JOIN us_options_chain c ON c.underlying = 'SPY' AND c.snapshot_ts = pending.snapshot_ts
LEFT JOIN gamma_wall_ledger l ON l.id = 1
WHERE c.open_interest > 0 AND c.gamma > 0
  AND (
      l.id IS NULL
      OR NOT (l.blob->'sessions' ? pending.session::text)
      OR (l.blob->'sessions'->pending.session::text->>'snapshot_ts')::timestamptz < pending.snapshot_ts
  );

INSERT INTO us_options_chain_pending
SELECT pending.session, c.option, c.snapshot_ts, c.expiry, c.strike,
       c.option_type, c.open_interest, c.gamma, c.underlying_price
FROM us_options_chain_pending_snapshots pending
JOIN us_options_chain c ON c.underlying = 'SPY' AND c.snapshot_ts = pending.snapshot_ts
WHERE c.open_interest > 0 AND c.gamma > 0
  AND c.expiry BETWEEN pending.session AND pending.session + 30;


DROP TABLE us_options_chain;
ALTER TABLE us_options_chain_latest RENAME TO us_options_chain;
CREATE INDEX us_options_chain_underlying_ts
  ON us_options_chain (underlying, snapshot_ts DESC);

ANALYZE us_options_chain;
ANALYZE us_options_chain_pending_snapshots;
ANALYZE us_options_chain_pending;
