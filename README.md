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
| Work the owners | `ptk deal serve` → /crm, `ptk crm` | The Landowner CRM: owners and their holdings from pooling exhibits, calls, the outreach email cadence, the lead pipeline and a call queue, on the same database as the deals. A signed owner is handed to title follow-up with one button |
| Set up the desk | `ptk desk init` | One folder with the database, editable rules and a double-click "Start Land Desk" launcher; optionally imports the CRM backup |
| Start a title run | `ptk deal title-run` | For each asset on a deal: a search plan (where the records are, the legal keys, every name variant to search, the state checklist) and blank runsheet and takeoff files to fill |
| Check accuracy | `ptk audit` | Re-checks the math against known answers, every parser against 264 labelled record forms, the audit log, every deal's verification and package hashes, the CRM and the rules. Also on the desk's Accuracy page |
| Follow the deal | `ptk deal` | Takes each signed deal from intake to the first check: checks the deal record, verifies the seller owns what it sold (as of the effective date and today), tracks every curative item, drafts the closing package, gates the funds, logs recordings and operator notices, and reconciles division orders and checks against the title decimal. Local dashboard included |

Rebuilding or extending the kit? Start with [HANDOFF.md](HANDOFF.md). It has the module specs, the exact outputs a rebuild must reproduce, the bugs already fixed, and the roadmap.

**This is landman work product, not a title opinion.** It prepares runsheets and ownership schedules. Drilling, division order and acquisition opinions stay with your examining attorney, and so does every flag the tools raise.

## Install

Python 3.10 or later. The core has no dependencies; each extra adds one capability.

```bash
pip install -e .                 # core: ledger, deck, parsers, checks, plans
pip install -e ".[ai]"           # Claude extraction (anthropic, pillow, jsonschema)
pip install -e ".[xlsx]"         # spreadsheet exports (openpyxl)
pip install -e ".[ocr]"          # pillow; boto3 for Textract; RapidOCR
pip install -e ".[gis]"          # shapefile layers for mapcheck (pyshp)
python -m pytest                 # 151 tests
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

## Start here: run title on a deal

```bash
ptk desk init "C:/Land Desk" --crm land-crm-data-export.json --by "Your Name"
```

1. **Open the desk.** Double-click *Start Land Desk* in that folder (or run `ptk deal serve --open`). Owners, pipeline and outreach are under *Acquisition*; deals, the approval queue and the Accuracy page are under *Title*.
2. **Hand off a signed owner.** On an owner who's *Under Contract*, *Start title follow-up* (enter the buyer and the state) creates the deal and opens its intake tasks.
3. **Finish the deal record.** Fill in the deal's `deal.json` (legal description, tract, interest, effective date, price) and run `ptk deal intake deals/<id>/deal.json --reason "completed intake"`. Intake re-runs and lists anything still missing.
4. **Start the title run.** Press *Start title run* on the deal page (or `ptk deal title-run <id>`). Each asset gets `deals/<id>/title/<asset>/`: `SEARCH-PLAN.md`, a blank `runsheet.csv`, a `takeoff.csv` (minerals) or `lease_chain.csv` (leases and wellbores), and folders for images, OCR and abstracts. Existing files are never overwritten.
5. **Run the records** as the search plan says: pull each instrument image, `ptk ocr` it, abstract it (`ptk extract` with an API key, or in a Claude chat with `ptk abstract-prompt` and `ptk abstract-check`), add a runsheet row, then `ptk check runsheet.csv`.
6. **Build the chain** in the takeoff or lease chain, then press *Rerun verification* on the deal page (or `ptk deal verify <id>`). From there the deal moves through curative, closing, recording, operator notices and payment as described below.
7. **Check accuracy** any time with `ptk audit` or the Accuracy page.

## The CRM (acquisition side)

The Landowner CRM is now part of the desk. `ptk deal serve` opens it at http://127.0.0.1:8780/crm, and the title deals are one click away at /deals. Both read and write the same database, and every change to an owner is in the audit log alongside the deals.

```bash
ptk crm import land-crm-data-export.json   # bring in the old CRM's backup (re-running it only adds what changed)
ptk deal serve                             # CRM at /crm, title deals at /deals, approval queue at /outbox
ptk crm list                               # owners with stage, holdings and title deal
ptk crm handoff c1 --buyer "Your Company LLC" [--state NM]
ptk crm export backup.json                 # the CRM's own backup format, restorable in the old app
```

- **Same app, new storage.** The CRM page is the original single-file app. Only its storage seam changed: records load from and save to the desk (one record per owner, saved only when it changes) instead of the browser. Owners, holdings, calls, tags, email cadence and the views (Dashboard, Owners, Pipeline, Call queue, Outreach) work as before. The desk uses one look throughout: a sidebar, line icons instead of emoji, and light and dark themes.
- **Hand-off to title.** On an owner who's Under Contract, *Start title follow-up* creates the deal: the owner becomes the seller (linked back to the CRM), each holding becomes an asset, and intake lists everything acquisitions still has to supply (legal description, tract, interest, effective date, price, signed agreement). The CRM's interest types are mapped to asset kinds (Royalty → mineral, Working Interest → wellbore, ORRI → ORRI, NPRI → royalty) and that mapping opens a task for acquisitions to confirm; the exhibit's % is shown, never used as title. The owner's record then shows the deal's stage, and the deal page links back to the owner.
- **Outreach email.** Every CRM email is previewed, and Send goes through the desk's mailer: SMTP when `PTK_SMTP_*` is set, otherwise an .eml file in `outbox/`, labeled "saved as .eml, not emailed". Email to an owner marked Dead or tagged `do-not-contact` is refused. Each send is logged on the owner.
- **Attachments** are kept beside the database in `crm-files/` (PDF, images, CSV/JSON/TXT/Markdown, 20 MB each). Removing one moves it to `crm-files/.removed`.
- The original integration notes and record schema are in [docs/crm/](docs/crm).

## Deal follow-up (`ptk deal`)

The acquisition team hands over a signed deal; `ptk deal` follows it to a recorded, paid interest. A deal can mix minerals, royalties, ORRIs, leases and horizontal wellbores, in Texas and New Mexico, and each asset gets the checklist for its state and kind.

```bash
export PTK_DEALS_DB=deals.db PTK_USER="Your Name"     # or pass --db and --by
ptk deal intake examples/deals/tx-mineral.json         # stage 1: check the record, open the TX/NM checklists
ptk deal verify D-2026-001                              # stage 2: does each seller own what it sold? (exit 1 if not)
ptk deal status D-2026-001                              # six stages, what blocks each, what to do next
ptk deal tasks D-2026-001                               # the curative tracker
ptk deal draft D-2026-001 T12                           # draft the cure (ratification, affidavit, correction, release)
ptk deal request D-2026-001 T12                         # draft the letter to whoever must act (for approval)
ptk deal opinion D-2026-001 --attorney "Name" --covers all --requirement "..."
ptk deal package D-2026-001                             # stage 4: conveyances, Exhibit A, closing statement
ptk deal closing D-2026-001 approve|sign|release        # release refuses until everything is clear
ptk deal record D-2026-001 --county Reeves --recorded 2026-11-03 --instrument-no 2026-004321
ptk deal notify D-2026-001                              # stage 5: transfer notices to every operator and payor
ptk deal division-order D-2026-001 --payor "..." --decimal 0.06 --well "..." --type RI
ptk deal reconcile D-2026-001 statement.csv             # stage 6: the buyer's lines against the title decimal
ptk deal remind --send                                  # daily: due reminders, late payments, then send approved email
ptk deal serve                                          # dashboard at http://127.0.0.1:8780
```

| Stage | Done when |
|---|---|
| 1 Intake | Every tract parses (and maps, with `--layer`); county and state agree; interest × gross acres = NMA; New Mexico land status and spouses are given; the signed agreement is listed |
| 2 Verify | Title supports each sale, or acquisitions has decided each shortfall. Catches a seller who holds less than it sold, holds nothing of record, or **conveyed part of it after the effective date** (a possible double sale). A horizontal well's WI and NRI come from each lease's share of the well |
| 3 Curative | Every title task is cleared or waived. **An attorney opinion is required only when title raised a legal question** (Duhig, NPRI scope, double fractions, term interests, merger, heirship...); those tasks can be cleared only by an opinion that covers them, or waived only by the attorney |
| 4 Closing | Package drafted, approved and signed; funds released through the gate |
| 5 Record and notify | Recorded in every county; BLM / NMSLO / RRC / OCD filings done; every operator and payor sent a transfer notice |
| 6 Track payment | A division order and first check from every payor, at the title decimal (computed by replaying the takeoff with the deal's conveyance added) |

**What it decides and what it doesn't.** Every flag the kit raises becomes a task with an owner, a due date and a requirement. Title flags count only when they bear on the seller's interest: a chain flag when its instrument is in the seller's chain, an NPRI flag when that NPRI burdens the seller's minerals. Everything else stays visible as a note. Drafts carry a banner for attorney review and a `[bracketed blank]` for every fact the program doesn't know; a title shortfall appears on the closing statement as a *proposed* adjustment for acquisitions to approve.

**Messages.** First contacts and anything substantive (curative requests, transfer notices, decimal questions) wait for a person to approve them (`ptk deal outbox list`, `approve`, or the dashboard's approval queue). **Routine reminders go out on their own**, by email only, to a party who already got a person-approved message on the deal, never to anyone marked `do_not_contact`, at most 3 per recipient per task, 14 days apart. They stop when the task is marked received or a person rejects one, and they're withdrawn if the task closes first. After that, a `NO_RESPONSE` task asks a person to call. Email goes through SMTP when `PTK_SMTP_HOST` (plus `PTK_SMTP_USER`, `PTK_SMTP_PASSWORD`, `PTK_SMTP_FROM`) is set; without it, approved email is written to `outbox/*.eml` and marked *exported*, not sent.

**Records.** Everything lives in one SQLite file as an append-only event log: who did what, when. Status is always rebuilt from the log, nothing can be edited or deleted in place, and `ptk deal verify-log` checks the hash chain. `ptk deal export --csv status.csv --xlsx status.xlsx` writes reports; `ptk deal import deals.csv` reads a deal sheet (one row per asset).

**Rules.** Severities, owners, due dates, the Texas and New Mexico checklists, the attorney policy (`flagged`, `always`, or a price threshold) and the reminder policy are in [`ptk/deal/rules.json`](ptk/deal/rules.json). Copy it to `ptk-rules.json` beside the database and edit the copy; keys you leave out keep the defaults. The legal citations in it are starting points to confirm with counsel.

**Title run.** `ptk deal title-run <id>` (or the deal page's *Start title run*) writes each asset's search plan from the county table, the parsed legal description, the seller's name variants and the state checklist, links the new files on the deal, and logs the run. `--asset` limits it to some assets; `--from` sets the first year to search (default 1850, back to the patent).

**Formats.** Deal JSON: see `examples/deals/tx-mineral.json` and `nm-leasehold.json`, or `ptk deal template deal.json`. A lease or wellbore buy needs a lease chain CSV (`ptk deal template chain.csv --kind lease-chain`): the lease, every assignment of the working interest, and every ORRI, replayed like the mineral ledger so ORRIs travel with the WI they burden. Operator statements: `payor, well, owner, decimal, type, amount, check_date, period, status`.

## Accuracy checks (`ptk audit`)

```bash
ptk audit                       # everything; exit 1 if any check fails
ptk audit --only engine,deals   # some areas
ptk audit --json --out audit.json
```

| Area | What it re-checks |
|---|---|
| Engine | The HANDOFF 8.1 chain and 8.2 unit deck to the exact fraction, the lease chain, deed fractions, dates, 8-place rounding, that every fraction a draft spells out reads back to the same value, and every parser against `ptk/deal/parser_corpus.json` (264 hand-labelled record forms: fractions, dates, Texas and New Mexico descriptions, names, runsheet flags). A silently wrong reading fails; a refusal warns |
| Records | The audit log's hash chain |
| Deals | Each deal's verification re-run in memory against the stored result, title files present, closing package and curative draft hashes against their manifests, released funds still passing the gate, open legal questions, overdue tasks |
| CRM | Owner stages, dates, numbers, percentages and email addresses, owner-to-deal links, attached files |
| Rules | Every flag code the program raises has a rule, checklist ids are unique, named templates exist and render, every legal citation says to confirm with counsel |
| Setup | AI abstracting, OCR tools and outgoing email (information only) |

The same report is on the desk's Accuracy page. `tests/test_parser_corpus.py` fails the build if a parser case that passes today ever stops passing.

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

- An **NPRI** burdens only the minerals the ledger says it burdens, and comes out of those owners' royalty in proportion to their burdened minerals (the kit's modelling choice, not a rule of law: *Wenske v. Ealy* turned on that deed's words, so the examiner reads each reservation). If a burden exceeds the royalty, it raises `NPRI_EXCEEDS_ROYALTY`.
- A **fixed NPRI** is a fraction of the production from the burdened minerals; a **floating NPRI** is a fraction of each burdened owner's royalty.
- An **ORRI** is a fraction of 8/8, proportionately reduced to the share of the minerals the lease covers, and burdens the lessees.
- An **unleased owner** in Texas is a cost-bearing cotenant (share of net proceeds, not royalty). In New Mexico, a pooling order makes it a 7/8 WI plus a 1/8 royalty (NMSA 70-2-17).
- A **Texas NPRI under a unit or allocation well** that hasn't ratified raises `NPRI_NOT_RATIFIED` (*Montgomery v. Rittersbacher*; allocation wells are unsettled). Ratifying the lease doesn't reduce a fixed NPRI (*ConocoPhillips v. Hahn*, 2024).
- **Double fractions** are flagged, never interpreted. After *Van Dyke* (2023), "1/2 of 1/8" in an older instrument is presumed to be a floating 1/2, but the presumption is rebuttable (*Clifton v. Johnson*, 2026) and the examiner decides.
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
- **No API key? Abstract in a chat.** The API is only needed for unattended or batch abstracting. In a Claude Code chat, `ptk abstract-prompt pages... --doc-id ... --county ... --state ...` writes the same instructions the API call uses; have Claude read the page images and write the JSON, then `ptk abstract-check answer.json --pages pages...` runs the same schema, quote and fraction checks. `ptk smoke-test --render-only` writes the sample deed for a chat test and `ptk smoke-test --answer answer.json` grades it against the eight facts (a chat abstract passed 8 of 8).
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

- **The AI extraction hasn't run against the live API yet.** The same prompt and checks passed through a chat (8 of 8 facts); `ptk smoke-test` is the one-command check once credentials are set.
- **The parser corpus is 264 forms, not the world.** Every case passes, but new county forms will turn up. Add each one to `parser_corpus.json` with the right answer, then fix the parser.
- **Same-name owners** (a father and son both "John Smith") are one owner to the ledger. Give one a distinguishing name in the takeoff ("John Smith Jr.").
- **Texas descriptions naming two surveys** keep only the first survey (and say so in the issues). Metes-and-bounds calls are captured as text, not plotted.
- **New Mexico lots** are matched by section; lot acreage must be compared by hand against the BLM survey.
- **Handwritten records**: no OCR engine here reads 1880s–1920s clerk hands reliably.
- **No review screen** for AI abstracts. Review happens in the spreadsheets and JSON. (Deals have a dashboard: `ptk deal serve`.)
- **The desk is single-user**, on 127.0.0.1, with no sign-in. Two people editing the same owner at once: the last save wins.

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
ptk/leasehold.py  lease chains: WI, ORRI burdens and NRI for lease and wellbore buys
ptk/deal/         the deal pipeline: event store, rules, intake, verify, tasks, drafts, outbox, dashboard
ptk/deal/crm.py   the CRM on the same store: import/export, API seam, hand-off to title, outreach, attachments
ptk/deal/web/     the CRM page (crm.html) and the icon set (icons.svg)
ptk/deal/titlerun.py   per-asset search plans and blank title files
ptk/deal/accuracy.py   ptk audit and the Accuracy page
ptk/deal/parser_corpus.json, parser_accuracy.py   labelled record forms and their scorer
docs/crm/         the CRM's original integration notes and record schema
ptk/schema/       instrument JSON schema
examples/         fictional takeoff, project, runsheet, map layers, sample answer, batch jobs, deals
tests/            unit and end-to-end tests
playbook/         the Permian Title Playbook page (built) and its source (src/build.py)
HANDOFF.md        builder's guide: specs, acceptance outputs, pitfalls, roadmap
```
