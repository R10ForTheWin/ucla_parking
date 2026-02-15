"""Main Playwright script to automate UCLA parking purchase.

This is a scaffold. After running `playwright codegen` to record your
actual purchase flow, paste the recorded steps into the `_do_purchase`
method and adjust as needed.
"""

import os
import sys
import time
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

import config


def _ensure_screenshot_dir():
    os.makedirs(config.SCREENSHOT_DIR, exist_ok=True)


def _screenshot(page, name):
    """Save a screenshot for debugging."""
    _ensure_screenshot_dir()
    path = os.path.join(config.SCREENSHOT_DIR, f"{name}.png")
    page.screenshot(path=path, full_page=True)
    print(f"Screenshot saved: {path}")
    return path


def _wait_for_queue_it(page):
    """If Queue-it waiting room appears, wait until redirected through."""
    if "queue-it" in page.url.lower() or "queue.t2hosted" in page.url.lower():
        print("In Queue-it waiting room, waiting...")
        deadline = time.time() + config.QUEUE_IT_TIMEOUT
        while time.time() < deadline:
            if "queue-it" not in page.url.lower() and "queue.t2hosted" not in page.url.lower():
                print("Passed through Queue-it.")
                return
            time.sleep(5)
        raise TimeoutError("Stuck in Queue-it waiting room")


def _login_sso(page):
    """Handle UCLA SSO login page."""
    # Wait for the SSO login form
    page.wait_for_selector('input[name="loginfmt"], input[name="j_username"], input#username', timeout=15000)

    # Try common UCLA SSO field selectors
    username_sel = page.query_selector('input[name="loginfmt"]') or \
                   page.query_selector('input[name="j_username"]') or \
                   page.query_selector('input#username')
    password_sel = page.query_selector('input[name="passwd"]') or \
                   page.query_selector('input[name="j_password"]') or \
                   page.query_selector('input#password')

    if username_sel:
        username_sel.fill(config.UCLA_USERNAME)
    if password_sel:
        password_sel.fill(config.UCLA_PASSWORD)

    # Submit
    submit = page.query_selector('input[type="submit"], button[type="submit"]')
    if submit:
        submit.click()


def _wait_for_duo(page):
    """Wait for the user to approve the DUO 2FA push on their phone."""
    print("Waiting for DUO 2FA approval...")
    deadline = time.time() + config.DUO_WAIT_TIMEOUT
    while time.time() < deadline:
        # DUO completed when we leave the DUO/SSO pages
        url = page.url.lower()
        if "duosecurity" not in url and "shibboleth" not in url and "login" not in url:
            print("DUO 2FA approved.")
            return
        time.sleep(3)
    raise TimeoutError("DUO 2FA was not approved in time")


def _do_purchase(page):
    """Execute the recorded purchase flow.

    TODO: Replace this with your recorded Playwright codegen steps.
    Run `playwright codegen https://bruinepermit.t2hosted.com` to record.
    """
    # ── Step 1: Navigate to the ePermit site ──
    page.goto(config.EPERMIT_URL, timeout=config.PAGE_LOAD_TIMEOUT)
    _wait_for_queue_it(page)

    # ── Step 2: Click login / buy permit (adjust selector from recording) ──
    # Example: page.click('text=Login')
    # Example: page.click('a:has-text("Buy a Permit")')
    raise NotImplementedError(
        "Purchase flow not yet recorded. "
        "Run: python -m playwright codegen https://bruinepermit.t2hosted.com\n"
        "Then paste the recorded steps into buy_parking.py:_do_purchase()"
    )

    # ── Step 3: SSO login ──
    # _login_sso(page)
    # _wait_for_duo(page)

    # ── Step 4: Select permit & checkout ──
    # (paste recorded steps here)

    # ── Step 5: Confirm purchase ──
    # (paste recorded steps here)


def run(headless=True):
    """Run the full purchase flow. Returns True on success."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(config.PAGE_LOAD_TIMEOUT)

        try:
            _do_purchase(page)
            _screenshot(page, "success")
            print("Purchase completed successfully!")
            return True
        except NotImplementedError as e:
            print(f"SETUP NEEDED: {e}")
            return False
        except Exception as e:
            print(f"Purchase failed: {e}")
            _screenshot(page, "error")
            return False
        finally:
            browser.close()


if __name__ == "__main__":
    headless = "--headless" in sys.argv
    success = run(headless=headless)
    sys.exit(0 if success else 1)
