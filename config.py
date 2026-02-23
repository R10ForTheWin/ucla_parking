"""Configuration settings for UCLA parking auto-purchase."""

import os

# UCLA Bruin ePermit
EPERMIT_URL = "https://bruinepermit.t2hosted.com"

# Timeouts (seconds)
DUO_WAIT_TIMEOUT = 120        # How long to wait for DUO 2FA approval
QUEUE_IT_TIMEOUT = 300         # How long to wait in Queue-it waiting room
PAGE_LOAD_TIMEOUT = 60000      # Playwright page timeout (ms)

# Retry — keep low to avoid hammering the site and worsening IP reputation
MAX_RETRIES = 1

# Parking structures (option values from the Parking Area dropdown)
STRUCTURE_4 = "2127"     # Structure 4 (default)
STRUCTURE_P7 = "2130"    # P7 — confirm this value from the dropdown

# Screenshots
SCREENSHOT_DIR = "screenshots"

