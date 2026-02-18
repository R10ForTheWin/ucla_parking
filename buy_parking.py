"""Playwright script to automate UCLA parking purchase.

Based on recorded flow from playwright codegen. Supports multi-user.
"""

import os
import re
import sys
import time
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

import bot
import config

# Retry delay (seconds) when Bruin Bill payment system is temporarily down
BRUIN_BILL_RETRY_DELAY = 120  # wait 2 min before retrying


class BruinBillUnavailable(Exception):
    """Raised when Bruin Bill payment system is temporarily down."""
    pass


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


def _handle_duo_passcode(page, chat_id):
    """Handle DUO 2FA by requesting a passcode via Telegram."""
    print("Clicking 'Send a passcode'...")
    page.get_by_role("button", name="Send a passcode").click()

    print("Asking user for DUO code via Telegram...")
    code = bot.ask_for_duo_code(chat_id)
    if not code:
        raise TimeoutError("No DUO passcode received from Telegram")

    print("Entering DUO passcode...")
    page.get_by_role("textbox", name="Passcode").fill(code)
    page.get_by_role("textbox", name="Passcode").press("Enter")

    # Wait for the page to navigate past DUO
    page.wait_for_url("**/bruinepermit.t2hosted.com/**", timeout=config.PAGE_LOAD_TIMEOUT)
    print("DUO 2FA complete.")


def _find_permit_radio(page):
    """Find the 1-Day Student permit radio button with flexible matching."""
    # Try exact name first
    radio = page.get_by_role("radio", name="Yellow / 1-Day Student")
    if radio.count() > 0:
        return radio

    # Try partial matches
    for pattern in ["1-Day Student", "1-Day", "Yellow"]:
        radio = page.get_by_role("radio", name=re.compile(pattern, re.IGNORECASE))
        if radio.count() > 0:
            print(f"Found permit via pattern: {pattern}")
            return radio

    # Log what's available for debugging
    all_radios = page.get_by_role("radio").all()
    names = []
    for r in all_radios:
        label = r.get_attribute("aria-label") or r.inner_text()
        names.append(label)
    print(f"Available permits: {names}")
    raise Exception(f"Could not find 1-Day Student permit. Available: {names}")


def _verify_purchase_success(page):
    """Check the confirmation page for success indicators. Returns confirmation text or None."""
    try:
        page.wait_for_load_state("networkidle", timeout=config.PAGE_LOAD_TIMEOUT)
        body = page.inner_text("body")

        # Look for common success indicators
        success_patterns = ["confirmation", "receipt", "transaction complete", "successfully"]
        for pattern in success_patterns:
            if pattern.lower() in body.lower():
                # Try to extract confirmation/receipt number
                match = re.search(r'(?:confirmation|receipt|transaction)\s*(?:#|number|no)?[:\s]*(\w+)', body, re.IGNORECASE)
                if match:
                    return f"Confirmation: {match.group(1)}"
                return "Purchase confirmed"

        # Look for error indicators
        error_patterns = ["error", "failed", "declined", "unable", "not available"]
        for pattern in error_patterns:
            if pattern.lower() in body.lower():
                return None

        # If we can't determine, assume success (page loaded without error)
        return "Transaction submitted"
    except Exception as e:
        print(f"Error verifying purchase: {e}")
        return None


def _do_purchase(page, username, password, plate, structure, chat_id, dry_run=False):
    """Execute the full purchase flow for a single user."""

    # ── Step 1: Navigate to portal ──
    print("Navigating to ePermit portal...")
    page.goto("https://bruinepermit.t2hosted.com/Account/Portal",
              timeout=config.PAGE_LOAD_TIMEOUT, wait_until="domcontentloaded")
    _wait_for_queue_it(page)

    # ── Check for Bruin Bill outage ──
    body_text = page.inner_text("body")
    if "bruin bill is not currently available" in body_text.lower():
        raise BruinBillUnavailable("Bruin Bill payment system is temporarily down")

    # ── Step 2: Start permit flow ──
    print("Clicking 'Get Permits'...")
    page.get_by_role("button", name=re.compile(r"Get Permits", re.IGNORECASE)).click()
    page.get_by_role("button", name="UCLA Logon").click()

    # ── Step 3: UCLA SSO login ──
    print("Logging in with UCLA credentials...")
    page.get_by_placeholder("Your UCLA Logon ID").fill(username)
    page.get_by_placeholder("Your UCLA Logon ID").press("Tab")
    page.get_by_placeholder("Your UCLA Logon Password").fill(password)
    page.get_by_role("button", name="Sign In").click()

    # ── Step 4: DUO 2FA via passcode ──
    _handle_duo_passcode(page, chat_id)

    # ── Step 5: Handle orphaned cart if present ──
    # Page title: "Previous Basket Load" — shows a table of old baskets.
    # We need to click SELECT on the empty one (0 permits, $0.00).
    if "orphan" in page.url.lower() or "basket" in page.url.lower() or "Previous Basket" in page.inner_text("body"):
        print("Orphaned baskets page detected, selecting empty basket...")
        _screenshot(page, "orphaned_cart")

        empty_clicked = False

        # Primary approach: find table rows and click SELECT on the empty basket
        # The empty basket has $0.00 total and 0 permits
        rows = page.locator("table tr").all()
        for row in rows:
            text = row.inner_text()
            # Look for the row with $0.00 or 0 permits (the empty basket)
            if "$0.00" in text:
                select_link = row.get_by_role("link", name=re.compile(r"select", re.IGNORECASE))
                if select_link.count() > 0:
                    print("Clicking SELECT on empty basket ($0.00)")
                    select_link.first.click()
                    empty_clicked = True
                    break

        # Fallback: if no $0.00 row, click the last SELECT link (most recent, likely empty)
        if not empty_clicked:
            select_links = page.get_by_role("link", name=re.compile(r"select", re.IGNORECASE))
            if select_links.count() > 0:
                print(f"Clicking last SELECT link (fallback)")
                select_links.last.click()
                empty_clicked = True

        if not empty_clicked:
            print("Could not find empty basket, navigating directly...")
            page.goto("https://bruinepermit.t2hosted.com/per/index.aspx",
                       timeout=config.PAGE_LOAD_TIMEOUT, wait_until="domcontentloaded")
        else:
            page.wait_for_load_state("domcontentloaded", timeout=config.PAGE_LOAD_TIMEOUT)

    print("Selecting permit...")
    page.wait_for_url("**/per/index.aspx", timeout=config.PAGE_LOAD_TIMEOUT)
    page.get_by_role("button", name="Next >>").click()

    # ── Step 6: Choose Yellow / 1-Day Student permit ──
    page.wait_for_url("**/per/selectpermit.aspx", timeout=config.PAGE_LOAD_TIMEOUT)
    permit_radio = _find_permit_radio(page)
    permit_radio.check()
    page.get_by_role("checkbox", name="I agree to abide by my").check()
    page.get_by_role("checkbox", name="I agree to the University").check()
    page.get_by_role("button", name="Next >>").click()

    # ── Step 7: Select vehicle ──
    print(f"Selecting vehicle ({plate})...")
    page.get_by_role("checkbox", name=plate).check()
    page.get_by_role("button", name="Next >>").click()

    # ── Step 8: Select parking area ──
    print(f"Selecting parking area ({structure})...")
    dropdown = page.get_by_label("Parking Area")

    # Check if the selected structure is available in the dropdown
    available_options = dropdown.evaluate(
        "el => Array.from(el.options).map(o => ({value: o.value, text: o.text, disabled: o.disabled}))"
    )
    selected_available = any(
        o["value"] == structure and not o.get("disabled", False)
        for o in available_options
    )

    if not selected_available:
        # Offer the other structure
        if structure == config.STRUCTURE_4:
            alt_structure = config.STRUCTURE_P7
            sold_out_name = "P4"
            alt_name = "P7"
        else:
            alt_structure = config.STRUCTURE_4
            sold_out_name = "P7"
            alt_name = "P4"

        alt_available = any(
            o["value"] == alt_structure and not o.get("disabled", False)
            for o in available_options
        )

        if alt_available:
            bot.send_message(chat_id, f"{sold_out_name} is sold out!")
            # Ask if they want the other structure
            keyboard = {
                "inline_keyboard": [
                    [
                        {"text": f"Yes, use {alt_name}", "callback_data": "alt_yes"},
                        {"text": "No, cancel", "callback_data": "alt_no"},
                    ]
                ]
            }
            msg_id = bot._send(chat_id, f"Would you like {alt_name} instead?", reply_markup=keyboard)

            deadline = time.time() + config.TELEGRAM_POLL_TIMEOUT
            last_update_id = 0
            accepted = False

            while time.time() < deadline:
                updates = bot._get_updates(offset=last_update_id + 1)
                for update in updates:
                    last_update_id = update["update_id"]
                    callback = update.get("callback_query")
                    if not callback:
                        continue
                    if callback.get("message", {}).get("message_id") != msg_id:
                        continue

                    accepted = callback["data"] == "alt_yes"
                    label = f"Yes, {alt_name}" if accepted else "Cancelled"
                    import requests
                    requests.post(
                        f"{bot.TELEGRAM_API}/answerCallbackQuery",
                        json={"callback_query_id": callback["id"], "text": label},
                        timeout=10,
                    )
                    bot._edit(chat_id, msg_id,
                              f"Would you like {alt_name} instead? → *{label}*",
                              parse_mode="Markdown")
                    break
                else:
                    time.sleep(config.TELEGRAM_POLL_INTERVAL)
                    continue
                break

            if not accepted:
                raise Exception(f"{sold_out_name} sold out, user declined alternative")

            structure = alt_structure
            print(f"Switched to {alt_name}")
        else:
            raise Exception("Both parking structures are unavailable")

    dropdown.select_option(structure)
    page.get_by_role("button", name="Next >>").click()

    # ── Step 9: Confirm purchase ──
    _screenshot(page, "pre_purchase")
    if dry_run:
        print("DRY RUN — stopping before 'Process Transaction'. Screenshot saved.")
        return "DRY RUN complete"

    print("Confirming purchase...")
    page.get_by_role("button", name="Process Transaction").click()

    # Verify purchase succeeded
    result = _verify_purchase_success(page)
    if result:
        print(f"Purchase verified: {result}")
        return result
    else:
        raise Exception("Purchase may have failed — no confirmation found on page")


def run(username, password, plate, structure, chat_id, headless=True, dry_run=False):
    """Run the full purchase flow for one user with retries. Returns success string or None."""
    last_error = None

    for attempt in range(1, config.MAX_RETRIES + 1):
        if attempt > 1:
            print(f"Retry attempt {attempt}/{config.MAX_RETRIES}...")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context()
            page = context.new_page()
            page.set_default_timeout(config.PAGE_LOAD_TIMEOUT)

            try:
                result = _do_purchase(page, username, password, plate, structure, chat_id, dry_run=dry_run)
                _screenshot(page, "success")
                print("Purchase completed successfully!")
                return result
            except BruinBillUnavailable as e:
                last_error = e
                print(f"Bruin Bill down (attempt {attempt}): {e}")
                _screenshot(page, f"bruin_bill_down_attempt{attempt}")
                if attempt < config.MAX_RETRIES:
                    bot.send_message(
                        chat_id,
                        "The UCLA payment system (Bruin Bill) is temporarily down.\n\n"
                        f"I'll try again in {BRUIN_BILL_RETRY_DELAY // 60} minutes. Hang tight!"
                    )
                    time.sleep(BRUIN_BILL_RETRY_DELAY)
            except Exception as e:
                last_error = e
                print(f"Purchase failed (attempt {attempt}): {e}")
                _screenshot(page, f"error_attempt{attempt}")
                if attempt < config.MAX_RETRIES:
                    bot.send_message(
                        chat_id,
                        f"Ran into an issue on the parking site (attempt {attempt}/{config.MAX_RETRIES}).\n\n"
                        "Retrying now..."
                    )
            finally:
                browser.close()

    # All retries exhausted — send one clean failure message
    if isinstance(last_error, BruinBillUnavailable):
        bot.send_message(
            chat_id,
            "The UCLA payment system (Bruin Bill) is still down after multiple attempts.\n\n"
            "You may need to purchase manually today:\nhttps://bruinepermit.t2hosted.com"
        )
    else:
        bot.send_message(
            chat_id,
            "I wasn't able to complete the purchase after multiple attempts.\n\n"
            "The parking site may be experiencing issues. "
            "You can try manually:\nhttps://bruinepermit.t2hosted.com"
        )
    print(f"All {config.MAX_RETRIES} attempts failed. Last error: {last_error}")
    return None


if __name__ == "__main__":
    headless = "--headless" in sys.argv
    dry_run = "--dry-run" in sys.argv
    result = run(
        username=config.UCLA_USERNAME,
        password=config.UCLA_PASSWORD,
        plate="9VSK311",
        structure=config.STRUCTURE_4,
        chat_id=config.TELEGRAM_CHAT_ID,
        headless=headless,
        dry_run=dry_run,
    )
    sys.exit(0 if result else 1)
