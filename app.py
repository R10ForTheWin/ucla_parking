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
            job = _jobs[jid]
            msg = kw.get("message")
            if msg and kw.get("status") not in ("done", "error"):
                trail = job.setdefault("steps", [])
                if not trail or trail[-1][1] != msg:
                    trail.append((round(time.time() - job["created_at"]), msg))
            job.update(kw)


def _alert(title, message, priority="high", tags="warning"):
    """Admin alert via ntfy.sh (topic in NTFY_TOPIC). Never include usernames."""
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return

    def send():
        import urllib.request as _ur
        try:
            req = _ur.Request(
                f"https://ntfy.sh/{topic}",
                data=message.encode(),
                headers={"Title": title.encode("ascii", "ignore").decode(), "Priority": priority, "Tags": tags},
                method="POST",
            )
            with _ur.urlopen(req, timeout=10) as r:
                r.read()
        except Exception as ex:
            print(f"[JUSTIN ALERT] ntfy failed: {ex}", flush=True)

    threading.Thread(target=send, daemon=True).start()


def _alert_purchase_problem(jid, headline, detail, username="", structure=""):
    """Tell the admin where in the flow a user got stuck."""
    import datetime as _dt
    from zoneinfo import ZoneInfo
    with _jobs_lock:
        job   = _jobs.get(jid, {})
        steps = list(job.get("steps", []))
        took  = round(time.time() - job.get("created_at", time.time()))
    if username:
        detail = detail.replace(username, "<user>")
    last = steps[-1][1] if steps else "before the first step"
    trail = "\n".join(f"  {t // 60}:{t % 60:02d}  {m}" for t, m in steps[-6:])
    when = _dt.datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%a %b %-d, %-I:%M %p PT")
    _alert(
        f"Justin: {headline}",
        f"Stopped at: {last}\n"
        f"Reason: {detail[:300]}\n"
        f"Structure: {structure or '?'} | {took // 60}m {took % 60}s in | {when}\n\n"
        f"Last steps:\n{trail}",
    )


def _get(jid):
    with _jobs_lock:
        j = _jobs.get(jid)
        if not j:
            return {"status": "not_found", "message": "Job not found"}
        return {k: v for k, v in j.items() if k not in ("duo_event", "duo_code", "vehicle_event", "vehicle_index", "structure_event", "structure_index", "steps")}


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
    _usage_visit()
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
                _usage_inc('error: queue full')
                _alert_purchase_problem(jid, "user turned away (queue full)",
                                        "Waited 2 min for a free browser slot.", structure=structure)
                return
            _update(jid, status="queued",
                    message="Justin is helping another EMBA with their parking right now. You are next in line. Please standby!")

        try:
            from parking_automation import run_purchase, AlreadyHasActivePermit, PurchaseAlreadyAttempted

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

            if not dry_run and not real_dry_run:
                _usage_inc('buy_attempts')
                _usage_purchaser(username)
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
                _usage_inc('buy_dry_run')
                _update(jid, status="done",
                        message="Dry run complete — everything worked up to checkout!",
                        result={"dry_run": True})
            else:
                _usage_inc('buy_success')
                _update(jid, status="done",
                        message="Parking purchased! Check your inbox for a confirmation email.",
                        result={"dry_run": False, "detail": result})

        except AlreadyHasActivePermit as e:
            permit_info = str(e)
            already_today = "already purchased today" in permit_info
            msg = (
                f"Already purchased today! Your permit is active: {permit_info}."
                if already_today else
                f"You're already covered! Active permit found: {permit_info}. No daily purchase needed."
            )
            print(f"[JUSTIN] Already has active permit: {permit_info}", flush=True)
            _usage_inc('buy_already_covered')
            _update(jid, status="done", message=msg, result={"already_covered": True})
        except PurchaseAlreadyAttempted as e:
            print(f"[JUSTIN] PurchaseAlreadyAttempted: {e}", flush=True)
            _usage_inc('buy_already_covered')
            _update(jid, status="done", message="Your permit was submitted — you're covered.",
                    result={"already_covered": True, "submitted": True})
        except Exception as e:
            import traceback
            print(f"[JUSTIN ERROR] {traceback.format_exc()}", flush=True)
            shown = _safe_error(e)
            with _jobs_lock:
                steps = _jobs.get(jid, {}).get("steps", [])
                last_step = steps[-1][1] if steps else "start"
            if not dry_run and not real_dry_run:
                _usage_inc('buy_error')
                _usage_inc(f'error: {last_step}')
            first_line = (str(e).strip().splitlines() or [type(e).__name__])[0]
            _alert_purchase_problem(
                jid,
                "purchase failed" + (" (test run)" if (dry_run or real_dry_run) else ""),
                f"{type(e).__name__}: {first_line}\nUser saw: {shown}",
                username=username, structure=structure,
            )
            _update(jid, status="error", message=shown)
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
_PUSH_STATE_FILE = 'push_state.json'


def _gist_read(gist_id, filename, default):
    if not gist_id or not _GIST_TOKEN:
        return default
    try:
        import urllib.request as _ur
        req = _ur.Request(
            f'https://api.github.com/gists/{gist_id}',
            headers={'Authorization': f'token {_GIST_TOKEN}', 'Accept': 'application/vnd.github+json'},
        )
        with _ur.urlopen(req, timeout=10) as r:
            data = _json_mod.loads(r.read())
        f = data['files'].get(filename)
        if not f:
            return default
        return _json_mod.loads(f['content'])
    except Exception:
        return default


def _gist_write(gist_id, filename, content, indent=None):
    if not gist_id or not _GIST_TOKEN:
        return
    import urllib.request as _ur
    payload = _json_mod.dumps({'files': {filename: {'content': _json_mod.dumps(content, indent=indent)}}}).encode()
    req = _ur.Request(
        f'https://api.github.com/gists/{gist_id}',
        data=payload,
        headers={'Authorization': f'token {_GIST_TOKEN}', 'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json'},
        method='PATCH',
    )
    with _ur.urlopen(req, timeout=10) as r:
        r.read()


def _load_subs():
    return _gist_read(_GIST_ID, _GIST_FILE, [])


def _save_subs(subs):
    _gist_write(_GIST_ID, _GIST_FILE, subs)


def _load_push_state():
    return _gist_read(_GIST_ID, _PUSH_STATE_FILE, {})


def _save_push_state(state):
    _gist_write(_GIST_ID, _PUSH_STATE_FILE, state)


def _send_push(title, body):
    """Send to all subscribers, prune expired ones, return count actually sent."""
    sent = 0
    try:
        subs = _load_subs()
        if not subs:
            return sent
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
                sent += 1
            except WebPushException as ex:
                if ex.response is not None and ex.response.status_code in (404, 410):
                    expired.append(sub['endpoint'])
            except Exception:
                pass
        if expired:
            with _push_lock:
                clean = [s for s in _load_subs() if s.get('endpoint') not in expired]
                _save_subs(clean)
    except Exception as ex:
        print(f"[JUSTIN PUSH] _send_push failed: {ex}", flush=True)
    return sent


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
        _usage_inc('push_subscribes')
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


@app.route('/api/unsubscribe', methods=['POST'])
def api_unsubscribe():
    endpoint = (request.json or {}).get('endpoint')
    if not endpoint:
        return jsonify({'error': 'endpoint required'}), 400
    with _push_lock:
        subs = _load_subs()
        keep = [s for s in subs if s.get('endpoint') != endpoint]
        if len(keep) != len(subs):
            _save_subs(keep)
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
            status = ex.response.status_code if ex.response is not None else 'no response'
            app.logger.warning('Push failed [%s]: %s', status, ex)
            if ex.response is not None and ex.response.status_code in (404, 410):
                expired.append(sub['endpoint'])
        except Exception as ex:
            app.logger.warning('Push unexpected error: %s', ex)

    if expired:
        with _push_lock:
            clean = [s for s in _load_subs() if s.get('endpoint') not in expired]
            _save_subs(clean)

    return jsonify({'sent': sent, 'expired_cleaned': len(expired)})


def _class_day_push_body(today):
    special_day = config.SPECIAL_DAYS.get(today)
    if special_day:
        return f'Tap to open Justin and buy parking for today. Good luck on {special_day}!'
    if today in config.AUGUST_BLOCK_DATES:
        return "Tap to open Justin and buy parking for today — if you're enrolled in August Block."
    if today in config.ELECTIVE_DATES:
        return "Tap to open Justin and buy parking for today — if you're taking an elective."
    if today in config.BIWEEKLY_ONLY_DATES:
        return "Tap to open Justin and buy parking for today — Bi-Weekly students only."
    return 'Tap to open Justin and buy parking for today — Bi-Weekly + Monthly students.'


_daily_push_lock = threading.Lock()


@app.route('/api/daily-push', methods=['GET', 'POST'])
def api_daily_push():
    """Class-day morning alert. Called at 6 AM PT by cron-job.org (primary)
    and the GitHub Actions workflows (backup). Safe to call repeatedly: it
    only sends on class days, between 5:55 and 11:00 AM PT, once per day."""
    token = request.headers.get('X-Push-Secret', '') or request.args.get('key', '')
    if not token or token != os.environ.get('PUSH_SECRET', ''):
        return jsonify({'error': 'Unauthorized'}), 401

    from zoneinfo import ZoneInfo
    import datetime as _dt
    pacific = ZoneInfo('America/Los_Angeles')
    now     = _dt.datetime.now(pacific)
    force   = request.args.get('force') == '1'
    # ?dry_run=1[&at=YYYY-MM-DDTHH:MM] runs every check (optionally as of a
    # simulated PT time) and verifies Gist read/write, but sends nothing.
    dry_run = request.args.get('dry_run') == '1'
    if dry_run and request.args.get('at'):
        now = _dt.datetime.fromisoformat(request.args['at']).replace(tzinfo=pacific)
    today = now.strftime('%Y-%m-%d')

    if not force:
        if today not in config.PARKING_DATES:
            return jsonify({'status': 'skipped', 'reason': 'not a class day', 'today': today, 'dry_run': dry_run})
        minutes = now.hour * 60 + now.minute
        if not (5 * 60 + 55 <= minutes < 11 * 60):
            return jsonify({'status': 'skipped', 'reason': 'outside 5:55-11:00 AM PT window', 'now': now.strftime('%H:%M'), 'dry_run': dry_run})

    with _daily_push_lock:
        state = _load_push_state()
        if not force and state.get('last_daily_push') == today:
            return jsonify({'status': 'already_sent', 'today': today, 'sent_at': state.get('last_daily_push_at'), 'dry_run': dry_run})
        body = _class_day_push_body(today)

        if dry_run:
            state['last_dry_run_at'] = _dt.datetime.now(pacific).isoformat(timespec='seconds')
            try:
                _save_push_state(state)
                gist_write = 'ok'
            except Exception as ex:
                gist_write = f'failed: {ex}'
            return jsonify({'status': 'would_send', 'today': today, 'title': "It's a School Day!", 'body': body,
                            'subscribers': len(_load_subs()), 'gist_write': gist_write, 'dry_run': True})

        sent = _send_push("It's a School Day!", body)
        if sent:
            state['last_daily_push']    = today
            state['last_daily_push_at'] = now.strftime('%H:%M:%S')
            try:
                _save_push_state(state)
            except Exception as ex:
                # The push already went out — don't report failure (that would
                # make the GitHub backup send a duplicate).
                print(f"[JUSTIN PUSH] daily-push: failed to record send: {ex}", flush=True)

    print(f"[JUSTIN PUSH] daily-push {today} {now.strftime('%H:%M:%S')} PT: sent={sent}", flush=True)
    if not sent:
        _alert("Justin: class-day push reached 0 phones",
               f"{today} {now.strftime('%H:%M')} PT: /api/daily-push sent to 0 subscribers. Check Railway logs.")
    return jsonify({'status': 'sent' if sent else 'failed', 'sent': sent, 'today': today}), (200 if sent else 500)


# ── Leave-by time ─────────────────────────────────────────────────────────
# Google Routes API (traffic-aware) → latest departure that still arrives at the
# structure ARRIVE_BEFORE_CLASS_MIN before class. The user's location is used
# The starting address lives on the user's phone (localStorage); the server
# uses it for the lookup only — never stored or logged (cache keys are hashes
# held in memory for 10 minutes).

# Hard caps on Google requests so usage stays inside the Routes "Compute Routes
# Pro" free tier (5,000/month as of 2026-10). Google-side daily quota is a second
# layer. Counts persist in Gist usage.json ("routes_quota") across restarts.
ROUTES_DAILY_LIMIT   = 140
ROUTES_MONTHLY_LIMIT = 4500
_routes_quota = None   # {'day','day_count','month','month_count'}; loaded lazily
_leave_by_cache = {}   # key → (expires_at, payload)
_last_route_end = {}   # where Google put the destination (admin test output)
_leave_by_hits  = {}   # ip → [timestamps]
_leave_by_lock  = threading.Lock()


class RoutesQuotaExceeded(Exception):
    pass


def _routes_quota_take():
    """Reserve one Google request against the daily/monthly caps, or raise."""
    global _routes_quota
    today = _usage_today()
    with _leave_by_lock:
        if _routes_quota is None:
            _routes_quota = dict(_gist_read(_GIST_ID, _USAGE_FILE, {}).get('routes_quota') or {})
        q = _routes_quota
        if q.get('month') != today[:7]:
            q.update(month=today[:7], month_count=0)
        if q.get('day') != today:
            q.update(day=today, day_count=0)
        if q['day_count'] >= ROUTES_DAILY_LIMIT or q['month_count'] >= ROUTES_MONTHLY_LIMIT:
            raise RoutesQuotaExceeded(f"{q['day_count']} today / {q['month_count']} this month")
        q['day_count'] += 1
        q['month_count'] += 1
        if q['month_count'] == int(ROUTES_MONTHLY_LIMIT * 0.8):
            _alert("Justin: leave-by at 80% of free Google quota",
                   f"{q['month_count']}/{ROUTES_MONTHLY_LIMIT} Routes requests used this month. "
                   "It stops automatically at the limit, so no charges.", priority="default", tags="chart_with_upwards_trend")
    _usage_inc('routes_requests')


def _drive_seconds(origin, dest, depart_utc):
    import urllib.request as _ur
    import datetime as _dt
    _routes_quota_take()
    key = os.environ.get('GOOGLE_MAPS_API_KEY', '').strip()
    if 'lat' in dest:
        destination = {'location': {'latLng': {'latitude': dest['lat'], 'longitude': dest['lng']}}}
    else:
        destination = {'address': dest['address']}
    depart_utc = max(depart_utc, _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=30))
    body = {
        'origin': {'address': origin} if isinstance(origin, str)
                  else {'location': {'latLng': {'latitude': origin[0], 'longitude': origin[1]}}},
        'destination': destination,
        'travelMode': 'DRIVE',
        'routingPreference': 'TRAFFIC_AWARE_OPTIMAL',
        'departureTime': depart_utc.strftime('%Y-%m-%dT%H:%M:%SZ'),
    }
    req = _ur.Request(
        'https://routes.googleapis.com/directions/v2:computeRoutes',
        data=_json_mod.dumps(body).encode(),
        headers={'Content-Type': 'application/json', 'X-Goog-Api-Key': key,
                 'X-Goog-FieldMask': 'routes.duration,routes.legs.endLocation'},
        method='POST',
    )
    with _ur.urlopen(req, timeout=10) as r:
        routes = _json_mod.loads(r.read()).get('routes') or []
    if not routes:
        raise ValueError('no route')
    try:
        _last_route_end.update(routes[0]['legs'][-1]['endLocation']['latLng'])
    except (KeyError, IndexError):
        pass
    return int(routes[0]['duration'].rstrip('s'))


def _compute_leave_by(origin, structure, now_pt, drive_seconds=_drive_seconds):
    import datetime as _dt
    fmt = lambda t: t.strftime('%-I:%M %p')
    today = now_pt.strftime('%Y-%m-%d')
    start = config.class_start(today) if today in config.PARKING_DATES else None
    if not start:
        return {'status': 'no_class'}
    dest = config.STRUCTURE_DESTINATIONS.get(structure) or config.STRUCTURE_DESTINATIONS[config.STRUCTURE_4]
    h, m = map(int, start.split(':'))
    class_at  = now_pt.replace(hour=h, minute=m, second=0, microsecond=0)
    arrive_by = class_at - _dt.timedelta(minutes=config.ARRIVE_BEFORE_CLASS_MIN)
    if 'lat' in dest:
        waze = f"https://waze.com/ul?ll={dest['lat']},{dest['lng']}&navigate=yes"
    else:
        import urllib.parse as _up
        waze = f"https://waze.com/ul?q={_up.quote(dest['address'])}&navigate=yes"
    # Saturday's arrive-by is breakfast (8:00–9:00 on the dot calendar legend).
    goal = 'breakfast' if class_at.weekday() == 5 else 'be on campus'
    base = {'arrive_by': fmt(arrive_by), 'class_start': fmt(class_at), 'goal': goal,
            'destination': dest['name'], 'waze_url': waze}
    if now_pt >= arrive_by:
        return {**base, 'status': 'past'}

    utc = _dt.timezone.utc
    drive = lambda t: drive_seconds(origin, dest, t.astimezone(utc))

    def leave_now():
        secs = drive(now_pt)
        eta = now_pt + _dt.timedelta(seconds=secs)
        return {**base, 'status': 'leave_now', 'drive_min': round(secs / 60), 'eta': fmt(eta),
                'late_min': max(0, round((eta - arrive_by).total_seconds() / 60))}

    # Fixed-point guess: depart = arrive_by − drive(depart) …
    depart = max(now_pt, arrive_by - _dt.timedelta(minutes=45))
    for _ in range(3):
        new_depart = arrive_by - _dt.timedelta(seconds=drive(depart))
        if new_depart <= now_pt:
            return leave_now()
        converged = abs((new_depart - depart).total_seconds()) < 90
        depart = new_depart
        if converged:
            break
    # … then verify it really arrives on time; traffic jumps at rush hour can
    # make the guess optimistic, so step earlier until it does.
    for _ in range(5):
        secs = drive(depart)
        overshoot = (depart + _dt.timedelta(seconds=secs) - arrive_by).total_seconds()
        if overshoot <= 0:
            break
        depart -= _dt.timedelta(seconds=max(300, overshoot))
        if depart <= now_pt:
            return leave_now()
    leave_by = depart.replace(second=0, microsecond=0)  # round down = a little early
    return {**base, 'status': 'ok', 'leave_by': fmt(leave_by),
            'leave_by_iso': leave_by.isoformat(), 'drive_min': round(secs / 60)}


@app.route('/api/leave-by', methods=['POST'])
def api_leave_by():
    if not os.environ.get('GOOGLE_MAPS_API_KEY', '').strip():
        return jsonify({'status': 'unavailable'})
    data = request.json or {}
    address = ' '.join(str(data.get('address') or '').split())
    if not 5 <= len(address) <= 200:
        return jsonify({'error': 'address required'}), 400
    structure = str(data.get('structure') or config.STRUCTURE_4)

    from zoneinfo import ZoneInfo
    import datetime as _dt
    now_pt = _dt.datetime.now(ZoneInfo('America/Los_Angeles'))
    # Admin test: X-Push-Secret + {"at": "YYYY-MM-DDTHH:MM"} simulates that PT time
    # (uncached, still counted against the free-tier caps).
    secret = os.environ.get('PUSH_SECRET', '')
    if data.get('at') and secret and request.headers.get('X-Push-Secret') == secret:
        sim = _dt.datetime.fromisoformat(data['at']).replace(tzinfo=ZoneInfo('America/Los_Angeles'))
        try:
            payload = _compute_leave_by(address, structure, sim)
        except Exception as ex:
            return jsonify({'status': 'error', 'error': f'{type(ex).__name__}: {ex}'}), 502
        return jsonify({**payload, 'destination_latlng': dict(_last_route_end)})
    ip = request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()
    import hashlib
    key = (hashlib.sha256(address.lower().encode()).hexdigest()[:16], structure, now_pt.strftime('%Y-%m-%d'))
    with _leave_by_lock:
        hit = _leave_by_cache.get(key)
        if hit and hit[0] > time.time():
            return jsonify(hit[1])
        recent = [t for t in _leave_by_hits.get(ip, []) if t > time.time() - 3600]
        if len(recent) >= 30:
            return jsonify({'status': 'rate_limited'}), 429
        _leave_by_hits[ip] = recent + [time.time()]
    try:
        payload = _compute_leave_by(address, structure, now_pt)
    except RoutesQuotaExceeded as ex:
        print(f"[JUSTIN LEAVE-BY] free-tier cap reached ({ex}) — hiding leave-by", flush=True)
        return jsonify({'status': 'unavailable'})
    except Exception as ex:
        print(f"[JUSTIN LEAVE-BY] lookup failed: {type(ex).__name__}: {ex}", flush=True)
        return jsonify({'status': 'error'}), 502
    with _leave_by_lock:
        _leave_by_cache[key] = (time.time() + 900, payload)
    return jsonify(payload)


# ── Usage counter ─────────────────────────────────────────────────────────
# Daily counts in Gist usage.json. No usernames or IPs are stored: visitors are
# counted via a hash with a per-process random salt (count only), purchasers via
# an HMAC of the username keyed by ENCRYPTION_KEY (one-way, for a unique count).

_USAGE_FILE = 'usage.json'
_usage_lock = threading.Lock()
_usage_pending = {}       # {date: {counter: delta}}
_usage_visitors = {}      # {date: set(salted visitor hashes)}
_usage_purchasers = set() # new purchaser HMACs not yet flushed
_usage_salt = uuid.uuid4().hex


def _usage_today():
    from zoneinfo import ZoneInfo
    import datetime as _dt
    return _dt.datetime.now(ZoneInfo('America/Los_Angeles')).strftime('%Y-%m-%d')


def _usage_inc(counter, n=1):
    with _usage_lock:
        day = _usage_pending.setdefault(_usage_today(), {})
        day[counter] = day.get(counter, 0) + n


def _usage_visit():
    import hashlib
    ident = f"{request.headers.get('X-Forwarded-For', request.remote_addr)}|{request.headers.get('User-Agent', '')}"
    h = hashlib.sha256((_usage_salt + ident).encode()).hexdigest()[:16]
    with _usage_lock:
        today = _usage_today()
        _usage_visitors.setdefault(today, set()).add(h)
        day = _usage_pending.setdefault(today, {})
        day['visits'] = day.get('visits', 0) + 1


def _usage_purchaser(username):
    import hashlib, hmac
    key = os.environ.get('ENCRYPTION_KEY', '').encode()
    h = hmac.new(key, username.strip().lower().encode(), hashlib.sha256).hexdigest()[:16]
    with _usage_lock:
        _usage_purchasers.add(h)


def _usage_flush():
    with _usage_lock:
        pending    = {d: dict(c) for d, c in _usage_pending.items()}
        visitors   = {d: len(s) for d, s in _usage_visitors.items()}
        purchasers = set(_usage_purchasers)
    if not pending and not purchasers and not _routes_quota:
        return
    usage = _gist_read(_GIST_ID, _USAGE_FILE, {})
    days = usage.setdefault('days', {})
    for d, counters in pending.items():
        day = days.setdefault(d, {})
        for k, v in counters.items():
            day[k] = day.get(k, 0) + v
    for d, n in visitors.items():
        # Salt resets on restart, so this can undercount a day that spans a restart.
        days.setdefault(d, {})['visitors'] = max(days[d].get('visitors', 0), n)
    usage['purchasers'] = sorted(set(usage.get('purchasers', [])) | purchasers)
    with _leave_by_lock:
        q = dict(_routes_quota) if _routes_quota else None
    if q:
        old = usage.get('routes_quota') or {}
        if old.get('month') == q.get('month'):   # never let a restart lower the count
            q['month_count'] = max(q['month_count'], old.get('month_count', 0))
            if old.get('day') == q.get('day'):
                q['day_count'] = max(q['day_count'], old.get('day_count', 0))
        usage['routes_quota'] = q
    _gist_write(_GIST_ID, _USAGE_FILE, usage, indent=1)
    with _usage_lock:
        for d, counters in pending.items():
            day = _usage_pending.get(d, {})
            for k, v in counters.items():
                day[k] = day.get(k, 0) - v
                if not day[k]:
                    del day[k]
            if not day:
                _usage_pending.pop(d, None)
        _usage_purchasers.difference_update(purchasers)
        today = _usage_today()
        for d in [d for d in _usage_visitors if d != today]:
            del _usage_visitors[d]


def _usage_flush_loop():
    while True:
        time.sleep(60)
        try:
            _usage_flush()
        except Exception as ex:
            print(f"[JUSTIN USAGE] flush failed: {ex}", flush=True)


threading.Thread(target=_usage_flush_loop, daemon=True).start()


@app.route('/api/usage')
def api_usage():
    secret = os.environ.get('PUSH_SECRET', '')
    token = request.headers.get('X-Push-Secret', '') or request.args.get('key', '')
    if not secret or token != secret:
        return jsonify({'error': 'unauthorized'}), 401
    try:
        _usage_flush()
    except Exception as ex:
        print(f"[JUSTIN USAGE] flush failed: {ex}", flush=True)
    usage = _gist_read(_GIST_ID, _USAGE_FILE, {})
    days = usage.get('days', {})
    totals = {}
    for counters in days.values():
        for k, v in counters.items():
            totals[k] = totals.get(k, 0) + v
    totals.pop('visitors', None)  # not additive across days
    return jsonify({
        'unique_purchasers_all_time': len(usage.get('purchasers', [])),
        'push_subscriptions_stored': len(_load_subs()),
        'totals': totals,
        'days': dict(sorted(days.items(), reverse=True)[:30]),
    })


# ── Feedback ──────────────────────────────────────────────────────────────

_FEEDBACK_GIST_ID = os.environ.get('FEEDBACK_GIST_ID', '')
_FEEDBACK_FILE    = 'feedback.json'


def _load_feedback():
    return _gist_read(_FEEDBACK_GIST_ID, _FEEDBACK_FILE, [])


def _save_feedback(entries):
    _gist_write(_FEEDBACK_GIST_ID, _FEEDBACK_FILE, entries, indent=2)


@app.route('/api/feedback', methods=['POST'])
def submit_feedback():
    data = request.get_json() or {}
    message = (data.get('message') or '').strip()
    if not message:
        return jsonify({'error': 'empty'}), 400
    import datetime
    entry = {
        'ts': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'message': message,
    }
    try:
        entries = _load_feedback()
        entries.append(entry)
        _save_feedback(entries)
    except Exception as e:
        app.logger.error('Feedback save failed: %s', e)
        return jsonify({'error': 'save failed'}), 500
    return jsonify({'ok': True})


@app.route('/api/feedback', methods=['GET'])
def view_feedback():
    secret = os.environ.get('PUSH_SECRET', '')
    if not secret or request.args.get('key') != secret:
        return jsonify({'error': 'unauthorized'}), 401
    return jsonify(_load_feedback())


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
    "Duo",
    "password",
    "overloaded",
    "went wrong",
    "already be covered",
    "payment step",
    "parking account",
    "parking structure",
    "parking options",
    "too busy",
    "higher than expected",
    "covered for today",
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
