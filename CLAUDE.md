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

### The webshop catalogue — where the real euro rate lives

```
POST /api/articles?limit=<n>&offset=<n>
Authorization: Bearer <token>
```

Note: **POST**, not GET (a GET returns 405). Body can be `{}`.

→ **200** paged `{limit, total, offset, items: [...]}`. An item's keys:
`id`, `articleNumber`, `articleName`, `description`, `category`,
`trappersPrice`, `purchasePrice` `{currency, amount}`, `salesPriceSupplier`
`{currency, amount}`, `availableInPublicShop`, `archived`, `availableFrom`,
`availableUntil`, `supplierName`, `handlingFee`, `handlingFeeApplies`,
`requireIban`, `vatScale`, `attachments`, `internalRemark`, `property1`,
`property2`. **No personal data at all** — the whole response is catalogue.

99 articles on the probed account, all returned in a single page at
`limit=400` (the limit is echoed verbatim, as on `/events`).
`GET /api/categories` is the sibling endpoint, same paged envelope.

#### The trap: `trapperToEuroConversionRatio` is not a spendable rate

This is worth reading before "simplifying" the euro sensor back.

`/api/articleOrders` carries a field called `trapperToEuroConversionRatio`
(0.01 on the probed account). It is named like the answer, it looks like the
answer, and 0.1.0 used it: `balance × 0.01`, giving €100.00 for a
10 000-point balance. It is wrong, and it is wrong in the direction that flatters.

It is an **identity**. Across all 99 catalogue articles,
`purchasePrice / trappersPrice` was exactly `0.01` for every single one — the
ratio just reproduces `purchasePrice`, which is the scheme's own cost basis,
not a price anything can be bought at. The balance was overstated by the
operator's margin, ~5%.

The rate that can actually be spent is `trappersPrice` per euro of **face**
value, taken off the article name — `"bol. cadeaukaart € 25"` at
`trappersPrice: 2625` is **105 points per euro**. Uniform across all 59
priced articles on the probed account, with no outliers. 10 000 / 105 =
€95.24, which is what the shop actually charges — ~5% under what the ratio
claimed.

**Nobody would have caught this from the API alone.** Both numbers are
internally consistent; the ratio genuinely is a real field with a real
meaning. It took a human looking at a real shop listing — "bol. cadeaukaart
€ 25 / 2625 Trappers, so the pricing is a bit off" — to notice the sensor
disagreed with the shop. Treat "field named like the thing I want" as a
hypothesis to check against reality, not as the answer.

Gift cards are also the *best* rate in the catalogue: physical goods run
~131 points per euro of `salesPriceSupplier` (JBL Flip 7: 13209 pt / €100.83;
Apple Watch Series 11: 48610 pt / €371.07), with one outlier at ~143. So the
gift-card rate is the *best achievable* value of a balance, which is the right
thing for a sensor to report — averaging the goods in would understate it.

Don't hardcode 105. It is this employer's contract term, exactly as 0.01 was.

### Orders — deliberately not called

```
GET /api/articleOrders?limit=<n>&offset=<n>
```

→ **200** paged. Item keys: `id`, `orderNumber`, `orderDate`,
`articleOrderStatus` (e.g. `EXPORTED`), `articleLines`, `mutationLogs`,
`exportDate`, `orderCancelledDate`, `bookHandlingCostsDate`,
`trapperToEuroConversionRatio`, **`employee_id`** and **`ibanAccountNumber`**.
`mutationLogs[]` contains the ordering person's **name**.

The integration called this in 0.1.x for the conversion ratio and **no longer
calls it at all** (0.2.0). The ratio was the wrong number (above), and this is
the only endpoint in the API that returns a bank account number and a person's
name for data nothing needed. Deriving the rate from `/api/articles` fixed the
arithmetic and removed the PII-carrying request in the same change — the
second being the larger win.

If some future sensor genuinely needs order history, read only the fields it
needs and never log the response.

### Endpoints seen in the SPA bundle but not needed here

`POST /event` (manual cycling-day entry, only for accounts where
`allowManualAdditionOfCyclingDays` is true), `POST /articleOrder`,
`GET /article/{id}`, `POST /auth/password/{change,reset,reset/confirm}`.
All are write/shop paths a read-only sensor integration has no business
calling.

## Design constraints

- **Domain is `trappers`.** Display name "Trappers" in `manifest.json`.
- **`iot_class` is `cloud_polling`, `integration_type` is `service`.**
- **Polls at fixed wall-clock slots — 08:00, 11:00, 14:00, 17:00, 20:00 local,
  plus a stable per-entry offset of up to 15 minutes — not on an interval** (0.1.x polled every 30 minutes). Points are credited
  at most once per working day, when the collector unit reads the tag on
  arrival, so there is nothing to see overnight, and this is an unofficial API
  belonging to an employer benefits provider. Five polls × four requests is
  ~20 requests/day, down from 192; the catalogue fetch behind the euro rate is
  cached 24h on top. `POLL_HOURS` in `const.py`. See the schedule notes under
  "Design decisions" for the traps.
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

Implemented and deployed 2026-09-07: seven sensors, verified end-to-end
against the live account and read back off a real Home Assistant instance.
Shipped as 0.1.0, then 0.1.1 (entity IDs no longer carry the account's email
address) and 0.2.0 (the euro sensor was overstating the balance by ~5%; polling moved
from a 30-minute interval to five fixed daytime slots). The corrections above came out of
that work; these are the choices the code makes that the contract alone
doesn't dictate.

- **Sensor set.** `balance`, `balance_value_eur`, `cycling_days_total`,
  `cycling_days_this_month`, `last_cycling_day`, `points_earned_this_month`,
  `commute_distance`. All seven are supported honestly — none had to be
  dropped.

- **The config entry is titled from `firstName`, never the email address**
  (fixed in 0.1.1; 0.1.0 shipped `title=email` and was wrong). HA slugifies the
  entry title into the device name and from there into every entity_id, so
  `title=email` produced `sensor.you_example_com_puntensaldo` — the user's whole
  address, local part and employer domain both — and a friendly_name to match. That is not merely ugly: entity_ids travel into
  dashboard YAML pasted into forum threads and issues on this repo, and into
  any screenshot committed under `docs/screenshots/` — so it would make
  redacting an address a chore the user has to remember every single time.
  `_entry_title()` in `config_flow.py` uses the account's first name, falling
  back to the address's **local part** (never the whole address — the domain is
  what identifies an employer) and then to "Trappers". `firstName` is the only
  field taken out of the login response's `userDetails`; `async_login()` returns
  that one string rather than the object, so the address, telephone number and
  employee numbers alongside it never reach a caller.

  `unique_id` remains the full lowercased email. It is the duplicate-account
  guard and it is never rendered anywhere.

  Two tests pin this: `test_entry_title_never_contains_the_email_address` and
  `test_nothing_user_visible_carries_the_account_email`, the latter sweeping
  every entity_id, every state attribute and every DeviceInfo field — so a
  future "simplification" back to `title=email`, or an address quietly added to
  `model`/`sw_version`, fails instead of silently re-leaking.

  **Note `ha-50plusmobiel` has the same `title=username` line and has not been
  changed** — different repo, deliberately left for its owner to decide.

- **A poll is four requests, not one.** `/status` alone answers the headline
  balance, but the activity sensors need `/events`, `/transactions` and
  `/commute` too. Five slots a day makes that ~20 requests, plus the
  once-daily `/articles` fetch behind the euro rate.

- **The schedule is a moving `update_interval`, reassigned at the end of every
  successful `_async_update_data` to the gap until the next `POLL_HOURS` slot.**
  Four things about it are load-bearing and were each a way to get it wrong:

  1. **Subtract in UTC, not in local time.** `interval_until_next_poll()`
     converts both ends with `dt_util.as_utc()` before subtracting. Python's
     `datetime.__sub__` *skips* `utcoffset()` when both operands share the same
     `tzinfo` object — which they do here, both carrying HA's configured zone —
     and returns the wall-clock difference. Across the spring-forward night
     that is an hour too long (20:01 CET → 08:00 CEST is 10h59m of real time,
     not 11h59m), so the poll fires at 09:00 instead of 08:00. A test caught
     this; without it the bug would have surfaced once a year and been
     impossible to attribute.

  2. **There is no "outside the window, skip" branch.** The coordinator is
     simply not woken between 20:00 and 08:00. A skip branch would be a path
     that could raise `UpdateFailed` overnight and paint every sensor
     unavailable until morning — looking exactly like a broken integration.
     Not having the branch means not being able to get that wrong.

  3. **The first refresh is not window-gated.** It comes from
     `async_config_entry_first_refresh()` at setup, so a restart at 02:00
     populates the sensors immediately rather than leaving them `unknown` until
     08:00. The window governs the recurring schedule, not the initial load —
     as does `homeassistant.update_entity`, which still works at any hour.

  4. **The interval is clamped to `MIN_UPDATE_INTERVAL` (60s).** A clock step,
     a DST transition or waking exactly on a slot boundary can compute a zero
     or negative delta; unclamped, that is a hot loop against an employer
     benefits provider's API.

  5. **The per-entry offset uses `hashlib`, never the builtin `hash()`.**
     Python randomises string hashing per process, so `hash()` would re-roll
     the offset on every HA restart while the code still read as
     deterministic — the same species of bug as (1): correct-looking, and
     invisible from a single run. `test_offset_is_stable_across_processes`
     recomputes it in subprocesses under two different `PYTHONHASHSEED`s,
     which is the only way to catch it; the `hash()` version passes every
     other test in the file. Seeded from `entry.entry_id` (a random ULID)
     rather than the email: it spreads a two-account household across two
     offsets and keeps an address out of the calculation.

     Added **after** the slot, never around it — a symmetric spread would let
     the first poll of the day fire at 07:52, which is the one thing the
     08:00 boundary exists to prevent.

     Stable per install rather than re-rolled per poll, deliberately:
     spreading load is a property *across* installs, so a fixed offset buys
     all of the herd-avoidance while keeping the predictability that fixed
     slots exist for. Its practical value today is nil — there is one install
     — but this is on HACS, and the cost of adding it later, after people are
     running it, is higher than the cost of having it now.

  Fixed slots rather than a free-running 3h timer because a timer is anchored
  to whenever HA last restarted, drifts to arbitrary times, and makes "when
  does it poll?" unanswerable. `tests/test_schedule.py` pins the slot
  arithmetic, both DST transitions, the clamp over a full simulated year, and
  that data is held rather than dropped between polls.

- **`balance_value_eur` is derived from the catalogue, as a mode.** See "The
  trap" above for why not from `trapperToEuroConversionRatio`. The mechanics:
  filter `/articles` to non-archived, publicly-available items, parse a face
  value out of `articleName` (Dutch formatting — `"€ 1.000,00"` is a thousand),
  compute `trappersPrice / face` per item and take the **mode**.

  The mode rather than the mean, deliberately. One mispriced or oddly-named
  article shifts a mean and cannot shift a mode. More importantly, if the
  catalogue ever stops being uniform, a mean produces a confident number that
  buys nothing, whereas the mode fails its plurality check
  (`MIN_MODE_SHARE`, `MIN_PRICED_ARTICLES`) and the sensor reads `unknown` —
  which is the truth at that point. A catalogue with two rates means the
  single-rate model is wrong, and that should surface, not average out.

  A known rate is cached 24h (`POINTS_PER_EURO_MAX_AGE`); an unknown one is
  not, so an unreadable catalogue resolves on the next poll rather than being
  stuck for a day. Note the cache justification changed with the endpoint: for
  `/articleOrders` it was a privacy rule, for `/articles` it is only about
  traffic, since that response carries nothing personal.

- **`balance_value_eur` is `unknown`, never 0 and never a guess**, when the
  catalogue yields no usable rate. 105 is one employer's contract term exactly
  as 0.01 was; defaulting to either would invent a number the API never gave.

- **Month boundaries follow Home Assistant's clock, not the container's.**
  `api.py` is HA-independent and takes `today`/`now` as arguments;
  `coordinator.py` passes `dt_util.now()`. Without that, a UTC container would
  roll the "this month" sensors over at the wrong local moment. `api.py`
  defaults to `date.today()` so it stays usable (and testable) outside HA.

- **Token handling is 401-driven re-login, not scheduled refresh.** The JWT
  lives four hours and `/api/security/token-refresh` exists, but a poll that
  gets a 401 simply logs in again and retries once. With slots three hours
  apart the token has always expired by the next poll, so in practice this is
  one login per poll — still only five a day, in exchange for no expiry
  bookkeeping at all. A second 401 straight after a successful login raises
  `TrappersApiError`, not `TrappersAuthError` — re-entering the password
  cannot fix that, so routing it through reauth would be a dead end.

- **Paging is bounded, and hitting the bound is an error.** `/events` is
  fetched whole (needed for both the lifetime and the this-month count) at
  `limit=500`, capped at 20 pages; `/transactions` pages at 100 until a page
  reaches into last month, capped at 12; `/articles` at 400, capped at 10. The caps exist so a paging bug or a
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
