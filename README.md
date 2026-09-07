# Trappers for Home Assistant

Custom Home Assistant integration for
[Trappers](https://fiscfree.nl/onze-oplossingen/fietsstimulering/) — the Dutch
employer bike-to-work scheme run by FiscFree, where every day you commute by
bike earns points ("trappers") you spend in their
[webshop](https://trappersshop.fiscfree.nl/).

It reports your points balance and your cycling activity as normal Home
Assistant sensors, so you can put them on a dashboard, chart them over time,
or trigger automations off them.

**This is an unofficial, community-built integration, not affiliated with or
endorsed by Trappers or FiscFree.** It talks to their app's unofficial JSON
API — see [`CLAUDE.md`](CLAUDE.md) for how it was reverse-engineered and what
it does.

## Installation

Requires **Home Assistant 2026.3.0 or newer**.

### HACS

HACS → ⋮ → Custom repositories → add this repository's URL, category
"Integration" → install "Trappers" → restart Home Assistant.

### Manual / direct-copy

Copy `custom_components/trappers/` into your Home Assistant
`config/custom_components/` directory and restart Home Assistant.

## Configuration

Settings → Devices & Services → Add Integration → "Trappers", then enter the
email address and password you use on `trappersshop.fiscfree.nl`.

Add the integration once per account — a household with two people on the
scheme gets two config entries, each with its own sensors.

### Language

The config flow and sensor names are translated into English and Dutch and
follow your Home Assistant instance's configured language automatically.

## Sensors

One device per account, with seven sensors on it. Entity names follow your
Home Assistant language (English and Dutch ship with the integration).

| Sensor | Unit | Description |
|---|---|---|
| Points balance | trappers | Your current points balance — the headline number. |
| Points balance value | € | The balance converted to euros. `unknown` until the account has placed at least one order (see below). |
| Cycling days total | — | Cycling days registered since you joined the scheme. |
| Cycling days this month | — | Cycling days in the current calendar month. Resets on the 1st. |
| Last cycling day | date | The most recent day a collector unit registered your bike. |
| Points earned this month | trappers | Points credited so far this calendar month. Points *spent* in the webshop are not subtracted. |
| Commute distance | km | Your registered one-way commute distance. `unknown` if no commute is registered. |

### Two things worth knowing about the numbers

**Cycling days are counted, not totalled.** A collector unit sometimes reads
the same tag twice in a day. Trappers stores that as a second entry, marked as
a duplicate, which earns no points — so the raw entry count runs ahead of the
number of days you actually cycled. Both cycling-day sensors count distinct,
non-duplicate days, which is the figure that matches the points you were paid.

**The euro value needs an order to exist.** The points-to-euro rate is part of
the contract between your employer and FiscFree, and the API only reveals it on
an order record. On an account that has never ordered anything there is no rate
to read, so "Points balance value" stays `unknown` rather than showing a made-up
number. It fills in by itself after your first order.

## Polling

Each account polls on a **30-minute interval** by default. This is a
deliberately conservative default for an unofficial API belonging to an
employer benefits provider; points change at most once per working day, so
nothing is lost by polling gently. Trigger an immediate refresh from
**Developer tools → Actions → `homeassistant.update_entity`** or the reload
button on the integration's entry.

## Privacy

The Trappers API returns a lot more than points: your home address, telephone
number and employer employee numbers all come back on every call. This
integration **only exposes the numbers it needs** and never logs the rest —
see the "Privacy" and "Design constraints" sections of
[`CLAUDE.md`](CLAUDE.md). Nothing account-specific is committed to this
repository, and a pre-commit hook (`.githooks/pre-commit`) enforces that.

## Running tests

```bash
pip install -r requirements_test.txt
pytest tests/
```

Tests run against local fakes — no real HTTP, no real account needed. CI runs
them on every push, alongside HACS validation and Home Assistant's hassfest.

## License

MIT — see [`LICENSE`](LICENSE).
