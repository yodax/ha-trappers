"""Constants for the Trappers integration."""
from datetime import timedelta

DOMAIN = "trappers"

CONF_EMAIL = "email"
CONF_PASSWORD = "password"

# Points are credited at most once per working day — when the collector unit
# reads the bike tag on arrival at the office — so there is nothing to see
# overnight, and this is an unofficial API belonging to an employer benefits
# provider. Rather than a free-running interval, polls happen at these fixed
# wall-clock hours in Home Assistant's configured timezone: five a day, none
# between 20:00 and 08:00.
#
# Fixed slots rather than a 3-hour timer on purpose. A free-running timer is
# anchored to whenever Home Assistant last restarted, so it drifts to arbitrary
# times and "when does it poll?" stops having an answer. Slots are predictable
# and inspectable, which is worth more here than the handful of lines it costs.
POLL_HOURS = (8, 11, 14, 17, 20)

# Each config entry polls at a fixed offset of its own after each slot, between
# zero and this. Nothing here needs minute precision — points are credited once
# a working day — and without it every install of this integration in the world
# would hit an employer benefits provider's API at exactly 08:00:00. It also
# keeps polls off the top of the hour, where every cron job and every other
# integration already is.
#
# The offset is derived from the config entry rather than rolled fresh each
# time, so it is stable for a given install: spreading load is a property
# *across* installs, and re-rolling would buy none of it while giving up the
# predictability that fixed slots exist for. See `poll_jitter()`.
MAX_POLL_JITTER = timedelta(minutes=15)

# Floor on the computed gap to the next slot. A clock step, a DST transition or
# waking exactly on a slot boundary can compute a zero or negative delta, and an
# unclamped value there is a hot loop against someone's employer's API.
MIN_UPDATE_INTERVAL = timedelta(seconds=60)

# The account's points balance is denominated in "trappers" (the scheme's own
# name for its points). Not a physical unit, so it is passed through as-is.
UNIT_TRAPPERS = "trappers"
