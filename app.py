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

_REDIRECT_TO = os.environ.get("REDIRECT_TO", "").strip()
if _REDIRECT_TO:
    @app.before_request
    def _redirect_all():
        from flask import make_response
        html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="10;url={_REDIRECT_TO}">
  <title>Justin EMBAlake has moved!</title>
  <style>
    body {{ font-family: -apple-system, sans-serif; max-width: 480px; margin: 60px auto; padding: 24px; text-align: center; background: #f5f5f5; }}
    h1 {{ font-size: 1.6em; color: #1a1a1a; }}
    p {{ color: #555; line-height: 1.6; }}
    a.btn {{ display: inline-block; margin: 16px 0; padding: 14px 28px; background: #0070f3; color: white; border-radius: 8px; text-decoration: none; font-weight: bold; font-size: 1.1em; }}
    .steps {{ text-align: left; background: white; border-radius: 12px; padding: 20px 24px; margin-top: 24px; }}
    .steps h2 {{ font-size: 1em; margin-top: 0; }}
    .steps ol {{ padding-left: 20px; color: #333; }}
    .steps li {{ margin-bottom: 8px; }}
  </style>
</head>
<body>
  <h1>🚀 Justin EMBAlake has a new home!</h1>
  <p>We've moved to a faster, more reliable server. You'll be redirected automatically in 10 seconds.</p>
  <a class="btn" href="{_REDIRECT_TO}">Go Now</a>
  <div class="steps">
    <h2>📱 Update your Home Screen icon:</h2>
    <ol>
      <li><strong>Delete</strong> the old Justin icon from your home screen</li>
      <li>Tap <strong>Go Now</strong> above to open the new site in Safari</li>
      <li>Tap the <strong>Share</strong> button (box with arrow ↑)</li>
      <li>Scroll down and tap <strong>"Add to Home Screen"</strong></li>
      <li>Tap <strong>Add</strong> — done!</li>
    </ol>
  </div>
</body>
</html>"""
        return make_response(html, 200)

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
            "status":        "running",
            "message":       "Starting...",
            "result":        None,
            "created_at":    time.time(),
            "duo_event":     threading.Event(),
            "duo_code":      None,
            "vehicle_event":    threading.Event(),
            "vehicle_index":    None,
            "vehicle_labels":   None,
            "structure_event":   threading.Event(),
            "structure_index":   None,
            "structure_options": None,
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
        return {k: v for k, v in j.items() if k not in ("duo_event", "duo_code", "vehicle_event", "vehicle_index", "structure_event", "structure_index")}


# ── Routes ────────────────────────────────────────────────────────────────

@app.route("/ping")
def ping():
    return "ok", 200


@app.route("/api/screenshot/<name>")
def api_screenshot(name):
    """Serve a debug screenshot (PNG) or api_capture (JSON) by name."""
    import re as _re2
    if not _re2.match(r'^[\w\-]+$', name):
        return "Invalid name", 400
    import os as _os
    from flask import send_file
    # Special case: api_capture is JSON not PNG
    if name == "api_capture":
        path = _os.path.join(config.SCREENSHOT_DIR, "api_capture.json")
        if not _os.path.exists(path):
            return "Not captured yet — run the bot first", 404
        return send_file(path, mimetype="application/json")
    path = _os.path.join(config.SCREENSHOT_DIR, f"{name}.png")
    if not _os.path.exists(path):
        return "Not found", 404
    return send_file(path, mimetype="image/png")


@app.route("/sw.js")
def sw_js():
    response = app.send_static_file("sw.js")
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@app.route("/")
def index():
    resp = app.make_response(render_template("index.html"))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/buy", methods=["POST"])
def api_buy():
    _cleanup()
    data      = request.json or {}
    username  = data.get("username",  "").strip()
    password  = data.get("password",  "").strip()
    structure = data.get("structure", "").strip()
    duo_method   = data.get("duo_method", "push").strip()
    dry_run      = bool(data.get("dry_run", False))
    real_dry_run = bool(data.get("real_dry_run", False))

    if duo_method not in ("push", "passcode"):
        duo_method = "push"

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
                """Notify UI of DUO state.
                Push: non-blocking — just shows 'check your phone' screen.
                Passcode (or after push timeout/switch): blocks until user submits code.
                """
                with _jobs_lock:
                    force_passcode = _jobs[jid].get("use_passcode", False)

                if duo_method == "push" and not force_passcode:
                    _update(jid, status="awaiting_duo_push",
                            message="Check your phone and tap Approve in the Duo Mobile app")
                    return None
                else:
                    _update(jid, status="awaiting_duo",
                            message="Enter your 6-digit DUO passcode below")
                    with _jobs_lock:
                        _jobs[jid]["duo_event"].clear()
                        _jobs[jid]["duo_code"] = None
                        event = _jobs[jid]["duo_event"]
                    event.wait(timeout=300)
                    with _jobs_lock:
                        code = _jobs[jid].get("duo_code")
                    _update(jid, status="running", message="DUO code received, continuing...")
                    return code

            def check_passcode_switch(force=False):
                """Check (or force) a switch from push to passcode mode."""
                with _jobs_lock:
                    if force:
                        _jobs[jid]["use_passcode"] = True
                    return _jobs[jid].get("use_passcode", False)

            def vehicle_provider(labels):
                _update(jid, status="awaiting_vehicle_selection",
                        message="Which vehicle are you driving today?",
                        vehicle_labels=labels)
                with _jobs_lock:
                    _jobs[jid]["vehicle_event"].clear()
                    _jobs[jid]["vehicle_index"] = None
                    event = _jobs[jid]["vehicle_event"]
                event.wait(timeout=300)
                with _jobs_lock:
                    idx = _jobs[jid].get("vehicle_index") or 0
                _update(jid, status="running", message="Vehicle selected...")
                return idx

            def on_config_drift(name, old_val, new_val):
                print(f"[JUSTIN CONFIG_DRIFT] {name} ID changed {old_val} → {new_val} — update config.py", flush=True)
                threading.Thread(
                    target=_send_push,
                    args=(f"⚠️ Justin config drift detected",
                          f"{name} dropdown ID changed from {old_val} to {new_val}. Update STRUCTURE_{name.lstrip('P')} in config.py."),
                    daemon=True,
                ).start()

            def structure_provider(options, sold_out_name):
                values = [o["value"] for o in options]
                _update(jid, status="awaiting_structure_selection",
                        message=f"{sold_out_name} is sold out — choose a different structure below, or cancel.",
                        structure_options=[o["text"] for o in options])
                with _jobs_lock:
                    _jobs[jid]["structure_event"].clear()
                    _jobs[jid]["structure_index"] = None
                    event = _jobs[jid]["structure_event"]
                event.wait(timeout=300)
                with _jobs_lock:
                    idx = _jobs[jid].get("structure_index")
                if idx is None or idx < 0:
                    return None
                _update(jid, status="running", message="Got it — continuing...")
                return values[idx] if idx < len(values) else None

            result = run_purchase(
                username, password, structure,
                callback=lambda m: _update(jid, status="running", message=m),
                duo_provider=duo_provider,
                duo_method=duo_method,
                dry_run=dry_run,
                real_dry_run=real_dry_run,
                check_passcode_switch=check_passcode_switch,
                vehicle_provider=vehicle_provider,
                structure_provider=structure_provider,
                on_config_drift=on_config_drift,
            )

            if result == "dry_run":
                _update(jid, status="done",
                        message="Dry run complete — everything worked up to checkout!",
                        result={"dry_run": True})
            else:
                _update(jid, status="done",
                        message="Parking purchased! Check your inbox for a confirmation email.",
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


@app.route("/api/duo/<jid>/use-passcode", methods=["POST"])
def api_use_passcode(jid):
    with _jobs_lock:
        if jid not in _jobs:
            return jsonify({"error": "Job not found"}), 404
        _jobs[jid]["use_passcode"] = True
    return jsonify({"ok": True})


@app.route("/api/structure/<jid>", methods=["POST"])
def api_structure(jid):
    data  = request.json or {}
    index = data.get("index", -1)
    if not isinstance(index, int):
        return jsonify({"error": "Invalid structure index"}), 400
    with _jobs_lock:
        if jid not in _jobs:
            return jsonify({"error": "Job not found"}), 404
        _jobs[jid]["structure_index"] = index
        _jobs[jid]["structure_event"].set()
    return jsonify({"ok": True})


@app.route("/api/vehicle/<jid>", methods=["POST"])
def api_vehicle(jid):
    data  = request.json or {}
    index = data.get("index", 0)
    if not isinstance(index, int) or index < 0:
        return jsonify({"error": "Invalid vehicle index"}), 400
    with _jobs_lock:
        if jid not in _jobs:
            return jsonify({"error": "Job not found"}), 404
        _jobs[jid]["vehicle_index"] = index
        _jobs[jid]["vehicle_event"].set()
    return jsonify({"ok": True})


@app.route("/api/job/<jid>")
def api_job(jid):
    return jsonify(_get(jid))


# ── Push notifications ────────────────────────────────────────────────────

import json as _json_mod

_push_lock = threading.Lock()

_GIST_ID    = os.environ.get('GIST_ID', '')
_GIST_TOKEN = os.environ.get('GITHUB_TOKEN', '')
_GIST_FILE  = 'push_subs.json'


def _load_subs():
    if not _GIST_ID or not _GIST_TOKEN:
        return []
    try:
        import urllib.request as _ur
        req = _ur.Request(
            f'https://api.github.com/gists/{_GIST_ID}',
            headers={'Authorization': f'token {_GIST_TOKEN}', 'Accept': 'application/vnd.github+json'},
        )
        with _ur.urlopen(req, timeout=10) as r:
            data = _json_mod.loads(r.read())
        return _json_mod.loads(data['files'][_GIST_FILE]['content'])
    except Exception:
        return []


def _save_subs(subs):
    if not _GIST_ID or not _GIST_TOKEN:
        return
    import urllib.request as _ur
    payload = _json_mod.dumps({'files': {_GIST_FILE: {'content': _json_mod.dumps(subs)}}}).encode()
    req = _ur.Request(
        f'https://api.github.com/gists/{_GIST_ID}',
        data=payload,
        headers={'Authorization': f'token {_GIST_TOKEN}', 'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json'},
        method='PATCH',
    )
    with _ur.urlopen(req, timeout=10) as r:
        r.read()


def _send_push(title, body):
    try:
        subs = _load_subs()
        if not subs:
            return
        from pywebpush import webpush, WebPushException
        vapid_private = os.environ.get('VAPID_PRIVATE_KEY', '').strip()
        vapid_claims  = {'sub': 'mailto:djnurre@gmail.com'}
        expired = []
        for sub in subs:
            try:
                webpush(
                    subscription_info=sub,
                    data=_json_mod.dumps({'title': title, 'body': body}),
                    vapid_private_key=vapid_private,
                    vapid_claims=vapid_claims,
                )
            except WebPushException as ex:
                if ex.response and ex.response.status_code in (404, 410):
                    expired.append(sub['endpoint'])
            except Exception:
                pass
        if expired:
            with _push_lock:
                clean = [s for s in _load_subs() if s.get('endpoint') not in expired]
                _save_subs(clean)
    except Exception as ex:
        print(f"[JUSTIN PUSH] Admin alert failed: {ex}", flush=True)


@app.route('/api/vapid-public-key')
def api_vapid_public_key():
    key = os.environ.get('VAPID_PUBLIC_KEY', '').strip()
    if not key:
        return jsonify({'error': 'Not configured'}), 503
    return jsonify({'key': key})


def _send_admin_email(subject, body):
    """Send a plain-text email to the admin. Requires GMAIL_APP_PASSWORD env var."""
    import smtplib
    from email.mime.text import MIMEText
    gmail_pass = os.environ.get('GMAIL_APP_PASSWORD', '').strip()
    if not gmail_pass:
        return
    addr = 'djnurre@gmail.com'
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From']    = addr
    msg['To']      = addr
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=10) as server:
            server.login(addr, gmail_pass)
            server.send_message(msg)
        app.logger.info('Admin email sent: %s', subject)
    except Exception as e:
        app.logger.warning('Admin email failed: %s', e)


@app.route('/api/subscribe', methods=['POST'])
def api_subscribe():
    sub = request.json
    if not sub or 'endpoint' not in sub:
        return jsonify({'error': 'Invalid subscription'}), 400
    with _push_lock:
        subs = _load_subs()
        is_new = not any(s.get('endpoint') == sub['endpoint'] for s in subs)
        subs = [s for s in subs if s.get('endpoint') != sub['endpoint']]
        subs.append(sub)
        _save_subs(subs)
    if is_new:
        total = len(subs)
        threading.Thread(
            target=_send_admin_email,
            args=(
                f'Justin: new subscriber ({total} total)',
                f'A new device just subscribed to Justin EMBAlake push notifications.\n\nTotal subscribers: {total}',
            ),
            daemon=True,
        ).start()
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
    vapid_claims  = {'sub': 'mailto:djnurre@gmail.com'}
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
            status = ex.response.status_code if ex.response else 'no response'
            app.logger.warning('Push failed [%s]: %s', status, ex)
            if ex.response and ex.response.status_code in (404, 410):
                expired.append(sub['endpoint'])
        except Exception as ex:
            app.logger.warning('Push unexpected error: %s', ex)

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
    "not available right now",
    "only showing",
    "No permits found",
    "permit",
    "parking site",
    "structures",
    "Check your",
    "timed out",
    "check manually",
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
