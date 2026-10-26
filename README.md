# fightcard-engine

The data engine behind [ufc.xlobax.com](https://ufc.xlobax.com). It collects UFC fight data, rates the fighters,
scores each upcoming bout's chance of ending early, and writes the JSON the site is built from.

## What it does

1. **Scrape.** Events, bouts, results and fighter records come from ufcstats.com (`fightcard scrape`). Broadcast
   segments (prelims, main card) come from ESPN (`fightcard segments`), and fighter headshots are fetched too
   (`fightcard images`).
2. **Rate.** Each fighter gets an Elo-style rating.
3. **Score.** A gradient-boosted model gives every upcoming bout a finish chance and a Low / Medium / High tier
   (`fightcard score`).
4. **Publish.** The results are written as static JSON in `data/`, and `fightcard publish` turns that into the
   public versioned API served at `https://ufc-data.xlobax.com/v1/` (not live yet). See [docs/API.md](docs/API.md).

## The rating

Every fighter starts at 1500 and gains or loses points after each bout. Beating someone rated above you counts for
more, split decisions count for less, and the rating drifts back toward 1500 during long layoffs. The full spec is in
[docs/ELO.md](docs/ELO.md).

## Finish chance

The finish model is trained in a separate research repo, and the exported bundle is kept here in `model/`. It
looks at each fighter's recent form, how their fights tend to end, striking and grappling rates, physical attributes
and experience. The tier cut-offs split past out-of-sample predictions into thirds. Historically those tiers ended
early 37%, 48% and 62% of the time. [model/README.md](model/README.md) covers the bundle, the tiers and how a new
export is brought in.

## Running it

Needs [uv](https://docs.astral.sh/uv/) and Python 3.12+.

```
uv sync
uv run pytest                            # full suite; tests that hit the network are excluded
uv run pytest -m live                    # only the network tests
uv run pytest -m docker                  # only the nginx container tests (needs docker)
uv run fightcard history ingest --html-dir <ufcstats cache> --db history/history.sqlite  # once: seed the store
uv run fightcard --data data scrape      # keeps the store current (--no-history to skip); then segments, images, score
```

The data layout and what each field means are in [docs/CONTRACT.md](docs/CONTRACT.md).
