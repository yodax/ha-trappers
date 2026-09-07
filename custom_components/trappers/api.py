"""Client for the (unofficial) api.trappers.net JSON API.

Trappers is a Dutch employer bike-to-work scheme (run by FiscFree): a roadside
or office collector unit registers a bike tag on every commute and credits the
employee's account with points ("trappers"), spendable in the scheme's webshop.
The shop front-end is a Vue SPA that talks to this API and nothing else, so
there is no HTML to scrape and no reason to run a browser at runtime — see
``CLAUDE.md`` for how the endpoints below were captured and re-confirmed.

Everything is plain JSON over HTTPS with a bearer token: no cookies, no CSRF
token, no session state.

Endpoints used here
-------------------

``POST /auth/v2/login``
    ``{"employeeEmail", "password"}`` → ``{"token", "userDetails"}``. Note the
    failure code: rejected credentials answer **400**, not 401, with body
    ``{"employeeEmail": "Invalid credentials."}`` — identical for a wrong
    password and an unknown address (no user enumeration). Anything keyed off
    401 alone would misread a bad password as a transport error and strand the
    config entry instead of prompting for reauth.

``GET /status``
    → ``{"employee": {...}, "balance": <float>, "assignEndDate": ...}``. The
    headline number, fresh, without re-authenticating.

``POST /events?limit&offset``
    POST, not GET (a GET answers 405); body ``{}``. Paged
    ``{limit, total, offset, items}``, newest first. One item per *tag read*,
    with a ``status`` — see ``EVENT_STATUS_DUPLICATE`` below.

``GET /transactions?limit&offset``
    Paged, newest first. ``type`` is ``INCOME_DISTANCE`` for points credited
    for a cycling day and ``EXPENSE_ORDER`` (negative ``amount``) for points
    spent in the webshop. Treat the vocabulary as open: this client branches on
    the sign of ``amount``, never on an exhaustive list of type names.

``GET /commute``
    Paged. ``distance`` is the **one-way** commute in metres.

``GET /articleOrders?limit&offset``
    Paged. The only place ``trapperToEuroConversionRatio`` appears — but the
    same response also carries the ordering person's name in ``mutationLogs``
    and an ``ibanAccountNumber`` field. Only the ratio and the order date are
    ever read out of it, and it is fetched at most once a day (see
    ``ORDER_RATIO_MAX_AGE``) rather than on every poll cycle.

Privacy
-------

Every one of these responses carries the account holder's home address,
telephone number and employer employee numbers alongside the two or three
numbers the sensors want. Nothing in this module logs a response body, a
token, a password or any identity field — debug logging is endpoint + status
code only.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

API_BASE = "https://api.trappers.net/api"
LOGIN_URL = f"{API_BASE}/auth/v2/login"
STATUS_URL = f"{API_BASE}/status"
EVENTS_URL = f"{API_BASE}/events"
TRANSACTIONS_URL = f"{API_BASE}/transactions"
COMMUTE_URL = f"{API_BASE}/commute"
ARTICLE_ORDERS_URL = f"{API_BASE}/articleOrders"

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)

# A collector unit sometimes reads the same tag twice on one day. The second
# read is stored as a full event row with this status and earns no points —
# confirmed live: an account with 155 event rows had 24 of them duplicates,
# 131 non-duplicates, 131 distinct event dates and exactly 131 INCOME_DISTANCE
# transactions. So `total` from the events endpoint is a count of *tag reads*,
# not of cycling days, and duplicates have to be filtered out.
EVENT_STATUS_DUPLICATE = "PROCESSED_DUPLICATE"

# The events endpoint honours a large `limit` (echoed back verbatim), so a
# whole account's history is normally one request — a few years of commuting is
# well under a thousand rows. The page cap is a safety rail against a paging
# bug turning into an unbounded request loop, not an expected code path.
EVENTS_PAGE_LIMIT = 500
MAX_EVENT_PAGES = 20

# Transactions are only needed back to the start of the current month, and are
# returned newest first, so paging stops as soon as a page reaches into last
# month. One page covers roughly four months of daily commuting.
TRANSACTIONS_PAGE_LIMIT = 100
MAX_TRANSACTION_PAGES = 12

# How long a known points→euro conversion ratio is reused before re-reading it.
# It is a contract term between the employer and FiscFree, so it effectively
# never changes; the reason to cache it is that its response is the one that
# carries names and an IBAN. An *unknown* ratio (an account with no orders yet)
# is deliberately not cached — that response is `{"total": 0, "items": []}`,
# which carries nothing at all, so retrying it every poll costs no privacy and
# picks up the account's first order promptly.
ORDER_RATIO_MAX_AGE = timedelta(hours=24)


class TrappersAuthError(Exception):
    """Raised when the credentials are rejected."""


class TrappersApiError(Exception):
    """Raised when the API returns an unexpected or unusable response."""


def _month_start(today: date) -> date:
    """First day of `today`'s calendar month."""
    return today.replace(day=1)


class TrappersApiClient:
    """Talks to the Trappers API for one account."""

    def __init__(
        self, session: aiohttp.ClientSession, email: str, password: str
    ) -> None:
        self._session = session
        self._email = email
        self._password = password
        self._token: str | None = None
        self._euro_ratio: float | None = None
        self._euro_ratio_fetched: datetime | None = None

    # ── authentication ────────────────────────────────────────────────────

    async def async_login(self) -> str | None:
        """Authenticate, store a bearer token, and return the account's first name.

        The token is valid for four hours. Rather than tracking its expiry,
        this client re-logs-in when a request comes back 401 (see
        ``_async_request``) — one fewer moving part than a scheduled refresh,
        and at a 30-minute poll interval it costs one extra round-trip roughly
        every eighth poll.

        The login response's ``userDetails`` object carries the whole account
        holder — name, address, telephone number, employer employee numbers.
        Exactly one field is returned from it: ``firstName``, which the config
        flow uses to title the entry. Returning the object wholesale would put
        the rest of it in reach of every caller for no reason, so it does not
        leave this method. ``None`` when the account has no first name set.
        """
        async with self._session.post(
            LOGIN_URL,
            json={"employeeEmail": self._email, "password": self._password},
            timeout=REQUEST_TIMEOUT,
        ) as resp:
            _LOGGER.debug("POST /auth/v2/login -> %s", resp.status)
            if resp.status == 400:
                # The documented rejected-credentials response. Deliberately a
                # 400 and not a 401, so this check cannot be folded into the
                # generic 401 handling below.
                raise TrappersAuthError("Invalid credentials")
            resp.raise_for_status()
            data = await resp.json()

        if not isinstance(data, dict):
            raise TrappersApiError("Login response was not an object")
        token = data.get("token")
        if not token:
            raise TrappersApiError("Login response carried no token")
        self._token = token

        details = data.get("userDetails")
        first_name = details.get("firstName") if isinstance(details, dict) else None
        return first_name if isinstance(first_name, str) else None

    async def _async_request(
        self, method: str, url: str, *, params: dict[str, int] | None = None
    ) -> Any:
        """Make one authenticated request, re-authenticating once on a 401."""
        if self._token is None:
            await self.async_login()

        payload = await self._async_raw_request(method, url, params=params)
        if payload is not None:
            return payload

        # Token expired or revoked — log in again and retry exactly once.
        await self.async_login()
        payload = await self._async_raw_request(method, url, params=params)
        if payload is None:
            # Login just succeeded, so a second 401 is not a credentials
            # problem; routing it through HA's reauth flow would be a dead end.
            raise TrappersApiError(
                f"Still unauthorized after re-authenticating: {method} {url}"
            )
        return payload

    async def _async_raw_request(
        self, method: str, url: str, *, params: dict[str, int] | None = None
    ) -> Any:
        """Make one authenticated request. Returns None on a 401."""
        # The events endpoint is a POST that wants a body; the read endpoints
        # are GETs. Sending `{}` on a GET would be unusual, so it is omitted.
        json_body: dict[str, Any] | None = {} if method == "POST" else None
        async with self._session.request(
            method,
            url,
            headers={"Authorization": f"Bearer {self._token}"},
            params=params,
            json=json_body,
            timeout=REQUEST_TIMEOUT,
        ) as resp:
            _LOGGER.debug("%s %s -> %s", method, url, resp.status)
            if resp.status == 401:
                return None
            resp.raise_for_status()
            return await resp.json()

    # ── paged list endpoints ──────────────────────────────────────────────

    @staticmethod
    def _validate_page(payload: Any, what: str) -> tuple[list[Any], int]:
        """Pull `(items, total)` out of a `{limit, total, offset, items}` body.

        Every list endpoint answers that shape. A renamed or missing key has to
        become a clean integration error here rather than a KeyError or an
        IndexError somewhere further down.
        """
        if not isinstance(payload, dict):
            raise TrappersApiError(f"Unexpected {what} response: not an object")
        items = payload.get("items")
        total = payload.get("total")
        if not isinstance(items, list):
            raise TrappersApiError(f"Unexpected {what} response: no 'items' list")
        if not isinstance(total, int):
            raise TrappersApiError(f"Unexpected {what} response: no numeric 'total'")
        return items, total

    async def _async_fetch_all(
        self, method: str, url: str, what: str, *, page_limit: int, max_pages: int
    ) -> list[Any]:
        """Page through a list endpoint until every item has been collected."""
        collected: list[Any] = []
        for page in range(max_pages):
            payload = await self._async_request(
                method, url, params={"limit": page_limit, "offset": page * page_limit}
            )
            items, total = self._validate_page(payload, what)
            collected.extend(items)
            if len(collected) >= total or not items:
                return collected
        raise TrappersApiError(
            f"Gave up paging {what} after {max_pages} pages — the endpoint is "
            "returning more items than this client is willing to fetch"
        )

    # ── the individual figures ────────────────────────────────────────────

    async def async_get_balance(self) -> float:
        """Current points balance, from the cheap single-request poll endpoint."""
        payload = await self._async_request("GET", STATUS_URL)
        if not isinstance(payload, dict) or not isinstance(
            payload.get("balance"), (int, float)
        ):
            raise TrappersApiError("Unexpected status response: no numeric 'balance'")
        return float(payload["balance"])

    async def _async_get_cycling_days(self, today: date) -> dict[str, Any]:
        """Cycling-day counts and the newest cycling day, from the events endpoint.

        Duplicate tag reads are dropped: they are extra rows on a date that
        already counted, and they earn no points, so counting raw rows would
        overstate both totals.
        """
        items = await self._async_fetch_all(
            "POST",
            EVENTS_URL,
            "events",
            page_limit=EVENTS_PAGE_LIMIT,
            max_pages=MAX_EVENT_PAGES,
        )

        dates: set[date] = set()
        for item in items:
            if not isinstance(item, dict):
                raise TrappersApiError("Unexpected events response: item is not an object")
            if item.get("status") == EVENT_STATUS_DUPLICATE:
                continue
            raw_date = item.get("date")
            if not isinstance(raw_date, str):
                raise TrappersApiError("Unexpected events response: item has no 'date'")
            try:
                dates.add(date.fromisoformat(raw_date))
            except ValueError as err:
                raise TrappersApiError(f"Unparseable event date: {err}") from err

        month_start = _month_start(today)
        return {
            "cycling_days_total": len(dates),
            "last_cycling_day": max(dates) if dates else None,
            "cycling_days_this_month": sum(1 for day in dates if day >= month_start),
        }

    async def _async_get_points_earned_this_month(self, today: date) -> float:
        """Sum of points credited so far this calendar month.

        Only positive amounts count. Spending points in the webshop produces an
        ``EXPENSE_ORDER`` row with a negative amount; including those would make
        this "net points movement", which is not what the sensor claims.
        """
        month_start = _month_start(today)
        total_earned = 0.0
        collected = 0

        for page in range(MAX_TRANSACTION_PAGES):
            payload = await self._async_request(
                "GET",
                TRANSACTIONS_URL,
                params={
                    "limit": TRANSACTIONS_PAGE_LIMIT,
                    "offset": page * TRANSACTIONS_PAGE_LIMIT,
                },
            )
            items, total = self._validate_page(payload, "transactions")
            collected += len(items)

            reached_last_month = False
            for item in items:
                if not isinstance(item, dict):
                    raise TrappersApiError(
                        "Unexpected transactions response: item is not an object"
                    )
                raw_date = item.get("date")
                if not isinstance(raw_date, str):
                    raise TrappersApiError(
                        "Unexpected transactions response: item has no 'date'"
                    )
                try:
                    # "YYYY-MM-DDTHH:MM:SS" here, unlike the plain dates on events.
                    stamp = datetime.fromisoformat(raw_date).date()
                except ValueError as err:
                    raise TrappersApiError(
                        f"Unparseable transaction date: {err}"
                    ) from err

                if stamp < month_start:
                    # Newest first, so everything from here on is older still.
                    reached_last_month = True
                    break

                amount = item.get("amount")
                if not isinstance(amount, (int, float)):
                    raise TrappersApiError(
                        "Unexpected transactions response: item has no numeric 'amount'"
                    )
                if amount > 0:
                    total_earned += float(amount)

            if reached_last_month or not items or collected >= total:
                return total_earned

        raise TrappersApiError(
            f"Gave up paging transactions after {MAX_TRANSACTION_PAGES} pages"
        )

    async def _async_get_commute_distance_m(self) -> int | None:
        """One-way commute distance in metres, or None if none is registered."""
        payload = await self._async_request("GET", COMMUTE_URL)
        items, _total = self._validate_page(payload, "commute")

        newest: dict[str, Any] | None = None
        newest_start: date | None = None
        for item in items:
            if not isinstance(item, dict):
                raise TrappersApiError("Unexpected commute response: item is not an object")
            raw_start = item.get("startDate")
            try:
                start = date.fromisoformat(raw_start) if isinstance(raw_start, str) else None
            except ValueError as err:
                raise TrappersApiError(f"Unparseable commute start date: {err}") from err
            if newest_start is None or (start is not None and start > newest_start):
                newest, newest_start = item, start

        if newest is None:
            return None
        distance = newest.get("distance")
        if distance is None:
            return None
        if not isinstance(distance, (int, float)):
            raise TrappersApiError(
                "Unexpected commute response: 'distance' is not numeric"
            )
        return int(distance)

    async def _async_get_euro_ratio(self, now: datetime) -> float | None:
        """Points→euro conversion ratio from the newest order, or None if unknown.

        Genuinely unknown for an account that has never ordered anything: the
        ratio only exists on order records. It is *not* defaulted to a constant
        — the 0.01 seen on one probed account is that employer's contract term,
        not a property of the scheme.

        Only the ratio is read out of this response. The rest of it carries the
        ordering person's name and an IBAN, which is also why a known ratio is
        reused for a day instead of re-fetched every poll.
        """
        if (
            self._euro_ratio is not None
            and self._euro_ratio_fetched is not None
            and now - self._euro_ratio_fetched < ORDER_RATIO_MAX_AGE
        ):
            return self._euro_ratio

        payload = await self._async_request(
            "GET", ARTICLE_ORDERS_URL, params={"limit": 1, "offset": 0}
        )
        items, _total = self._validate_page(payload, "articleOrders")
        if not items:
            # No orders yet — the ratio is unknown, not zero.
            return None
        newest = items[0]
        if not isinstance(newest, dict):
            raise TrappersApiError(
                "Unexpected articleOrders response: item is not an object"
            )
        ratio = newest.get("trapperToEuroConversionRatio")
        if ratio is None:
            return None
        if not isinstance(ratio, (int, float)):
            raise TrappersApiError(
                "Unexpected articleOrders response: conversion ratio is not numeric"
            )
        self._euro_ratio = float(ratio)
        self._euro_ratio_fetched = now
        return self._euro_ratio

    # ── the one call the coordinator makes ────────────────────────────────

    async def async_get_data(
        self, *, today: date | None = None, now: datetime | None = None
    ) -> dict[str, Any]:
        """Fetch everything the sensor platform exposes, in one poll cycle.

        `today`/`now` are injected so the caller can supply Home Assistant's
        configured timezone (the month boundary that `cycling_days_this_month`
        and `points_earned_this_month` hinge on should follow the user's clock,
        not the container's).
        """
        today = today or date.today()
        now = now or datetime.now()

        try:
            data: dict[str, Any] = {"balance": await self.async_get_balance()}
            data.update(await self._async_get_cycling_days(today))
            data["points_earned_this_month"] = (
                await self._async_get_points_earned_this_month(today)
            )
            distance_m = await self._async_get_commute_distance_m()
            data["commute_distance"] = (
                round(distance_m / 1000, 2) if distance_m is not None else None
            )
            ratio = await self._async_get_euro_ratio(now)
            data["balance_value_eur"] = (
                round(data["balance"] * ratio, 2) if ratio is not None else None
            )
        except TrappersApiError:
            raise
        except (KeyError, TypeError, IndexError, ValueError, AttributeError) as err:
            # Belt-and-braces: the checks above are meant to catch every shape
            # problem explicitly, but a renamed field must never reach the log
            # as a raw traceback.
            raise TrappersApiError(f"Unexpected response shape from Trappers API: {err}") from err

        return data
