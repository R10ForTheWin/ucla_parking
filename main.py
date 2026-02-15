"""Local entry point — run the full prompt + purchase flow from your Mac."""

import sys
import bot
import buy_parking


def main():
    headless = "--headless" in sys.argv

    print("Sending Telegram prompt...")
    msg_id = bot.send_purchase_prompt()
    print(f"Prompt sent (message_id={msg_id}). Check your phone!")

    result = bot.poll_for_response(msg_id)

    if result is True:
        print("Approved! Starting purchase...")
        success = buy_parking.run(headless=headless)
        if success:
            bot.send_message("Parking purchased successfully!")
            print("Done!")
        else:
            bot.send_message("Parking purchase FAILED.")
            print("Purchase failed.")
            sys.exit(1)
    elif result is False:
        print("Declined. Skipping purchase.")
        bot.send_message("Parking purchase skipped.")
    else:
        print("No response. Skipping.")
        bot.send_message("No response to parking prompt. Skipped.")


if __name__ == "__main__":
    main()
