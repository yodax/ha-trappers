"""Tests for the fixed-slot poll schedule.

The integration polls at 08:00, 11:00, 14:00, 17:00 and 20:00 in Home
Assistant's configured timezone, and not at all overnight. These pin the slot
arithmetic — including both DST transitions, where a gap between two slots is
genuinely two or four hours of real time — and pin the two properties that
would be user-visible bugs: sensors never going unavailable between polls, and
the computed interval never dropping below the clamp.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.trappers.api import TrappersApiClient
from custom_components.trappers.const import (
    CONF_EMAIL,
    CONF_PASSWORD,
    DOMAIN,
    MAX_POLL_JITTER,
    MIN_UPDATE_INTERVAL,
    POLL_HOURS,
)
from custom_components.trappers.coordinator import (
    TrappersCoordinator,
    interval_until_next_poll,
    next_poll_time,
    poll_jitter,
)

AMSTERDAM = ZoneInfo("Europe/Amsterdam")


@pytest.fixture
def amsterdam_time_zone():
    """Run with Europe/Amsterdam as HA's configured timezone, as on the real box."""
    original = dt_util.get_default_time_zone()
    dt_util.set_default_time_zone(AMSTERDAM)
    yield
    dt_util.set_default_time_zone(original)


def local(year, month, day, hour, minute=0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=AMSTERDAM)


@pytest.mark.usefixtures("amsterdam_time_zone")
class TestNextPollTime:
    @pytest.mark.parametrize(
        ("now", "expected"),
        [
            pytest.param(local(2026, 9, 7, 10, 30), local(2026, 9, 7, 11), id="mid-window"),
            pytest.param(local(2026, 9, 7, 8, 1), local(2026, 9, 7, 11), id="just-after-a-slot"),
            pytest.param(local(2026, 9, 7, 16, 59), local(2026, 9, 7, 17), id="a-minute-before"),
            pytest.param(local(2026, 9, 7, 7, 59), local(2026, 9, 7, 8), id="just-before-opening"),
        ],
    )
    def test_within_the_day(self, now: datetime, expected: datetime) -> None:
        assert next_poll_time(now) == expected

    def test_exactly_on_a_slot_moves_to_the_next_one(self) -> None:
        """Never returns `now` itself — that would schedule a zero-length wait."""
        assert next_poll_time(local(2026, 9, 7, 14)) == local(2026, 9, 7, 17)

    @pytest.mark.parametrize(
        "now",
        [
            pytest.param(local(2026, 9, 7, 20, 1), id="just-after-the-last-slot"),
            pytest.param(local(2026, 9, 7, 23, 59), id="late-evening"),
        ],
    )
    def test_after_the_last_slot_waits_for_tomorrow_morning(self, now: datetime) -> None:
        assert next_poll_time(now) == local(2026, 9, 8, 8)

    def test_overnight_waits_for_the_morning(self) -> None:
        assert next_poll_time(local(2026, 9, 7, 3)) == local(2026, 9, 7, 8)

    def test_month_and_year_boundaries(self) -> None:
        assert next_poll_time(local(2026, 9, 30, 21)) == local(2026, 10, 1, 8)
        assert next_poll_time(local(2026, 12, 31, 22)) == local(2027, 1, 1, 8)

    def test_a_utc_input_is_converted_to_local_first(self) -> None:
        """A UTC container clock must not shift the slots by an hour or two."""
        # 09:30 UTC is 11:30 Amsterdam in summer, so the next slot is 14:00 local.
        utc_now = datetime(2026, 9, 7, 9, 30, tzinfo=dt_util.UTC)

        assert next_poll_time(utc_now) == local(2026, 9, 7, 14)


@pytest.mark.usefixtures("amsterdam_time_zone")
class TestDaylightSavingTransitions:
    """Slots are wall-clock, so one real-time gap per year is 2h and one is 4h.

    That is the intended behaviour — the point is to poll at 08:00 local
    whatever 08:00 local means that day — and it is the tz database, not this
    code, that works out how long that is.
    """

    def test_spring_forward_gap_is_two_real_hours(self) -> None:
        # 2026-03-29: Europe/Amsterdam jumps 02:00 -> 03:00, so the 20:00-to-08:00
        # overnight gap loses an hour.
        now = local(2026, 3, 28, 20, 1)

        assert next_poll_time(now) == local(2026, 3, 29, 8)
        # 20:01 CET -> 08:00 CEST is 10h59m of real time, not 11h59m.
        assert interval_until_next_poll(now) == timedelta(hours=10, minutes=59)

    def test_spring_forward_within_the_day_is_unaffected(self) -> None:
        now = local(2026, 3, 29, 10, 30)

        assert next_poll_time(now) == local(2026, 3, 29, 11)
        assert interval_until_next_poll(now) == timedelta(minutes=30)

    def test_fall_back_gap_is_an_hour_longer(self) -> None:
        # 2026-10-25: Europe/Amsterdam repeats 02:00-03:00, so the overnight
        # gap gains an hour of real time.
        now = local(2026, 10, 24, 20, 1)

        assert next_poll_time(now) == local(2026, 10, 25, 8)
        assert interval_until_next_poll(now) == timedelta(hours=12, minutes=59)

    def test_every_slot_on_a_transition_day_is_still_reachable(self) -> None:
        """No slot lands in the spring-forward gap or is skipped by it."""
        for day in (local(2026, 3, 29, 0), local(2026, 10, 25, 0)):
            reached = []
            cursor = day
            for _ in range(len(POLL_HOURS)):
                cursor = next_poll_time(cursor)
                reached.append(cursor.hour)
            assert reached == list(POLL_HOURS)


@pytest.mark.usefixtures("amsterdam_time_zone")
class TestIntervalClamp:
    @pytest.mark.parametrize(
        "now",
        [
            pytest.param(local(2026, 9, 7, 10, 59), id="a-minute-out"),
            pytest.param(local(2026, 9, 7, 11), id="exactly-on-a-slot"),
            pytest.param(local(2026, 9, 7, 20), id="on-the-last-slot"),
            pytest.param(local(2026, 3, 29, 2, 30), id="inside-the-dst-gap"),
        ],
    )
    def test_interval_is_never_below_the_clamp(self, now: datetime) -> None:
        """An unclamped zero or negative delta is a hot loop against the API."""
        assert interval_until_next_poll(now) >= MIN_UPDATE_INTERVAL

    def test_interval_is_never_negative_across_a_whole_year(self) -> None:
        cursor = local(2026, 1, 1, 0, 17)
        for _ in range(365 * 4):
            assert interval_until_next_poll(cursor) >= MIN_UPDATE_INTERVAL
            cursor += timedelta(hours=6, minutes=13)


async def _coordinator(hass: HomeAssistant) -> TrappersCoordinator:
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Alex",
        unique_id="user@example.com",
        data={CONF_EMAIL: "user@example.com", CONF_PASSWORD: "hunter2"},
    )
    entry.add_to_hass(hass)
    client = AsyncMock(spec=TrappersApiClient)
    client.async_get_data.return_value = {"balance": 10000.0}
    return TrappersCoordinator(hass, entry, client)


async def test_coordinator_starts_scheduled_on_a_slot(hass: HomeAssistant) -> None:
    coordinator = await _coordinator(hass)

    assert coordinator.update_interval >= MIN_UPDATE_INTERVAL
    # Longest gap is 20:00 -> 08:00, plus an hour on the fall-back night and up
    # to a quarter of an hour of this entry's own offset.
    assert coordinator.update_interval <= timedelta(hours=13) + MAX_POLL_JITTER


async def test_refresh_reschedules_onto_the_next_slot(hass: HomeAssistant) -> None:
    coordinator = await _coordinator(hass)

    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    expected = interval_until_next_poll(
        dt_util.now(), poll_jitter(coordinator.config_entry.entry_id)
    )
    # Same slot, allowing for the second or two the refresh itself took.
    assert abs(coordinator.update_interval - expected) < timedelta(seconds=5)


async def test_first_refresh_happens_even_outside_the_window(hass: HomeAssistant) -> None:
    """A restart at 02:00 must populate the sensors, not leave them unknown till 08:00.

    The window governs the recurring schedule only — there is no skip branch in
    `_async_update_data`, so an explicitly requested refresh always fetches.
    """
    coordinator = await _coordinator(hass)

    await coordinator.async_refresh()

    assert coordinator.data == {"balance": 10000.0}
    assert coordinator.client.async_get_data.await_count == 1


async def test_data_is_held_between_polls_not_dropped(hass: HomeAssistant) -> None:
    """Between slots the sensors keep their last value rather than going unavailable.

    There is no skip path that could raise UpdateFailed overnight — the
    coordinator is simply not woken. This pins that a long interval leaves the
    coordinator successful with its data intact, which is what keeps the
    entities available.
    """
    coordinator = await _coordinator(hass)
    await coordinator.async_refresh()

    coordinator.update_interval = timedelta(hours=12)

    assert coordinator.last_update_success is True
    assert coordinator.data == {"balance": 10000.0}


async def test_manual_refresh_works_at_any_hour(hass: HomeAssistant) -> None:
    """`homeassistant.update_entity` is the escape hatch and is not window-gated."""
    coordinator = await _coordinator(hass)
    await coordinator.async_refresh()
    coordinator.client.async_get_data.return_value = {"balance": 10154.0}

    await coordinator.async_request_refresh()
    await hass.async_block_till_done()

    assert coordinator.data == {"balance": 10154.0}
    assert coordinator.client.async_get_data.await_count == 2
    # async_request_refresh() debounces, which leaves a timer behind.
    await coordinator.async_shutdown()


class TestPollJitter:
    """Each install polls a fixed few minutes past its slots, not on the dot.

    Without this, every copy of a public HACS integration hits an employer
    benefits provider's API at exactly 08:00:00. The offset is per-install and
    stable rather than random per poll, so load spreads across installs without
    giving up the predictability that fixed slots exist for.
    """

    def test_offset_is_within_the_configured_bound(self) -> None:
        for n in range(500):
            assert timedelta() <= poll_jitter(f"entry-{n}") < MAX_POLL_JITTER

    def test_same_seed_always_gives_the_same_offset(self) -> None:
        assert poll_jitter("01M1XERWVWMQKHFAT35E7VWCJY") == poll_jitter(
            "01M1XERWVWMQKHFAT35E7VWCJY"
        )

    def test_offset_is_stable_across_processes(self) -> None:
        """The trap this guards: Python randomises `hash()` per process.

        Using the builtin `hash()` here would look deterministic and would in
        fact re-roll the offset on every Home Assistant restart. Recomputing in
        a subprocess with a different PYTHONHASHSEED is the only way to catch
        that, since within one process the builtin looks perfectly stable.
        """
        import os
        import subprocess
        import sys

        seeds = ["entry-a", "entry-b", "01M1XERWVWMQKHFAT35E7VWCJY"]
        expected = [poll_jitter(seed).total_seconds() for seed in seeds]

        script = (
            "import sys;"
            "sys.path.insert(0, %r);"
            "from custom_components.trappers.coordinator import poll_jitter;"
            "print([poll_jitter(s).total_seconds() for s in %r])"
            % (str(Path(__file__).parent.parent), seeds)
        )
        env = {**os.environ, "PYTHONHASHSEED": "1"}
        first = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, env=env
        )
        env["PYTHONHASHSEED"] = "12345"
        second = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, env=env
        )

        assert first.returncode == 0, first.stderr
        assert second.returncode == 0, second.stderr
        assert eval(first.stdout) == expected
        assert eval(second.stdout) == expected

    def test_different_entries_get_different_offsets(self) -> None:
        """The whole point — two installs must not land on the same second."""
        offsets = {poll_jitter(f"entry-{n}") for n in range(200)}

        # 15 minutes of whole seconds is 900 buckets; 200 draws should fill a
        # large fraction of them rather than clustering.
        assert len(offsets) > 150

    def test_offsets_spread_across_the_whole_window(self) -> None:
        offsets = [poll_jitter(f"entry-{n}").total_seconds() for n in range(500)]
        bound = MAX_POLL_JITTER.total_seconds()

        # Every quarter of the window gets a fair share — a broken derivation
        # that always returned a small value would pass the bound check above.
        for quarter in range(4):
            low, high = bound * quarter / 4, bound * (quarter + 1) / 4
            assert sum(1 for o in offsets if low <= o < high) > 60


@pytest.mark.usefixtures("amsterdam_time_zone")
class TestJitteredSlots:
    JITTER = timedelta(minutes=7)

    def test_poll_lands_after_the_slot(self) -> None:
        assert next_poll_time(local(2026, 9, 7, 10, 30), self.JITTER) == local(
            2026, 9, 7, 11, 7
        )

    def test_between_the_slot_and_the_offset_still_targets_that_slot(self) -> None:
        """At 11:03 with a 7-minute offset the next poll is 11:07, not 14:07."""
        assert next_poll_time(local(2026, 9, 7, 11, 3), self.JITTER) == local(
            2026, 9, 7, 11, 7
        )

    def test_after_the_offset_moves_to_the_following_slot(self) -> None:
        assert next_poll_time(local(2026, 9, 7, 11, 8), self.JITTER) == local(
            2026, 9, 7, 14, 7
        )

    def test_overnight_still_waits_for_the_morning(self) -> None:
        assert next_poll_time(local(2026, 9, 7, 3), self.JITTER) == local(
            2026, 9, 7, 8, 7
        )
        assert next_poll_time(local(2026, 9, 7, 20, 30), self.JITTER) == local(
            2026, 9, 8, 8, 7
        )

    def test_a_poll_never_fires_before_the_window_opens(self) -> None:
        """The offset is added after the slot, never around it.

        A symmetric spread would let the first poll of the day land at 07:52,
        which is the one thing the 08:00 boundary exists to prevent.
        """
        for minutes in range(0, int(MAX_POLL_JITTER.total_seconds() // 60) + 1):
            jitter = timedelta(minutes=minutes)
            for hour in range(0, 8):
                poll = next_poll_time(local(2026, 9, 7, hour, 30), jitter)
                assert poll >= local(2026, 9, 7, 8)

    def test_dst_handling_is_unaffected_by_the_offset(self) -> None:
        now = local(2026, 3, 28, 20, 30)

        assert next_poll_time(now, self.JITTER) == local(2026, 3, 29, 8, 7)
        # 20:30 CET -> 08:07 CEST is 10h37m of real time, not 11h37m.
        assert interval_until_next_poll(now, self.JITTER) == timedelta(
            hours=10, minutes=37
        )

    def test_interval_is_still_clamped(self) -> None:
        # Waking exactly on the offset moment.
        now = local(2026, 9, 7, 11, 7)

        assert interval_until_next_poll(now, self.JITTER) >= MIN_UPDATE_INTERVAL
