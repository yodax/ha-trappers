"""Constants for the Trappers integration."""
from datetime import timedelta

DOMAIN = "trappers"

CONF_EMAIL = "email"
CONF_PASSWORD = "password"

# Trappers points are credited at most once per working day, and this is an
# unofficial API belonging to an employer benefits provider — poll gently.
DEFAULT_SCAN_INTERVAL = timedelta(minutes=30)

# The account's points balance is denominated in "trappers" (the scheme's own
# name for its points). Not a physical unit, so it is passed through as-is.
UNIT_TRAPPERS = "trappers"
