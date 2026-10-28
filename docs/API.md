# The /v1/ API, briefly

`https://ufc-data.xlobax.com/v1/` is a read-only JSON API with UFC cards, fighters and finish tiers. It is just
static files: `fightcard publish` writes them and nginx serves them. The exact rules are in
[CONTRACT.md](CONTRACT.md); this page explains the ideas.

## Resources

| URL | What you get |
|---|---|
| `/v1/index.json` | The entry point: upcoming and recent event ids, the model summary, `generated_at`, `contract_version` |
| `/v1/events/{event_id}.json` | One card: its fights in order, tiers, results, and on a completed fight its `elo` at the time (from `contract_version` `"1.3"`) |
| `/v1/fighters.json` | Every fighter on those cards, keyed by id |
| `/v1/rankings.json` | The pound-for-pound lists (men, women) and every active fighter's Elo, for a rankings page and fighter search. Optional: it appears from `contract_version` `"1.2"` |
| `/v1/images/{fighter_id}.webp` | A headshot (the fighter's `image` field gives the exact path) |
| `/v1/schema.json` | A JSON Schema describing all of the JSON above |
| `/v1/snapshot.tar.gz` | All of the above in one file |

Start from `index.json`, then fetch the events it lists. Or just download the snapshot.

`index.json`'s `model` says which finish model scored this generation: `model.version` and `model.as_of` (the
export's date), and `model.stale_fighters`, how many fighters the model can't score right now because they've
fought since that export (their bouts show "—").

```
curl -s https://ufc-data.xlobax.com/v1/index.json
```

A completed fight's `elo` (`Fight.elo`, `"1.3"`) is each corner's Elo going in, the bout's own change and their
P4P rank going in, for a line like "1612 (+26) · #4 P4P":
`{"red_pre": 1612, "red_delta": 26, "blue_pre": 1588, "blue_delta": -26, "red_p4p_rank": 4, "blue_p4p_rank": null}`.
It's `null` on upcoming fights and on fights the ratings don't cover. The values are on today's scale, the same as
`fighters.json`'s `elo`, so an old fight's numbers can move by a point between publishes.

## Status codes

| Code | Meaning here |
|---|---|
| `200 OK` | Here is the file. |
| `304 Not Modified` | You already have the latest version (see caching). No body. |
| `404 Not Found` | No such resource: a wrong path, an unknown event id, or a directory. |
| `405 Method Not Allowed` | Only `GET` and `HEAD` work. The `Allow` header says so. |

## Caching, ETag and 304

Every response has a `Cache-Control` header saying how long you may reuse it without asking again:
60 seconds for `index.json` and the snapshot (they change on every update), 5 minutes for events, fighters,
the schema and rankings, a day for headshots.

After that, ask again cheaply. Each response has an `ETag`, a fingerprint of the file. Send it back as
`If-None-Match`, exactly as you got it. If the file hasn't changed, the server answers `304 Not Modified` with
no body, and you keep your copy:

```
curl -sI https://ufc-data.xlobax.com/v1/index.json            # note the ETag, e.g. W/"6ae18fc6-617"
curl -sI -H 'If-None-Match: W/"6ae18fc6-617"' https://ufc-data.xlobax.com/v1/index.json   # 304
```

The `ETag` comes in two forms. The service sits behind Cloudflare, which compresses the JSON on the way out and
may mark the `ETag` weak, with a `W/` in front. Whether it does depends on the file and on Cloudflare's cache, not
just on what you ask for: `index.json` gives `W/"6ae18fc6-617"` to plain curl and, usually, `"6ae18fc6-617"` with
`Accept-Encoding: gzip`, but expect either form for any JSON file. The snapshot and the headshots are already
compressed and always come with the plain `"..."` form. Either way, store the header as is, `W/` included, and send that
string back; don't strip or add the `W/`, and don't take the ETag apart. (Both forms get the `304` today, but
echoing it back is what keeps working.) `If-Modified-Since` with the `Last-Modified` you got works too.

A file that didn't change between updates keeps its ETag, so headshots aren't downloaded again.

## Versioning

The `v1` in the path is a promise: within `/v1/` things are only ever **added** (new fields, new files). Nothing
is removed or renamed, and no value changes meaning. So write clients that ignore fields and files they don't know.
The values of an existing enum (`finish_tier`, `segment`, `status`, `outcome`, a past fight's `result`, the keys
of `index.model.tiers`) are fixed too: a new value would go to `/v2/`, so you can validate them strictly.
`index.json`'s `contract_version` tells you which additions you're seeing (`"1"`, then `"1.1"`, …). A change that
would break clients goes to a new `/v2/`, and `/v1/` keeps running next to it for a while.

## Why a snapshot

The data is updated as a whole, a "generation" at a time, and the switch is atomic. But if you fetch
`index.json` and then twenty event files, an update can land in between, and you'd mix two generations (an
index listing an event from the newer one, say). `snapshot.tar.gz` is one file from one generation, so it is
always consistent. It is also byte-identical when nothing changed, so a conditional GET on it is a cheap
"anything new?" check. The site's refresh uses it.
