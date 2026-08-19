"""Configuration settings for UCLA parking auto-purchase."""

import os

# UCLA Bruin ePermit
EPERMIT_URL = "https://bruinepermit.t2hosted.com"

# Timeouts (seconds)
DUO_WAIT_TIMEOUT = 120        # How long to wait for DUO Push approval
QUEUE_IT_TIMEOUT = 300         # How long to wait in Queue-it waiting room
PAGE_LOAD_TIMEOUT = 60000      # Playwright page timeout (ms)

# Retry — keep low to avoid hammering the site and worsening IP reputation
MAX_RETRIES = 1

# Parking structures (option values from Str * - Student Daily Yellow dropdown)
# NOTE: These values drift — UCLA changes them periodically.
# The bot falls back to the only available option if configured values don't match.
STRUCTURE_4  = "2003"    # Str 4 - Student Daily Yellow (confirmed 2026-04-29)
STRUCTURE_P7 = "2128"    # Str 7 - Student Daily Yellow (confirmed 2026-04-02)
STRUCTURE_32 = "2129"    # Str 32 - Student Daily Yellow (confirmed 2026-04-02)

# Screenshots
SCREENSHOT_DIR = "screenshots"

# Elective-only days — parking is only needed if the user is taking an elective
ELECTIVE_DATES = {
    "2026-06-27", "2026-06-28",
    "2026-07-18", "2026-07-19",
    "2026-08-08", "2026-08-09", "2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14",
    "2026-08-29", "2026-08-30",
}

# August Block — in-person elective week (Mon-Fri). Everyone enrolled attends,
# so push notification / home screen show a specific label instead of the
# generic "(if you are taking an elective)" caveat.
AUGUST_BLOCK_DATES = {
    "2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14",
}

# Class days — workflow only runs the purchase flow on these dates.
# Sourced from the official REV26 dot calendars (Bi-Weekly + Monthly Section
# Calendars, Class of 2027, dated 08.06.2026). The Bi-Weekly section's
# schedule is a superset of the Monthly section's (see BIWEEKLY_ONLY_DATES
# below), so this set follows the Bi-Weekly calendar.
PARKING_DATES = {
    "2026-01-09", "2026-01-10", "2026-02-13", "2026-02-14", "2026-02-21",
    "2026-02-27", "2026-02-28", "2026-03-06", "2026-03-07", "2026-03-13", "2026-03-14", "2026-03-20", "2026-03-21",
    "2026-04-01", "2026-04-08", "2026-04-10", "2026-04-11", "2026-04-12", "2026-04-15", "2026-04-22", "2026-04-24",
    "2026-04-25", "2026-04-29",
    "2026-05-10", "2026-05-31", "2026-05-08", "2026-05-09", "2026-05-15", "2026-05-16",
    "2026-05-29", "2026-05-30", "2026-06-12",
    "2026-06-27", "2026-06-28", "2026-07-18", "2026-07-19",
    "2026-08-08", "2026-08-09", "2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14",
    "2026-08-29", "2026-08-30", "2026-10-02",
    "2026-10-03", "2026-10-16", "2026-10-17", "2026-10-30", "2026-10-31",
    "2026-11-13", "2026-11-14", "2026-12-04", "2026-12-05",
    "2027-01-08", "2027-01-09", "2027-01-29", "2027-01-30", "2027-01-31",
    "2027-02-05", "2027-02-06", "2027-03-05",
    "2027-03-06", "2027-04-02", "2027-04-03", "2027-04-30", "2027-05-01",
    "2027-06-04", "2027-06-05", "2027-06-11",
}

# Class weekends that ONLY apply to the Bi-Weekly section — the Monthly
# section does not meet those days (used in push notifications / home
# screen to show "who the announcement is for"). Every other date in
# PARKING_DATES is shared by both sections. Sourced from the REV26 dot
# calendars (Class of 2027, dated 08.06.2026).
BIWEEKLY_ONLY_DATES = {
    "2026-10-16", "2026-10-17", "2026-11-13", "2026-11-14",
}

# Special capstone days — push notifications and the home screen show a
# "good luck" message on these dates
SPECIAL_DAYS = {
    "2027-05-01": "Final Defense",
    "2027-06-05": "Demo Day",
}
