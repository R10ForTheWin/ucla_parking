"""
Justin EMBAlake – Flask web app
"""

import logging
import os
import re as _re
import threading
import time
import uuid

logging.basicConfig(level=logging.INFO)

from flask import Flask, jsonify, redirect, render_template, request

import config

app = Flask(__name__)
_jobs      = {}
_jobs_lock = threading.Lock()

# Up to 3 concurrent Playwright sessions
_browser_lock    = threading.BoundedSemaphore(3)


# ── Security headers ──────────────────────────────────────────────────────

@app.before_request
def https_redirect():
    if request.headers.get("X-Forwarded-Proto", "https") == "http":
        return redirect(request.url.replace("http://", "https://"), 301)


@app.after_request
def security_headers(response):
    response.headers["X-Frame-Options"]        = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"]        = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "media-src 'self'; "
        "connect-src 'self'; "
        "worker-src 'self';"
    )
    return response


# ── Job helpers ───────────────────────────────────────────────────────────

def _cleanup():
    cutoff = time.time() - 600
    with _jobs_lock:
        stale = [
            jid for jid, j in _jobs.items()
            if j["status"] in ("done", "error") and j["created_at"] < cutoff
        ]
        for jid in stale:
            del _jobs[jid]


def _create_job():
    jid = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[jid] = {
            "status":     "running",
            "message":    "Starting...",
            "result":     None,
            "created_at": time.time(),
            "duo_event":  threading.Event(),
            "duo_code":   None,
        }
    return jid


def _update(jid, **kw):
    with _jobs_lock:
        if jid in _jobs:
            _jobs[jid].update(kw)


def _get(jid):
    with _jobs_lock:
        j = _jobs.get(jid)
        if not j:
            return {"status": "not_found", "message": "Job not found"}
        return {k: v for k, v in j.items() if k not in ("duo_event", "duo_code")}


# ── Routes ────────────────────────────────────────────────────────────────

@app.route("/ping")
def ping():
    return "ok", 200


@app.route("/sw.js")
def sw_js():
    response = app.send_static_file("sw.js")
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/buy", methods=["POST"])
def api_buy():
    _cleanup()
    data      = request.json or {}
    username  = data.get("username",  "").strip()
    password  = data.get("password",  "").strip()
    structure = data.get("structure", "").strip()
    dry_run   = bool(data.get("dry_run", False))

    if not all([username, password, structure]):
        return jsonify({"error": "Missing required fields"}), 400
    if structure not in (config.STRUCTURE_4, config.STRUCTURE_P7):
        return jsonify({"error": "Invalid parking structure"}), 400

    jid = _create_job()

    def run():
        waited = 0
        while not _browser_lock.acquire(timeout=5):
            waited += 5
            if waited >= 120:
                _update(jid, status="error",
                        message="Justin is too busy right now — please try again in a moment.")
                return
            _update(jid, status="queued",
                    message="Justin is helping another EMBA with their parking right now. You are next in line. Please standby!")

        try:
            from parking_automation import run_purchase

            def duo_provider():
                """Pause automation and wait for user to submit DUO code via web UI."""
                _update(jid, status="awaiting_duo",
                        message="Enter your 6-digit DUO passcode below")
                with _jobs_lock:
                    event = _jobs[jid]["duo_event"]
                event.wait(timeout=120)
                with _jobs_lock:
                    code = _jobs[jid].get("duo_code")
                _update(jid, status="running", message="DUO code received, continuing...")
                return code

            result = run_purchase(
                username, password, structure,
                callback=lambda m: _update(jid, message=m),
                duo_provider=duo_provider,
                dry_run=dry_run,
            )

            if result == "dry_run":
                _update(jid, status="done",
                        message="Dry run complete — everything worked up to checkout!",
                        result={"dry_run": True})
            else:
                _update(jid, status="done",
                        message="Parking purchased!",
                        result={"dry_run": False, "detail": result})

        except Exception as e:
            import traceback
            print(f"[JUSTIN ERROR] {traceback.format_exc()}", flush=True)
            _update(jid, status="error", message=_safe_error(e))
        finally:
            _browser_lock.release()

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"job_id": jid})


@app.route("/api/duo/<jid>", methods=["POST"])
def api_duo(jid):
    data = request.json or {}
    code = data.get("code", "").strip()
    if not _re.match(r'^\d{6,8}$', code):
        return jsonify({"error": "DUO passcode must be 6–8 digits"}), 400
    with _jobs_lock:
        if jid not in _jobs:
            return jsonify({"error": "Job not found"}), 404
        _jobs[jid]["duo_code"] = code
        _jobs[jid]["duo_event"].set()
    return jsonify({"ok": True})


@app.route("/api/job/<jid>")
def api_job(jid):
    return jsonify(_get(jid))


# ── Push notifications ────────────────────────────────────────────────────

import json as _json_mod

_PUSH_SUBS_FILE = '/tmp/push_subs.json'
_push_lock = threading.Lock()


def _load_subs():
    try:
        with open(_PUSH_SUBS_FILE) as f:
            return _json_mod.load(f)
    except Exception:
        return []


def _save_subs(subs):
    with open(_PUSH_SUBS_FILE, 'w') as f:
        _json_mod.dump(subs, f)


@app.route('/api/vapid-public-key')
def api_vapid_public_key():
    key = os.environ.get('VAPID_PUBLIC_KEY', '').strip()
    if not key:
        return jsonify({'error': 'Not configured'}), 503
    return jsonify({'key': key})


@app.route('/api/subscribe', methods=['POST'])
def api_subscribe():
    sub = request.json
    if not sub or 'endpoint' not in sub:
        return jsonify({'error': 'Invalid subscription'}), 400
    with _push_lock:
        subs = _load_subs()
        subs = [s for s in subs if s.get('endpoint') != sub['endpoint']]
        subs.append(sub)
        _save_subs(subs)
    return jsonify({'ok': True})


@app.route('/api/send-push', methods=['POST'])
def api_send_push():
    token = request.headers.get('X-Push-Secret', '')
    if not token or token != os.environ.get('PUSH_SECRET', ''):
        return jsonify({'error': 'Unauthorized'}), 401

    data  = request.json or {}
    title = data.get('title', '🅿️ Time to buy parking!')
    body  = data.get('body',  'Today is a class day. Tap to open Justin.')

    with _push_lock:
        subs = _load_subs()

    if not subs:
        return jsonify({'sent': 0, 'note': 'No subscribers'})

    from pywebpush import webpush, WebPushException
    vapid_private = os.environ.get('VAPID_PRIVATE_KEY', '').strip()
    vapid_claims  = {'sub': 'mailto:noreply@example.com'}
    sent, expired = 0, []

    for sub in subs:
        try:
            webpush(
                subscription_info=sub,
                data=_json_mod.dumps({'title': title, 'body': body}),
                vapid_private_key=vapid_private,
                vapid_claims=vapid_claims,
            )
            sent += 1
        except WebPushException as ex:
            if ex.response and ex.response.status_code in (404, 410):
                expired.append(sub['endpoint'])
            app.logger.warning('Push failed: %s', ex)

    if expired:
        with _push_lock:
            clean = [s for s in _load_subs() if s.get('endpoint') not in expired]
            _save_subs(clean)

    return jsonify({'sent': sent, 'expired_cleaned': len(expired)})


# ── Error sanitiser ───────────────────────────────────────────────────────

_SAFE_ERRORS = (
    "CAPTCHA",
    "Queue-it",
    "sold out",
    "Bruin Bill",
    "manually",
    "DUO passcode",
    "confirmation found",
    "credentials",
    "Login failed",
    "Invalid",
    "No vehicles found",
    "No purchase confirmation",
)

def _safe_error(exc):
    msg = str(exc)
    for safe in _SAFE_ERRORS:
        if safe.lower() in msg.lower():
            return msg
    app.logger.error("Internal error: %s", msg)
    return "Something went wrong on the server — please try again."


# ── Entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5001)
