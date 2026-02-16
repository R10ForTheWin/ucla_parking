"""Telegram bot functions for multi-user prompts and responses."""

import os
import time
import requests
import config


TELEGRAM_API = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")


# ── Low-level helpers ──

def _send(chat_id, text, reply_markup=None, parse_mode=None):
    """Send a message to a specific chat. Returns message_id."""
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if parse_mode:
        payload["parse_mode"] = parse_mode
    resp = requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json()["result"]["message_id"]


def _edit(chat_id, message_id, text, parse_mode=None):
    """Edit an existing message."""
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    requests.post(f"{TELEGRAM_API}/editMessageText", json=payload, timeout=10)


def _get_updates(offset=0, timeout=10):
    """Fetch updates from Telegram."""
    resp = requests.get(
        f"{TELEGRAM_API}/getUpdates",
        params={"offset": offset, "timeout": timeout},
        timeout=timeout + 5,
    )
    resp.raise_for_status()
    return resp.json().get("result", [])


def _flush_updates():
    """Flush pending updates and return the last update_id."""
    updates = _get_updates(offset=-1, timeout=0)
    return updates[-1]["update_id"] if updates else 0


# ── Single-user functions ──

def send_message(chat_id, text):
    """Send a plain text message."""
    _send(chat_id, text)


def send_photo(chat_id, photo_path, caption=""):
    """Send a photo to a specific chat."""
    with open(photo_path, "rb") as f:
        resp = requests.post(
            f"{TELEGRAM_API}/sendPhoto",
            data={"chat_id": chat_id, "caption": caption},
            files={"photo": f},
            timeout=30,
        )
    resp.raise_for_status()


def ask_for_plate(chat_id, plates):
    """Pick which license plate to use.

    Args:
        plates: list of plate strings (e.g. ["9VSK311"] or ["9VSK311", "ABC1234"])

    - 1 plate  → auto-selects it with a confirmation message
    - 2+ plates → shows buttons to pick one
    Returns the chosen plate string, or None if timed out.
    """
    if not plates:
        return None

    if len(plates) == 1:
        _send(chat_id, f"Using plate *{plates[0]}*", parse_mode="Markdown")
        return plates[0]

    # Multiple plates — show selection buttons
    keyboard = {
        "inline_keyboard": [
            [{"text": plate, "callback_data": f"plate_{i}"}]
            for i, plate in enumerate(plates)
        ]
    }
    message_id = _send(chat_id, "Which car are you driving today?", reply_markup=keyboard)

    deadline = time.time() + config.TELEGRAM_POLL_TIMEOUT
    last_update_id = 0

    while time.time() < deadline:
        updates = _get_updates(offset=last_update_id + 1)
        for update in updates:
            last_update_id = update["update_id"]
            callback = update.get("callback_query")
            if not callback:
                continue
            if callback.get("message", {}).get("message_id") != message_id:
                continue

            requests.post(
                f"{TELEGRAM_API}/answerCallbackQuery",
                json={"callback_query_id": callback["id"]},
                timeout=10,
            )

            idx = int(callback["data"].split("_")[1])
            chosen = plates[idx]
            _edit(chat_id, message_id,
                  f"Which car are you driving today? → *{chosen}*",
                  parse_mode="Markdown")
            return chosen

        time.sleep(config.TELEGRAM_POLL_INTERVAL)

    return None


def ask_for_structure(chat_id):
    """Ask which parking structure. Default is Structure 4, option for P7.

    Returns the option value for the Parking Area dropdown.
    """
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "Structure 4", "callback_data": "struct_4"},
                {"text": "P7", "callback_data": "struct_p7"},
            ]
        ]
    }
    message_id = _send(chat_id, "Which parking structure?", reply_markup=keyboard)

    deadline = time.time() + config.TELEGRAM_POLL_TIMEOUT
    last_update_id = 0

    while time.time() < deadline:
        updates = _get_updates(offset=last_update_id + 1)
        for update in updates:
            last_update_id = update["update_id"]
            callback = update.get("callback_query")
            if not callback:
                continue
            if callback.get("message", {}).get("message_id") != message_id:
                continue

            requests.post(
                f"{TELEGRAM_API}/answerCallbackQuery",
                json={"callback_query_id": callback["id"]},
                timeout=10,
            )

            if callback["data"] == "struct_4":
                _edit(chat_id, message_id,
                      "Which parking structure? → *Structure 4*",
                      parse_mode="Markdown")
                return config.STRUCTURE_4
            else:
                _edit(chat_id, message_id,
                      "Which parking structure? → *P7*",
                      parse_mode="Markdown")
                return config.STRUCTURE_P7

        time.sleep(config.TELEGRAM_POLL_INTERVAL)

    return config.STRUCTURE_4  # default if timed out


def ask_for_duo_code(chat_id):
    """Ask for DUO passcode via Telegram. Returns code string or None."""
    last_update_id = _flush_updates()
    _send(chat_id, "Enter your DUO passcode:")

    deadline = time.time() + config.DUO_WAIT_TIMEOUT
    while time.time() < deadline:
        updates = _get_updates(offset=last_update_id + 1)
        for update in updates:
            last_update_id = update["update_id"]
            msg = update.get("message")
            if not msg:
                continue
            if str(msg.get("chat", {}).get("id")) != str(chat_id):
                continue
            text = msg.get("text", "").strip()
            if text:
                _send(chat_id, f"Got it: {text}")
                return text

        time.sleep(config.TELEGRAM_POLL_INTERVAL)

    return None


def _wait_for_text_reply(chat_id, last_update_id, upper=False):
    """Wait for a text message from a specific chat."""
    deadline = time.time() + config.TELEGRAM_POLL_TIMEOUT
    while time.time() < deadline:
        updates = _get_updates(offset=last_update_id + 1)
        for update in updates:
            last_update_id = update["update_id"]
            msg = update.get("message")
            if not msg:
                continue
            if str(msg.get("chat", {}).get("id")) != str(chat_id):
                continue
            text = msg.get("text", "").strip()
            if text:
                return text.upper() if upper else text

        time.sleep(config.TELEGRAM_POLL_INTERVAL)

    return None


# ── Multi-user prompt ──

def send_purchase_prompts(users):
    """Send Yes/No prompts to all users simultaneously.

    Args:
        users: list of user dicts with at least 'telegram_chat_id'

    Returns:
        dict mapping chat_id -> message_id for each sent prompt
    """
    prompts = {}
    bruin_img = os.path.join(ASSETS_DIR, "bruin.png")
    has_image = os.path.exists(bruin_img)

    for user in users:
        chat_id = user["telegram_chat_id"]
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "Yes", "callback_data": "buy_yes"},
                    {"text": "No", "callback_data": "buy_no"},
                ]
            ]
        }
        caption = "UCLA EMBA Parking Reminder\n\n\U0001F43B Buy UCLA day pass today?\n\nYellow 1-Day Student\n$7.28 + $0.73 tax = $8.01\nCharged to Payment Method On File"
        try:
            if has_image:
                with open(bruin_img, "rb") as f:
                    resp = requests.post(
                        f"{TELEGRAM_API}/sendPhoto",
                        data={
                            "chat_id": chat_id,
                            "caption": caption,
                            "reply_markup": __import__("json").dumps(keyboard),
                        },
                        files={"photo": f},
                        timeout=15,
                    )
                resp.raise_for_status()
                msg_id = resp.json()["result"]["message_id"]
            else:
                msg_id = _send(chat_id, caption, reply_markup=keyboard)
            prompts[str(chat_id)] = msg_id
        except Exception as e:
            print(f"Failed to send prompt to {chat_id}: {e}")
    return prompts


def poll_all_responses(prompts, timeout=None):
    """Poll for Yes/No responses from multiple users.

    Args:
        prompts: dict of chat_id -> message_id
        timeout: seconds to wait (default: TELEGRAM_POLL_TIMEOUT)

    Returns:
        dict of chat_id -> True (yes) / False (no) for users who responded
    """
    if timeout is None:
        timeout = config.TELEGRAM_POLL_TIMEOUT

    responses = {}
    pending = set(prompts.keys())
    deadline = time.time() + timeout
    last_update_id = 0

    while time.time() < deadline and pending:
        updates = _get_updates(offset=last_update_id + 1)
        for update in updates:
            last_update_id = update["update_id"]
            callback = update.get("callback_query")
            if not callback:
                continue

            cb_msg_id = callback.get("message", {}).get("message_id")
            cb_chat_id = str(callback.get("message", {}).get("chat", {}).get("id"))

            if cb_chat_id not in pending:
                continue
            if cb_msg_id != prompts.get(cb_chat_id):
                continue

            answer = callback["data"] == "buy_yes"
            responses[cb_chat_id] = answer
            pending.discard(cb_chat_id)

            # Acknowledge callback
            requests.post(
                f"{TELEGRAM_API}/answerCallbackQuery",
                json={"callback_query_id": callback["id"]},
                timeout=10,
            )

            # Edit message to show choice
            choice_text = "Yes" if answer else "No"
            _edit(cb_chat_id, cb_msg_id,
                  f"Buy UCLA parking today? → *{choice_text}*",
                  parse_mode="Markdown")

        time.sleep(config.TELEGRAM_POLL_INTERVAL)

    return responses


# ── Registration flow ──

def collect_registration(chat_id):
    """Walk a new user through registration via Telegram DMs.

    Returns dict with ucla_username, ucla_password, plates or None.
    """
    last_update_id = _flush_updates()

    _send(chat_id, "Welcome to UCLA Parking Bot! Let's get you set up.\n\nWhat is your UCLA username (Logon ID)?")
    username = _wait_for_text_reply(chat_id, last_update_id)
    if not username:
        _send(chat_id, "Registration timed out.")
        return None
    last_update_id = _flush_updates()

    _send(chat_id, "What is your UCLA password?")
    password = _wait_for_text_reply(chat_id, last_update_id)
    if not password:
        _send(chat_id, "Registration timed out.")
        return None
    last_update_id = _flush_updates()

    # Collect license plates
    _send(chat_id, "What is your license plate number?")
    plate = _wait_for_text_reply(chat_id, last_update_id, upper=True)
    if not plate:
        _send(chat_id, "Registration timed out.")
        return None
    plates = [plate]

    # Ask if they have a second car
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "Yes", "callback_data": "add_yes"},
                {"text": "No", "callback_data": "add_no"},
            ]
        ]
    }
    msg_id = _send(chat_id, "Do you have a second car?", reply_markup=keyboard)
    last_update_id = _flush_updates()

    deadline = time.time() + config.TELEGRAM_POLL_TIMEOUT
    while time.time() < deadline:
        updates = _get_updates(offset=last_update_id + 1)
        for update in updates:
            last_update_id = update["update_id"]
            callback = update.get("callback_query")
            if not callback:
                continue
            if callback.get("message", {}).get("message_id") != msg_id:
                continue

            requests.post(
                f"{TELEGRAM_API}/answerCallbackQuery",
                json={"callback_query_id": callback["id"]},
                timeout=10,
            )

            if callback["data"] == "add_yes":
                _edit(chat_id, msg_id, "Do you have a second car? → *Yes*", parse_mode="Markdown")
                _send(chat_id, "What is the second license plate?")
                plate2 = _wait_for_text_reply(chat_id, last_update_id, upper=True)
                if plate2:
                    plates.append(plate2)
            else:
                _edit(chat_id, msg_id, "Do you have a second car? → *No*", parse_mode="Markdown")
            break
        else:
            time.sleep(config.TELEGRAM_POLL_INTERVAL)
            continue
        break

    plates_str = ", ".join(plates)
    _send(chat_id, f"You're all set!\n\nUsername: {username}\nPlates: {plates_str}\n\nYou'll get a parking prompt on class days at 7 AM.")
    return {
        "ucla_username": username,
        "ucla_password": password,
        "plates": plates,
    }


def get_new_chat_ids(known_chat_ids):
    """Check for /start messages from new (unregistered) users.

    Returns list of new chat_id strings.
    """
    new_ids = []
    updates = _get_updates(offset=-100, timeout=0)
    for update in updates:
        msg = update.get("message")
        if not msg:
            continue
        chat_id = str(msg.get("chat", {}).get("id"))
        text = msg.get("text", "").strip()
        if text.lower() in ("/start", "/register") and chat_id not in known_chat_ids:
            if chat_id not in new_ids:
                new_ids.append(chat_id)
    return new_ids
