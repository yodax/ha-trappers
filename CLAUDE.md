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
"employee": …, "status": "PROCESSED"}, … ]}`

Newest first. `total` is the lifetime count of registered cycling days —
the cheapest way to get "days cycled ever" is `limit=1` and read `total`.

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

Newest first. `type` seen live: `INCOME_DISTANCE` (points credited for a
cycling day). Spending an order presumably produces a negative amount with
`articleOrder` set and a different `type` — **not observed live**, so treat
the type vocabulary as open and don't branch exhaustively on it.

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
conversion ratio appears, but the response also carries names and (in some
shapes) an IBAN field. If a euro-value sensor is wanted, read only
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
