"""Constants for the Trappers integration."""
from datetime import timedelta

DOMAIN = "trappers"

CONF_EMAIL = "email"
CONF_PASSWORD = "password"

# Points are credited at most once per working day — when the collector unit
# reads the tag on arrival at the office — and this is an unofficial API
# belonging to an employer benefits provider, so poll gently. Hourly still
# surfaces the day's points within an hour of arriving; anything longer starts
# to feel stale for no further saving.
DEFAULT_SCAN_INTERVAL = timedelta(hours=1)

# The account's points balance is denominated in "trappers" (the scheme's own
# name for its points). Not a physical unit, so it is passed through as-is.
UNIT_TRAPPERS = "trappers"
