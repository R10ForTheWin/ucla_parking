"""Playwright script to automate UCLA parking purchase.

Based on recorded flow from playwright codegen.
"""

import os
import sys
import time
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

import bot
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


def _handle_duo_passcode(page):
    """Handle DUO 2FA by requesting a passcode via Telegram.

    1. Clicks 'Send a passcode' on the DUO page
    2. Asks the user for the code via Telegram
    3. Enters the code and verifies
    """
    print("Clicking 'Send a passcode'...")
    page.get_by_role("button", name="Send a passcode").click()

    print("Asking user for DUO code via Telegram...")
    code = bot.ask_for_duo_code()
    if not code:
        raise TimeoutError("No DUO passcode received from Telegram")

    print(f"Entering DUO passcode...")
    page.get_by_role("textbox", name="Passcode").fill(code)
    page.get_by_role("textbox", name="Passcode").press("Enter")

    # Wait for the page to navigate past DUO (to any bruinepermit page)
    page.wait_for_url("**/bruinepermit.t2hosted.com/**", timeout=config.PAGE_LOAD_TIMEOUT)
    print("DUO 2FA complete.")


def _do_purchase(page, dry_run=False):
    """Execute the full purchase flow. If dry_run=True, stop before final transaction."""

    # ── Step 1: Navigate to portal ──
    print("Navigating to ePermit portal...")
    page.goto("https://bruinepermit.t2hosted.com/Account/Portal", timeout=config.PAGE_LOAD_TIMEOUT, wait_until="domcontentloaded")
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

    # ── Step 4: DUO 2FA via passcode ──
    _handle_duo_passcode(page)

    # ── Step 5: Handle orphaned cart if present, then permit selection ──
    if "orphan" in page.url.lower() or "assumeOrphanedCart" in page.url:
        print("Orphaned cart page detected, starting fresh...")
        page.goto("https://bruinepermit.t2hosted.com/per/index.aspx",
                   timeout=config.PAGE_LOAD_TIMEOUT, wait_until="domcontentloaded")

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
    _screenshot(page, "pre_purchase")
    if dry_run:
        print("DRY RUN — stopping before 'Process Transaction'. Screenshot saved.")
        return

    print("Confirming purchase...")
    page.get_by_role("button", name="Process Transaction").click()

    # Wait for confirmation page
    page.wait_for_load_state("networkidle", timeout=config.PAGE_LOAD_TIMEOUT)
    print("Transaction submitted.")


def run(headless=True, dry_run=False):
    """Run the full purchase flow. Returns True on success."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(config.PAGE_LOAD_TIMEOUT)

        try:
            _do_purchase(page, dry_run=dry_run)
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
    dry_run = "--dry-run" in sys.argv
    success = run(headless=headless, dry_run=dry_run)
    sys.exit(0 if success else 1)
