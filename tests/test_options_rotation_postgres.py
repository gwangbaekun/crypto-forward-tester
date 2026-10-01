import datetime as dt
import json
import os
import unittest
import uuid
from unittest.mock import patch

import pandas as pd
import psycopg2
from psycopg2 import sql
from psycopg2.extensions import make_dsn
from psycopg2.extras import execute_values
from sqlalchemy import create_engine

from features.collectors.retention import LATEST_COLUMNS, maintain_storage, store_snapshot
from features.strategy.us_options_gamma_wall import engine


class RotationPostgresTests(unittest.TestCase):
    def setUp(self):
        self.url = os.environ["TEST_DATABASE_URL"]
        self.conn = psycopg2.connect(self.url)
        self.conn.autocommit = True
        with self.conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            if cur.fetchone()[0] == "railway":
                self.conn.close()
                raise RuntimeError("운영 railway DB에서는 이 테스트를 실행하지 않습니다")
            self.schema = "options_test_" + uuid.uuid4().hex
            cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
            cur.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.cleanup_schema)
        self.db = create_engine(self.url, connect_args={"options": f"-csearch_path={self.schema}"})
        self.addCleanup(self.db.dispose)
        self.engine_patch = patch.object(engine, "_engine", self.db)
        self.engine_patch.start()
        self.addCleanup(self.engine_patch.stop)
        self.conn.autocommit = False
        with self.conn, self.conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE us_options_chain (
                    option text, bid double precision, ask double precision,
                    last_trade_price double precision, iv double precision,
                    delta double precision, gamma double precision, open_interest double precision,
                    volume double precision, underlying_price double precision, expiry date,
                    strike double precision, option_type text, underlying text,
                    snapshot_ts timestamptz, last_trade_time text,
                    UNIQUE (option, snapshot_ts)
                );
                CREATE TABLE gamma_wall_ledger (id int PRIMARY KEY, blob jsonb NOT NULL, updated_at timestamptz);
                CREATE TABLE us_etf_daily (
                    symbol text, date date, open double precision, high double precision,
                    low double precision, close double precision, UNIQUE(symbol, date)
                );
            """)
            for hour in (20, 21):
                frame = self.frame(hour)
                columns = ", ".join(frame.columns)
                execute_values(cur, f"INSERT INTO us_options_chain ({columns}) VALUES %s", list(frame.itertuples(index=False, name=None)))

    def cleanup_schema(self):
        self.conn.rollback()
        self.conn.autocommit = True
        with self.conn.cursor() as cur:
            cur.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))
        self.conn.close()

    def frame(self, hour):
        timestamp = dt.datetime(2026, 10, 1, hour, tzinfo=dt.timezone.utc)
        rows = []
        for symbol in ("SPY", "QQQ", "IWM", "DIA"):
            for side, strike in (("C", 110.0), ("P", 90.0)):
                row = dict.fromkeys(LATEST_COLUMNS, 1.0)
                row.update(option=symbol + side, underlying=symbol, snapshot_ts=timestamp,
                           expiry=dt.date(2026, 10, 2), strike=strike, option_type=side,
                           underlying_price=100.0, last_trade_time="2026-10-01 16:00:00")
                rows.append(row)
        frame = pd.DataFrame(rows)
        return frame

    def migrate(self):
        url = make_dsn(self.url, options=f"-csearch_path={self.schema}")
        with patch.dict(os.environ, {"DATABASE_URL": url}):
            result = maintain_storage()
        return result

    def test_repeated_startup_preserves_current_snapshot(self):
        first = self.migrate()
        self.assertTrue(first["migrated"])
        with self.conn, self.conn.cursor() as cur:
            store_snapshot(cur, self.frame(22))
            cur.execute("SELECT 'us_options_chain'::regclass::oid")
            original_oid = cur.fetchone()[0]
        second = self.migrate()
        self.assertFalse(second["migrated"])
        with self.conn, self.conn.cursor() as cur:
            cur.execute("SELECT 'us_options_chain'::regclass::oid, extract(hour FROM max(snapshot_ts)) FROM us_options_chain")
            oid, hour = cur.fetchone()
            self.assertEqual(oid, original_oid)
            self.assertEqual(int(hour), 22)

    def test_repeated_startup_deletes_already_processed_pending(self):
        self.migrate()
        session = dt.date(2026, 10, 1)
        record = engine.compute_walls(engine.load_chain(), session)
        record["backfilled"] = False
        ledger = {"sessions": {session.isoformat(): record}, "last_run": None}
        with self.conn, self.conn.cursor() as cur:
            cur.execute("INSERT INTO gamma_wall_ledger VALUES (1, %s::jsonb, now())", (json.dumps(ledger),))
        outcome = self.migrate()
        self.assertEqual(outcome["processed_sessions_deleted"], 1)
        self.assertTrue(engine.load_chain().empty)
        with self.conn, self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM us_options_chain")
            self.assertEqual(cur.fetchone()[0], 8)

    def test_incomplete_schema_fails_without_deleting_original(self):
        with self.conn, self.conn.cursor() as cur:
            cur.execute("CREATE TABLE us_options_chain_pending_snapshots (session date PRIMARY KEY)")
        with self.assertRaises(RuntimeError):
            self.migrate()
        with self.conn, self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM us_options_chain")
            self.assertEqual(cur.fetchone()[0], 16)


    def test_migration_and_rotation_do_not_accumulate_snapshots(self):
        self.migrate()
        with self.conn, self.conn.cursor() as cur:
            cur.execute("SELECT count(*), count(DISTINCT snapshot_ts) FROM us_options_chain")
            self.assertEqual(cur.fetchone(), (8, 1))
            store_snapshot(cur, self.frame(22))
            cur.execute("SELECT count(*), count(DISTINCT snapshot_ts) FROM us_options_chain")
            self.assertEqual(cur.fetchone(), (8, 1))
            cur.execute("SELECT count(*), count(DISTINCT snapshot_ts) FROM us_options_chain_pending")
            self.assertEqual(cur.fetchone(), (2, 1))
            smaller = self.frame(23)
            smaller = smaller[smaller["option"] != "QQQP"]
            store_snapshot(cur, smaller)
            cur.execute("SELECT count(*) FROM us_options_chain WHERE option = 'QQQP'")
            self.assertEqual(cur.fetchone()[0], 0)

    def test_failed_pending_write_rolls_back_latest_replacement(self):
        self.migrate()
        with self.conn, self.conn.cursor() as cur:
            cur.execute("ALTER TABLE us_options_chain_pending ADD CHECK (gamma < 2)")
        bad = self.frame(22)
        bad.loc[bad["underlying"] == "SPY", "gamma"] = 3.0
        with self.assertRaises(psycopg2.errors.CheckViolation):
            with self.conn, self.conn.cursor() as cur:
                store_snapshot(cur, bad)
        with self.conn, self.conn.cursor() as cur:
            cur.execute("SELECT extract(hour FROM max(snapshot_ts)) FROM us_options_chain")
            self.assertEqual(int(cur.fetchone()[0]), 21)

    def test_acknowledging_older_snapshot_preserves_newer_pending(self):
        self.migrate()
        before = engine.load_chain()
        old_ts = before["snapshot_ts"].max()
        session = dt.date(2026, 10, 1)
        record = engine.compute_walls(before, session)
        record["backfilled"] = False
        with self.conn, self.conn.cursor() as cur:
            store_snapshot(cur, self.frame(22))
        engine.save_ledger({"sessions": {session.isoformat(): record}, "last_run": None},
                           [{"session": session, "snapshot_ts": old_ts.to_pydatetime()}])
        after = engine.load_chain()
        self.assertEqual(after["snapshot_ts"].max().hour, 22)
        engine.run()
        self.assertTrue(engine.load_chain().empty)
        self.assertEqual(pd.Timestamp(engine.load_ledger()["sessions"][session.isoformat()]["snapshot_ts"]).hour, 22)

    def test_empty_pending_still_scores_next_trading_day(self):
        self.migrate()
        engine.run()
        self.assertTrue(engine.load_chain().empty)
        with self.conn, self.conn.cursor() as cur:
            cur.execute("INSERT INTO us_etf_daily VALUES ('SPY', '2026-10-02', 100, 111, 89, 101)")
        outcome = engine.run()
        self.assertEqual(outcome["scored"], 1)
        self.assertEqual(engine.load_ledger()["sessions"]["2026-10-01"]["result"]["next_session"], "2026-10-02")

    def test_dry_run_keeps_pending_and_ledger_unchanged(self):
        self.migrate()
        engine.run(dry=True)
        self.assertFalse(engine.load_chain().empty)
        self.assertEqual(engine.load_ledger()["sessions"], {})

    def test_no_signal_is_consumed_without_removing_latest_chain(self):
        self.migrate()
        with self.conn, self.conn.cursor() as cur:
            cur.execute("UPDATE us_options_chain_pending_snapshots SET underlying_price = 50")
        outcome = engine.run()
        self.assertEqual(outcome["no_signal"], 1)
        self.assertTrue(engine.load_chain().empty)
        with self.conn, self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM us_options_chain")
            self.assertEqual(cur.fetchone()[0], 8)

    def test_invalid_price_raises_and_keeps_pending(self):
        self.migrate()
        with self.conn, self.conn.cursor() as cur:
            cur.execute("UPDATE us_options_chain_pending_snapshots SET underlying_price = 0")
        with self.assertRaises(ValueError):
            engine.run()
        self.assertFalse(engine.load_chain().empty)

    def test_newer_no_signal_removes_outdated_session_result(self):
        self.migrate()
        engine.run()
        newer = self.frame(22)
        newer.loc[newer["underlying"] == "SPY", "underlying_price"] = 50.0
        with self.conn, self.conn.cursor() as cur:
            store_snapshot(cur, newer)
        outcome = engine.run()
        self.assertEqual(outcome["no_signal"], 1)
        self.assertNotIn("2026-10-01", engine.load_ledger()["sessions"])
        self.assertTrue(engine.load_chain().empty)

    def test_no_eligible_expiry_removes_outdated_session_result(self):
        self.migrate()
        engine.run()
        newer = self.frame(22)
        newer.loc[newer["underlying"] == "SPY", "expiry"] = dt.date(2026, 12, 18)
        with self.conn, self.conn.cursor() as cur:
            store_snapshot(cur, newer)
            cur.execute("SELECT count(*) FROM us_options_chain_pending")
            self.assertEqual(cur.fetchone()[0], 0)
        outcome = engine.run()
        self.assertEqual(outcome["no_signal"], 1)
        self.assertNotIn("2026-10-01", engine.load_ledger()["sessions"])
        self.assertTrue(engine.load_chain().empty)

    def test_invalid_ledger_aborts_migration_before_data_loss(self):
        with self.conn, self.conn.cursor() as cur:
            cur.execute("INSERT INTO gamma_wall_ledger VALUES (1, %s::jsonb, now())", (json.dumps({}),))
        with self.assertRaises(psycopg2.errors.RaiseException):
            self.migrate()
        self.conn.rollback()
        with self.conn, self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM us_options_chain")
            self.assertEqual(cur.fetchone()[0], 16)


if __name__ == "__main__":
    unittest.main()
