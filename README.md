# Permian title kit

Working tools for running mineral and lease title across the Texas and New Mexico Permian Basin. They cover the automatable steps of the Permian Title Playbook. They do the clerical work and the math, and they flag every legal question for a person to decide.

| Step in the run | Command | What it does |
|---|---|---|
| Plan the search | `ptk plan`, `ptk counties` | Shows where a county's records are for your window: online index start years, parent counties to search before organization, late-index counties that need courthouse or plant time. `ptk counties --checklist` writes a re-verification worksheet |
| Search the indexes | `ptk names` | Turns one name into the grantor/grantee searches a careful abstractor runs (initials, Jno./Wm./Chas., nicknames, et ux couples, heirs, estates, trusts, entity suffixes) plus a Soundex key |
| Map the tract | `ptk legal`, `ptk mapcheck` | Parses Texas abstract/survey/block/section and New Mexico PLSS descriptions, then checks each one lands on a real survey or section of the right size |
| Read the pages | `ptk ocr` | OCR for page images or PDFs (Tesseract by default; AWS Textract or RapidOCR optional), one text file per page |
| Abstract instruments | `ptk extract`, `ptk batch`, `ptk smoke-test` | AI first pass with Claude: strict JSON per `ptk/schema/instrument.schema.json`, every quote checked against the OCR lines it cites |
| Check the runsheet | `ptk check` | Red-flag rules: missing images, words vs numerals ("one-half (1/4)"), double fractions, term interests, blanket county conveyances, fiduciary capacity, NM spousal joinder, heirship affidavit timing, liens, top leases, depth limits. `--xlsx` writes a color-coded spreadsheet with image links |
| Build the chain | `ptk ledger` | Replays a takeoff in recording order, in exact fractions. Flags Duhig candidates, chain gaps, over-conveyances and NPRI merger without deciding them |
| Compute decimals | `ptk deck`, `ptk calc` | Unit deck for a pooled unit or allocation well: royalty, fixed/floating NPRI (only on the minerals it burdens), ORRI, WI/NRI, unleased owners (TX cotenant; NM 7/8 + 1/8). Every tract must add to exactly 1. `--xlsx` writes a live spreadsheet |

**This is landman work product, not a title opinion.** It prepares runsheets and ownership schedules. Drilling, division order and acquisition opinions stay with your examining attorney, and so does every flag the tools raise.

## Install

Python 3.10 or later. The core has no dependencies; each extra adds one capability.

```bash
pip install -e .                 # core: ledger, deck, parsers, checks, plans
pip install -e ".[ai]"           # Claude extraction (anthropic, pillow, jsonschema)
pip install -e ".[xlsx]"         # spreadsheet exports (openpyxl)
pip install -e ".[ocr]"          # pillow; boto3 for Textract; RapidOCR
pip install -e ".[gis]"          # shapefile layers for mapcheck (pyshp)
python -m pytest                 # 91 tests
```

`ptk ocr` uses the Tesseract and poppler command-line tools: `apt install tesseract-ocr poppler-utils` or `brew install tesseract poppler`.

## Try it on the fictional example

Everything in `examples/` is invented: an allocation well across two Texas sections, built to set off each kind of flag.

```bash
ptk ledger examples/takeoff.csv                       # ownership, NPRI burdens, Duhig candidate, chain gap
ptk deck examples/project.json --xlsx deck.xlsx       # unit deck; exits 2 if any tract isn't whole
ptk check examples/runsheet.csv --xlsx runsheet.xlsx  # red flags by row; exits 1 if any "stop" flag
ptk mapcheck examples/mapcheck/tracts.csv \
  --layer examples/mapcheck/fictional_tx_surveys.geojson \
  --layer examples/mapcheck/fictional_nm_sections.geojson
ptk plan --county Irion --from 1905                   # where to search before 1983
ptk legal "SE/4 of NW/4 and W/2 W/2 of Section 6, T24S, R32E, NMPM"
ptk names "John Smith and wife, Mary Smith" "Heirs of Jno. W. Smith"
ptk calc --acres 640 --owned 1/16 --royalty 1/4 --unit 4800/9600
```

## Formats

**Takeoff CSV** (`ptk template takeoff.csv --kind takeoff`): one row per conveyance event.

| Column | Meaning |
|---|---|
| `recorded` | 1948-05-10, 5/10/1948 or May 10, 1948. Two-digit years are refused. Events replay in (recorded, seq) order |
| `tract`, `depth` | Tract ID, and `ALL` or a named depth interval (declare intervals in a depths JSON) |
| `estate` | `MI` (mineral fraction of the whole), `NPRI`, or `ALL` (everything the grantor holds: use it for probates, heirship distributions and "all right, title and interest" deeds) |
| `kind` | `root` (patent, or a base opinion's owners), `convey`, or `reserve` (NPRI) |
| `grantees`, `shares` | `;`-separated. Shares default to equal and must add to 1 |
| `interest` | `1/4` = of the whole estate; `all` or `1/2 of grantor` = relative to what the grantor holds then |
| `warranty` | `Y` turns an over-conveyance into a Duhig candidate |
| `npri_kind`, `npri_value` | `fixed` (of production) or `floating` (of royalty), from the minerals it burdens |
| `npri_burdens` | What the NPRI was carved from: `all` (default), `conveyed` (the minerals the same instrument conveyed), `retained` (what the grantor kept), or `owners:A;B` |

The NPRI burden follows the burdened minerals through every later conveyance, so an NPRI reserved out of a half interest keeps burdening only that half. `ptk ledger` shows each NPRI's burdened share, and an owner who ends up holding minerals that carry their own NPRI is flagged `MERGER_REVIEW`.

Mineral reservations are modeled by conveying less: a grantor who deeds 1/2 and keeps 1/2 conveys `1/2` (an `MI` `reserve` row is flagged and moves nothing). A probate or heirship distribution is one `ALL` row with every heir as a grantee, so the decedent's NPRIs pass along with the minerals. Owner names match regardless of case, spacing and periods, and each such match is listed as `NAME_VARIANT`.

**Project JSON** (`examples/project.json`): the takeoff path, leases (lessors, royalty, lessee WI shares, ORRIs), participation weights (acres in the unit or completed lateral feet per tract), the producing depth, state, and which NPRI owners have ratified pooling.

**Runsheet CSV** (`ptk template runsheet.csv`): the playbook's runsheet columns. Image file names follow `ptk.runsheet.image_key`, e.g. `TX-REEVES-OPR-2019-012345.pdf` or `TX-MIDLAND-DR-0512-0033.pdf`.

## Ownership rules the deck applies

These are defaults. Confirm each one with your examiner for the instruments in front of you.

- An **NPRI** burdens only the minerals the ledger says it burdens, and comes out of those owners' royalty in proportion to their burdened minerals (the Texas default under *Wenske v. Ealy* when the burden is spread across the estate). If a burden exceeds the royalty, it raises `NPRI_EXCEEDS_ROYALTY`.
- A **fixed NPRI** is a fraction of the production from the burdened minerals; a **floating NPRI** is a fraction of each burdened owner's royalty.
- An **ORRI** is a fraction of 8/8, proportionately reduced to the share of the minerals the lease covers, and burdens the lessees.
- An **unleased owner** in Texas is a cost-bearing cotenant (share of net proceeds, not royalty). In New Mexico, a pooling order makes it a 7/8 WI plus a 1/8 royalty (NMSA 70-2-17).
- A **Texas NPRI under a unit or allocation well** that hasn't ratified raises `NPRI_NOT_RATIFIED` (*Montgomery v. Rittersbacher*).
- **Double fractions** are flagged, never interpreted. After *Van Dyke* (2023), "1/2 of 1/8" is usually a floating 1/2, but the examiner decides.
- **Rounding:** exact fractions throughout, rounded half-up to 8 places only for display.

## Spreadsheets

- `ptk check runsheet.csv --images scans/ --xlsx runsheet.xlsx`: the runsheet with each image file name as a clickable link (relative to the workbook), rows colored by their worst flag (red stop, amber review, green info), a Flags sheet and a legend.
- `ptk deck project.json --xlsx deck.xlsx`: By owner, Participation, By tract, Flags and About sheets. The participation weights (blue on yellow) are live inputs: change a tract's acres or lateral feet and every unit decimal, owner total and the "must read OK" check recalculates. Tract decimals come from the title work and show the exact fraction beside them. Formulas were verified with LibreOffice: 59 formulas, no errors, values matching the exact results.

## OCR

```bash
ptk ocr deed.pdf --out ocr/ --pages-dir pages/    # PDFs are split into 300-dpi page images
ptk extract pages/deed-p1.png pages/deed-p2.png --doc-id ... --county Reeves --state TX --ocr-dir ocr/
```

Each page gets `<page>.txt` (what `ptk extract` reads) and `<page>.ocr.json` (line confidence and pixel boxes). `ptk ocr` lists lines below 0.80 confidence. On the rendered sample deed (`ptk/sample.py`):

- **Tesseract** (default) read all 20 lines correctly, including "one-half (1/2) of the one-eighth (1/8)", at 0.81–0.97 line confidence, in under a second.
- **RapidOCR**'s default model ran capitalized words together ("MINERALDEED"), read "$10.00" as "$1o.oo" and dropped a line. Use it only as a fallback.
- **AWS Textract** (`--engine textract`) needs boto3 and AWS credentials, and costs about $1.50 per 1,000 pages.

None of these read 1880s clerk handwriting well. For handwritten deed books, let `ptk extract` read the image directly and review every field.

## AI first-pass abstracting

```bash
ptk smoke-test                    # one real call on the sample deed, checked against 8 known facts
ptk extract p1.png p2.png --doc-id TX-REEVES-DR-0088-0077 --county Reeves --state TX --ocr-dir ocr/ --out d1.json
ptk batch submit jobs.json        # half price, results within 24 hours; see examples/jobs.example.json
ptk batch collect <batch-id> --jobs jobs.json --out results.json
```

- **Run `ptk smoke-test` first** once `ANTHROPIC_API_KEY` is set (or after `ant auth login`). It renders the fictional sample deed, OCRs it, makes one request, validates the answer against the schema and checks eight facts on the page: instrument type, Vol. 88 Pg. 77, the parties, the 1/2 mineral conveyance, the 1/2-of-1/8 reservation, the 15-year term, the double-fraction flag, and Section 12, Block 33, A-123. `examples/sample_extraction.json` is what a correct answer looks like.
- **Input:** page images (PNG/JPEG/WebP/GIF) or a PDF. Images larger than 2,576 px are downscaled when Pillow is installed, and requests over 30 MB are refused with a clear message.
- **Request:** defaults to `claude-opus-5-5` at effort `high`. Use `--model claude-sonnet-5-5` for volume and keep Opus for flagged pages. Output is constrained to the JSON schema (structured outputs) and validated again locally. The system prompt is cached. Synchronous calls opt into server-side refusal fallback; Batches don't support it.
- **Checks on every answer:** `QUOTE_NOT_IN_CITED_LINES`, `EVIDENCE_NOT_CITED`, `WORD_NUMERAL_MISMATCH`, schema errors; refusals and `max_tokens` cut-offs come back as errors, never as data.
- **Review tiers:** anything that changes ownership (fractions and their basis, reservations, parties and capacity on old instruments, every flag) gets a person. Header fields that pass every check can be spot-checked.
- **Privacy:** redact SSNs, birth dates and account numbers before sending pages, and use API terms that don't retain or train on your data.
- **Before production:** after the smoke test passes, run a gold set of 300–500 instruments you've already abstracted (stratified by county, era and instrument type) and measure field accuracy.

## Map check

```bash
ptk mapcheck tracts.csv --layer reeves_surveys.geojson --layer cadnsdi_sections.shp --out matched.geojson
```

- **Tracts CSV:** `tract, state, county, legal_description, stated_acres`.
- **Layers:**
  - Texas: RRC digital map survey polygons (free, by county) or a licensed grid such as Whitestar.
  - New Mexico: BLM CadNSDI `PLSSFirstDivision`.
  - Both must be in longitude/latitude; reproject first with `ogr2ogr -t_srs EPSG:4326`.
- **Matching:**
  - Texas: by abstract, or by block and section when there's no abstract.
  - New Mexico: by township, range and section (CadNSDI PLSSID, e.g. `NM230230S0310E0`).
- **What each tract gets:**
  - A status: `OK`, `NOT_FOUND`, `AMBIGUOUS` (several different surveys match), `ACREAGE_MISMATCH` (mapped area × described fraction is more than 5% off the stated acres), `OUTSIDE_PERMIAN` or `UNPARSED`.
  - The mapped acres, the centroid, and a GeoJSON of the matched features to view in QGIS.
- **Field names:** the defaults are a starting point (RRC `ABSTRACT_N`, `LEVEL2_BLO`, `LEVEL3_SUR`; CadNSDI `PLSSID`, `FRSTDIVNO`). Check your layer's attribute table and override them with `--fields fields.json`.
- **Accuracy:** areas are computed on a sphere (about 1%), which is plenty to catch a wrong section, county or aliquot. The legal description controls title, not the map.

## County data

`ptk/counties.py` holds online index and image start years for 20 Texas and 4 New Mexico counties. They come from TexasFile and CourthouseDirect coverage pages, read through search-engine summaries on Oct 1, 2026, plus parent counties to search before each county organized. To re-verify:

1. Run `ptk counties --checklist check.csv`. It writes the exact TexasFile and CourthouseDirect page for every county.
2. Fill the `confirmed_*` columns as you check each page.
3. Run `ptk plan --county Irion --from 1905 --overrides check.csv`. The plan then uses your confirmed years.

Entries marked `verify=True` come from county histories, not a recording office.

## Known limits

Check these by hand until they're handled.

- **The AI extraction hasn't run against the live API yet.** `ptk smoke-test` is the one-command check once credentials are set.
- **Same-name owners** (a father and son both "John Smith") are one owner to the ledger. Give one a distinguishing name in the takeoff ("John Smith Jr.").
- **Texas descriptions naming two surveys** keep only the first survey. Metes-and-bounds calls are captured as text, not plotted.
- **New Mexico lots** are matched by section; lot acreage must be compared by hand against the BLM survey.
- **Handwritten records**: no OCR engine here reads 1880s–1920s clerk hands reliably.
- **No review screen.** Review happens in the spreadsheets and JSON.

## Layout

```
ptk/fracs.py      exact fractions, words and numerals, 8-place rounding
ptk/dates.py      recording dates, two-digit years refused
ptk/ledger.py     takeoff replay, NPRI burdens, Duhig/gap/merger flags, depth intervals
ptk/doi.py        participation factors and the unit deck
ptk/legal_tx.py   Texas abstract/survey/block/section parser
ptk/legal_nm.py   New Mexico PLSS parser and aliquot expansion
ptk/mapcheck.py   survey/PLSS layer matching, acreage and location checks
ptk/names.py      index search variants and Soundex
ptk/runsheet.py   runsheet template, image keys, red-flag rules
ptk/counties.py   county coverage, parent-county search plans, re-verification checklist
ptk/ocr.py        Tesseract, Textract and RapidOCR page OCR
ptk/extract.py    Claude extraction, batch submit/collect, answer checks
ptk/smoke.py      one-call live check on the sample deed
ptk/sample.py     the fictional sample deed page
ptk/xlsx.py       runsheet and deck spreadsheets
ptk/schema/       instrument JSON schema
examples/         fictional takeoff, project, runsheet, map layers, sample answer, batch jobs
tests/            unit and end-to-end tests
```
