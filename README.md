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
| Points balance value | € | What the balance is worth in the webshop, at the gift-card rate (see below). |
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

**The euro value is what you can actually spend, not the scheme's cost
basis.** The API does have a field called `trapperToEuroConversionRatio`, and it
is tempting, but it is not the rate you can buy at — it reproduces the scheme's
own purchase price, which sits above shop prices by the operator's margin. Using
it overstated the balance by about 5%.

"Points balance value" instead reads the webshop catalogue and works out what
the points buy: a "bol. cadeaukaart € 25" costing 2625 points is 105 points per
euro, so a balance of 7574 points is worth € 72,13. The rate is read from your
own employer's catalogue, never assumed — it is a contract term and it differs
between employers.

Gift cards are deliberately the yardstick, because they are the *best* rate in
the shop: physical goods (headphones, smartwatches) run around 131 points per
euro of their supplier price. So this sensor reports the best achievable value
of your balance. Please don't "fix" it by averaging the physical goods in —
that would understate what the balance is worth.

If the catalogue ever stops having one consistent rate, the sensor reads
`unknown` rather than reporting an average that would buy nothing.

## Polling

Each account polls **five times a day — at 08:00, 11:00, 14:00, 17:00 and
20:00** in your Home Assistant timezone. Nothing overnight.

Points are credited at most once per working day, when the collector unit
reads your bike tag as you arrive at the office, so there is genuinely nothing
to see between 20:00 and 08:00. This is an unofficial API belonging to an
employer benefits provider, and polling it 48 times a day to catch one event
would be rude.

The slots are fixed wall-clock times rather than a free-running three-hour
timer, so "when does it poll?" has an answer that does not depend on when you
last restarted Home Assistant. Across the daylight-saving changes one gap is
an hour shorter or longer in real time — the slots stay at 08:00 and 20:00
local, which is the point.

A poll is four requests (balance, cycling days, transactions, commute). The
webshop catalogue behind the euro value is read at most once a day on top of
that, since the rate is a contract term that does not move. That is about 20
requests a day in total.

Your sensors keep their values between polls; they do not go unavailable
overnight. And you can always force an immediate refresh at any hour from
**Developer tools → Actions → `homeassistant.update_entity`** or the reload
button on the integration's entry.

## Privacy

The Trappers API returns a lot more than points: your home address, telephone
number and employer employee numbers all come back on every call. This
integration **only exposes the numbers it needs** and never logs the rest. It
also does not call the orders endpoint at all — that is the one that returns
the ordering person's name and a bank account number, and nothing here needs
it. See the "Privacy" and "Design constraints" sections of
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
