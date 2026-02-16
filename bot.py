"""Telegram bot functions for sending prompts and receiving responses."""

import json
import time
import requests
import config


TELEGRAM_API = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"


def send_purchase_prompt():
    """Send a Yes/No inline keyboard message asking to buy parking.

    Returns the message_id of the sent message.
    """
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "Yes", "callback_data": "buy_yes"},
                {"text": "No", "callback_data": "buy_no"},
            ]
        ]
    }
    resp = requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json={
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": "Buy UCLA parking today?",
            "reply_markup": keyboard,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["result"]["message_id"]


def poll_for_response(message_id):
    """Poll for a callback query response to the given message.

    Returns True if user tapped Yes, False if No, None if timed out.
    """
    deadline = time.time() + config.TELEGRAM_POLL_TIMEOUT
    last_update_id = 0

    while time.time() < deadline:
        resp = requests.get(
            f"{TELEGRAM_API}/getUpdates",
            params={"offset": last_update_id + 1, "timeout": 10},
            timeout=15,
        )
        resp.raise_for_status()
        updates = resp.json().get("result", [])

        for update in updates:
            last_update_id = update["update_id"]
            callback = update.get("callback_query")
            if not callback:
                continue
            # Match the callback to our message
            if callback.get("message", {}).get("message_id") != message_id:
                continue

            answer = callback["data"] == "buy_yes"

            # Acknowledge the callback so the button stops spinning
            requests.post(
                f"{TELEGRAM_API}/answerCallbackQuery",
                json={"callback_query_id": callback["id"]},
                timeout=10,
            )

            # Edit the message to show the choice
            choice_text = "Yes" if answer else "No"
            requests.post(
                f"{TELEGRAM_API}/editMessageText",
                json={
                    "chat_id": config.TELEGRAM_CHAT_ID,
                    "message_id": message_id,
                    "text": f"Buy UCLA parking today? → *{choice_text}*",
                    "parse_mode": "Markdown",
                },
                timeout=10,
            )
            return answer

        time.sleep(config.TELEGRAM_POLL_INTERVAL)

    return None


def ask_for_plate():
    """Ask which license plate to use. Returns the plate string.

    Shows 9VSK311 as default button, with option to type a different one.
    """
    keyboard = {
        "inline_keyboard": [
            [{"text": "9VSK311 (default)", "callback_data": "plate_9VSK311"}],
            [{"text": "Different plate", "callback_data": "plate_other"}],
        ]
    }
    resp = requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json={
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": "Which car are you driving today?",
            "reply_markup": keyboard,
        },
        timeout=10,
    )
    resp.raise_for_status()
    message_id = resp.json()["result"]["message_id"]

    # Poll for callback
    deadline = time.time() + config.TELEGRAM_POLL_TIMEOUT
    last_update_id = 0

    while time.time() < deadline:
        resp = requests.get(
            f"{TELEGRAM_API}/getUpdates",
            params={"offset": last_update_id + 1, "timeout": 10},
            timeout=15,
        )
        resp.raise_for_status()
        updates = resp.json().get("result", [])

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

            if callback["data"] == "plate_9VSK311":
                requests.post(
                    f"{TELEGRAM_API}/editMessageText",
                    json={
                        "chat_id": config.TELEGRAM_CHAT_ID,
                        "message_id": message_id,
                        "text": "Which car are you driving today? → *9VSK311*",
                        "parse_mode": "Markdown",
                    },
                    timeout=10,
                )
                return "9VSK311"
            else:
                # Ask them to type the plate
                requests.post(
                    f"{TELEGRAM_API}/editMessageText",
                    json={
                        "chat_id": config.TELEGRAM_CHAT_ID,
                        "message_id": message_id,
                        "text": "Type your license plate number:",
                    },
                    timeout=10,
                )
                return _wait_for_text_reply(last_update_id)

        time.sleep(config.TELEGRAM_POLL_INTERVAL)

    return None


def _wait_for_text_reply(last_update_id):
    """Wait for a text message reply. Returns the text or None."""
    deadline = time.time() + config.TELEGRAM_POLL_TIMEOUT
    while time.time() < deadline:
        resp = requests.get(
            f"{TELEGRAM_API}/getUpdates",
            params={"offset": last_update_id + 1, "timeout": 10},
            timeout=15,
        )
        resp.raise_for_status()
        updates = resp.json().get("result", [])

        for update in updates:
            last_update_id = update["update_id"]
            msg = update.get("message")
            if not msg:
                continue
            if str(msg.get("chat", {}).get("id")) != str(config.TELEGRAM_CHAT_ID):
                continue
            text = msg.get("text", "").strip()
            if text:
                return text.upper()

        time.sleep(config.TELEGRAM_POLL_INTERVAL)

    return None


def ask_for_duo_code():
    """Send a Telegram message asking for the DUO code, then wait for a reply.

    Returns the code as a string, or None if timed out.
    """
    # Flush any pending updates first so we don't pick up old messages
    resp = requests.get(
        f"{TELEGRAM_API}/getUpdates",
        params={"offset": -1, "timeout": 0},
        timeout=10,
    )
    resp.raise_for_status()
    results = resp.json().get("result", [])
    last_update_id = results[-1]["update_id"] if results else 0

    # Send the prompt
    requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json={
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": "Enter your DUO passcode:",
        },
        timeout=10,
    )

    # Poll for a text message reply (up to 2 min)
    deadline = time.time() + config.DUO_WAIT_TIMEOUT
    while time.time() < deadline:
        resp = requests.get(
            f"{TELEGRAM_API}/getUpdates",
            params={"offset": last_update_id + 1, "timeout": 10},
            timeout=15,
        )
        resp.raise_for_status()
        updates = resp.json().get("result", [])

        for update in updates:
            last_update_id = update["update_id"]
            msg = update.get("message")
            if not msg:
                continue
            if str(msg.get("chat", {}).get("id")) != str(config.TELEGRAM_CHAT_ID):
                continue
            text = msg.get("text", "").strip()
            if text:
                requests.post(
                    f"{TELEGRAM_API}/sendMessage",
                    json={
                        "chat_id": config.TELEGRAM_CHAT_ID,
                        "text": f"Got it: {text}",
                    },
                    timeout=10,
                )
                return text

        time.sleep(config.TELEGRAM_POLL_INTERVAL)

    return None


def send_message(text):
    """Send a plain text message to the configured chat."""
    resp = requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json={
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": text,
        },
        timeout=10,
    )
    resp.raise_for_status()


def send_photo(photo_path, caption=""):
    """Send a photo (e.g. screenshot) to the configured chat."""
    with open(photo_path, "rb") as f:
        resp = requests.post(
            f"{TELEGRAM_API}/sendPhoto",
            data={"chat_id": config.TELEGRAM_CHAT_ID, "caption": caption},
            files={"photo": f},
            timeout=30,
        )
    resp.raise_for_status()


if __name__ == "__main__":
    # Quick test: send a prompt and wait for response
    print("Sending purchase prompt...")
    msg_id = send_purchase_prompt()
    print(f"Message sent (id={msg_id}). Waiting for response...")
    result = poll_for_response(msg_id)
    if result is True:
        print("User said YES")
    elif result is False:
        print("User said NO")
    else:
        print("Timed out waiting for response")
