"""
Justin EMBAlake – Playwright automation, decoupled from Telegram.
Called by app.py in background threads.
"""

import os
import re
import time
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

import config

BRUIN_BILL_RETRY_DELAY = 120  # seconds to wait when Bruin Bill is down


class BruinBillUnavailable(Exception):
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
        # Hard block: "softblock" in URL
        if "softblock" in page.url.lower():
            return True
        # Check for recaptcha iframe (strongest signal)
        if page.locator("iframe[src*='recaptcha'], iframe[src*='hcaptcha']").count() > 0:
            return True
        # Check for Queue-it bot-check input
        if page.locator("input[id*='captcha'], input[name*='captcha']").count() > 0:
            return True
        # Text signals only as a last resort
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

    # Log where we landed for debugging
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


def _handle_duo(page, method, duo_provider, cb):
    """Wait for DUO Universal Prompt, then authenticate via push or passcode.

    method: "push" — sends a Duo Push to the user's phone (tap Approve).
            "passcode" — clicks Send Passcode and waits for user to enter the code.
    """
    cb("DUO authentication required...")

    # Wait for the browser to land on the DUO page after UCLA SSO redirect.
    # Use regex — DUO now uses subdomains like api-xxxx.duosecurity.com so the
    # glob "**/duosecurity.com/**" (which requires a leading "/") won't match.
    try:
        page.wait_for_url(re.compile(r'duosecurity\.com'), timeout=config.PAGE_LOAD_TIMEOUT)
    except PwTimeout:
        _screenshot(page, "duo_timeout")
        url = page.url
        body_text = ""
        try:
            print(f"[JUSTIN DUO] Timeout. URL: {url}", flush=True)
            body_text = page.inner_text("body")
            print(f"[JUSTIN DUO] Body:\n{body_text[:800]}", flush=True)
        except Exception:
            pass
        # Detect wrong credentials — SSO bounces back to login with an error
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

    if method == "push":
        # Log all buttons and links on the Duo page to identify the correct label
        try:
            btns = page.evaluate("() => Array.from(document.querySelectorAll('button,a,[role=button],[role=link]')).map(e => e.innerText.trim()).filter(t => t)")
            print(f"[JUSTIN DUO] Buttons/links on page: {btns}", flush=True)
        except Exception:
            pass

        # UCLA's Duo Universal Prompt hides methods behind "Other options" first
        for expand_label in ("Other options",):
            for role in ("button", "link"):
                try:
                    page.get_by_role(role, name=re.compile(expand_label, re.IGNORECASE)).first.click(timeout=8000)
                    page.wait_for_load_state("networkidle")
                    break
                except Exception:
                    pass

        # Log available buttons after expanding
        try:
            btns = page.evaluate("() => Array.from(document.querySelectorAll('button,a,[role=button],[role=link]')).map(e => e.innerText.trim()).filter(t => t)")
            print(f"[JUSTIN DUO] Buttons/links after expand: {btns}", flush=True)
        except Exception:
            pass

        clicked = False
        for label in ("Duo Push", "Send me a Push", "Push Notification", "Push"):
            for role in ("button", "link"):
                try:
                    page.get_by_role(role, name=re.compile(label, re.IGNORECASE)).first.click(timeout=8000)
                    clicked = True
                    break
                except Exception:
                    pass
            if clicked:
                break
        if not clicked:
            # Push not available — fall back to passcode
            cb("Duo Push not available — switching to passcode...")
            method = "passcode"
        else:
            # Notify the UI (non-blocking — just shows "check your phone" screen)
            duo_provider()
            # Wait for Duo to redirect back after user taps Approve on their phone
            page.wait_for_url("**/bruinepermit.t2hosted.com/**", timeout=config.PAGE_LOAD_TIMEOUT)
            cb("DUO verified!")
            return

    # ── Passcode flow ──────────────────────────────────────────────────────
    # Click the passcode option — label varies by DUO UI version.
    # New Universal Prompt (frameless): "Use a Passcode" or "Enter a Passcode"
    # Old prompt: "Send a passcode"
    clicked = False
    for label in ("Use a Passcode", "Enter a Passcode", "Send a passcode", "Passcode"):
        for role in ("button", "link"):
            try:
                page.get_by_role(role, name=re.compile(label, re.IGNORECASE)).first.click(timeout=8000)
                clicked = True
                break
            except Exception:
                pass
        if clicked:
            break
    if not clicked:
        _screenshot(page, "duo_no_button")
        raise Exception("Could not find passcode button on the DUO page — try again.")

    # Wait for the passcode textbox to appear on the DUO page before notifying
    # the user — this confirms DUO has actually sent the SMS/code, so Justin's
    # "enter your code" screen and the arriving text are in sync.
    try:
        page.get_by_role("textbox").first.wait_for(timeout=30000)
    except Exception:
        _screenshot(page, "duo_no_textbox")
        raise Exception("DUO did not show a passcode input — try again.")

    # duo_provider() blocks until the user submits their code via the web UI
    code = duo_provider()
    if not code:
        raise TimeoutError("DUO passcode not received — timed out after 5 minutes.")

    # Fill the passcode input and submit
    page.get_by_role("textbox").first.fill(code)
    page.get_by_role("textbox").first.press("Enter")

    page.wait_for_url("**/bruinepermit.t2hosted.com/**", timeout=config.PAGE_LOAD_TIMEOUT)
    cb("DUO verified!")


def _find_permit_radio(page):
    radio = page.get_by_role("radio", name="Yellow / 1-Day Student")
    if radio.count() > 0:
        return radio
    for pattern in ["1-Day Student", "1-Day", "Yellow"]:
        radio = page.get_by_role("radio", name=re.compile(pattern, re.IGNORECASE))
        if radio.count() > 0:
            return radio
    all_radios = page.get_by_role("radio").all()
    names = [r.get_attribute("aria-label") or r.inner_text() for r in all_radios]
    raise Exception(f"Could not find 1-Day Student permit. Available: {names}")


def _verify_purchase_success(page):
    try:
        page.wait_for_load_state("networkidle", timeout=config.PAGE_LOAD_TIMEOUT)
        body = page.inner_text("body")
        for pattern in ["confirmation", "receipt", "transaction complete", "successfully"]:
            if pattern.lower() in body.lower():
                match = re.search(
                    r'(?:confirmation|receipt|transaction)\s*(?:#|number|no)?[:\s]*(\w+)',
                    body, re.IGNORECASE
                )
                if match:
                    return f"Confirmation #{match.group(1)}"
                return "Purchase confirmed"
        return None
    except Exception:
        return None


def _do_purchase(page, username, password, structure, cb, duo_provider, duo_method="push", dry_run=False):
    # Step 1: Navigate
    cb("Opening the UCLA parking site...")
    page.goto(
        "https://bruinepermit.t2hosted.com/Account/Portal",
        timeout=config.PAGE_LOAD_TIMEOUT,
        wait_until="domcontentloaded",
    )
    _wait_for_queue_it(page, cb)

    body_text = page.inner_text("body")
    print(f"[JUSTIN] Page URL: {page.url}", flush=True)
    print(f"[JUSTIN] Page title: {page.title()}", flush=True)
    print(f"[JUSTIN] Body snippet: {body_text[:300]}", flush=True)

    if "bruin bill is not currently available" in body_text.lower():
        raise BruinBillUnavailable("Bruin Bill payment system is temporarily down.")

    # Step 2: Start permit flow — try several button name variants
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
        # Fallback: any link or button containing "permit"
        page.locator("a, button").filter(
            has_text=re.compile(r"permit", re.IGNORECASE)
        ).first.click(timeout=10000)

    page.get_by_role("button", name="UCLA Logon").click()

    # Step 3: UCLA SSO login
    cb("Logging in with your UCLA credentials...")
    page.get_by_placeholder("Your UCLA Logon ID").fill(username)
    page.get_by_placeholder("Your UCLA Logon ID").press("Tab")
    page.get_by_placeholder("Your UCLA Logon Password").fill(password)
    page.get_by_role("button", name="Sign In").click()

    # Diagnostic: log what page we land on after Sign In
    page.wait_for_load_state("domcontentloaded")
    print(f"[JUSTIN SSO] After Sign In — URL: {page.url}", flush=True)
    sso_body = ""
    try:
        sso_body = page.inner_text("body")
        print(f"[JUSTIN SSO] Body:\n{sso_body[:800]}", flush=True)
    except Exception:
        pass
    # If still on Shibboleth SSO, check for a credential error immediately
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
    _handle_duo(page, duo_method, duo_provider, cb)
    cb("Navigating to permits...")

    # Step 5: Handle orphaned cart if present
    try:
        body = page.inner_text("body")
        if ("orphan" in page.url.lower() or "basket" in page.url.lower()
                or "Previous Basket" in body):
            cb("Clearing previous cart...")
            empty_clicked = False
            rows = page.locator("table tr").all()
            for row in rows:
                if "$0.00" in row.inner_text():
                    link = row.get_by_role("link", name=re.compile(r"select", re.IGNORECASE))
                    if link.count() > 0:
                        link.first.click()
                        empty_clicked = True
                        break
            if not empty_clicked:
                links = page.get_by_role("link", name=re.compile(r"select", re.IGNORECASE))
                if links.count() > 0:
                    links.last.click()
                    empty_clicked = True
            if not empty_clicked:
                page.goto(
                    "https://bruinepermit.t2hosted.com/per/index.aspx",
                    timeout=config.PAGE_LOAD_TIMEOUT,
                    wait_until="domcontentloaded",
                )
            else:
                page.wait_for_load_state("domcontentloaded", timeout=config.PAGE_LOAD_TIMEOUT)
    except Exception:
        pass

    # Step 6: Select permit type
    cb("Selecting permit...")
    page.wait_for_url("**/per/index.aspx", timeout=config.PAGE_LOAD_TIMEOUT)
    page.get_by_role("button", name="Next >>").click()
    page.wait_for_url("**/per/selectpermit.aspx", timeout=config.PAGE_LOAD_TIMEOUT)

    permit_radio = _find_permit_radio(page)
    permit_radio.check()
    page.get_by_role("checkbox", name="I agree to abide by my").check()
    page.get_by_role("checkbox", name="I agree to the University").check()
    page.get_by_role("button", name="Next >>").click()

    # Step 7: Select vehicle (auto-select all vehicles on the account)
    cb("Selecting vehicle...")
    page.wait_for_load_state("domcontentloaded", timeout=config.PAGE_LOAD_TIMEOUT)
    # Wait for at least one checkbox to appear before collecting all
    page.get_by_role("checkbox").first.wait_for(timeout=config.PAGE_LOAD_TIMEOUT)
    checkboxes = page.get_by_role("checkbox").all()
    checked = 0
    for cb_el in checkboxes:
        try:
            cb_el.check()
            checked += 1
        except Exception:
            pass
    if checked == 0:
        raise Exception("No vehicles found on your UCLA account — add a vehicle at bruinepermit.t2hosted.com first.")
    page.get_by_role("button", name="Next >>").click()

    # Step 8: Select parking structure
    cb("Selecting parking structure...")
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
        if structure == config.STRUCTURE_4:
            alt_structure, sold_out_name, alt_name = config.STRUCTURE_P7, "P4", "P7"
        else:
            alt_structure, sold_out_name, alt_name = config.STRUCTURE_4, "P7", "P4"

        alt_available = any(
            o["value"] == alt_structure and not o.get("disabled", False)
            for o in available_options
        )

        if alt_available:
            cb(f"{sold_out_name} is sold out — automatically switching to {alt_name}...")
            structure = alt_structure
        else:
            options_str = ", ".join(f"{o['text']}={o['value']}(disabled={o['disabled']})" for o in available_options)
            raise Exception(f"Both P4 and P7 are sold out — parking is not available today. Raw options: {options_str}")

    dropdown.select_option(structure)
    page.get_by_role("button", name="Next >>").click()

    _screenshot(page, "pre_purchase")

    if dry_run:
        cb("Dry run complete — stopping before Process Transaction.")
        return "dry_run"

    # Step 9: Process transaction
    cb("Processing transaction...")
    page.get_by_role("button", name="Process Transaction").click()

    result = _verify_purchase_success(page)
    if result:
        return result
    raise Exception("No purchase confirmation found on page — check manually: https://bruinepermit.t2hosted.com")


def run_purchase(username, password, structure, callback, duo_provider, duo_method="push", dry_run=False, real_dry_run=False):
    """Run the full purchase flow for one user, with retries.

    dry_run=True      — mock flow, no browser, just tests the web UI state machine.
    real_dry_run=True — real browser, real UCLA site, real DUO, stops before Process Transaction.
    """
    def cb(msg):
        if callback:
            callback(msg)

    if dry_run and not real_dry_run:
        # Mock flow — no browser, no UCLA servers touched.
        # Exercises the full web-UI state machine (loading → DUO → loading → success).
        cb("Opening the UCLA parking site...")
        time.sleep(1.5)
        cb("Logging in with your UCLA credentials...")
        time.sleep(1.5)
        cb("DUO authentication required...")
        duo_provider()           # blocks until user submits code in the UI
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

    last_error = None

    for attempt in range(1, config.MAX_RETRIES + 1):
        if attempt > 1:
            cb(f"Retrying... (attempt {attempt}/{config.MAX_RETRIES})")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--disable-gpu", "--no-sandbox"])

            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/121.0.0.0 Safari/537.36"
                ),
                locale="en-US",
            )
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

            # Block images and media to speed up page loads
            # NOTE: fonts are NOT blocked — some bot-detection checks for font loading
            page.route("**/*", lambda route: route.abort()
                if route.request.resource_type in ("image", "media")
                else route.continue_())

            try:
                result = _do_purchase(
                    page, username, password, structure,
                    cb, duo_provider, duo_method=duo_method, dry_run=(dry_run or real_dry_run),
                )
                _screenshot(page, "success")
                return result

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
                # If login failed, wipe cached session so next attempt does fresh login
                _screenshot(page, f"error_attempt{attempt}")
                if attempt < config.MAX_RETRIES:
                    cb(f"Hit an issue (attempt {attempt}/{config.MAX_RETRIES}) — retrying...")

            finally:
                browser.close()

    raise Exception(str(last_error) or "Purchase failed after all retry attempts.")
