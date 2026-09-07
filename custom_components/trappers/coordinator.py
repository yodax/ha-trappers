"""DataUpdateCoordinator for the Trappers integration."""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import TrappersApiClient, TrappersApiError, TrappersAuthError
from .const import DOMAIN, MAX_POLL_JITTER, MIN_UPDATE_INTERVAL, POLL_HOURS

_LOGGER = logging.getLogger(__name__)


def poll_jitter(seed: str) -> timedelta:
    """This install's fixed offset after each slot, in [0, MAX_POLL_JITTER).

    Spreads load across installs so that every copy of this integration does
    not hit the API at exactly 08:00:00, without giving up the predictability
    that fixed slots exist for: the same seed always yields the same offset, so
    a given install polls at the same few minutes past the hour every day.

    **`hashlib`, not the builtin `hash()`.** Python randomises string hashing
    per process, so `hash()` here would silently produce a different offset
    after every Home Assistant restart — turning a deliberately stable offset
    back into a per-restart random one while the code still read as
    deterministic. `test_offset_is_stable_across_processes` pins this.

    Seeded from the config entry id rather than the account email: it is
    already a random per-entry ULID, so it spreads a two-account household
    across two offsets, and it keeps an email address out of the calculation
    entirely.
    """
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    fraction = int.from_bytes(digest[:8], "big") / 2**64
    return timedelta(seconds=int(MAX_POLL_JITTER.total_seconds() * fraction))


def next_poll_time(now: datetime, jitter: timedelta = timedelta()) -> datetime:
    """The next poll time at or after `now`, in HA's local timezone.

    Slots are local wall-clock hours, so across a DST transition one gap is
    genuinely two or four hours of real time. That is correct, not something to
    compensate for: the point is to poll at 08:00 local, whatever 08:00 local
    happens to mean that day. Doing the arithmetic on aware datetimes hands the
    whole problem to the tz database.

    `jitter` is added **after** each slot, never around it. Spreading
    symmetrically would let the first poll of the day fire before 08:00, which
    is exactly the thing the window is for.
    """
    local = dt_util.as_local(now)

    for hour in POLL_HOURS:
        candidate = (
            local.replace(hour=hour, minute=0, second=0, microsecond=0) + jitter
        )
        if candidate > local:
            return candidate

    # Past the last slot of the day — the first slot tomorrow. `+ timedelta` on
    # an aware datetime is wall-clock arithmetic, which is what "tomorrow"
    # should mean here.
    tomorrow = local + timedelta(days=1)
    return (
        tomorrow.replace(hour=POLL_HOURS[0], minute=0, second=0, microsecond=0)
        + jitter
    )


def interval_until_next_poll(
    now: datetime, jitter: timedelta = timedelta()
) -> timedelta:
    """How long to wait before the next poll, never less than the clamp.

    Both datetimes are converted to UTC before subtracting, and that is
    load-bearing rather than tidiness. Subtracting two aware datetimes that
    share the *same* ``tzinfo`` object — which they do here, both carrying Home
    Assistant's configured zone — makes Python skip ``utcoffset()`` entirely and
    return the wall-clock difference. Across a DST transition that is wrong by
    an hour in the direction that fires the poll late: on the spring-forward
    day, 20:01 CET to 08:00 CEST is 10h59m of real time, not the 11h59m a naive
    subtraction reports. Converting to UTC first resolves each offset and hands
    the problem to the tz database, which is where it belongs.
    """
    delta = dt_util.as_utc(next_poll_time(now, jitter)) - dt_util.as_utc(now)
    return max(delta, MIN_UPDATE_INTERVAL)


class TrappersCoordinator(DataUpdateCoordinator[dict]):
    """Polls the Trappers API for one account's balance and cycling activity.

    The schedule is a moving `update_interval`, recomputed after every refresh
    to point at the next slot in `POLL_HOURS`, rather than a fixed interval.

    There is deliberately **no "outside the window, skip" branch**. The
    coordinator simply is not woken between 20:00 and 08:00, so nothing needs to
    decide whether to skip — and therefore nothing can accidentally raise
    `UpdateFailed` overnight and paint every sensor unavailable until morning.
    The first refresh is requested explicitly at setup, so a restart at 02:00
    still populates the sensors immediately; the window governs the recurring
    schedule, not the initial load. `homeassistant.update_entity` and the entry's
    reload button also refresh at any hour.

    Each entry adds its own fixed offset of up to 15 minutes after each slot,
    so that installs do not all arrive at exactly 08:00:00 — see
    `poll_jitter()`.
    """

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: TrappersApiClient
    ) -> None:
        # Computed once per entry, not per refresh: the offset has to be the
        # same every day for this install, and only differ between installs.
        self._jitter = poll_jitter(entry.entry_id)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} ({entry.title})",
            update_interval=interval_until_next_poll(dt_util.now(), self._jitter),
        )
        self.client = client

    async def _async_update_data(self) -> dict:
        # dt_util.now() is Home Assistant's configured timezone, which is the
        # clock both the "this month" sensors and the poll schedule should
        # follow — not the container's, which may well be UTC.
        now = dt_util.now()
        try:
            data = await self.client.async_get_data(today=now.date(), now=now)
        except TrappersAuthError as err:
            # Not UpdateFailed: a rejected password has to start HA's reauth
            # flow, or the sensors sit unavailable forever with no prompt.
            raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
        except TrappersApiError as err:
            raise UpdateFailed(str(err)) from err
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise UpdateFailed(f"Error communicating with Trappers: {err}") from err

        # Reschedule onto the next slot. Assigning here rather than in a
        # separate timer keeps the schedule in one place; HA re-arms the
        # listener with the new interval once this refresh completes.
        self.update_interval = interval_until_next_poll(now, self._jitter)
        _LOGGER.debug(
            "Next Trappers poll at %s", next_poll_time(now, self._jitter).isoformat()
        )
        return data
