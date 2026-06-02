from __future__ import annotations

import json
import logging
from datetime import datetime, UTC
from typing import Any

from db.models import PolymarketJob, PolymarketSignal
from db.session import get_session
from features.strategy.polymarket._data.executor import is_live_mode, place_order

log = logging.getLogger("polymarket.retry_service")


async def retry_failed_orders_impl(max_rows: int = 500) -> dict[str, Any]:
    """failed/skipped 미해소 시그널 재주문 실행."""
    from sqlalchemy import select

    db = get_session()
    try:
        rows = db.execute(
            select(PolymarketSignal).where(
                PolymarketSignal.is_resolved == 0,
                PolymarketSignal.order_status.in_(["skipped", "failed"]),
            ).limit(max_rows)
        ).scalars().all()
    finally:
        db.close()

    if not rows:
        return {"retried": 0, "matched": 0, "results": []}

    try:
        import yaml
        import pathlib
        cfg_path = pathlib.Path(__file__).parent / "late_convergence" / "config.yaml"
        cfg = yaml.safe_load(cfg_path.read_text())
    except Exception:
        cfg = {}

    results: list[dict[str, Any]] = []
    for row in rows:
        token_id = row.yes_token_id if row.side == "YES" else row.no_token_id
        entry_price = row.yes_price if row.side == "YES" else row.no_price
        if not token_id or not entry_price:
            results.append({"id": row.id, "status": "skipped", "error": "token_id or price missing"})
            continue

        result = await place_order(token_id, entry_price, max_usd=cfg.get("max_order_usd", 0.0))

        db2 = get_session()
        try:
            r = db2.get(PolymarketSignal, row.id)
            if r:
                r.order_status = result.get("status", "failed")
                r.poly_order_id = result.get("order_id") or ""
                r.order_error = result.get("error") or ""
                db2.commit()
        except Exception:
            db2.rollback()
        finally:
            db2.close()

        results.append({
            "id": row.id,
            "question": (row.question or "")[:60],
            "status": result.get("status"),
            "order_id": result.get("order_id"),
            "error": result.get("error"),
        })

    matched = sum(1 for r in results if r["status"] == "matched")
    return {"retried": len(results), "matched": matched, "results": results}


def enqueue_retry_failed_job(source: str = "api") -> int:
    """retry_failed 작업 큐 적재."""
    payload = {"source": source}
    db = get_session()
    try:
        job = PolymarketJob(
            job_type="retry_failed",
            status="pending",
            payload=json.dumps(payload, ensure_ascii=False),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return job.id
    finally:
        db.close()


async def process_pending_jobs(limit: int = 10) -> int:
    """GCP worker에서 pending job 처리."""
    if not is_live_mode():
        return 0

    from sqlalchemy import select

    db = get_session()
    try:
        jobs = db.execute(
            select(PolymarketJob)
            .where(
                PolymarketJob.status == "pending",
                PolymarketJob.job_type == "retry_failed",
            )
            .order_by(PolymarketJob.created_at.asc())
            .limit(limit)
        ).scalars().all()
    finally:
        db.close()

    processed = 0
    for job in jobs:
        lock_db = get_session()
        try:
            cur = lock_db.get(PolymarketJob, job.id)
            if cur is None or cur.status != "pending":
                continue
            cur.status = "running"
            cur.started_at = datetime.now(UTC)
            lock_db.commit()
        finally:
            lock_db.close()

        try:
            result = await retry_failed_orders_impl()
            done_db = get_session()
            try:
                cur = done_db.get(PolymarketJob, job.id)
                if cur:
                    cur.status = "done"
                    cur.result = json.dumps(result, ensure_ascii=False)
                    cur.finished_at = datetime.now(UTC)
                    done_db.commit()
            finally:
                done_db.close()
            processed += 1
        except Exception as e:
            err_db = get_session()
            try:
                cur = err_db.get(PolymarketJob, job.id)
                if cur:
                    cur.status = "failed"
                    cur.error = str(e)
                    cur.finished_at = datetime.now(UTC)
                    err_db.commit()
            finally:
                err_db.close()
            log.warning("[retry_service] job failed id=%s err=%s", job.id, e)

    return processed
