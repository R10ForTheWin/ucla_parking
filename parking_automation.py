"""
Justin EMBAlake – Playwright automation, decoupled from Telegram.
Called by app.py in background threads.

Session caching (Reggie-style):
  After Duo authenticates successfully, the browser session state (cookies,
  localStorage) is saved to /tmp/justin_cache/<hash>.json.  On subsequent
  runs the saved state is restored and the SSO+Duo steps are skipped entirely,
  reducing the purchase flow from ~3 minutes to ~30 seconds.  The cache is
  valid for 25 days (Duo "remember this device" is ~30 days).  If the cached
  session turns out to be expired the code falls back automatically to the
  full login+Duo flow and saves a fresh session afterwards.

To roll back to the Playwright-only version (no caching):
  git checkout v1-no-session-cache -- parking_automation.py
"""

import hashlib
import json as _json
import os
import re
import time
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

import config

BRUIN_BILL_RETRY_DELAY = 120  # seconds to wait when Bruin Bill is down

# ── Session cache ─────────────────────────────────────────────────────────────

_CACHE_DIR = "/tmp/justin_cache"
_SESSION_TTL = 25 * 24 * 3600   # 25 days — conservative vs Duo's ~30-day remember-me


def _cache_path(username):
    h = hashlib.sha256(username.lower().encode()).hexdigest()[:16]
    return os.path.join(_CACHE_DIR, f"{h}.json")


def _load_session(username):
    """Return saved Playwright storage_state dict, or None if absent/expired."""
    try:
        with open(_cache_path(username)) as f:
            data = _json.load(f)
        age = time.time() - data.get("saved_at", 0)
        if age > _SESSION_TTL:
            print(f"[JUSTIN CACHE] Session too old ({age/3600:.0f}h) — ignoring", flush=True)
            return None
        print(f"[JUSTIN CACHE] Loaded session ({age/3600:.1f}h old) — will try to skip Duo", flush=True)
        return data["storage_state"]
    except Exception:
        return None


def _save_session(username, storage_state):
    """Persist browser session state after successful Duo auth."""
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(_cache_path(username), "w") as f:
            _json.dump({"saved_at": time.time(), "storage_state": storage_state}, f)
        print(f"[JUSTIN CACHE] Session saved — Duo will be skipped for ~25 days", flush=True)
    except Exception as e:
        print(f"[JUSTIN CACHE] Save failed (non-fatal): {e}", flush=True)


def _clear_session(username):
    """Invalidate cached session (called after auth failure)."""
    try:
        os.remove(_cache_path(username))
        print(f"[JUSTIN CACHE] Session cleared", flush=True)
    except Exception:
        pass


# ── Helpers ───────────────────────────────────────────────────────────────────

class BruinBillUnavailable(Exception):
    pass


class PurchaseAlreadyAttempted(Exception):
    """Raised when the transaction was submitted but confirmation could not be verified.
    Should NOT trigger a cache-clear or retry — the permit may already be purchased."""
    pass


class AlreadyHasActivePermit(Exception):
    """Raised when the user already has an active quarterly/long-term permit covering today.
    Not an error — just means no daily purchase is needed."""
    pass


def _ensure_screenshot_dir():
    os.makedirs(config.SCREENSHOT_DIR, exist_ok=True)


def _screenshot(page, name):
    _ensure_screenshot_dir()
    path = os.path.join(config.SCREENSHOT_DIR, f"{name}.png")
    try:
        page.screenshot(path=path, full_page=True)
    except Exception:
        pass
    return path


def _is_captcha_active(page):
    """Return True only if a real CAPTCHA challenge is actually on screen."""
    try:
        if "softblock" in page.url.lower():
            return True
        if page.locator("iframe[src*='recaptcha'], iframe[src*='hcaptcha']").count() > 0:
            return True
        if page.locator("input[id*='captcha'], input[name*='captcha']").count() > 0:
            return True
        body = page.inner_text("body").lower()
        CAPTCHA_PHRASES = [
            "i'm not a robot",
            "verify you are human",
            "enter the characters",
            "enter the code below",
        ]
        if any(p in body for p in CAPTCHA_PHRASES):
            return True
    except Exception:
        pass
    return False


def _wait_for_queue_it(page, cb):
    in_queue = (
        "queue-it" in page.url.lower()
        or "queue.t2hosted" in page.url.lower()
        or "permitlobby.t2hosted" in page.url.lower()
    )
    if not in_queue:
        return

    print(f"[JUSTIN] Queue-it detected. URL: {page.url}", flush=True)
    try:
        body_snippet = page.inner_text("body")[:400]
        print(f"[JUSTIN] Queue-it body: {body_snippet}", flush=True)
    except Exception:
        pass

    if _is_captcha_active(page):
        raise Exception(
            "Queue-it is showing a CAPTCHA — the parking site is under heavy load. "
            "Purchase manually: https://bruinepermit.t2hosted.com"
        )

    cb("In the parking site queue — please wait, this may take a few minutes...")
    deadline = time.time() + config.QUEUE_IT_TIMEOUT
    while time.time() < deadline:
        url = page.url.lower()
        if "queue-it" not in url and "queue.t2hosted" not in url and "permitlobby.t2hosted" not in url:
            cb("Through the queue!")
            return
        if _is_captcha_active(page):
            raise Exception("CAPTCHA detected — purchase manually: https://bruinepermit.t2hosted.com")
        time.sleep(5)
    raise TimeoutError("Stuck in Queue-it waiting room — try again in a moment.")


def _handle_duo(page, method, duo_provider, cb, check_passcode_switch=None):
    """Wait for DUO, then authenticate via push or passcode.

    UCLA SSO has two Duo integration modes:
    - Universal Prompt: browser redirects to duosecurity.com (standalone page)
    - Classic (Shibboleth-embedded): Duo iframe on shb.ais.ucla.edu (no redirect)

    method: "push"     — Duo Push to phone (tap Approve)
            "passcode" — enter 6-digit code
    """
    cb("DUO authentication required...")

    _screenshot(page, "duo_start")
    loc = page
    current_url = page.url
    print(f"[JUSTIN DUO] Entry URL: {current_url}", flush=True)

    if "bruinepermit.t2hosted.com" in current_url:
        # Auto-approved before we even started
        cb("DUO verified!")
        return

    if "duosecurity.com" in current_url:
        # Already on standalone Duo page — just wait for it to settle
        print(f"[JUSTIN DUO] Already on standalone Duo page", flush=True)
        page.wait_for_load_state("networkidle")

    elif "shb.ais.ucla.edu" in current_url:
        # UCLA changed their Duo integration (effective ~March 31 2026):
        # Duo is now embedded inline on the Shibboleth e1s3 page instead of
        # redirecting to duosecurity.com.  No need to wait — interact directly.
        print(f"[JUSTIN DUO] On Shibboleth page — using inline Duo (no redirect expected)", flush=True)
        page.wait_for_load_state("networkidle")

        # Log all iframes for diagnosis
        try:
            all_iframes = page.evaluate("""() => Array.from(document.querySelectorAll('iframe')).map(f => ({
                id: f.id, name: f.name, src: f.src, title: f.title, className: f.className
            }))""")
            print(f"[JUSTIN DUO] Iframes on page: {all_iframes}", flush=True)
        except Exception:
            pass

        # Check for a Duo iframe (classic integration)
        for iframe_sel in ["iframe#duo_iframe", "iframe[id*='duo']",
                           "iframe[src*='duosecurity']", "iframe[title*='Duo']"]:
            if page.locator(iframe_sel).count() > 0:
                print(f"[JUSTIN DUO] Found Duo iframe: {iframe_sel}", flush=True)
                loc = page.frame_locator(iframe_sel)
                break
        else:
            # No iframe — Duo is rendered inline as a div (Web SDK v4)
            print(f"[JUSTIN DUO] No iframe — will interact with page directly", flush=True)

    else:
        # Unknown starting URL — wait up to 30s for a redirect to duosecurity.com
        print(f"[JUSTIN DUO] Unexpected URL {current_url} — waiting for Duo redirect", flush=True)
        try:
            page.wait_for_url(re.compile(r'duosecurity\.com'), timeout=30000)
            print(f"[JUSTIN DUO] Redirected to standalone Duo", flush=True)
        except PwTimeout:
            _screenshot(page, "duo_timeout")
            url = page.url
            body_text = ""
            try:
                body_text = page.inner_text("body")
            except Exception:
                pass
            print(f"[JUSTIN DUO] No redirect after 30s. URL: {url}", flush=True)
            print(f"[JUSTIN DUO] Body:\n{body_text[:400]}", flush=True)
            body_lower = body_text.lower()
            if any(p in body_lower for p in [
                "incorrect", "invalid", "login failed",
                "authentication failed", "wrong password", "please try again",
            ]):
                raise Exception(
                    "UCLA login failed — wrong username or password. "
                    "Go to Setup and double-check your credentials."
                )
            raise Exception(
                f"DUO page did not load (still on {url}). "
                "Check your UCLA credentials and try again."
            )
        page.wait_for_load_state("networkidle")

    # Duo sometimes auto-approves (remembered session) — already on bruinepermit
    if "bruinepermit.t2hosted.com" in page.url:
        cb("DUO verified!")
        return

    # UCLA's Duo Universal Prompt hides auth methods behind "Other options".
    # After clicking it, the method buttons render asynchronously (React SPA) —
    # networkidle fires before they appear, so wait for actual button content instead.
    for role in ("button", "link"):
        try:
            loc.get_by_role(role, name=re.compile("other options", re.IGNORECASE)).first.click(timeout=5000)
            try:
                page.wait_for_function(
                    "() => Array.from(document.querySelectorAll('button,a,[role=button]'))"
                    ".some(e => /push|passcode|bypass|security/i.test((e.innerText||'').trim()))",
                    timeout=8000,
                )
            except Exception:
                page.wait_for_timeout(3000)  # fallback: fixed 3s wait
            break
        except Exception:
            pass

    if "bruinepermit.t2hosted.com" in page.url:
        cb("DUO verified!")
        return

    try:
        if loc is page:
            btns = page.evaluate("() => Array.from(document.querySelectorAll('button,a,[role=button],[role=link]')).map(e => e.innerText.trim()).filter(t => t)")
        else:
            btns = loc.locator("button, a").all_inner_texts()
        print(f"[JUSTIN DUO] Buttons/links after expand: {btns}", flush=True)
    except Exception:
        pass

    if method == "push":
        clicked = False
        for label in ("Duo Push", "Send me a Push", "Push Notification", "Push"):
            for role in ("button", "link"):
                try:
                    loc.get_by_role(role, name=re.compile(label, re.IGNORECASE)).first.click(timeout=8000)
                    clicked = True
                    break
                except Exception:
                    pass
            if clicked:
                break
        if not clicked:
            cb("Duo Push not available — switching to passcode...")
            method = "passcode"
        else:
            duo_provider()  # sets status=awaiting_duo_push, returns immediately
            # Poll in 5-second increments so we can detect a mid-wait switch request
            deadline = time.time() + config.DUO_WAIT_TIMEOUT
            approved = False
            while time.time() < deadline:
                try:
                    page.wait_for_url("**/bruinepermit.t2hosted.com/**", timeout=5000)
                    approved = True
                    break
                except PwTimeout:
                    if check_passcode_switch and check_passcode_switch():
                        cb("Switching to passcode...")
                        break
            if approved:
                cb("DUO verified!")
                return
            # Push timed out or user switched — fall through to passcode
            cb("Duo Push didn't arrive — enter a passcode to continue...")
            if check_passcode_switch:
                check_passcode_switch(force=True)  # ensure duo_provider enters passcode mode
            method = "passcode"

    # ── Passcode flow ──────────────────────────────────────────────────────
    # If we fell through from a timed-out push, the page is still on the
    # "awaiting push" screen.  Click "Other options" to get back to the
    # method selector before looking for the passcode button.
    for role in ("button", "link"):
        try:
            loc.get_by_role(role, name=re.compile("other options", re.IGNORECASE)).first.click(timeout=5000)
            try:
                page.wait_for_function(
                    "() => Array.from(document.querySelectorAll('button,a,[role=button]'))"
                    ".some(e => /passcode/i.test((e.innerText||'').trim()))",
                    timeout=8000,
                )
            except Exception:
                page.wait_for_timeout(2000)
            break
        except Exception:
            pass

    clicked = False
    for label in ("Duo Mobile passcode", "Use a Passcode", "Enter a Passcode", "Send a passcode", "Passcode"):
        for role in ("button", "link"):
            try:
                loc.get_by_role(role, name=re.compile(label, re.IGNORECASE)).first.click(timeout=8000)
                clicked = True
                break
            except Exception:
                pass
        if clicked:
            break
    if not clicked:
        _screenshot(page, "duo_no_button")
        raise Exception("Could not find passcode button on the DUO page — try again.")

    try:
        loc.get_by_role("textbox").first.wait_for(timeout=30000)
    except Exception:
        _screenshot(page, "duo_no_textbox")
        raise Exception("DUO did not show a passcode input — try again.")

    code = duo_provider()
    if not code:
        raise TimeoutError("DUO passcode not received — timed out after 5 minutes.")

    loc.get_by_role("textbox").first.fill(code)
    loc.get_by_role("textbox").first.press("Enter")

    page.wait_for_url("**/bruinepermit.t2hosted.com/**", timeout=config.PAGE_LOAD_TIMEOUT)
    cb("DUO verified!")


_PERMIT_EXCLUDED = ["quarterly", "quarter", "night", "annual", "monthly"]

def _permit_label_is_daily(label_text):
    """Return True only if the label looks like a 1-day permit, not quarterly/night/long-term."""
    lt = label_text.lower()
    if any(kw in lt for kw in _PERMIT_EXCLUDED):
        return False
    return any(kw in lt for kw in ["1-day", "daily", "yellow"])

def _find_permit_radio(page):
    all_radios = page.get_by_role("radio").all()
    candidates = []
    for radio in all_radios:
        radio_id = radio.get_attribute("id") or ""
        if radio_id:
            label = page.locator(f"label[for='{radio_id}']")
            if label.count() > 0:
                label_text = label.first.inner_text()
                print(f"[JUSTIN PERMIT] Radio id={radio_id} label={label_text!r}", flush=True)
                if _permit_label_is_daily(label_text):
                    candidates.append((radio, label_text))

    # Prefer "1-Day Student" exact match first, then any daily
    for radio, lbl in candidates:
        if "1-day student" in lbl.lower():
            return radio
    if candidates:
        return candidates[0][0]

    labels = []
    for radio in all_radios:
        rid = radio.get_attribute("id") or ""
        label = page.locator(f"label[for='{rid}']").first.inner_text() if rid else ""
        labels.append(label.strip())
    print(f"[JUSTIN PERMIT] All radios: {labels}", flush=True)

    if labels:
        listed = ", ".join(f'"{l}"' for l in labels if l)
        raise Exception(
            f"1-Day Student permits are not available right now — "
            f"the site is only showing: {listed}. "
            f"You may need to purchase manually: https://bruinepermit.t2hosted.com"
        )
    raise Exception(
        "No permits found on the page — the UCLA parking site may be down or changed. "
        "Try purchasing manually: https://bruinepermit.t2hosted.com"
    )


def _check_payment_failure(page):
    """After clicking Complete Transaction, scan the page body for real UCLA
    payment failure messages and raise a clear exception if found.
    Only call this after the transaction has been submitted."""
    try:
        body = page.inner_text("body")
    except Exception:
        return
    body_lower = body.lower()

    FAILURE_SIGNALS = [
        ("payment was declined",          "Payment was declined by your bank — contact your bank or try again."),
        ("unable to process",             "UCLA was unable to process the payment — try again or buy manually: https://bruinepermit.t2hosted.com"),
        ("transaction could not",         "Transaction could not be completed — try again or buy manually: https://bruinepermit.t2hosted.com"),
        ("payment method is not",         "Payment method not accepted — buy manually: https://bruinepermit.t2hosted.com"),
        ("bruin bill account",            "Bruin Bill account issue — check your account at https://bruinepermit.t2hosted.com"),
        ("insufficient funds",            "Insufficient funds on Bruin Bill — add funds and try again."),
        ("system is currently unavailable", "UCLA payment system is temporarily unavailable — try again in a few minutes."),
        ("error processing",              "Error processing payment — try again or buy manually: https://bruinepermit.t2hosted.com"),
    ]

    for signal, message in FAILURE_SIGNALS:
        if signal in body_lower:
            print(f"[JUSTIN PAYMENT] Failure signal detected: '{signal}'", flush=True)
            raise Exception(message)


def _verify_purchase_success(page):
    try:
        page.wait_for_load_state("networkidle", timeout=config.PAGE_LOAD_TIMEOUT)
        _screenshot(page, "after_purchase")
        body = page.inner_text("body")
        print(f"[JUSTIN VERIFY] Page URL: {page.url}", flush=True)
        print(f"[JUSTIN VERIFY] Page body (first 600 chars):\n{body[:600]}", flush=True)
        for pattern in ["confirmation", "receipt", "transaction complete", "successfully",
                        "permit number", "issued", "approved", "thank you", "order"]:
            if pattern.lower() in body.lower():
                # Try bracketed permit number first: e.g. "[85CSY00011593511]" in description
                bracket_match = re.search(r'\[([A-Z0-9]{8,})\]', body)
                if bracket_match:
                    return f"Confirmation #{bracket_match.group(1)}"
                # Fallback: "permit/order/confirmation number: XXXXX"
                match = re.search(
                    r'(?:confirmation|receipt|transaction|permit|order)\s*(?:#|number|no)[:\s]*(\w{3,})',
                    body, re.IGNORECASE
                )
                if match:
                    return f"Confirmation #{match.group(1)}"
                return "Purchase confirmed"
        return None
    except Exception:
        return None


_STRUCTURE_NAMES = {
    config.STRUCTURE_4:  ("P4", ["structure 4", "str 4", "str. 4"]),
    config.STRUCTURE_P7: ("P7", ["structure 7", "str 7", "str. 7", "p7"]),
    config.STRUCTURE_32: ("P32", ["structure 32", "str 32", "str. 32"]),
}

_LONG_TERM_KEYWORDS = ["quarterly", "quarter", "annual", "monthly"]


def _check_already_has_permit_today(page, structure_value):
    """After login, check the permit list for any active permit at the target structure
    that already covers today — either a long-term/quarterly permit or a daily already
    purchased today (guards against double-purchase on retry).

    Returns a human-readable string if found, else None.
    Always silently returns None on any error — never blocks the flow.
    """
    try:
        from datetime import datetime
        try:
            from zoneinfo import ZoneInfo
            today = datetime.now(ZoneInfo("America/Los_Angeles")).date()
        except ImportError:
            import time as _t
            utc_offset = -8 if _t.localtime().tm_isdst == 0 else -7
            from datetime import timezone, timedelta
            today = datetime.now(timezone(timedelta(hours=utc_offset))).date()

        today_fmt = today.strftime("%m/%d/%Y")
        _, structure_keywords = _STRUCTURE_NAMES.get(structure_value, ("", []))

        page.goto(
            "https://bruinepermit.t2hosted.com/per/listpermit.aspx",
            timeout=config.PAGE_LOAD_TIMEOUT,
            wait_until="domcontentloaded",
        )
        page.wait_for_load_state("domcontentloaded", timeout=config.PAGE_LOAD_TIMEOUT)

        rows = page.locator("table tr").all()
        for row in rows:
            try:
                cells = row.locator("td").all()
                if len(cells) < 6:
                    continue
                permit_num    = cells[0].inner_text().strip()
                permit_type   = cells[1].inner_text().strip()
                status        = cells[2].inner_text().strip().lower()
                effective_text = cells[4].inner_text().strip()
                expiry_text   = cells[5].inner_text().strip()
                location      = cells[6].inner_text().strip().lower() if len(cells) > 6 else ""

                if status != "active":
                    continue

                type_lower = permit_type.lower()
                loc_lower  = location.lower()
                at_structure = any(kw in type_lower or kw in loc_lower for kw in structure_keywords)
                if not at_structure:
                    continue

                try:
                    effective = datetime.strptime(effective_text, "%m/%d/%Y").date()
                    expiry    = datetime.strptime(expiry_text,    "%m/%d/%Y").date()
                except ValueError:
                    continue

                # Permit must be currently valid (effective ≤ today ≤ expiry)
                if not (effective <= today <= expiry):
                    continue

                is_long_term = any(kw in type_lower for kw in _LONG_TERM_KEYWORDS)
                is_daily_today = (effective == today == expiry)  # 1-day permit effective and expiring today

                if is_long_term:
                    print(f"[JUSTIN PERMIT_CHECK] Active long-term permit: #{permit_num} ({permit_type}) {effective_text}–{expiry_text}", flush=True)
                    return f"#{permit_num} ({permit_type}) — expires {expiry_text}"
                elif is_daily_today:
                    print(f"[JUSTIN PERMIT_CHECK] Daily permit already purchased today: #{permit_num} ({permit_type})", flush=True)
                    return f"#{permit_num} ({permit_type}) — already purchased today"

            except Exception:
                continue
    except Exception as e:
        print(f"[JUSTIN PERMIT_CHECK] Could not check permit list: {e}", flush=True)
    return None


def _verify_cart_is_daily(page):
    """Read the cart page before clicking 'Proceed with Transaction'.
    Raises an exception if the cart contains a quarterly/non-daily permit or
    if the price exceeds the daily permit ceiling ($30).
    """
    try:
        body = page.inner_text("body")
        body_lower = body.lower()

        if any(kw in body_lower for kw in _LONG_TERM_KEYWORDS):
            raise Exception(
                "Safety check failed: the cart contains a non-daily permit "
                "(quarterly/seasonal). Purchase cancelled to protect your account. "
                "Check https://bruinepermit.t2hosted.com and purchase a 1-Day Student permit manually."
            )

        # Extract any dollar amounts from the cart body and reject if any exceed $30
        import re as _re
        amounts = [float(m.replace(",", "")) for m in _re.findall(r'\$\s*([\d,]+\.\d{2})', body)]
        print(f"[JUSTIN CART_GUARD] Cart amounts: {amounts}", flush=True)
        suspicious = [a for a in amounts if a > 15]
        if suspicious:
            raise Exception(
                f"Safety check failed: cart total ${max(suspicious):.2f} exceeds the daily permit limit ($15). "
                f"Purchase cancelled to protect your account. "
                f"Check https://bruinepermit.t2hosted.com before trying again."
            )
    except Exception as e:
        if "Safety check failed" in str(e):
            raise
        print(f"[JUSTIN CART_GUARD] Could not verify cart: {e}", flush=True)


# ── Permit flow (steps shared by both full and cached paths) ──────────────────

def _enter_permit_flow(page, cb):
    """Navigate to Account/Portal and enter the permit purchase flow.

    If called with a valid cached session, UCLA SSO auto-redirects after
    'UCLA Logon' without asking for credentials or Duo again.
    If the session is expired, the site will redirect to the SSO login page
    and the caller should detect that and fall back to the full flow.

    Returns "cart_ready" if an orphaned cart with today's permit was found and
    selected — callers should skip permit-selection steps and go straight to
    checkout.  Returns None for the normal flow.
    """
    cb("Opening the UCLA parking site...")
    page.goto(
        "https://bruinepermit.t2hosted.com/Account/Portal",
        timeout=config.PAGE_LOAD_TIMEOUT,
        wait_until="domcontentloaded",
    )
    _wait_for_queue_it(page, cb)

    # ── Orphaned cart handling ─────────────────────────────────────────────────
    # UCLA redirects to assumeOrphanedCart.aspx when a previous run (including
    # real_dry_run) left items in a cart.  The right move is to SELECT the
    # basket that already has today's permit — we can go straight to checkout
    # and skip all the permit-selection steps.  If the basket is stale (wrong
    # date) we cancel it and start fresh.
    if "assumeOrphanedCart" in page.url:
        print(f"[JUSTIN] Orphaned cart page — URL: {page.url}", flush=True)
        cb("Checking previous cart...")

        # Today's date in PT, formatted to match UCLA's permit description
        # e.g. "04/15/2026" as in "Yellow / 1-Day Student (04/15/2026 - 04/15/2026)"
        from datetime import datetime
        try:
            from zoneinfo import ZoneInfo
            la_now = datetime.now(ZoneInfo("America/Los_Angeles"))
        except ImportError:
            from datetime import timezone, timedelta
            # Fallback: approximate PT offset (handles both PST/PDT conservatively)
            import time as _time
            utc_offset = -8 if _time.localtime().tm_isdst == 0 else -7
            la_now = datetime.now(timezone(timedelta(hours=utc_offset)))
        today_fmt = la_now.strftime("%m/%d/%Y")

        selected_with_permit = False
        rows = page.locator("table tr").all()
        for row in rows:
            try:
                row_text = row.inner_text()
            except Exception:
                continue
            # Non-empty basket: has a dollar amount > $0.00
            if "$" in row_text and "$0.00" not in row_text:
                try:
                    row.get_by_role("link", name=re.compile("select", re.IGNORECASE)).first.click(timeout=5000)
                    page.wait_for_load_state("domcontentloaded", timeout=config.PAGE_LOAD_TIMEOUT)
                    selected_with_permit = True
                    print(f"[JUSTIN] Selected non-empty basket — now at {page.url}", flush=True)
                    break
                except Exception:
                    continue

        if selected_with_permit and "crt/" in page.url:
            # Verify the permit in this cart is for today AND is a 1-day permit (not quarterly)
            body = page.inner_text("body")
            body_lower = body.lower()
            is_today = today_fmt in body
            is_daily = any(kw in body_lower for kw in ["1-day", "daily"])
            is_quarterly = any(kw in body_lower for kw in ["quarterly", "quarter"])
            print(f"[JUSTIN] Orphaned cart check — today={is_today} daily={is_daily} quarterly={is_quarterly}", flush=True)
            if is_today and is_daily and not is_quarterly:
                print(f"[JUSTIN] Orphaned cart has today's 1-day permit ({today_fmt}) — proceeding to checkout", flush=True)
                cb("Found today's permit in cart — proceeding to checkout...")
                if "bruin bill is not currently available" in body.lower():
                    print(f"[JUSTIN] Note: Bruin Bill warning present — this is non-fatal, continuing", flush=True)
                return "cart_ready"
            else:
                # Stale permit, quarterly, or wrong type — cancel it and start fresh
                if is_quarterly:
                    print(f"[JUSTIN] Orphaned cart has a QUARTERLY permit — cancelling, will buy fresh daily", flush=True)
                    cb("Clearing wrong permit type from cart...")
                else:
                    print(f"[JUSTIN] Orphaned cart has stale permit (not {today_fmt}) — cancelling", flush=True)
                    cb("Clearing stale cart...")
                try:
                    page.get_by_role("button", name=re.compile("cancel purchase", re.IGNORECASE)).click(timeout=8000)
                    page.wait_for_load_state("domcontentloaded", timeout=config.PAGE_LOAD_TIMEOUT)
                except Exception:
                    pass
                # Re-navigate to portal for a clean start
                page.goto(
                    "https://bruinepermit.t2hosted.com/Account/Portal",
                    timeout=config.PAGE_LOAD_TIMEOUT,
                    wait_until="domcontentloaded",
                )
        elif not selected_with_permit:
            # No non-empty basket — select the $0.00 empty basket to discard all orphans
            print(f"[JUSTIN] No non-empty basket found — selecting empty basket", flush=True)
            for row in page.locator("table tr").all():
                try:
                    row_text = row.inner_text()
                except Exception:
                    continue
                if "$0.00" in row_text:
                    try:
                        row.get_by_role("link", name=re.compile("select", re.IGNORECASE)).first.click(timeout=5000)
                        page.wait_for_load_state("domcontentloaded", timeout=config.PAGE_LOAD_TIMEOUT)
                        print(f"[JUSTIN] Empty basket selected — now at {page.url}", flush=True)
                        break
                    except Exception:
                        continue

    # ── Normal portal flow ─────────────────────────────────────────────────────
    # Note: UCLA shows a "Bruin Bill not available for Event Permits" warning on
    # both the portal and cart pages.  This is informational only — the transaction
    # still processes via Bruin Bill ("Invoice Sent to Bruin Bill" on confirmation).
    # Do NOT raise here; just log and continue.
    body_text = page.inner_text("body")
    print(f"[JUSTIN] Page URL: {page.url}", flush=True)
    if "bruin bill is not currently available" in body_text.lower():
        print(f"[JUSTIN] Note: Bruin Bill warning detected — non-fatal, continuing", flush=True)

    cb("Starting permit flow...")
    clicked = False
    for btn_text in ["Get Permits", "Buy Permits", "Purchase Permits", "Permits"]:
        try:
            page.get_by_role("button", name=re.compile(btn_text, re.IGNORECASE)).click(timeout=5000)
            clicked = True
            break
        except Exception:
            pass
    if not clicked:
        page.locator("a, button").filter(
            has_text=re.compile(r"permit", re.IGNORECASE)
        ).first.click(timeout=10000)

    try:
        page.get_by_role("button", name="UCLA Logon").click(timeout=8000)
    except Exception:
        pass

    page.wait_for_load_state("domcontentloaded", timeout=config.PAGE_LOAD_TIMEOUT)
    print(f"[JUSTIN] After UCLA Logon: {page.url}", flush=True)
    return None


def _run_permit_steps(page, structure, cb, dry_run=False, cart_ready=False, vehicle_provider=None, structure_provider=None, on_config_drift=None):
    """Steps 6–9: permit → vehicle → structure → checkout.
    Called from both the full flow and the cached-session fast path.

    cart_ready=True: an orphaned cart with today's permit was already selected
    in _enter_permit_flow — skip steps 6-8 and go straight to checkout.
    """
    # If the cart is already loaded (orphaned basket with today's permit),
    # skip all permit/vehicle/structure selection and jump to transaction.
    if cart_ready or "crt/" in page.url:
        print(f"[JUSTIN] Cart already ready at {page.url} — skipping permit selection", flush=True)
        if dry_run:
            cb("Dry run complete — stopping before Process Transaction.")
            return "dry_run"
        # Fall through to Step 9 below
        cb("Cart ready — processing transaction...")
        _screenshot(page, "pre_purchase")
        _verify_cart_is_daily(page)

        pre_purchase_url = page.url
        page.get_by_role("button", name=re.compile("Proceed with Transaction", re.IGNORECASE)).click()
        try:
            page.wait_for_load_state("networkidle", timeout=config.PAGE_LOAD_TIMEOUT)
        except Exception:
            pass
        complete_btn = page.get_by_role("button", name=re.compile("Complete Transaction", re.IGNORECASE))
        if complete_btn.count() > 0:
            print(f"[JUSTIN VERIFY] Payment review step — clicking Complete Transaction", flush=True)
            cb("Completing transaction...")
            _screenshot(page, "pre_complete")
            payment_url = page.url  # new baseline — we're now on Payment Info page
            complete_btn.click()
            try:
                page.wait_for_function(
                    f"() => window.location.href !== '{payment_url}'",
                    timeout=config.PAGE_LOAD_TIMEOUT,
                )
            except Exception:
                raise Exception("Payment did not advance after clicking 'Complete Transaction' — check manually: https://bruinepermit.t2hosted.com")
        else:
            if page.url == pre_purchase_url:
                raise Exception("Payment page did not advance — check manually: https://bruinepermit.t2hosted.com")
        _check_payment_failure(page)
        result = _verify_purchase_success(page)
        if result:
            return result
        raise PurchaseAlreadyAttempted("Transaction was submitted but no confirmation page appeared — check your email and https://bruinepermit.t2hosted.com before trying again.")

    # Step 6: Select permit type
    cb("Selecting permit...")
    page.wait_for_url("**/per/index.aspx", timeout=config.PAGE_LOAD_TIMEOUT)
    page.wait_for_load_state("domcontentloaded")

    try:
        idx_body = page.inner_text("body")
        print(f"[JUSTIN INDEX] per/index.aspx body:\n{idx_body[:800]}", flush=True)
        radios = page.get_by_role("radio").all()
        for r in radios:
            rid = r.get_attribute("id") or ""
            val = r.get_attribute("value") or ""
            lbl = page.locator(f"label[for='{rid}']").first.inner_text() if rid else ""
            print(f"[JUSTIN INDEX] Radio id={rid} value={val} label={lbl!r}", flush=True)
        selects = page.locator("select").all()
        for sel in selects:
            sel_name = sel.get_attribute("name") or sel.get_attribute("id") or ""
            opts = sel.evaluate("el => Array.from(el.options).map(o => ({value: o.value, text: o.text}))")
            print(f"[JUSTIN INDEX] Select {sel_name!r}: {opts}", flush=True)
    except Exception as e:
        print(f"[JUSTIN INDEX] Log error: {e}", flush=True)

    # Only match daily permit categories — explicitly skip quarterly/night/long-term
    EXCLUDED_CATEGORY_KEYWORDS = ["quarterly", "quarter", "night", "annual", "monthly"]
    selected_category = False
    for pattern in ["1-Day", "Daily"]:
        try:
            radios_matched = page.get_by_role("radio", name=re.compile(pattern, re.IGNORECASE))
            for i in range(radios_matched.count()):
                r = radios_matched.nth(i)
                rid = r.get_attribute("id") or ""
                lbl = page.locator(f"label[for='{rid}']").first.inner_text() if rid else ""
                if not any(kw in lbl.lower() for kw in EXCLUDED_CATEGORY_KEYWORDS):
                    print(f"[JUSTIN INDEX] Selecting permit category: {lbl!r}", flush=True)
                    r.check()
                    selected_category = True
                    break
            if selected_category:
                break
        except Exception:
            pass
    if not selected_category:
        print(f"[JUSTIN INDEX] WARNING: Could not find a 1-Day/Daily category — will let UCLA default", flush=True)
        except Exception:
            pass

    page.get_by_role("button", name="Next >>").click()
    page.wait_for_url("**/per/selectpermit.aspx", timeout=config.PAGE_LOAD_TIMEOUT)

    permit_radio = _find_permit_radio(page)
    permit_radio.check()
    page.get_by_role("checkbox", name="I agree to abide by my").check()
    page.get_by_role("checkbox", name="I agree to the University").check()
    page.get_by_role("button", name="Next >>").click()

    # Step 7: Select vehicle
    cb("Selecting vehicle...")
    page.wait_for_load_state("domcontentloaded", timeout=config.PAGE_LOAD_TIMEOUT)
    page.get_by_role("checkbox").first.wait_for(timeout=config.PAGE_LOAD_TIMEOUT)
    checkboxes = page.get_by_role("checkbox").all()
    if len(checkboxes) == 0:
        raise Exception("No vehicles found on your UCLA account — add a vehicle at bruinepermit.t2hosted.com first.")

    if len(checkboxes) >= 2 and vehicle_provider is not None:
        labels = []
        for i, cb_el in enumerate(checkboxes):
            try:
                label = cb_el.evaluate(
                    "el => { let row = el.closest('tr'); if (row) return row.innerText.trim();"
                    " let p = el.parentElement; return p ? p.innerText.trim() : ''; }"
                )
                label = " ".join(label.split())
            except Exception:
                label = ""
            labels.append(label or f"Vehicle {i + 1}")
        chosen = vehicle_provider(labels)
        try:
            checkboxes[chosen].check()
        except Exception:
            checkboxes[0].check()
    else:
        try:
            checkboxes[0].check()
        except Exception:
            raise Exception("Could not select vehicle — check your UCLA account at bruinepermit.t2hosted.com.")

    page.get_by_role("button", name="Next >>").click()

    # Step 8: Select parking structure
    cb("Selecting parking structure...")
    page.wait_for_load_state("domcontentloaded", timeout=config.PAGE_LOAD_TIMEOUT)
    page.get_by_label("Parking Area").wait_for(timeout=config.PAGE_LOAD_TIMEOUT)
    dropdown = page.get_by_label("Parking Area")
    available_options = dropdown.evaluate(
        "el => Array.from(el.options).map(o => ({value: o.value, text: o.text, disabled: o.disabled}))"
    )
    print(f"[JUSTIN DROPDOWN] Available options: {available_options}", flush=True)

    selected_available = any(
        o["value"] == structure and not o.get("disabled", False)
        for o in available_options
    )

    if not selected_available:
        _structure_names    = {config.STRUCTURE_4: "P4", config.STRUCTURE_P7: "P7", config.STRUCTURE_32: "P32"}
        _structure_keywords = {config.STRUCTURE_4: "str 4", config.STRUCTURE_P7: "str 7", config.STRUCTURE_32: "str 32"}
        sold_out_name = _structure_names.get(structure, "Your preferred structure")
        keyword = _structure_keywords.get(structure, "")

        # Check if UCLA changed the dropdown ID but the structure is still available by name
        name_match = next(
            (o for o in available_options
             if keyword and keyword in o["text"].lower() and not o.get("disabled", False)),
            None
        )
        if name_match:
            print(f"[JUSTIN CONFIG_DRIFT] {sold_out_name} ID changed {structure} → {name_match['value']} — update config.py", flush=True)
            if on_config_drift:
                on_config_drift(sold_out_name, structure, name_match["value"])
            structure = name_match["value"]
        else:
            # Structure is genuinely unavailable — offer alternatives
            real_options = [o for o in available_options
                            if not o.get('disabled', False)
                            and o['value'] not in ('', 'Select One', '-1', '0')]

            if not real_options:
                print(f"[JUSTIN DROPDOWN] Full options: {available_options}", flush=True)
                raise Exception("No parking structures are available today — check bruinepermit.t2hosted.com.")

            if structure_provider is not None:
                chosen_value = structure_provider(real_options, sold_out_name)
                if chosen_value is None:
                    raise Exception("Purchase cancelled — your preferred parking structure was sold out.")
                structure = chosen_value
            else:
                # Fallback when no provider is wired up: auto-switch to configured alternate
                alt = config.STRUCTURE_P7 if structure == config.STRUCTURE_4 else config.STRUCTURE_4
                alt_opt = next((o for o in real_options if o['value'] == alt), None)
                if alt_opt:
                    cb(f"{sold_out_name} is sold out — automatically switching...")
                    structure = alt
                elif len(real_options) == 1:
                    cb(f"Selecting {real_options[0]['text']}...")
                    structure = real_options[0]['value']
                else:
                    avail_str = ", ".join(o['text'] for o in real_options)
                    raise Exception(f"{sold_out_name} is sold out — available today: {avail_str}.")

    dropdown.select_option(structure)
    page.get_by_role("button", name="Next >>").click()

    _screenshot(page, "pre_purchase")

    if dry_run:
        cb("Dry run complete — stopping before Process Transaction.")
        return "dry_run"

    # Step 9: Process transaction
    # The checkout flow has two steps on the same URL (crt/collect.aspx):
    #   9a. "Select Payment Method" → click "Proceed with Transaction"
    #   9b. "Payment Information"   → click "Complete Transaction"
    _verify_cart_is_daily(page)
    cb("Processing transaction — this can take up to 30 seconds...")
    pre_purchase_url = page.url
    page.get_by_role("button", name="Proceed with Transaction").click()

    # Let the page settle (may stay on the same URL with new content)
    try:
        page.wait_for_load_state("networkidle", timeout=config.PAGE_LOAD_TIMEOUT)
    except Exception:
        pass

    # Check if we landed on the payment review step ("Complete Transaction" button)
    # Note: "Proceed with Transaction" on crt/view.aspx navigates to a *new* URL
    # (the Payment Information page), so pre_purchase_url is now stale. Capture
    # the current URL as the new baseline before clicking Complete Transaction.
    complete_btn = page.get_by_role("button", name="Complete Transaction")
    if complete_btn.count() > 0:
        print(f"[JUSTIN VERIFY] Payment review step detected — clicking Complete Transaction", flush=True)
        cb("Completing transaction...")
        _screenshot(page, "pre_complete")
        payment_url = page.url  # capture Payment Info page URL as new baseline
        complete_btn.click()
        # Wait for navigation away from the payment page to the confirmation page
        try:
            page.wait_for_function(
                f"() => window.location.href !== '{payment_url}'",
                timeout=config.PAGE_LOAD_TIMEOUT,
            )
        except Exception:
            print(f"[JUSTIN VERIFY] URL did not change after clicking Complete Transaction — still at {page.url}", flush=True)
            raise Exception("Payment did not advance after clicking 'Complete Transaction' — check manually: https://bruinepermit.t2hosted.com")
    else:
        # No "Complete Transaction" button — "Proceed" navigated directly to confirmation.
        if page.url == pre_purchase_url:
            print(f"[JUSTIN VERIFY] URL did not change after clicking Proceed — still at {page.url}", flush=True)
            raise Exception("Payment page did not advance — check manually: https://bruinepermit.t2hosted.com")

    _check_payment_failure(page)
    result = _verify_purchase_success(page)
    if result:
        return result
    raise PurchaseAlreadyAttempted("Transaction was submitted but no confirmation page appeared — check your email and https://bruinepermit.t2hosted.com before trying again.")


# ── Purchase flows ────────────────────────────────────────────────────────────

def _do_purchase_full(page, context, username, password, structure, cb, duo_provider,
                      duo_method="push", dry_run=False, save_session_for=None,
                      check_passcode_switch=None, vehicle_provider=None, structure_provider=None, on_config_drift=None):
    """Full flow: navigate → UCLA login → Duo → permit steps.

    save_session_for: if set (username string), saves browser session after
    Duo so future runs can skip login+Duo entirely.
    """
    cart_ready_status = _enter_permit_flow(page, cb)
    if cart_ready_status == "cart_ready":
        return _run_permit_steps(page, structure, cb, dry_run, cart_ready=True, vehicle_provider=vehicle_provider, structure_provider=structure_provider, on_config_drift=on_config_drift)

    # Step 3: UCLA SSO login
    cb("Logging in with your UCLA credentials...")
    page.get_by_placeholder("Your UCLA Logon ID").fill(username)
    page.get_by_placeholder("Your UCLA Logon ID").press("Tab")
    page.get_by_placeholder("Your UCLA Logon Password").fill(password)
    page.get_by_role("button", name="Sign In").click()

    page.wait_for_load_state("domcontentloaded")
    print(f"[JUSTIN SSO] After Sign In — URL: {page.url}", flush=True)
    sso_body = ""
    try:
        sso_body = page.inner_text("body")
        print(f"[JUSTIN SSO] Body:\n{sso_body[:800]}", flush=True)
    except Exception:
        pass
    if "shb.ais.ucla.edu" in page.url and sso_body:
        sso_lower = sso_body.lower()
        if any(p in sso_lower for p in [
            "incorrect", "invalid", "login failed",
            "authentication failed", "wrong password", "please try again",
        ]):
            raise Exception(
                "UCLA login failed — wrong username or password. "
                "Go to Setup and double-check your credentials."
            )
    _screenshot(page, "after_sign_in")

    # Step 4: DUO 2FA
    _handle_duo(page, duo_method, duo_provider, cb,
                check_passcode_switch=check_passcode_switch)

    # ── Save session immediately after Duo so next run can skip this whole section ──
    if save_session_for:
        _save_session(save_session_for, context.storage_state())

    cb("Checking for existing permits...")
    existing = _check_already_has_permit_today(page, structure)
    if existing:
        raise AlreadyHasActivePermit(existing)

    cb("Navigating to permits...")
    print(f"[JUSTIN POST-DUO] URL: {page.url}", flush=True)

    # If Duo auto-approved and landed us off the permit path, re-enter via Account/Portal
    if "bruinepermit.t2hosted.com" in page.url and "per/index.aspx" not in page.url and "crt/" not in page.url:
        print(f"[JUSTIN] Re-entering permit flow from {page.url}", flush=True)
        cb("Setting up permit session...")
        page.goto(
            "https://bruinepermit.t2hosted.com/Account/Portal",
            timeout=config.PAGE_LOAD_TIMEOUT,
            wait_until="domcontentloaded",
        )
        clicked = False
        for btn_text in ["Get Permits", "Buy Permits", "Purchase Permits", "Permits"]:
            try:
                page.get_by_role("button", name=re.compile(btn_text, re.IGNORECASE)).click(timeout=5000)
                clicked = True
                break
            except Exception:
                pass
        if not clicked:
            page.locator("a, button").filter(
                has_text=re.compile(r"permit", re.IGNORECASE)
            ).first.click(timeout=10000)
        try:
            page.get_by_role("button", name="UCLA Logon").click(timeout=8000)
        except Exception:
            pass
        page.wait_for_load_state("domcontentloaded", timeout=config.PAGE_LOAD_TIMEOUT)
        print(f"[JUSTIN REENTER] URL: {page.url}", flush=True)

    return _run_permit_steps(page, structure, cb, dry_run, vehicle_provider=vehicle_provider, structure_provider=structure_provider, on_config_drift=on_config_drift)


def _do_purchase_cached(page, structure, cb, dry_run=False, vehicle_provider=None, structure_provider=None, on_config_drift=None):
    """Fast path: restored session, skip UCLA login + Duo entirely.

    Raises if the session turned out to be expired (caller falls back to full flow).
    """
    cart_ready_status = _enter_permit_flow(page, cb)

    # If we ended up on the SSO login page, session has expired
    if "shb.ais.ucla.edu" in page.url or "login" in page.url.lower():
        raise Exception("Cached session expired — falling back to full login")

    print(f"[JUSTIN CACHE] Fast path active — skipped login & Duo", flush=True)
    cb("Session restored — checking for existing permits...")
    existing = _check_already_has_permit_today(page, structure)
    if existing:
        raise AlreadyHasActivePermit(existing)

    # If orphaned cart handling landed us at the cart, go straight to checkout
    if cart_ready_status == "cart_ready":
        return _run_permit_steps(page, structure, cb, dry_run, cart_ready=True, vehicle_provider=vehicle_provider, structure_provider=structure_provider, on_config_drift=on_config_drift)

    # May have landed off the permit path (e.g. Account/Portal dashboard)
    # Skip this navigation if already on a cart page
    if "bruinepermit.t2hosted.com" in page.url and "per/index.aspx" not in page.url and "crt/" not in page.url:
        print(f"[JUSTIN CACHE] Not on permit path ({page.url}), navigating in...", flush=True)
        clicked = False
        for btn_text in ["Get Permits", "Buy Permits", "Purchase Permits", "Permits"]:
            try:
                page.get_by_role("button", name=re.compile(btn_text, re.IGNORECASE)).click(timeout=5000)
                clicked = True
                break
            except Exception:
                pass
        if not clicked:
            page.locator("a, button").filter(
                has_text=re.compile(r"permit", re.IGNORECASE)
            ).first.click(timeout=10000)
        try:
            page.get_by_role("button", name="UCLA Logon").click(timeout=8000)
        except Exception:
            pass
        page.wait_for_load_state("domcontentloaded", timeout=config.PAGE_LOAD_TIMEOUT)

    return _run_permit_steps(page, structure, cb, dry_run, vehicle_provider=vehicle_provider, structure_provider=structure_provider, on_config_drift=on_config_drift)


# ── Network capture (API discovery) ──────────────────────────────────────────
#
# Every request/response to bruinepermit.t2hosted.com is logged so we can
# eventually replace browser automation with direct HTTP calls (Reggie-style).
# Captured traffic is written to screenshots/api_capture.json after each run.
# View it at /api/screenshot/api_capture  (served as JSON, not image — rename
# the endpoint if you want, but the file lives in the screenshots dir for now).

_api_capture = []          # list of {method, url, post_data, status, body}
_api_capture_lock = __import__("threading").Lock()

_API_CAPTURE_FILE = os.path.join(config.SCREENSHOT_DIR, "api_capture.json")

# Fields that may contain sensitive values — redact from logged request bodies
_REDACT_FIELDS = {"password", "pass", "pwd", "token", "secret", "credential"}


def _redact(obj, depth=0):
    """Recursively redact sensitive keys from a dict/list for safe logging."""
    if depth > 5:
        return obj
    if isinstance(obj, dict):
        return {
            k: "***REDACTED***" if k.lower() in _REDACT_FIELDS else _redact(v, depth + 1)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact(i, depth + 1) for i in obj]
    return obj


def _attach_capture(page):
    """Attach request/response listeners that log bruinepermit traffic."""

    def on_request(req):
        if "bruinepermit.t2hosted.com" not in req.url:
            return
        try:
            post_raw = req.post_data
            post_safe = None
            if post_raw:
                try:
                    post_safe = _redact(_json.loads(post_raw))
                except Exception:
                    post_safe = post_raw[:500]   # not JSON — log raw (truncated)
            entry = {
                "type": "request",
                "method": req.method,
                "url": req.url,
                "post_data": post_safe,
            }
            with _api_capture_lock:
                _api_capture.append(entry)
            print(f"[JUSTIN API] ► {req.method} {req.url}", flush=True)
            if post_safe:
                print(f"[JUSTIN API]   body: {_json.dumps(post_safe)[:300]}", flush=True)
        except Exception:
            pass

    def on_response(resp):
        if "bruinepermit.t2hosted.com" not in resp.url:
            return
        try:
            body = None
            content_type = resp.headers.get("content-type", "")
            if "json" in content_type or "javascript" in content_type:
                try:
                    body = _redact(resp.json())
                except Exception:
                    try:
                        body = resp.text()[:500]
                    except Exception:
                        pass
            entry = {
                "type": "response",
                "status": resp.status,
                "url": resp.url,
                "body": body,
            }
            with _api_capture_lock:
                _api_capture.append(entry)
            print(f"[JUSTIN API] ◄ {resp.status} {resp.url}", flush=True)
        except Exception:
            pass

    page.on("request", on_request)
    page.on("response", on_response)


def _save_capture():
    """Write captured traffic to disk after a run."""
    try:
        _ensure_screenshot_dir()
        with _api_capture_lock:
            data = list(_api_capture)
        with open(_API_CAPTURE_FILE, "w") as f:
            _json.dump(data, f, indent=2)
        print(f"[JUSTIN API] Capture saved: {len(data)} entries → {_API_CAPTURE_FILE}", flush=True)
    except Exception as e:
        print(f"[JUSTIN API] Capture save failed: {e}", flush=True)


# ── Browser / context setup ───────────────────────────────────────────────────

def _make_context(browser, storage_state=None):
    kwargs = dict(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/121.0.0.0 Safari/537.36"
        ),
        locale="en-US",
    )
    if storage_state:
        kwargs["storage_state"] = storage_state
    return browser.new_context(**kwargs)


def _setup_page(context):
    page = context.new_page()
    page.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    try:
        from playwright_stealth import stealth_sync
        stealth_sync(page)
    except Exception:
        pass
    page.set_default_timeout(config.PAGE_LOAD_TIMEOUT)
    page.route("**/*", lambda route: route.abort()
        if route.request.resource_type in ("image", "media")
        else route.continue_())
    _attach_capture(page)   # start logging bruinepermit traffic
    return page


# ── Entry point ───────────────────────────────────────────────────────────────

def run_purchase(username, password, structure, callback, duo_provider, duo_method="push",
                 dry_run=False, real_dry_run=False, check_passcode_switch=None, vehicle_provider=None, structure_provider=None, on_config_drift=None):
    """Run the full purchase flow for one user.

    dry_run=True      — mock flow (no browser), tests the web UI state machine.
    real_dry_run=True — real browser + UCLA site, stops before Process Transaction.
    """
    def cb(msg):
        if callback:
            callback(msg)

    if dry_run and not real_dry_run:
        cb("Opening the UCLA parking site...")
        time.sleep(1.5)
        cb("Logging in with your UCLA credentials...")
        time.sleep(1.5)
        cb("DUO authentication required...")
        duo_provider()
        cb("Navigating to permits...")
        time.sleep(1.0)
        cb("Selecting permit...")
        time.sleep(1.0)
        cb("Selecting vehicle...")
        time.sleep(0.8)
        cb("Selecting parking structure...")
        time.sleep(0.8)
        cb("Dry run complete — stopping before Process Transaction.")
        return "dry_run"

    actual_dry_run = dry_run or real_dry_run

    # Clear capture log for this run
    with _api_capture_lock:
        _api_capture.clear()

    # ── Fast path: try cached session (skip login + Duo) ──────────────────
    cached_session = _load_session(username)
    if cached_session:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--disable-gpu", "--no-sandbox"])
            context = _make_context(browser, storage_state=cached_session)
            page = _setup_page(context)
            try:
                result = _do_purchase_cached(page, structure, cb, dry_run=actual_dry_run, vehicle_provider=vehicle_provider, structure_provider=structure_provider, on_config_drift=on_config_drift)
                _screenshot(page, "success")
                print(f"[JUSTIN CACHE] Fast path succeeded!", flush=True)
                return result
            except (BruinBillUnavailable, PurchaseAlreadyAttempted):
                raise
            except Exception as e:
                print(f"[JUSTIN CACHE] Fast path failed: {e} — clearing cache, retrying with full login", flush=True)
                _clear_session(username)
                _screenshot(page, "cache_miss")
            finally:
                _save_capture()
                browser.close()

    # ── Full path: login + Duo + save session for next time ───────────────
    last_error = None

    for attempt in range(1, config.MAX_RETRIES + 1):
        if attempt > 1:
            cb(f"Retrying... (attempt {attempt}/{config.MAX_RETRIES})")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--disable-gpu", "--no-sandbox"])
            context = _make_context(browser)
            page = _setup_page(context)

            try:
                result = _do_purchase_full(
                    page, context, username, password, structure,
                    cb, duo_provider, duo_method=duo_method, dry_run=actual_dry_run,
                    save_session_for=username, check_passcode_switch=check_passcode_switch,
                    vehicle_provider=vehicle_provider, structure_provider=structure_provider, on_config_drift=on_config_drift,
                )
                _screenshot(page, "success")
                return result

            except PurchaseAlreadyAttempted:
                raise

            except BruinBillUnavailable as e:
                last_error = e
                _screenshot(page, f"bruin_bill_down_{attempt}")
                if attempt < config.MAX_RETRIES:
                    cb(
                        "UCLA payment system (Bruin Bill) is temporarily down. "
                        f"Retrying in {BRUIN_BILL_RETRY_DELAY // 60} minutes..."
                    )
                    time.sleep(BRUIN_BILL_RETRY_DELAY)

            except Exception as e:
                last_error = e
                _screenshot(page, f"error_attempt{attempt}")
                if attempt < config.MAX_RETRIES:
                    cb(f"Hit an issue (attempt {attempt}/{config.MAX_RETRIES}) — retrying...")

            finally:
                _save_capture()
                browser.close()

    raise Exception(str(last_error) or "Purchase failed after all retry attempts.")
