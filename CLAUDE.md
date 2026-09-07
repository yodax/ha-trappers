# Trappers (FiscFree) Home Assistant integration

Custom HA integration reporting a
[Trappers](https://fiscfree.nl/onze-oplossingen/fietsstimulering/) account's
points balance and cycling activity. Trappers is a Dutch employer
bike-to-work scheme run by FiscFree: every day you commute by bike, a
roadside/office collector unit registers your bike tag and credits your
account with points ("trappers"), which you spend in their webshop
(`trappersshop.fiscfree.nl`). Distributed via HACS.

## Privacy: this is a public repository

**Never commit anything account-specific.** The API's own responses carry a
real person's home address, telephone number, employer employee number and a
corporate email domain — see "The real API" below, where every example value
is a placeholder for exactly that reason.

- The development/test account's credentials live **outside this repo**, at
  `~/.config/trappers-test/account.env` on the maintainer's machine. Nothing
  in the tree references them.
- `.githooks/pre-commit` is a preventive leak gate: it blocks a commit whose
  added lines contain private-network/infrastructure vocabulary, a real JWT or
  an IBAN, plus any identity-specific pattern loaded from
  `~/.config/trappers-test/leak-patterns.txt` (override with
  `TRAPPERS_LEAK_PATTERNS`). Enable it with
  `git config core.hooksPath .githooks`; verify with
  `git config core.hooksPath`. GitHub keeps deleted-file content reachable by
  commit SHA even after a force-push, so "notice it later and remove it" is not
  a recovery path — hence a gate rather than a note.

  **The identity patterns live outside the repo on purpose.** An earlier draft
  listed the real email address, postal code, phone number and employee numbers
  inline in the hook, and was correctly blocked by itself on the very first
  commit: a public repo cannot carry the list of strings it is guarding without
  becoming the leak. A clone without that file still gets the generic half and
  is told so explicitly — a missing pattern file must never look like full
  coverage.

  Generic patterns skip files under `.githooks/` — that directory has to
  contain a sample LAN address in order to assert LAN addresses are blocked —
  but the **identity** patterns still apply there, because `.githooks/` is
  precisely where the mistake above would land. The asymmetry is asserted, not
  assumed.

  `.githooks/test-pre-commit.sh` proves the gate works (22 cases: each generic
  pattern blocks, benign code still commits, the private-pattern *mechanism* is
  exercised with a synthetic canary rather than a real secret, a corrupt pattern
  file aborts rather than checking with fewer patterns, and a missing one warns).
  Run it after touching the hook. It exists because the hook's first version
  used a `grep` pipeline that errors under ugrep (this machine's `grep`), had
  the error swallowed by `|| true`, and exited 0 on a diff containing a live
  corporate email address — installed, plausible-looking, and guarding nothing.
- Captured API responses are gitignored (`*-response.json`, `capture/`).
  Sanitize before pasting one into a test fixture: replace the name, email,
  address, phone, `employeeNumber`, `employeeNumberEmployer` and any
  `employee`/`employer` id with obvious dummies.
- Deploy tooling tied to the maintainer's own infrastructure lives in
  `.claude/skills/deploy-test/`, which is gitignored on purpose.

## The real API

Reverse-engineered 2026-09-07 via Playwright devtools against a live account,
then every endpoint below re-confirmed with plain `curl` outside the browser.
Base URL: **`https://api.trappers.net/api/`**. The shop SPA at
`trappersshop.fiscfree.nl` is a Vue app that talks to this API and nothing
else — the shop origin serves no data of its own, so there is no HTML to
scrape and no reason to run a browser at runtime.

All calls are plain JSON over HTTPS with a bearer token; **no cookies, no
CSRF token, no session state** (confirmed — `curl` reproduces every call
below with only the `Authorization` header).

### Login

```
POST /api/auth/v2/login
Content-Type: application/json

{"employeeEmail": "<email>", "password": "<password>"}
```

Success → **200**:

```jsonc
{
  "token": "<JWT>",
  "userDetails": {
    "id": 12345,
    "firstName": "…", "lastName": "…", "prefix": null, "initials": "…",
    "emailAddress": "…",
    "employer": 99,            // numeric employer id
    "department": null,
    "address": { "line1": "…", "houseNumber": "…", "postalCode": "…",
                 "city": "…", "countryCode": "NLD" },
    "telephoneNumber": "…",
    "employeeNumber": "…", "employeeNumberEmployer": "…",
    "loginStartDate": "YYYY-MM-DD", "loginEndDate": null,
    "assignStartDate": "YYYY-MM-DD", "assignEndDate": null,
    "balance": 1234.0,         // ← the points balance, the headline number
    "numberOfWorkdays": 5.0,
    "allowManualAdditionOfCyclingDays": false,
    "balanceOverviewMail": true,
    "bikeType": "BIKE_EBIKE",
    "myTrappersActivated": true,
    "myOrdersActivated": true,
    "trappersWebshopActivated": true
  }
}
```

Failure → **400** with body `{"employeeEmail": "Invalid credentials."}` —
identical for a wrong password and for an unknown email address (no user
enumeration). Note the status code: a *400*, not a 401, so don't key auth
handling off 401 for the login call.

The JWT's claims are `{iat, iss, sub: "User Details", principal:
"employee:<email>", exp}` and it is valid for **4 hours** (`exp - iat` =
14400s). Every other call sends it as `Authorization: Bearer <token>`.

### Status — the poll endpoint

```
GET /api/status
Authorization: Bearer <token>
```

→ **200** `{"employee": { …the same object as userDetails above… },
"balance": 1234.0, "assignEndDate": null}`

This is what the integration should poll: it returns a **fresh balance**
without re-authenticating, so a normal poll cycle is one request. Verified
live that its `employee.balance` and top-level `balance` match the login
response's `userDetails.balance`.

An invalid or expired token → **401** `{"status":..., "error":
"Unauthorized", "message": "Invalid JWT Token"}`.

### Token refresh

```
POST /api/security/token-refresh
Authorization: Bearer <token>
```

→ **200** `{"token": "<new JWT>"}`. The SPA calls this on app load. An
integration can equally just re-run the login call when the token nears
`exp`; refresh is one fewer credential round-trip, login is one fewer moving
part. Either is fine — but a re-login must handle the 400 case above.

### Cycling days (the "fietsdagen" table)

```
POST /api/events?limit=<n>&offset=<n>
Authorization: Bearer <token>
```

Note: **POST**, not GET (a GET returns 405). Body can be `{}`.

→ **200** `{"limit": n, "total": <n>, "offset": 0, "items": [
{"id": …, "date": "YYYY-MM-DD", "tag": …, "collectorUnit": …,
"employee": …, "status": "PROCESSED" | "PROCESSED_DUPLICATE"}, … ]}`

Newest first. `limit` is honoured up to at least 500 and echoed back
verbatim, so a whole account's history is normally one request.

**`total` is a count of tag reads, not of cycling days** — corrected
2026-09-07, the original capture had this wrong. A collector unit sometimes
reads the same tag twice on one day; the second read is stored as a full
event row with `"status": "PROCESSED_DUPLICATE"` and earns no points. The
probed account had **155 rows: 131 `PROCESSED` and 24
`PROCESSED_DUPLICATE`**, over exactly 131 distinct dates — and exactly 131
`INCOME_DISTANCE` transactions, which is what confirms the duplicates are
uncredited rather than merely relabelled.

So "days cycled ever" is *not* `limit=1` + `total`; that overstates it by the
duplicate count (155 vs 131 here, an 18% error). Fetch the rows and count
distinct non-duplicate dates. Treat the status vocabulary as open the same
way as the transaction types: filter *out* the known duplicate marker rather
than filtering *in* an exhaustive list of good ones, so an unseen third
status still counts as a cycling day instead of silently vanishing.

### Points transactions

```
GET /api/transactions?limit=<n>&offset=<n>
Authorization: Bearer <token>
```

→ **200** `{"limit": n, "total": <n>, "offset": 0, "items": [
{"id": …, "employee": …, "date": "YYYY-MM-DDTHH:MM:SS", "tagEvent": …,
"articleOrder": null, "amount": 154.0, "type": "INCOME_DISTANCE",
"description": " 01 september 2026", "subcontract": …, "skipDate": null},
… ]}`

Newest first. Types seen live (updated 2026-09-07): `INCOME_DISTANCE`
(points credited for a cycling day, positive `amount`) and **`EXPENSE_ORDER`**
(points spent in the webshop, **negative** `amount`, `articleOrder` set to the
order's id). The earlier note that spending was "not observed live" is
superseded — on the probed account, 137 transactions were 131
`INCOME_DISTANCE` plus 6 `EXPENSE_ORDER`, matching its 6 orders exactly.

Still treat the vocabulary as open and don't branch exhaustively on it: the
integration keys off the **sign of `amount`**, not the type name, so a third
type appearing later lands on the right side of "earned" vs "spent" without a
code change.

Points per cycling day are employer- and distance-dependent — one probed
account earned 154 points/day. Read the rate from the data if you need it;
never assume a constant.

### Commute registration

```
GET /api/commute
Authorization: Bearer <token>
```

→ **200** `{"limit": 100, "total": 1, "offset": 0, "items": [
{"id": …, "employee": …, "location": …, "locationAddress": {…},
"startDate": "YYYY-MM-DD", "endDate": null, "distance": 11000}]}`

`distance` is the **one-way** commute in **metres** (11000 = 11 km). `locationAddress` is
the employer site's address, not the employee's home — still avoid logging
it.

### Orders

```
GET /api/articleOrders?limit=<n>&offset=<n>
Authorization: Bearer <token>
```

→ **200** paged `{limit, total, offset, items: [...]}`. Each item has
`orderNumber`, `orderDate`, `articleOrderStatus` (e.g. `EXPORTED`),
`trapperToEuroConversionRatio` (**0.01** on one probed account — i.e. 100
points = €1), `articleLines[]` with `trapperPrice`, plus `mutationLogs[]`
containing the ordering person's **name**.

**Careful with this endpoint**: it is the only place the points→euro
conversion ratio appears, but the response also carries names and an IBAN
field. Confirmed live 2026-09-07 — an order item's keys are `id`,
`orderNumber`, `orderDate`, `articleOrderStatus`, `articleLines`,
`mutationLogs`, `exportDate`, `orderCancelledDate`, `bookHandlingCostsDate`,
`trapperToEuroConversionRatio`, **`employee_id`** and **`ibanAccountNumber`**.
That is the worst response in the API to handle carelessly. If a euro-value
sensor is wanted, read only
`trapperToEuroConversionRatio` from the newest order and drop the rest —
and handle "no orders yet" (`total: 0`), where the ratio is simply unknown.
Don't hardcode 0.01: it is one employer's contract term, not a constant.

### Endpoints seen in the SPA bundle but not needed here

`POST /event` (manual cycling-day entry, only for accounts where
`allowManualAdditionOfCyclingDays` is true), `POST /articleOrder`,
`GET /article/{id}`, `POST /auth/password/{change,reset,reset/confirm}`.
All are write/shop paths a read-only sensor integration has no business
calling.

## Design constraints

- **Domain is `trappers`.** Display name "Trappers" in `manifest.json`.
- **`iot_class` is `cloud_polling`, `integration_type` is `service`.**
- **Poll interval: 30 minutes by default.** This is an unofficial,
  reverse-engineered API belonging to an employer benefits provider — be a
  good citizen. Points change at most once per working day, so even hourly
  would lose nothing.
- **Every request gets an explicit `aiohttp.ClientTimeout`** and
  `aiohttp.ClientError`/`asyncio.TimeoutError` must surface as `UpdateFailed`,
  never as an uncaught exception in the log.
- **Bad credentials must raise `ConfigEntryAuthFailed`, not `UpdateFailed`**,
  so HA starts its reauth flow instead of leaving sensors silently
  unavailable forever. Remember the login failure is a **400**.
- **Validate before indexing.** Every list endpoint answers `{limit, total,
  offset, items}`; a missing key or an empty `items` must produce a clean
  integration error, not a `KeyError`/`IndexError` in the log.
- **Never log the token, the password, or any field from `address`,
  `telephoneNumber`, `employeeNumber`, `employeeNumberEmployer`.** Debug
  logging in this integration should log endpoint + status code + the
  numeric fields it actually uses, nothing else.
- **`unique_id` on the config entry = the lowercased email address**, so the
  same account can't be added twice while a household with two Trappers
  accounts can add both as separate entries (the standard HA pattern; same
  as the maintainer's `ha-50plusmobiel`).
- **Entity names are translated, not hardcoded** — `translation_key` +
  `has_entity_name = True` per `SensorEntityDescription`, strings in
  `strings.json` (source/English) plus `translations/en.json` and
  `translations/nl.json`. Dutch matters here: the service itself is
  Dutch-only.

## Design decisions made while building it

Implemented and deployed 2026-09-07 (v0.1.0): seven sensors, verified
end-to-end against the live account and read back off a real Home Assistant
instance. The two API-contract corrections above came out of that work; these
are the choices the code makes that the contract alone doesn't dictate.

- **Sensor set.** `balance`, `balance_value_eur`, `cycling_days_total`,
  `cycling_days_this_month`, `last_cycling_day`, `points_earned_this_month`,
  `commute_distance`. All seven are supported honestly — none had to be
  dropped.

- **A poll is four requests, not one.** `/status` alone answers the headline
  balance, but the activity sensors need `/events`, `/transactions` and
  `/commute` too. `/articleOrders` is the fifth and is deliberately *not* on
  every cycle — see below. At a 30-minute interval that is ~200 requests a
  day, which is still a gentle citizen for this API.

- **`/articleOrders` is fetched at most once a day while the ratio is known,
  and every poll while it is unknown.** That asymmetry is a privacy rule, not
  a caching heuristic: the response for an account *with* orders is the one
  carrying names and `ibanAccountNumber`, so it is pulled as rarely as the
  data allows; the response for an account *without* orders is
  `{"total": 0, "items": []}`, which carries nothing, so retrying it costs
  nothing and picks up the first order promptly. `ORDER_RATIO_MAX_AGE` in
  `api.py`.

- **`balance_value_eur` is `unknown`, never 0, on an account with no orders.**
  The ratio only exists on order records. 0.01 is one employer's contract
  term; defaulting to it would invent a number the API never returned.

- **Month boundaries follow Home Assistant's clock, not the container's.**
  `api.py` is HA-independent and takes `today`/`now` as arguments;
  `coordinator.py` passes `dt_util.now()`. Without that, a UTC container would
  roll the "this month" sensors over at the wrong local moment. `api.py`
  defaults to `date.today()` so it stays usable (and testable) outside HA.

- **Token handling is 401-driven re-login, not scheduled refresh.** The JWT
  lives four hours and `/api/security/token-refresh` exists, but a poll that
  gets a 401 simply logs in again and retries once. At a 30-minute interval
  that is one extra round-trip roughly every eighth poll, in exchange for no
  expiry bookkeeping. A second 401 straight after a successful login raises
  `TrappersApiError`, not `TrappersAuthError` — re-entering the password
  cannot fix that, so routing it through reauth would be a dead end.

- **Paging is bounded, and hitting the bound is an error.** `/events` is
  fetched whole (needed for both the lifetime and the this-month count) at
  `limit=500`, capped at 20 pages; `/transactions` pages at 100 until a page
  reaches into last month, capped at 12. The caps exist so a paging bug or a
  changed `total` becomes a clean `UpdateFailed` rather than an unbounded
  request loop against someone else's API.

- **Icon is an original mark, not Trappers' or FiscFree's.**
  `custom_components/trappers/brand/icon.svg` (+ rasterized
  `icon.png`/`icon@2x.png`/`logo.png`/`logo@2x.png`) is a bicycle whose front
  wheel is a points token, on a deep indigo (`#4C3E8E`→`#2E2559`) chosen
  precisely because it is nowhere near the greens and teals Dutch cycling-
  benefit brands use — so it reads as related-but-unofficial. Served straight
  out of the integration package by HA's own `/api/brands/` endpoint on
  2026.3.0+ (`hacs.json`'s `homeassistant` floor is set there for this
  reason); confirmed working in the live Add-Integration dialog, so no
  `home-assistant/brands` PR is needed.

- **Tests use a hand-rolled fake `aiohttp.ClientSession`.** Not `aioresponses`
  — as of 0.7.9 it is incompatible with the aiohttp version HA pins
  (`ClientResponse.__init__()` gained a required `stream_writer` kwarg it
  doesn't pass), so every test using it fails with a `TypeError` unrelated to
  the code under test. Same conclusion, same reason, as `ha-50plusmobiel`.
  `tests/test_coordinator.py`, `test_config_flow.py` and `test_sensor.py` use
  `pytest-homeassistant-custom-component`'s real in-memory `hass` instead, and
  `test_coordinator.py` imports `config_flow` before the test so the handler is
  registered — otherwise `async_start_reauth_if_available()` silently no-ops
  and the reauth assertion passes for the wrong reason.

- **Local test venv is Python 3.14** (`uv venv --python 3.14 .venv`),
  matching what recent Home Assistant requires. The system Python 3.11 cannot
  install `pytest-homeassistant-custom-component`.

## Cutting a release

HACS installs whatever the **latest GitHub release** points at, but the
version it displays — and the one HA logs at startup — comes from
`manifest.json` inside that archive, so the two must be bumped together.
Releases are cut by hand:

```bash
# 1. bump "version" in custom_components/trappers/manifest.json
# 2. commit + push to main (runs Validate / hassfest / Test)
# 3. tag the release
gh release create vX.Y.Z --title "..." --notes "..."
```

Tag name is `v` + the exact manifest version. Step 3 also triggers Validate
on the tag, where the `manifest-version` job asserts exactly that. The job is
**detective, not preventive**: `gh release create` publishes release and tag
in one step, so a red run means delete the release *and* the tag, fix
`manifest.json`, and re-cut. Watch the tag's run, not just `main`'s.

## Development notes

Developed with AI assistance and tested end-to-end against a real Trappers
account and a real Home Assistant instance. Deploy/test tooling tied to any
specific person's own infrastructure is intentionally kept out of this
repository.
