"""Local entry point — run the full prompt + purchase flow from your Mac."""

import sys
import bot
import buy_parking
import config
from crypto_utils import load_users


def main():
    headless = "--headless" in sys.argv
    dry_run = "--dry-run" in sys.argv

    users = load_users()
    if not users:
        print("No registered users. Run the register workflow first.")
        sys.exit(1)

    # For local testing, just run for the first user
    user = users[0]
    chat_id = user["telegram_chat_id"]

    print("Sending Telegram prompt...")
    prompts = bot.send_purchase_prompts([user])
    responses = bot.poll_all_responses(prompts)

    if responses.get(str(chat_id)):
        plates = user.get("plates", [user["default_plate"]] if "default_plate" in user else [])
        plate = bot.ask_for_plate(chat_id, plates)
        if not plate:
            print("No plate selected.")
            sys.exit(0)

        structure = bot.ask_for_structure(chat_id)

        print(f"Approved! Starting purchase (plate: {plate})...")
        bot.send_message(chat_id, f"Purchasing Yellow 1-Day pass (~$7.28)\nPlate: {plate}\n\nStarting now...")
        success = buy_parking.run(
            username=user["ucla_username"],
            password=user["ucla_password"],
            plate=plate,
            structure=structure,
            chat_id=chat_id,
            headless=headless,
            dry_run=dry_run,
        )
        if success:
            bot.send_message(chat_id, "Parking purchased successfully!")
        else:
            bot.send_message(chat_id, "Parking purchase FAILED.")
            sys.exit(1)
    else:
        print("Declined or timed out.")


if __name__ == "__main__":
    main()
