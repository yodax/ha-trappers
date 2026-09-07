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

_Populated once the integration is built — see `custom_components/trappers/`._

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
