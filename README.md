# Trappers for Home Assistant

[![HACS: custom repository](https://img.shields.io/badge/HACS-custom%20repository-41BDF5.svg)](https://hacs.xyz/)
[![Release](https://img.shields.io/github/v/release/yodax/ha-trappers?display_name=tag)](https://github.com/yodax/ha-trappers/releases)
[![Validate](https://github.com/yodax/ha-trappers/actions/workflows/validate.yml/badge.svg)](https://github.com/yodax/ha-trappers/actions/workflows/validate.yml)
[![Test](https://github.com/yodax/ha-trappers/actions/workflows/test.yml/badge.svg)](https://github.com/yodax/ha-trappers/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Track your Trappers points balance and cycling activity in Home Assistant, as
normal sensors you can put on a dashboard, chart over time, or trigger
automations from.

[Trappers](https://fiscfree.nl/onze-oplossingen/fietsstimulering/) is a Dutch
employer bike-to-work scheme run by FiscFree: every day you commute by bike,
your bike tag is registered and you earn points ("trappers") to spend in their
[webshop](https://trappersshop.fiscfree.nl/). You need an existing Trappers
account through a participating employer — this integration signs in with it,
it cannot create one.

**This is an unofficial, community-built integration, not affiliated with or
endorsed by Trappers or FiscFree.** It talks to the unofficial JSON API behind
their webshop.

## Installation

Requires **Home Assistant 2026.3.0 or newer**.

### HACS

This is **not in the HACS default store**, so it has to be added as a custom
repository first:

[![Open your Home Assistant instance and open this repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=yodax&repository=ha-trappers&category=integration)

1. Add the repository with the button above, or by hand: **HACS → ⋮ → Custom
   repositories**, paste `https://github.com/yodax/ha-trappers`, pick category
   **Integration**, and choose **Add**.
2. Find **Trappers** in HACS and install it.
3. Restart Home Assistant.
4. Set it up, as described under [Configuration](#configuration) below.

### Manual / direct-copy

Copy the `trappers` folder out of this repository's `custom_components/` into
your Home Assistant configuration directory, so that you end up with
`config/custom_components/trappers/`. Restart Home Assistant, then set it up
below.

## Configuration

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=trappers)

**Settings → Devices & Services → Add Integration → "Trappers"**, then enter
the email address and password you use on `trappersshop.fiscfree.nl`.

For a second account in the same household, run the setup again with the other
person's login. Each account gets its own device and its own sensors.

Setup screens and sensor names are available in English and Dutch, and follow
your Home Assistant instance's configured language.

## Sensors

One device per account, with seven sensors on it.

| Sensor | Unit | Description |
|---|---|---|
| Points balance | trappers | Points you currently have to spend in the Trappers webshop. |
| Points balance value | € | What that balance is worth in euro, at the webshop's gift-card rate (see below). |
| Cycling days total | — | Days you have cycled since joining the scheme. |
| Cycling days this month | — | Days you have cycled in the current calendar month. Resets on the 1st. |
| Last cycling day | date | The most recent date your bike tag was registered. |
| Points earned this month | trappers | Points credited so far this calendar month. Points *spent* in the webshop are not subtracted. |
| Commute distance | km | Your registered one-way commute distance. `unknown` if no commute is registered. |

Each date you cycle counts once, even when your bike tag happens to be read
more than once that day — so the cycling-day sensors match the days you were
actually paid points for.

### What the euro value means

"Points balance value" estimates what your balance buys at the best rate the
webshop offers, which is its gift cards. For example, if a €25 gift card costs
2,625 points, that is 105 points per euro, and a 10,000-point balance is worth
€95.24.

Two things follow from that:

- **Physical goods are worse value.** Headphones and smartwatches run around
  131 points per euro, so spending your balance on those buys less than this
  sensor says. It reports the *best* achievable value, not an average.
- **The rate is your employer's, not a constant.** It is read from the
  catalogue your own account sees, because it is a contract term and it differs
  between employers. If no clear rate can be read, the sensor shows `unknown`
  rather than a guess.

## Updates

Each account updates **five times a day — shortly after 08:00, 11:00, 14:00,
17:00 and 20:00** in your Home Assistant timezone. Nothing overnight.

"Shortly after" is a fixed delay of up to 15 minutes that your installation
picks once and then keeps, so yours might update at 08:06, 11:06, 14:06 and so
on, every day. That keeps every installation from arriving at the API at
exactly 08:00:00, and keeps updates off the top of the hour where the rest of
your system already is.

Five is enough because points are credited at most once per working day, when
your bike tag is read as you arrive — there is genuinely nothing to see
overnight.

Your sensors keep their values between updates; they do not go unavailable at
night. Home Assistant also fetches fresh values when the integration starts, at
whatever hour that is. To refresh immediately, use **Developer tools → Actions
→ `homeassistant.update_entity`**, or the reload button on the integration's
entry.

## Privacy

Trappers' API returns far more than points: your home address, telephone
number and employer employee numbers come back on nearly every call. This
integration **exposes only the numbers it needs** and never logs the rest. It
also never requests order history — the one response that carries a name and a
bank account number — because no sensor here needs it.

## Contributing

Issues and pull requests are welcome. Two things to know before you open one:

- **Redact before you paste.** The API returns a home address, telephone
  number, employer employee numbers and an email address on nearly every call,
  and entity IDs can carry a name. The issue templates say this too.
- **Tests are expected to pass and to come with changes.**

```bash
pip install -r requirements_test.txt
pytest tests/
```

Tests run against local fakes — no real HTTP and no Trappers account needed to
work on this. CI runs them on every push, alongside HACS validation and Home
Assistant's hassfest.

[`CLAUDE.md`](CLAUDE.md) is the developer-facing document: the API contract,
the design decisions behind the sensors, and the privacy rules this repository
is committed to (including the pre-commit hook that enforces them).

## License

MIT — see [`LICENSE`](LICENSE).
