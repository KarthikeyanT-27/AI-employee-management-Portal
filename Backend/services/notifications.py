"""Real-time notifications (free: no external service needed).

How it works
  1. A route calls notify_user()/notify_staff() while handling a request. The notification row is written in the
     same database transaction as the business change (leave decision, payroll run, ...).
  2. After the transaction COMMITS, the event is pushed to every browser tab the target user has open, through
     Server-Sent Events (SSE, `GET /api/notifications/stream`). If the commit rolls back nothing is sent.
  3. Users who are offline (or whose connection dropped) still get the notification: it is stored in the DB and the
     bell loads unread items on the next page load / reconnect. The browser falls back to polling if SSE is blocked.

The in-process broker below is correct for ONE server process (the free Render plan runs exactly one). To scale to
several workers, replace Broker with PostgreSQL LISTEN/NOTIFY or Redis pub/sub; the rest of the code stays the same.
"""
import logging
import queue
import threading

from sqlalchemy import event
from sqlalchemy.orm import Session

from models import Employee, Notification, db

log = logging.getLogger(__name__)

MAX_STREAMS_PER_USER = 5
MAX_STREAMS_TOTAL = 40


class Broker:
    def __init__(self):
        self._lock = threading.Lock()
        self._subs: dict[int, list[queue.Queue]] = {}

    def total(self) -> int:
        with self._lock:
            return sum(len(v) for v in self._subs.values())

    def subscribe(self, user_id: int) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=200)
        with self._lock:
            subs = self._subs.setdefault(user_id, [])
            subs.append(q)
            while len(subs) > MAX_STREAMS_PER_USER:      # a user with many tabs: drop the oldest stream
                subs.pop(0).put_nowait(None)             # None tells that generator to finish
        return q

    def unsubscribe(self, user_id: int, q: queue.Queue):
        with self._lock:
            subs = self._subs.get(user_id, [])
            if q in subs:
                subs.remove(q)
            if not subs:
                self._subs.pop(user_id, None)

    def publish(self, user_id: int, payload: dict):
        with self._lock:
            targets = list(self._subs.get(user_id, []))
        for q in targets:
            try:
                q.put_nowait(payload)
            except queue.Full:       # a stalled client: it will resync from the database on reconnect
                log.warning("Dropped a live event for user %s (slow client)", user_id)


broker = Broker()


# ----------------------------------------------------------------- publishing
def _pending() -> list:
    return db.session.info.setdefault("pending_events", [])


def staff_ids(exclude: int | None = None) -> list[int]:
    rows = db.session.query(Employee.id).filter(Employee.account_type.in_(["hr", "admin"]),
                                                Employee.status == "active").all()
    return [r[0] for r in rows if r[0] != exclude]


def notify_users(user_ids, kind: str, title: str, message: str, link: str | None = None, persist: bool = True):
    """Queue a notification for each user. persist=False sends a 'silent' live event only (used to refresh open
    dashboards without adding to the bell, e.g. when an employee checks in)."""
    ids = sorted({int(u) for u in user_ids if u})
    if not ids:
        return
    if persist:
        rows = [Notification(user_id=u, kind=kind, title=title[:120], message=message[:300], link=link) for u in ids]
        db.session.add_all(rows)
        db.session.flush()                   # assigns ids + created_at without committing
        for r in rows:
            _pending().append((r.user_id, {**r.to_dict(), "silent": False}))
    else:
        for u in ids:
            _pending().append((u, {"id": None, "kind": kind, "title": title, "message": message, "link": link,
                                   "is_read": True, "created_at": None, "silent": True}))


def notify_user(user_id, *a, **kw):
    notify_users([user_id], *a, **kw)


def notify_staff(kind: str, title: str, message: str, link: str | None = None, persist: bool = True,
                 exclude: int | None = None):
    notify_users(staff_ids(exclude), kind, title, message, link, persist)


@event.listens_for(Session, "after_commit")
def _deliver_after_commit(session):
    events = session.info.pop("pending_events", None)
    for user_id, payload in events or []:
        try:
            broker.publish(user_id, payload)
        except Exception:  # noqa: BLE001 - a notification problem must never break a request
            log.exception("Could not publish live event")


@event.listens_for(Session, "after_rollback")
def _drop_on_rollback(session):
    session.info.pop("pending_events", None)
