"""Configuration settings for UCLA parking auto-purchase."""

import os

# UCLA Bruin ePermit
EPERMIT_URL = "https://bruinepermit.t2hosted.com"
UCLA_USERNAME = os.environ.get("UCLA_USERNAME", "")
UCLA_PASSWORD = os.environ.get("UCLA_PASSWORD", "")

# Telegram Bot
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Timeouts (seconds)
DUO_WAIT_TIMEOUT = 120        # How long to wait for DUO 2FA approval
QUEUE_IT_TIMEOUT = 300         # How long to wait in Queue-it waiting room
TELEGRAM_POLL_TIMEOUT = 6000   # 100 min to respond to Yes/No prompt
TELEGRAM_POLL_INTERVAL = 5     # Poll every 5 seconds
PAGE_LOAD_TIMEOUT = 60000      # Playwright page timeout (ms)

# Retry
MAX_RETRIES = 3

# Parking structures (option values from the Parking Area dropdown)
STRUCTURE_4 = "2127"     # Structure 4 (default)
STRUCTURE_P7 = "2130"    # P7 — confirm this value from the dropdown

# Screenshots
SCREENSHOT_DIR = "screenshots"

# Class days that need parking (YYYY-MM-DD)
# Only Fridays and Saturdays from the EMBA bi-weekly calendar
PARKING_DATES = {
    # 2026
    "2026-01-09", "2026-01-10",
    "2026-02-13", "2026-02-14",
    "2026-02-27", "2026-02-28",
    "2026-04-10", "2026-04-11",
    "2026-04-24", "2026-04-25",
    "2026-05-08", "2026-05-09",
    "2026-05-15", "2026-05-16",
    "2026-05-29", "2026-05-30",
    "2026-06-12", "2026-06-13",
    "2026-06-19", "2026-06-20",
    "2026-06-27",
    "2026-07-18",
    "2026-08-29",
    "2026-10-02", "2026-10-03",
    "2026-10-16", "2026-10-17",
    "2026-10-30", "2026-10-31",
    "2026-11-13", "2026-11-14",
    "2026-12-04", "2026-12-05",
    "2026-12-11", "2026-12-12",
    # 2027
    "2027-01-08", "2027-01-09",
    "2027-01-29", "2027-01-30",
    "2027-02-05", "2027-02-06",
    "2027-02-19", "2027-02-20",
    "2027-03-05", "2027-03-06",
    "2027-04-02", "2027-04-03",
    "2027-04-30",
    "2027-05-28", "2027-05-29",
    "2027-06-11",
}
