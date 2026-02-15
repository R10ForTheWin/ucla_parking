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
TELEGRAM_POLL_TIMEOUT = 1800   # 30 min to respond to Yes/No prompt
TELEGRAM_POLL_INTERVAL = 5     # Poll every 5 seconds
PAGE_LOAD_TIMEOUT = 30000      # Playwright page timeout (ms)

# Retry
MAX_RETRIES = 2

# Screenshots
SCREENSHOT_DIR = "screenshots"
