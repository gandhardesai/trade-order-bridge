from __future__ import annotations

import queue
import threading

from sqlalchemy.orm import Session

from trade_order_bridge.database import engine
from trade_order_bridge.execution import process_order_submission

_order_queue: queue.Queue[str] = queue.Queue()
_stop_event = threading.Event()
_worker_thread: threading.Thread | None = None


def enqueue_order(order_id: str) -> None:
    _order_queue.put(order_id)


def start_worker() -> None:
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return

    _stop_event.clear()
    _worker_thread = threading.Thread(target=_run_worker, name="order-execution-worker", daemon=True)
    _worker_thread.start()


def stop_worker() -> None:
    _stop_event.set()
    _order_queue.put("__shutdown__")


def _run_worker() -> None:
    while not _stop_event.is_set():
        try:
            order_id = _order_queue.get(timeout=0.5)
        except queue.Empty:
            continue

        if order_id == "__shutdown__":
            _order_queue.task_done()
            break

        try:
            with Session(engine) as db:
                process_order_submission(db, order_id)
        finally:
            _order_queue.task_done()
