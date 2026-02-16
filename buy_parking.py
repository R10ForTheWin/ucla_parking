"""Playwright script to automate UCLA parking purchase.

Based on recorded flow from playwright codegen.
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
    if "queue-it" not in page.url.lower() and "queue.t2hosted" not in page.url.lower():
        return
    print("In Queue-it waiting room, waiting...")
    deadline = time.time() + config.QUEUE_IT_TIMEOUT
    while time.time() < deadline:
        if "queue-it" not in page.url.lower() and "queue.t2hosted" not in page.url.lower():
            print("Passed through Queue-it.")
            return
        time.sleep(5)
    raise TimeoutError("Stuck in Queue-it waiting room")


def _wait_for_duo(page):
    """Wait for the user to approve DUO 2FA on their phone.

    The recording showed a passcode flow, but in automated mode we use
    DUO push instead. We wait until the page navigates away from the
    login/DUO pages.
    """
    print("Waiting for DUO 2FA approval (check your phone)...")
    deadline = time.time() + config.DUO_WAIT_TIMEOUT
    while time.time() < deadline:
        url = page.url.lower()
        # Once we leave SSO/DUO pages, auth is complete
        if "duosecurity" not in url and "shibboleth" not in url and "login" not in url and "idp" not in url:
            print("DUO 2FA approved.")
            return
        time.sleep(3)
    raise TimeoutError("DUO 2FA was not approved in time")


def _do_purchase(page):
    """Execute the full purchase flow."""

    # ── Step 1: Navigate to portal ──
    print("Navigating to ePermit portal...")
    page.goto("https://bruinepermit.t2hosted.com/Account/Portal", timeout=config.PAGE_LOAD_TIMEOUT)
    _wait_for_queue_it(page)

    # ── Step 2: Start permit flow ──
    print("Clicking 'Get Permits'...")
    page.get_by_role("button", name=" Get Permits").click()
    page.get_by_role("button", name="UCLA Logon").click()

    # ── Step 3: UCLA SSO login ──
    print("Logging in with UCLA credentials...")
    page.get_by_placeholder("Your UCLA Logon ID").fill(config.UCLA_USERNAME)
    page.get_by_placeholder("Your UCLA Logon ID").press("Tab")
    page.get_by_placeholder("Your UCLA Logon Password").fill(config.UCLA_PASSWORD)
    page.get_by_role("button", name="Sign In").click()

    # ── Step 4: DUO 2FA ──
    # Try to trigger a DUO push automatically, then wait for approval
    try:
        push_btn = page.get_by_role("button", name="Send Me a Push")
        push_btn.wait_for(timeout=10000)
        push_btn.click()
        print("DUO push sent.")
    except PwTimeout:
        # Maybe it auto-sends or shows a different UI
        print("No push button found, waiting for DUO prompt...")

    _wait_for_duo(page)

    # ── Step 5: Permit selection page ──
    print("Selecting permit...")
    page.wait_for_url("**/per/index.aspx", timeout=config.PAGE_LOAD_TIMEOUT)
    page.get_by_role("button", name="Next >>").click()

    # ── Step 6: Choose Yellow / 1-Day Student permit ──
    page.wait_for_url("**/per/selectpermit.aspx", timeout=config.PAGE_LOAD_TIMEOUT)
    page.get_by_role("radio", name="Yellow / 1-Day Student").check()
    page.get_by_role("checkbox", name="I agree to abide by my").check()
    page.get_by_role("checkbox", name="I agree to the University").check()
    page.get_by_role("button", name="Next >>").click()

    # ── Step 7: Select vehicle ──
    print("Selecting vehicle...")
    page.get_by_role("checkbox", name="9VSK311").check()
    page.get_by_role("button", name="Next >>").click()

    # ── Step 8: Select parking area ──
    print("Selecting parking area...")
    page.get_by_label("Parking Area").select_option("2127")
    page.get_by_role("button", name="Next >>").click()

    # ── Step 9: Confirm purchase ──
    print("Confirming purchase...")
    _screenshot(page, "pre_purchase")
    page.get_by_role("button", name="Process Transaction").click()

    # Wait for confirmation page
    page.wait_for_load_state("networkidle", timeout=config.PAGE_LOAD_TIMEOUT)
    print("Transaction submitted.")


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
