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

``POST /articles?limit&offset``
    POST, not GET (a GET answers 405); body ``{}``. Paged. The webshop
    catalogue, and the only honest source of what the balance is actually
    worth — see ``_async_get_points_per_euro`` for why, and why
    ``/articleOrders``'s ``trapperToEuroConversionRatio`` is not it. Carries
    no personal data at all, which is the other reason to prefer it.

Deliberately not used
---------------------

``GET /articleOrders`` was read in 0.1.x for its
``trapperToEuroConversionRatio`` and is no longer called at all. The ratio
turned out to be the scheme's internal cost basis rather than a spendable
rate (see below), and that response is the one carrying the ordering person's
name and an ``ibanAccountNumber`` field — so dropping it removes both a wrong
number and the only endpoint here that returned personal data nothing wanted.

Privacy
-------

The account endpoints above carry the account holder's home address,
telephone number and employer employee numbers alongside the two or three
numbers the sensors want. Nothing in this module logs a response body, a
token, a password or any identity field — debug logging is endpoint + status
code only.
"""
from __future__ import annotations

import logging
import math
import re
from collections import Counter
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
ARTICLES_URL = f"{API_BASE}/articles"

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

# Commute registrations are few — one on the probed account — but the endpoint
# is paged like the rest, so it is walked like the rest rather than trusting
# that the first page is all of them.
COMMUTE_PAGE_LIMIT = 100
MAX_COMMUTE_PAGES = 5

# The webshop catalogue, from which the points→euro rate is derived. One page
# covers it comfortably — 99 articles on the probed account — but it is paged
# properly anyway.
ARTICLES_PAGE_LIMIT = 400
MAX_ARTICLE_PAGES = 10

# A gift card's face value, as it appears in the article name: "bol. cadeaukaart
# € 25", "VVV Cadeaukaart € 100". Dutch number formatting, so "1.000,00" is a
# thousand euros, not one.
FACE_VALUE_RE = re.compile(r"€\s*(\d[\d.,\s\u00a0]*)")

# Guards on deriving a single rate from the catalogue. The probed account gave
# 59 priced articles at *exactly* one rate, so these thresholds are nowhere near
# binding — they exist so that a catalogue which stops being uniform produces an
# honest "unknown" instead of a confident average of a bimodal distribution. A
# catalogue with two rates means the single-rate model is wrong, and that is
# worth surfacing as an unknown rather than papering over.
MIN_PRICED_ARTICLES = 5
# A *clear* plurality, and strictly greater. At 0.6-inclusive a 6-vs-4 split
# still produced a confident rate, which flatly contradicted the promise that a
# catalogue without one consistent rate reads `unknown`. The probed catalogue is
# 100% uniform, so a real one is nowhere near this bound; anything that is means
# the single-rate model has stopped holding and should say so.
MIN_MODE_SHARE = 0.8

# How long a derived rate is reused. It is a term of the employer's contract
# with FiscFree, so it effectively never changes; re-deriving it every poll
# would just re-fetch a hundred-article catalogue for the same answer. Unlike
# the /articleOrders response this replaced, /articles carries no personal data,
# so the cache here is about traffic, not privacy. An *unknown* rate is not
# cached, so a catalogue that becomes readable again is picked up on the next
# poll.
POINTS_PER_EURO_MAX_AGE = timedelta(hours=24)


class TrappersAuthError(Exception):
    """Raised when the credentials are rejected."""


class TrappersApiError(Exception):
    """Raised when the API returns an unexpected or unusable response."""


def _month_start(today: date) -> date:
    """First day of `today`'s calendar month."""
    return today.replace(day=1)


def _is_number(value: Any) -> bool:
    """True for a real, finite number.

    `isinstance(x, (int, float))` alone is not enough. `True` is an `int` in
    Python, so a boolean would sail through and then be arithmetic'd; and JSON
    can carry `Infinity`/`NaN`, which reach a sensor state as "inf"/"nan" or
    raise `OverflowError` on conversion rather than being caught here.
    """
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _parse_date(raw: Any) -> date | None:
    """Parse a "YYYY-MM-DD" field, or None if it is absent or unusable.

    Never raises and never repeats the offending value: a malformed date is a
    field this client cannot use, and the value itself may be anything the
    server put there — which must not reach the log. See the module docstring.
    """
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


class TrappersApiClient:
    """Talks to the Trappers API for one account."""

    def __init__(
        self, session: aiohttp.ClientSession, email: str, password: str
    ) -> None:
        self._session = session
        self._email = email
        self._password = password
        self._token: str | None = None
        self._points_per_euro: float | None = None
        self._points_per_euro_fetched: datetime | None = None

    # ── authentication ────────────────────────────────────────────────────

    async def async_login(self) -> str | None:
        """Authenticate, store a bearer token, and return the account's first name.

        The token is valid for four hours. Rather than tracking its expiry,
        this client re-logs-in when a request comes back 401 (see
        ``_async_request``) — one fewer moving part than a scheduled refresh,
        The token lives four hours and the slots are three apart, so it does
        *not* reliably expire between polls — roughly three of the five daily
        polls pay a 401 plus a login, the rest reuse the token. Either way it is
        a handful of extra round-trips a day in exchange for no expiry
        bookkeeping at all.

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
        """Page through a list endpoint until every item has been collected.

        Two things here are deliberate, and both were bugs first:

        The offset advances by **how many items have actually been collected**,
        not by `page * page_limit`. A server that caps its page size below the
        requested limit would otherwise make this skip every record between the
        short page's end and the next requested offset — silently, since the
        response still looks well-formed.

        An **empty page while `total` says there is more is an error**, not a
        stopping condition. Treating it as "done" meant a server answering
        `{"items": [], "total": 100}` produced a confident `cycling_days_total`
        of zero: a plausible number, wrong, and indistinguishable on a
        dashboard from a genuinely new account.
        """
        collected: list[Any] = []
        for _page in range(max_pages):
            payload = await self._async_request(
                method, url, params={"limit": page_limit, "offset": len(collected)}
            )
            items, total = self._validate_page(payload, what)

            if len(collected) >= total:
                return collected
            if not items:
                raise TrappersApiError(
                    f"Unexpected {what} response: an empty page while {total} "
                    f"item(s) were promised and {len(collected)} collected"
                )
            collected.extend(items)
            if len(collected) >= total:
                return collected

        raise TrappersApiError(
            f"Gave up paging {what} after {max_pages} pages — the endpoint is "
            "returning more items than this client is willing to fetch"
        )

    # ── the individual figures ────────────────────────────────────────────

    async def async_get_balance(self) -> float:
        """Current points balance, from the cheap single-request poll endpoint."""
        payload = await self._async_request("GET", STATUS_URL)
        if not isinstance(payload, dict) or not _is_number(payload.get("balance")):
            raise TrappersApiError(
                "Unexpected status response: no finite numeric 'balance'"
            )
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
            parsed = _parse_date(item.get("date"))
            if parsed is None:
                # Deliberately does not repeat the offending value: it is
                # server-controlled text and this error reaches the log.
                raise TrappersApiError(
                    "Unexpected events response: item has no usable 'date'"
                )
            dates.add(parsed)

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
                stamp = None
                if isinstance(raw_date, str):
                    try:
                        # "YYYY-MM-DDTHH:MM:SS" here, not the plain date on events.
                        stamp = datetime.fromisoformat(raw_date).date()
                    except ValueError:
                        stamp = None
                if stamp is None:
                    # No value interpolation — see the note on events above.
                    raise TrappersApiError(
                        "Unexpected transactions response: item has no usable 'date'"
                    )

                if stamp < month_start:
                    # Newest first, so everything from here on is older still.
                    reached_last_month = True
                    break

                amount = item.get("amount")
                if not _is_number(amount):
                    raise TrappersApiError(
                        "Unexpected transactions response: item has no finite "
                        "numeric 'amount'"
                    )
                if amount > 0:
                    total_earned += float(amount)

            if reached_last_month or not items or collected >= total:
                return total_earned

        raise TrappersApiError(
            f"Gave up paging transactions after {MAX_TRANSACTION_PAGES} pages"
        )

    async def _async_get_commute_distance_m(self, today: date) -> int | None:
        """The commute registered for *today*, in metres, or None if there is none.

        Picking simply the newest `startDate` was wrong in both directions: a
        registration starting next month would immediately override the one
        actually in force, and a registration that ended last year would go on
        being reported forever. Both produce a confident, plausible, wrong
        distance. So the item has to be in force today — started, and not
        ended — and among those the most recently started one wins.
        """
        items = await self._async_fetch_all(
            "GET",
            COMMUTE_URL,
            "commute",
            page_limit=COMMUTE_PAGE_LIMIT,
            max_pages=MAX_COMMUTE_PAGES,
        )

        current: dict[str, Any] | None = None
        current_start: date | None = None
        unreadable = 0
        for item in items:
            if not isinstance(item, dict):
                raise TrappersApiError("Unexpected commute response: item is not an object")

            start = _parse_date(item.get("startDate"))
            if start is None:
                # Cannot establish that this registration is in force, so it
                # cannot be used. Counted, not silently dropped — see below.
                unreadable += 1
                continue
            if start > today:
                continue  # Not in force yet.
            end = _parse_date(item.get("endDate"))
            if item.get("endDate") is not None and end is None:
                unreadable += 1
                continue
            if end is not None and end < today:
                continue  # Already expired.

            if current_start is None or start > current_start:
                current, current_start = item, start

        if current is None:
            if unreadable:
                # Rows exist but none could be read. That is a schema change,
                # not an account without a commute, and it should be loud
                # rather than quietly reporting `unknown` forever.
                raise TrappersApiError(
                    f"Unexpected commute response: {unreadable} registration(s) "
                    "with no usable start/end date"
                )
            # Genuinely nothing in force today — a legitimate `unknown`.
            return None
        distance = current.get("distance")
        if distance is None:
            return None
        if not _is_number(distance):
            raise TrappersApiError(
                "Unexpected commute response: 'distance' is not a finite number"
            )
        return int(distance)

    @staticmethod
    def _parse_face_value(article_name: object) -> float | None:
        """Euro face value out of an article name, or None if it has none.

        Gift cards carry their face value in the name ("bol. cadeaukaart € 25").
        Physical goods do not — a laptop's retail value is nowhere in the
        response — so those return None and take no part in the rate.

        Refuses to guess. A name carrying **more than one** euro amount
        ("Artikel van € 100 voor € 25") is ambiguous: picking the first match
        silently invented a face value that was 4x wrong. Ambiguous means None,
        which drops the article rather than poisoning the rate.
        """
        if not isinstance(article_name, str):
            return None

        matches = FACE_VALUE_RE.findall(article_name)
        if len(matches) != 1:
            return None

        raw = matches[0].strip(".,\u00a0 ")
        if "," in raw:
            # Dutch decimal comma: "1.000,00" is a thousand euros.
            raw = raw.replace(".", "").replace(" ", "").replace("\u00a0", "")
            raw = raw.replace(",", ".")
        else:
            groups = re.split(r"[.\s\u00a0]", raw)
            if len(groups) > 1 and all(len(g) == 3 for g in groups[1:]):
                # "1.000" / "1 000" with no comma is a thousands separator.
                raw = "".join(groups)
            elif len(groups) > 1:
                # Neither a clean decimal nor a clean thousands grouping.
                return None

        try:
            value = float(raw)
        except ValueError:
            return None
        return value if value > 0 and math.isfinite(value) else None

    @classmethod
    def _derive_points_per_euro(
        cls, items: list[Any], today: date | None = None
    ) -> float | None:
        """Points charged per euro of face value, as the mode across the catalogue.

        **The mode, not the mean.** A single mispriced or oddly-named article
        shifts a mean and cannot shift a mode. And if the catalogue genuinely
        stops being uniform, a mean would quietly report a number that buys
        nothing, whereas a mode that fails its plurality check returns None and
        the sensor says `unknown` — which is the truth at that point.

        Returns None rather than raising: a catalogue this client cannot read a
        rate from is a missing value, not a broken integration.
        """
        today = today or date.today()
        rates: list[float] = []
        for item in items:
            if not isinstance(item, dict):
                raise TrappersApiError("Unexpected articles response: item is not an object")
            if item.get("archived") or not item.get("availableInPublicShop"):
                continue

            # An article you cannot buy today tells you nothing about today's
            # rate. `availableUntil` was null on every article of the probed
            # catalogue, so this path is untested against real expiry data —
            # which is the reason to honour the field rather than assume it
            # stays null.
            available_from = _parse_date(item.get("availableFrom"))
            if available_from is not None and available_from > today:
                continue
            available_until = _parse_date(item.get("availableUntil"))
            if available_until is not None and available_until < today:
                continue

            # A handling fee makes the real cost higher than `trappersPrice`,
            # so such an article would understate the rate. Every article on the
            # probed catalogue had `handlingFeeApplies: false`; rather than
            # model a fee this client has never seen applied, drop the article.
            if item.get("handlingFeeApplies"):
                continue

            face_value = cls._parse_face_value(item.get("articleName"))
            if face_value is None:
                continue
            price = item.get("trappersPrice")
            if not _is_number(price) or price <= 0:
                continue
            # Rounded before counting so float noise can't split the mode.
            rates.append(round(price / face_value, 2))

        if len(rates) < MIN_PRICED_ARTICLES:
            return None
        rate, count = Counter(rates).most_common(1)[0]
        if count / len(rates) < MIN_MODE_SHARE:
            _LOGGER.debug(
                "Catalogue rate is not uniform (%s of %s articles at the mode) — "
                "reporting the balance's euro value as unknown",
                count,
                len(rates),
            )
            return None
        return rate

    async def _async_get_points_per_euro(self, now: datetime) -> float | None:
        """How many points one euro of spendable value costs, or None if unknown.

        Derived from the webshop catalogue, **not** from
        ``/articleOrders``'s ``trapperToEuroConversionRatio``. That field is
        real, but it is the scheme's internal cost basis, not a rate anything
        can be bought at: across all 99 catalogue articles on the probed
        account, ``purchasePrice / trappersPrice`` was exactly 0.01 for every
        single one — the ratio simply reproduces ``purchasePrice`` by
        construction. Valuing the balance with it overstates it by the
        operator's margin: a 10 000-point balance came out as €100.00, while
        the same points buy €95.24 of gift cards in the actual shop.

        The rate that can be spent is ``trappersPrice`` per euro of **face**
        value: a "bol. cadeaukaart € 25" costs 2625 points, so 105 points per
        euro. That was uniform across all 59 priced articles on the probed
        account.

        Gift cards are also the *best* rate available. Physical goods run
        around 131 points/€ against their supplier price, so reporting the
        gift-card rate is reporting the best achievable value of the balance —
        which is the right thing for the sensor to claim. Averaging the goods
        in would understate what the balance is worth.

        105 is this employer's contract term, exactly as 0.01 was, and is never
        hardcoded.
        """
        if (
            self._points_per_euro is not None
            and self._points_per_euro_fetched is not None
            and now - self._points_per_euro_fetched < POINTS_PER_EURO_MAX_AGE
        ):
            return self._points_per_euro

        items = await self._async_fetch_all(
            "POST",
            ARTICLES_URL,
            "articles",
            page_limit=ARTICLES_PAGE_LIMIT,
            max_pages=MAX_ARTICLE_PAGES,
        )
        rate = self._derive_points_per_euro(items, now.date())
        if rate is None:
            # Not cached: an unreadable catalogue should resolve on the next
            # poll rather than stay unknown for a day.
            return None
        self._points_per_euro = rate
        self._points_per_euro_fetched = now
        return rate

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
            distance_m = await self._async_get_commute_distance_m(today)
            data["commute_distance"] = (
                round(distance_m / 1000, 2) if distance_m is not None else None
            )
            points_per_euro = await self._async_get_points_per_euro(now)
            data["balance_value_eur"] = (
                round(data["balance"] / points_per_euro, 2)
                if points_per_euro
                else None
            )
        except TrappersApiError:
            raise
        except (KeyError, TypeError, IndexError, ValueError, AttributeError) as err:
            # Belt-and-braces: the checks above are meant to catch every shape
            # problem explicitly, but a renamed field must never reach the log
            # as a raw traceback.
            # The exception's text can quote a server-supplied value, so only
            # its type is reported. The specific checks above are where a useful
            # message comes from; this is the net beneath them.
            raise TrappersApiError(
                f"Unexpected response shape from Trappers API ({type(err).__name__})"
            ) from err

        return data
