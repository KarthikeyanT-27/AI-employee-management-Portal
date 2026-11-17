"""Notification centre API + the Server-Sent-Events stream used for real-time delivery."""
import json
import queue
import random
from datetime import timedelta

from flask import Blueprint, Response, current_app, jsonify, request

from models import Notification, db
from services.notifications import MAX_STREAMS_TOTAL, broker
from timeutil import utcnow
from utils import err, login_required

bp = Blueprint("notifications", __name__, url_prefix="/api/notifications")


@bp.get("")
@login_required
def list_notifications(user):
    limit = max(1, min(int(request.args.get("limit", 30)), 100))
    q = Notification.query.filter_by(user_id=user.id)
    if request.args.get("after_id"):
        q = q.filter(Notification.id > int(request.args["after_id"]))
    rows = q.order_by(Notification.id.desc()).limit(limit).all()
    unread = Notification.query.filter_by(user_id=user.id, is_read=False).count()
    if random.random() < 0.03:     # cheap housekeeping: drop old, already-read notifications
        cutoff = utcnow() - timedelta(days=current_app.config["NOTIFICATION_KEEP_DAYS"])
        Notification.query.filter(Notification.created_at < cutoff, Notification.is_read.is_(True)) \
            .delete(synchronize_session=False)
        db.session.commit()
    return jsonify({"notifications": [n.to_dict() for n in rows], "unread": unread})


@bp.post("/<int:nid>/read")
@login_required
def mark_read(user, nid):
    n = Notification.query.filter_by(id=nid, user_id=user.id).first()
    if not n:
        return err("Notification not found", 404)
    n.is_read = True
    db.session.commit()
    return jsonify({"ok": True})


@bp.post("/read-all")
@login_required
def mark_all_read(user):
    Notification.query.filter_by(user_id=user.id, is_read=False).update({"is_read": True})
    db.session.commit()
    return jsonify({"ok": True})


@bp.get("/stream")
@login_required
def stream(user):
    """Server-Sent Events. One long-lived GET per open tab; the browser reconnects automatically."""
    if broker.total() >= MAX_STREAMS_TOTAL:
        return err("Live updates are busy; the app will refresh periodically instead.", 503)
    uid = user.id
    beat = current_app.config["SSE_HEARTBEAT_SECONDS"]
    db.session.remove()          # IMPORTANT: do not hold a database connection for the life of the stream
    q = broker.subscribe(uid)

    def gen():
        try:
            yield "retry: 4000\n\nevent: ready\ndata: {}\n\n"
            while True:
                try:
                    payload = q.get(timeout=beat)
                except queue.Empty:
                    yield ": keep-alive\n\n"        # keeps proxies from closing an idle connection
                    continue
                if payload is None:
                    return
                yield f"event: notification\ndata: {json.dumps(payload)}\n\n"
        finally:
            broker.unsubscribe(uid, q)

    return Response(gen(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive"})
