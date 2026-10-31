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

# Leave-by: where each structure is (for Google Routes + Waze) and when class starts.
# Coordinates verified against Google Routes + OpenStreetMap on 2026-10-02.
STRUCTURE_DESTINATIONS = {
    STRUCTURE_4:  {"name": "Structure 4", "address": "UCLA Parking Structure 4, Los Angeles, CA 90095",
                   "lat": 34.0726354, "lng": -118.444769},   # Google's point, verified 2026-10-02
    STRUCTURE_P7: {"name": "Structure 7", "address": "UCLA Parking Structure 7, Charles E Young Dr N, Los Angeles, CA 90095",
                   "lat": 34.0727566, "lng": -118.4469530},
}

# "Arrive by" = class start minus this many minutes.
ARRIVE_BEFORE_CLASS_MIN = 60

# Class start times from the REV26 dot calendar's "Typical Schedule" legend:
# Fri Class A 4:00 PM, Sat Class B 9:00 AM, Sun electives 9:00 AM.
CLASS_START_BY_WEEKDAY = {4: "16:00", 5: "09:00", 6: "09:00"}  # Mon=0 … Sun=6
# Dates marked ★ "Mandatory Class starts at 10:00 a.m." on the dot calendar.
CLASS_START_OVERRIDES = {
    "2026-10-02": "10:00",
    "2027-01-08": "10:00",
    "2027-04-02": "10:00",
}


def class_start(date_str, cohort="2027"):
    """'HH:MM' class start for a class day of that cohort, or None if unknown."""
    import datetime as _dt
    overrides = COHORTS[cohort]["class_start_overrides"]
    if date_str in overrides:
        return overrides[date_str]
    return CLASS_START_BY_WEEKDAY.get(_dt.date.fromisoformat(date_str).weekday())


# Elective-only days — parking is only needed if the user is taking an elective
ELECTIVE_DATES = {
    "2026-06-27", "2026-06-28",
    "2026-07-18", "2026-07-19",
    "2026-08-08", "2026-08-09", "2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14",
    "2026-08-29", "2026-08-30",
    "2026-10-04", "2026-11-01", "2026-12-06", "2027-01-10", "2027-02-07", "2027-03-07", "2027-04-04", "2027-05-02", "2027-06-06",  # optional EMBA Hybrid Elective Sundays (dot calendar squares)
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
    "2026-10-04", "2026-11-01", "2026-12-06", "2027-01-10", "2027-02-07", "2027-03-07", "2027-04-04", "2027-05-02", "2027-06-06",  # optional EMBA Hybrid Elective Sundays (dot calendar squares)
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


# ── Class of 2028 ─────────────────────────────────────────────────────────
# Sourced from the Class of 2028 dot calendars (Bi-Weekly + Monthly Section
# Calendars, dated 08.17.2026). Same rules as the Class of 2027 sets above:
# PARKING_DATES follows the Bi-Weekly calendar plus the Monthly-only elective
# Sunday (2027-06-06); Zoom days and the International Business Residential
# are left out. Finals: Bi-Weekly's are in person, Monthly's are online.
PARKING_DATES_2028 = {
    "2026-08-23", "2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28",  # opening residential
    "2026-09-25", "2026-09-26", "2026-10-09", "2026-10-10", "2026-10-23", "2026-10-24",
    "2026-11-06", "2026-11-07", "2026-11-20", "2026-11-21", "2026-12-12",
    "2027-01-08", "2027-01-09", "2027-01-22", "2027-01-23", "2027-01-29", "2027-01-30",
    "2027-02-12", "2027-02-13", "2027-02-26", "2027-02-27", "2027-03-13",
    "2027-04-09", "2027-04-10", "2027-04-23", "2027-04-24",
    "2027-05-07", "2027-05-08", "2027-05-21", "2027-05-22",
    "2027-06-04", "2027-06-05", "2027-06-12",
    "2027-10-01", "2027-10-02", "2027-10-15", "2027-10-16", "2027-10-29", "2027-10-30",
    "2027-11-12", "2027-11-13", "2027-12-03", "2027-12-04", "2027-12-11",
    "2028-01-07", "2028-01-08", "2028-01-28", "2028-01-29", "2028-01-30",
    "2028-02-04", "2028-02-05", "2028-03-03", "2028-03-04",
    "2028-04-07", "2028-04-08", "2028-04-28", "2028-04-29",
    "2028-06-02", "2028-06-03", "2028-06-09",
    "2027-01-10", "2027-01-31", "2027-02-28", "2027-04-11", "2027-05-09", "2027-06-06",  # optional EMBA Hybrid Electives (dot calendar squares)
    "2027-06-26", "2027-06-27", "2027-07-17", "2027-07-18", "2027-08-07", "2027-08-08",
    "2027-08-28", "2027-08-29", "2027-10-03", "2027-10-31", "2027-12-05",
    "2028-01-09", "2028-02-06", "2028-03-05", "2028-04-09", "2028-04-30", "2028-06-04",
    "2027-08-09", "2027-08-10", "2027-08-11", "2027-08-12", "2027-08-13",  # August Block
}

ELECTIVE_DATES_2028 = {
    "2027-01-10", "2027-01-31", "2027-02-28", "2027-04-11", "2027-05-09", "2027-06-06",
    "2027-06-26", "2027-06-27", "2027-07-17", "2027-07-18", "2027-08-07", "2027-08-08",
    "2027-08-28", "2027-08-29", "2027-10-03", "2027-10-31", "2027-12-05",
    "2028-01-09", "2028-02-06", "2028-03-05", "2028-04-09", "2028-04-30", "2028-06-04",
    "2027-08-09", "2027-08-10", "2027-08-11", "2027-08-12", "2027-08-13",
}

AUGUST_BLOCK_DATES_2028 = {
    "2027-08-09", "2027-08-10", "2027-08-11", "2027-08-12", "2027-08-13",
}

BIWEEKLY_ONLY_DATES_2028 = {
    "2026-10-09", "2026-10-10", "2026-11-06", "2026-11-07", "2026-12-12",
    "2027-01-22", "2027-01-23", "2027-02-12", "2027-02-13", "2027-03-13",
    "2027-04-23", "2027-04-24", "2027-05-21", "2027-05-22", "2027-06-12",
    "2027-10-15", "2027-10-16", "2027-11-12", "2027-11-13", "2027-12-11",
}

# ★ "Mandatory Class starts at 10:00 a.m." The sections disagree on 2027-02-26
# and 2027-04-09 (★ on Monthly only); 10:00 is used so nobody is told to
# arrive late.
CLASS_START_OVERRIDES_2028 = {
    "2026-09-25": "10:00",
    "2027-02-12": "10:00",
    "2027-02-26": "10:00",
    "2027-04-09": "10:00",
    "2027-04-23": "10:00",
    "2027-10-01": "10:00",
    "2028-01-07": "10:00",
    "2028-04-07": "10:00",
}

# Per-cohort schedules. Phones that never picked a class are Class of 2027.
DEFAULT_COHORT = "2027"
COHORTS = {
    "2027": {
        "parking_dates": PARKING_DATES,
        "elective_dates": ELECTIVE_DATES,
        "august_block_dates": AUGUST_BLOCK_DATES,
        "biweekly_only_dates": BIWEEKLY_ONLY_DATES,
        "class_start_overrides": CLASS_START_OVERRIDES,
        "special_days": SPECIAL_DAYS,
    },
    "2028": {
        "parking_dates": PARKING_DATES_2028,
        "elective_dates": ELECTIVE_DATES_2028,
        "august_block_dates": AUGUST_BLOCK_DATES_2028,
        "biweekly_only_dates": BIWEEKLY_ONLY_DATES_2028,
        "class_start_overrides": CLASS_START_OVERRIDES_2028,
        "special_days": {},
    },
}

# Any cohort's class day (GitHub backup cron gate).
ALL_PARKING_DATES = PARKING_DATES | PARKING_DATES_2028


def cohort_of(value):
    """Normalise a client-sent cohort; anything unknown is the default."""
    value = str(value or "")
    return value if value in COHORTS else DEFAULT_COHORT
