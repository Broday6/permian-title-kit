# Permian title kit

Working tools for running mineral and lease title across the Texas and New Mexico Permian Basin. They cover the automatable steps of the Permian Title Playbook. They do the clerical work and the math, and they flag every legal question for a person to decide.

| Step in the run | Command | What it does |
|---|---|---|
| Plan the search | `ptk plan` | Shows where a county's records are for your window: online index start years, parent counties to search before organization, late-index counties that need courthouse or plant time |
| Search the indexes | `ptk names` | Turns one name into the grantor/grantee searches a careful abstractor runs (initials, Jno./Wm./Chas., nicknames, et ux, estates, trusts, entity suffixes) plus a Soundex key |
| Map the tract | `ptk legal` | Parses Texas abstract/survey/block/section and New Mexico PLSS descriptions into join keys. NM aliquots expand to quarter-quarters (`N/2 SE/4` → NESE, NWSE) |
| Abstract instruments | `ptk extract`, `ptk batch` | AI first pass with Claude: strict JSON per `ptk/schema/instrument.schema.json`, with every quote checked against the OCR lines it cites |
| Check the runsheet | `ptk check` | Red-flag rules: missing images, words vs numerals ("one-half (1/4)"), double fractions, term interests, blanket county conveyances, fiduciary capacity, NM spousal joinder, heirship affidavit timing, liens, top leases, depth limits |
| Build the chain | `ptk ledger` | Replays a takeoff in recording order, in exact fractions. Flags Duhig candidates, chain gaps and over-conveyances without deciding them |
| Compute decimals | `ptk deck`, `ptk calc` | Unit deck for a pooled unit or allocation well: royalty, fixed/floating NPRI, ORRI, WI/NRI, unleased owners (TX cotenant; NM 7/8 + 1/8). Every tract must add to exactly 1 |

**This is landman work product, not a title opinion.** It prepares runsheets and ownership schedules. Drilling, division order and acquisition opinions stay with your examining attorney, and so does every flag the tools raise.

## Install

Python 3.10 or later. The core has no dependencies.

```bash
pip install -e .            # or: pip install .
pip install -e ".[ai]"      # adds the Anthropic SDK for ptk extract / ptk batch
python -m pytest            # 42 tests; python -m unittest discover -s tests -t . also works
```

## Try it on the fictional example

Everything in `examples/` is invented: an allocation well across two Texas sections, built to set off each kind of flag.

```bash
ptk ledger examples/takeoff.csv          # ownership by tract + Duhig candidate + chain gap
ptk deck examples/project.json           # unit deck by owner; exits 2 if any tract isn't whole
ptk check examples/runsheet.csv          # red flags by row; exits 1 if any "stop" flag
ptk plan --county Irion --from 1905      # where to search before 1983
ptk legal "SE/4 of NW/4 and W/2 W/2 of Section 6, T24S, R32E, NMPM"
ptk names "Jno. W. Smith et ux" "XYZ Oil & Gas, L.L.C."
ptk calc --acres 640 --owned 1/16 --royalty 1/4 --unit 4800/9600
```

## Formats

**Takeoff CSV** (`ptk template takeoff.csv --kind takeoff`): one row per conveyance event.

| Column | Meaning |
|---|---|
| `recorded` | ISO date. Events replay in (recorded, seq) order |
| `tract`, `depth` | Tract ID, and `ALL` or a named depth interval (declare intervals in a depths JSON) |
| `estate` | `MI` (mineral fraction of the whole) or `NPRI` |
| `kind` | `root` (patent, or a base opinion's owners), `convey`, or `reserve` (NPRI) |
| `grantees`, `shares` | `;`-separated. Shares default to equal and must add to 1 |
| `interest` | `1/4` = of the whole estate; `all` or `1/2 of grantor` = relative to what the grantor holds then |
| `warranty` | `Y` turns an over-conveyance into a Duhig candidate |
| `npri_kind`, `npri_value` | `fixed` (of production) or `floating` (of royalty) |

Mineral reservations are modeled by conveying less: a grantor who deeds 1/2 and keeps 1/2 conveys `1/2`. A probate or heirship distribution is one row with every heir as a grantee.

**Project JSON** (`examples/project.json`): the takeoff path, leases (lessors, royalty, lessee WI shares, ORRIs), participation weights (acres in the unit or completed lateral feet per tract), the producing depth, state, and which NPRI owners have ratified pooling.

**Runsheet CSV** (`ptk template runsheet.csv`): the playbook's runsheet columns. Image file names follow `ptk.runsheet.image_key`, e.g. `TX-REEVES-OPR-2019-012345.pdf` or `TX-MIDLAND-DR-0512-0033.pdf`.

## Ownership rules the deck applies

These are defaults. Confirm each one with your examiner for the instruments in front of you.

- A **fixed NPRI** is a fraction of production. It burdens leased mineral owners in proportion to their mineral share and comes out of their royalty (the Texas default under *Wenske v. Ealy*). If the burden exceeds the royalty, it raises `NPRI_EXCEEDS_ROYALTY`.
- A **floating NPRI** is a fraction of each burdened owner's royalty.
- An **ORRI** is a fraction of 8/8, proportionately reduced to the share of the minerals the lease covers, and burdens the lessees.
- An **unleased owner** in Texas is a cost-bearing cotenant (share of net proceeds, not royalty). In New Mexico, a pooling order makes it a 7/8 WI plus a 1/8 royalty (NMSA 70-2-17).
- A **Texas NPRI under a unit or allocation well** that hasn't ratified raises `NPRI_NOT_RATIFIED` (*Montgomery v. Rittersbacher*).
- **Double fractions** are flagged, never interpreted. After *Van Dyke* (2023), "1/2 of 1/8" is usually a floating 1/2, but the examiner decides.
- **Rounding:** exact fractions throughout, rounded half-up to 8 places only for display.

## AI first-pass abstracting

```bash
ptk extract p1.png p2.png --doc-id TX-REEVES-DR-0088-0077 --county Reeves --state TX --ocr-dir ocr/ --out d1.json
ptk batch submit jobs.json        # half price, results within 24 hours; see examples/jobs.example.json
ptk batch collect <batch-id> --jobs jobs.json --out results.json
```

- **Input:** page images (PNG/JPEG/WebP/GIF) or a PDF. Add OCR text as `<ocr-dir>/<page stem>.txt`, one OCR line per line. Each line gets an ID like `p2.l14` that the model must cite.
- **Request:** defaults to `claude-opus-5-5` at effort `high`. Use `--model claude-sonnet-5-5` for volume and keep Opus for flagged pages. Output is constrained to the JSON schema (structured outputs). The system prompt is cached. Synchronous calls opt into server-side refusal fallback; Batches don't support it.
- **Checks on every answer:** `QUOTE_NOT_IN_CITED_LINES` when a quote doesn't match its cited OCR lines; `WORD_NUMERAL_MISMATCH` on fractions; refusals and `max_tokens` cut-offs come back as errors, never as data.
- **Review tiers:** anything that changes ownership (fractions and their basis, reservations, parties and capacity on old instruments, every flag) gets a person. Header fields that pass every check can be spot-checked.
- **Privacy:** redact SSNs, birth dates and account numbers before sending pages, and use API terms that don't retain or train on your data.
- **Before production:** the request shape follows the current Anthropic SDK and was tested against a mocked client. Run it on a gold set of 300–500 instruments you've already abstracted (stratified by county, era and instrument type) and measure field accuracy before you rely on it. The first live call also confirms that the API accepts the schema.

## County data

`ptk/counties.py` holds online index and image start years for 20 Texas and 4 New Mexico counties. They come from TexasFile and CourthouseDirect coverage pages as of Oct 1, 2026, plus parent counties to search before each county organized. Vendors keep scanning, so re-check at project start. Entries marked `verify=True` come from county histories, not a recording office.

## Layout

```
ptk/fracs.py      exact fractions, words and numerals, 8-place rounding
ptk/ledger.py     takeoff replay, Duhig/gap/over-conveyance flags, depth intervals
ptk/doi.py        participation factors and the unit deck
ptk/legal_tx.py   Texas abstract/survey/block/section parser
ptk/legal_nm.py   New Mexico PLSS parser and aliquot expansion
ptk/names.py      index search variants and Soundex
ptk/runsheet.py   runsheet template, image keys, red-flag rules
ptk/counties.py   county coverage and parent-county search plans
ptk/extract.py    Claude extraction, batch submit/collect, answer checks
ptk/schema/       instrument JSON schema
examples/         fictional takeoff, project, runsheet, batch jobs
tests/            unit tests for all of the above
```
