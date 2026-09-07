"""Unit tests for TrappersApiClient — no Home Assistant needed here.

Requests are faked with a small local `FakeSession`/`FakeResponse` pair rather
than a third-party HTTP-mocking library. aioresponses (as of 0.7.9) doesn't
support the aiohttp version Home Assistant pins — `ClientResponse.__init__()`
there gained a required `stream_writer` kwarg aioresponses doesn't pass, so
every test using it fails with a TypeError unrelated to the code under test.
The class under test only calls `session.request(...)`/`session.post(...)` as
an async context manager plus `.status`/`.json()`/`.raise_for_status()` on the
result, which is more robust to fake directly than to fight that coupling.

Every value in the fixtures below is a dummy: the real API's responses carry a
live person's home address, telephone number and employer employee numbers, and
none of that belongs in a public repo. See CLAUDE.md's Privacy section.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import aiohttp
import pytest

from custom_components.trappers.api import (
    ARTICLE_ORDERS_URL,
    COMMUTE_URL,
    EVENTS_URL,
    LOGIN_URL,
    STATUS_URL,
    TRANSACTIONS_URL,
    TrappersApiClient,
    TrappersApiError,
    TrappersAuthError,
)

EMAIL = "user@example.com"
PASSWORD = "hunter2"
TODAY = date(2026, 9, 7)
NOW = datetime(2026, 9, 7, 12, 0, 0)


class FakeResponse:
    """Stands in for an aiohttp `ClientResponse` used as an async context manager."""

    def __init__(self, *, status: int = 200, json_data: object = None) -> None:
        self.status = status
        self._json_data = {} if json_data is None else json_data

    async def json(self) -> object:
        return self._json_data

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                request_info=None, history=(), status=self.status
            )

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class FakeSession:
    """Answers by URL from a canned map; records every call in order.

    Each entry is a list of responses replayed in order for repeat calls to
    that URL (so paging and the 401-then-retry path can both be expressed).
    """

    def __init__(self, responses: dict[str, list[FakeResponse]]) -> None:
        self._responses = {url: list(items) for url, items in responses.items()}
        self.calls: list[tuple[str, str, dict]] = []

    def _take(self, method: str, url: str, kwargs: dict) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        queue = self._responses.get(url)
        if not queue:
            raise AssertionError(f"unexpected request: {method} {url}")
        return queue[0] if len(queue) == 1 else queue.pop(0)

    def request(self, method: str, url: str, **kwargs: object) -> FakeResponse:
        return self._take(method, url, kwargs)

    def post(self, url: str, **kwargs: object) -> FakeResponse:
        return self._take("POST", url, kwargs)


def login_ok(token: str = "jwt-1", first_name: object = "Alex") -> FakeResponse:
    # userDetails is deliberately trimmed to the two fields this client touches;
    # the live response also carries surname, address, phone and employee numbers.
    return FakeResponse(
        json_data={
            "token": token,
            "userDetails": {"balance": 7574.0, "firstName": first_name},
        }
    )


def page(items: list, total: int | None = None, limit: int = 500) -> FakeResponse:
    return FakeResponse(
        json_data={
            "limit": limit,
            "total": len(items) if total is None else total,
            "offset": 0,
            "items": items,
        }
    )


def event(day: str, status: str = "PROCESSED") -> dict:
    return {
        "id": 1,
        "date": day,
        "tag": 1,
        "collectorUnit": 1,
        "employee": 1,
        "status": status,
    }


def transaction(stamp: str, amount: float, tx_type: str = "INCOME_DISTANCE") -> dict:
    return {
        "id": 1,
        "employee": 1,
        "date": stamp,
        "amount": amount,
        "type": tx_type,
        "description": " a day",
        "articleOrder": None,
        "skipDate": None,
    }


DEFAULT_EVENTS = [
    event("2026-09-01"),
    event("2026-08-31"),
    event("2026-08-26"),
    event("2026-08-26", status="PROCESSED_DUPLICATE"),
    event("2026-08-25"),
]

DEFAULT_TRANSACTIONS = [
    transaction("2026-09-01T15:44:08", 154.0),
    transaction("2026-08-31T05:14:18", 154.0),
    transaction("2026-08-26T07:14:28", 154.0),
]

DEFAULT_COMMUTE = [
    {
        "id": 1,
        "employee": 1,
        "location": 1,
        "locationAddress": {},
        "startDate": "2025-03-04",
        "endDate": None,
        "distance": 11000,
    }
]

DEFAULT_ORDER = {
    "id": 1,
    "orderNumber": "X-1",
    "orderDate": "2026-03-26T10:04:39.451546",
    "articleOrderStatus": "EXPORTED",
    "trapperToEuroConversionRatio": 0.01,
}


def responses(**overrides: list[FakeResponse]) -> dict[str, list[FakeResponse]]:
    """A full happy-path response map, with per-endpoint overrides."""
    base = {
        LOGIN_URL: [login_ok()],
        STATUS_URL: [FakeResponse(json_data={"balance": 7574.0, "employee": {}})],
        EVENTS_URL: [page(DEFAULT_EVENTS)],
        TRANSACTIONS_URL: [page(DEFAULT_TRANSACTIONS, limit=100)],
        COMMUTE_URL: [page(DEFAULT_COMMUTE, limit=100)],
        ARTICLE_ORDERS_URL: [page([DEFAULT_ORDER], total=6, limit=1)],
    }
    base.update(overrides)
    return base


def make_client(
    response_map: dict[str, list[FakeResponse]] | None = None,
) -> tuple[TrappersApiClient, FakeSession]:
    session = FakeSession(response_map if response_map is not None else responses())
    return TrappersApiClient(session, EMAIL, PASSWORD), session


class TestAsyncLogin:
    async def test_happy_path_stores_token_and_returns_the_first_name(self) -> None:
        client, session = make_client()

        first_name = await client.async_login()

        assert client._token == "jwt-1"
        assert first_name == "Alex"
        assert session.calls[0][1] == LOGIN_URL

    @pytest.mark.parametrize(
        "first_name",
        [None, 12345, {"nested": "thing"}],
        ids=["null", "not-a-string", "an-object"],
    )
    async def test_missing_or_odd_first_name_is_just_none(self, first_name: object) -> None:
        """The title falls back elsewhere; a weird firstName must not break login."""
        client, _session = make_client(
            responses(**{LOGIN_URL: [login_ok(first_name=first_name)]})
        )

        assert await client.async_login() is None

    async def test_login_response_that_is_not_an_object_raises_api_error(self) -> None:
        client, _session = make_client(
            responses(**{LOGIN_URL: [FakeResponse(json_data=["surprise"])]})
        )

        with pytest.raises(TrappersApiError, match="not an object"):
            await client.async_login()

    async def test_rejected_credentials_are_a_400_not_a_401(self) -> None:
        """The login endpoint answers 400 for bad credentials — not 401.

        Keying auth handling off 401 alone would classify a wrong password as a
        transport error and leave the entry stranded instead of prompting for
        reauth.
        """
        client, _session = make_client(
            responses(
                **{
                    LOGIN_URL: [
                        FakeResponse(
                            status=400,
                            json_data={"employeeEmail": "Invalid credentials."},
                        )
                    ]
                }
            )
        )

        with pytest.raises(TrappersAuthError):
            await client.async_login()

    async def test_server_error_is_not_an_auth_error(self) -> None:
        client, _session = make_client(
            responses(**{LOGIN_URL: [FakeResponse(status=503)]})
        )

        with pytest.raises(aiohttp.ClientResponseError):
            await client.async_login()

    async def test_login_without_token_raises_api_error(self) -> None:
        client, _session = make_client(
            responses(**{LOGIN_URL: [FakeResponse(json_data={"userDetails": {}})]})
        )

        with pytest.raises(TrappersApiError, match="no token"):
            await client.async_login()

    async def test_password_is_never_placed_in_a_url(self) -> None:
        client, session = make_client()

        await client.async_get_data(today=TODAY, now=NOW)

        for _method, url, _kwargs in session.calls:
            assert PASSWORD not in url


class TestAsyncGetData:
    async def test_happy_path(self) -> None:
        client, _session = make_client()

        data = await client.async_get_data(today=TODAY, now=NOW)

        assert data == {
            "balance": 7574.0,
            # 5 event rows, one of which is a duplicate tag read → 4 days.
            "cycling_days_total": 4,
            "last_cycling_day": date(2026, 9, 1),
            "cycling_days_this_month": 1,
            "points_earned_this_month": 154.0,
            "commute_distance": 11.0,
            "balance_value_eur": 75.74,
        }

    async def test_duplicate_tag_reads_do_not_count_as_cycling_days(self) -> None:
        """A collector unit reading the same tag twice earns no second day.

        Confirmed live: an account with 155 event rows had 24 duplicates, 131
        non-duplicates, 131 distinct dates and exactly 131 income transactions.
        """
        events = [
            event("2026-09-03"),
            event("2026-09-03", status="PROCESSED_DUPLICATE"),
            event("2026-09-03", status="PROCESSED_DUPLICATE"),
            event("2026-09-02"),
        ]
        client, _session = make_client(responses(**{EVENTS_URL: [page(events)]}))

        data = await client.async_get_data(today=TODAY, now=NOW)

        assert data["cycling_days_total"] == 2
        assert data["cycling_days_this_month"] == 2

    async def test_events_are_paged_until_total_is_reached(self) -> None:
        first = [event(f"2026-08-{day:02d}") for day in range(1, 4)]
        second = [event(f"2026-07-{day:02d}") for day in range(1, 3)]
        client, session = make_client(
            responses(
                **{EVENTS_URL: [page(first, total=5), page(second, total=5)]}
            )
        )

        data = await client.async_get_data(today=TODAY, now=NOW)

        assert data["cycling_days_total"] == 5
        event_calls = [c for c in session.calls if c[1] == EVENTS_URL]
        assert len(event_calls) == 2
        assert event_calls[1][2]["params"]["offset"] == 500

    async def test_runaway_event_paging_raises_rather_than_looping(self) -> None:
        """A `total` that never gets reached must stop, not spin forever."""
        client, _session = make_client(
            responses(**{EVENTS_URL: [page([event("2026-09-01")], total=10**6)]})
        )

        with pytest.raises(TrappersApiError, match="Gave up paging events"):
            await client.async_get_data(today=TODAY, now=NOW)

    async def test_only_positive_amounts_count_as_points_earned(self) -> None:
        """Spending in the webshop is a negative EXPENSE_ORDER row."""
        txs = [
            transaction("2026-09-05T10:00:00", -500.0, tx_type="EXPENSE_ORDER"),
            transaction("2026-09-04T10:00:00", 154.0),
            transaction("2026-09-01T10:00:00", 154.0),
            transaction("2026-08-31T10:00:00", 154.0),
        ]
        client, _session = make_client(
            responses(**{TRANSACTIONS_URL: [page(txs, limit=100)]})
        )

        data = await client.async_get_data(today=TODAY, now=NOW)

        assert data["points_earned_this_month"] == 308.0

    async def test_transaction_paging_stops_at_the_month_boundary(self) -> None:
        """Newest-first ordering means one page reaching last month ends it."""
        txs = [transaction("2026-09-01T10:00:00", 154.0), transaction("2026-08-31T10:00:00", 154.0)]
        client, session = make_client(
            responses(**{TRANSACTIONS_URL: [page(txs, total=999, limit=100)]})
        )

        await client.async_get_data(today=TODAY, now=NOW)

        assert len([c for c in session.calls if c[1] == TRANSACTIONS_URL]) == 1

    async def test_transactions_page_when_the_whole_page_is_this_month(self) -> None:
        """A full page that never reaches last month has to fetch the next one."""
        first = [transaction("2026-09-01T10:00:00", 10.0) for _ in range(100)]
        second = [transaction("2026-08-30T10:00:00", 99.0)]
        client, session = make_client(
            responses(
                **{
                    TRANSACTIONS_URL: [
                        page(first, total=101, limit=100),
                        page(second, total=101, limit=100),
                    ]
                }
            )
        )

        data = await client.async_get_data(today=TODAY, now=NOW)

        assert data["points_earned_this_month"] == 1000.0
        transaction_calls = [c for c in session.calls if c[1] == TRANSACTIONS_URL]
        assert len(transaction_calls) == 2
        assert transaction_calls[1][2]["params"]["offset"] == 100

    async def test_commute_distance_is_metres_converted_to_km(self) -> None:
        client, _session = make_client()

        data = await client.async_get_data(today=TODAY, now=NOW)

        assert data["commute_distance"] == 11.0

    async def test_newest_commute_registration_wins(self) -> None:
        older = {**DEFAULT_COMMUTE[0], "startDate": "2024-01-01", "distance": 4000}
        newer = {**DEFAULT_COMMUTE[0], "startDate": "2026-01-01", "distance": 8500}
        client, _session = make_client(
            responses(**{COMMUTE_URL: [page([older, newer], limit=100)]})
        )

        data = await client.async_get_data(today=TODAY, now=NOW)

        assert data["commute_distance"] == 8.5

    async def test_no_commute_registration_is_unknown(self) -> None:
        client, _session = make_client(responses(**{COMMUTE_URL: [page([], limit=100)]}))

        data = await client.async_get_data(today=TODAY, now=NOW)

        assert data["commute_distance"] is None

    async def test_no_orders_means_euro_value_is_unknown_not_zero(self) -> None:
        """The conversion ratio only exists on order records.

        0.01 is one employer's contract term, not a property of the scheme —
        defaulting to it would invent a number the API never gave us.
        """
        client, _session = make_client(
            responses(**{ARTICLE_ORDERS_URL: [page([], limit=1)]})
        )

        data = await client.async_get_data(today=TODAY, now=NOW)

        assert data["balance_value_eur"] is None

    async def test_known_ratio_is_reused_for_a_day(self) -> None:
        """The orders response carries names and an IBAN — fetch it rarely."""
        client, session = make_client()

        await client.async_get_data(today=TODAY, now=NOW)
        await client.async_get_data(today=TODAY, now=NOW + timedelta(hours=1))

        assert len([c for c in session.calls if c[1] == ARTICLE_ORDERS_URL]) == 1

    async def test_ratio_is_refetched_after_a_day(self) -> None:
        client, session = make_client()

        await client.async_get_data(today=TODAY, now=NOW)
        await client.async_get_data(today=TODAY, now=NOW + timedelta(hours=25))

        assert len([c for c in session.calls if c[1] == ARTICLE_ORDERS_URL]) == 2

    async def test_unknown_ratio_is_retried_on_the_next_poll(self) -> None:
        """`{"total": 0, "items": []}` carries no PII, so retrying costs nothing."""
        client, session = make_client(
            responses(
                **{
                    ARTICLE_ORDERS_URL: [
                        page([], limit=1),
                        page([DEFAULT_ORDER], total=1, limit=1),
                    ]
                }
            )
        )

        first = await client.async_get_data(today=TODAY, now=NOW)
        second = await client.async_get_data(today=TODAY, now=NOW + timedelta(minutes=30))

        assert first["balance_value_eur"] is None
        assert second["balance_value_eur"] == 75.74

    async def test_no_cycling_days_at_all(self) -> None:
        client, _session = make_client(responses(**{EVENTS_URL: [page([])]}))

        data = await client.async_get_data(today=TODAY, now=NOW)

        assert data["cycling_days_total"] == 0
        assert data["cycling_days_this_month"] == 0
        assert data["last_cycling_day"] is None


class TestTokenHandling:
    async def test_expired_token_reauths_and_retries_once(self) -> None:
        client, session = make_client(
            responses(
                **{
                    LOGIN_URL: [login_ok(), login_ok("jwt-2")],
                    STATUS_URL: [
                        FakeResponse(status=401),
                        FakeResponse(json_data={"balance": 7574.0, "employee": {}}),
                    ],
                }
            )
        )

        data = await client.async_get_data(today=TODAY, now=NOW)

        assert data["balance"] == 7574.0
        assert len([c for c in session.calls if c[1] == LOGIN_URL]) == 2

    async def test_still_401_after_reauth_is_an_api_error_not_an_auth_error(self) -> None:
        """Login just succeeded, so a second 401 is not a credentials problem."""
        client, _session = make_client(
            responses(**{STATUS_URL: [FakeResponse(status=401)]})
        )

        with pytest.raises(TrappersApiError, match="Still unauthorized"):
            await client.async_get_data(today=TODAY, now=NOW)

    async def test_every_request_carries_the_bearer_token(self) -> None:
        client, session = make_client()

        await client.async_get_data(today=TODAY, now=NOW)

        for _method, url, kwargs in session.calls:
            if url == LOGIN_URL:
                continue
            assert kwargs["headers"]["Authorization"] == "Bearer jwt-1"

    async def test_every_request_carries_an_explicit_timeout(self) -> None:
        client, session = make_client()

        await client.async_get_data(today=TODAY, now=NOW)

        for _method, _url, kwargs in session.calls:
            assert isinstance(kwargs["timeout"], aiohttp.ClientTimeout)
            assert kwargs["timeout"].total == 30

    async def test_events_is_a_post_the_rest_are_gets(self) -> None:
        """A GET on /events answers 405 — the method is load-bearing."""
        client, session = make_client()

        await client.async_get_data(today=TODAY, now=NOW)

        methods = {url: method for method, url, _ in session.calls}
        assert methods[EVENTS_URL] == "POST"
        assert methods[STATUS_URL] == "GET"
        assert methods[TRANSACTIONS_URL] == "GET"
        assert methods[COMMUTE_URL] == "GET"
        assert methods[ARTICLE_ORDERS_URL] == "GET"


class TestMalformedResponses:
    """A renamed or missing field must be a clean error, not a KeyError."""

    @pytest.mark.parametrize(
        ("url", "payload"),
        [
            pytest.param(STATUS_URL, {}, id="status-no-balance"),
            pytest.param(STATUS_URL, {"balance": "lots"}, id="status-balance-not-a-number"),
            pytest.param(STATUS_URL, [], id="status-not-an-object"),
        ],
    )
    async def test_bad_status_response(self, url: str, payload: object) -> None:
        client, _session = make_client(
            responses(**{url: [FakeResponse(json_data=payload)]})
        )

        with pytest.raises(TrappersApiError):
            await client.async_get_data(today=TODAY, now=NOW)

    @pytest.mark.parametrize(
        ("url", "payload"),
        [
            pytest.param(EVENTS_URL, {"limit": 1, "offset": 0}, id="events-no-items"),
            pytest.param(
                EVENTS_URL, {"items": [], "offset": 0}, id="events-no-total"
            ),
            pytest.param(
                EVENTS_URL,
                {"items": [{"id": 1}], "total": 1, "offset": 0},
                id="events-item-without-date",
            ),
            pytest.param(
                EVENTS_URL,
                {"items": [{"date": "the-fourth"}], "total": 1, "offset": 0},
                id="events-unparseable-date",
            ),
            pytest.param(
                EVENTS_URL,
                {"items": ["nope"], "total": 1, "offset": 0},
                id="events-item-not-an-object",
            ),
            pytest.param(
                TRANSACTIONS_URL,
                {"items": [{"amount": 1.0}], "total": 1, "offset": 0},
                id="transactions-item-without-date",
            ),
            pytest.param(
                TRANSACTIONS_URL,
                {
                    "items": [{"date": "2026-09-01T00:00:00", "amount": "many"}],
                    "total": 1,
                    "offset": 0,
                },
                id="transactions-amount-not-a-number",
            ),
            pytest.param(
                TRANSACTIONS_URL,
                {"items": [{"date": "yesterday", "amount": 1.0}], "total": 1, "offset": 0},
                id="transactions-unparseable-date",
            ),
            pytest.param(
                COMMUTE_URL,
                {"items": [{"startDate": "2025-03-04", "distance": "far"}], "total": 1, "offset": 0},
                id="commute-distance-not-a-number",
            ),
            pytest.param(
                COMMUTE_URL,
                {"items": [{"startDate": "someday", "distance": 1}], "total": 1, "offset": 0},
                id="commute-unparseable-date",
            ),
            pytest.param(
                ARTICLE_ORDERS_URL,
                {
                    "items": [{"trapperToEuroConversionRatio": "one percent"}],
                    "total": 1,
                    "offset": 0,
                },
                id="orders-ratio-not-a-number",
            ),
            pytest.param(
                ARTICLE_ORDERS_URL,
                {"items": ["nope"], "total": 1, "offset": 0},
                id="orders-item-not-an-object",
            ),
        ],
    )
    async def test_bad_list_response(self, url: str, payload: object) -> None:
        client, _session = make_client(
            responses(**{url: [FakeResponse(json_data=payload)]})
        )

        with pytest.raises(TrappersApiError):
            await client.async_get_data(today=TODAY, now=NOW)

    async def test_missing_commute_distance_is_unknown_not_an_error(self) -> None:
        """An item that simply has no distance yet is a legitimate 'unknown'."""
        client, _session = make_client(
            responses(
                **{
                    COMMUTE_URL: [
                        FakeResponse(
                            json_data={
                                "items": [{"startDate": "2025-03-04", "distance": None}],
                                "total": 1,
                                "offset": 0,
                            }
                        )
                    ]
                }
            )
        )

        data = await client.async_get_data(today=TODAY, now=NOW)

        assert data["commute_distance"] is None

    async def test_order_without_a_ratio_is_unknown_not_an_error(self) -> None:
        client, _session = make_client(
            responses(
                **{
                    ARTICLE_ORDERS_URL: [
                        FakeResponse(
                            json_data={
                                "items": [{"orderNumber": "X-1"}],
                                "total": 1,
                                "offset": 0,
                            }
                        )
                    ]
                }
            )
        )

        data = await client.async_get_data(today=TODAY, now=NOW)

        assert data["balance_value_eur"] is None
