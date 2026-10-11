# Handoff: Permian Title Kit

This document is for the next builder, another Claude session or an engineer. It explains how to rebuild or extend the Permian Title Kit (`ptk`) and its companion Permian Title Playbook. It records:

- what the program is for
- the rules it must never break
- how each part works, in enough detail to rebuild it without the code
- the exact outputs that prove a rebuild is right
- the mistakes already made and fixed
- where improvements are expected

Parts are labeled one of two ways:

- **Spec**: required behavior that the tests and example outputs check.
- **Open**: a known gap or design choice where you should use your judgment and improve on what's here.

When this document and the code disagree, the code and its tests win. Fix the document.

---

## 1. Where things are

| Item | Location |
|---|---|
| Source code | GitHub `Broday6/permian-title-kit` (private), branch `main` |
| This document | `HANDOFF.md` at the repo root |
| User guide | `README.md` (commands, formats, ownership rules) |
| Playbook page (built) | `playbook/permian-title-playbook.html` (one self-contained page) |
| Playbook source | `playbook/src/` (HTML fragments, CSS, JS, `build.py`) |
| Published playbook | A private claude.ai artifact owned by the original account (`https://claude.ai/artifact/Ar48VthmtV4mJypWAuneQ7`). Ask the owner to share it, or rebuild it from `playbook/` |
| Acceptance tests | `tests/` (151 tests: 94 for the title kit, 2 for the parser corpus, 44 for the deal pipeline, 11 for the CRM; all passing) |
| Accuracy checks | `ptk audit` and the desk's Accuracy page (`ptk/deal/accuracy.py`); parser corpus `ptk/deal/parser_corpus.json` (264 labelled record forms) |
| CRM (acquisition side) | `ptk/deal/crm.py`, `ptk/deal/web/crm.html`; the old tool's notes and schema in `docs/crm/` |
| CI | `.github/workflows/tests.yml` (Python 3.10 and 3.12) |

**With repo access:**
1. Clone the repo.
2. Run `pip install -e ".[test,ai,xlsx]" && python -m pytest -q`. You should see `148 passed` (3 OCR tests skip where Tesseract or pdftoppm is missing).
3. Read sections 2, 9 and 12 of this document before changing anything. For the deal pipeline, read 5.16 and 5.17.

**Without repo access:** sections 3–8 are the rebuild spec, and section 8 is the acceptance test.

---

## 2. What the program is for

**The business.** The user runs a minerals-and-land company in the Permian Basin (Texas and New Mexico). An existing team handles acquisition: it finds land, identifies owners and contacts them to make deals. **Each signed deal is then handed to Claude for title follow-up.** Follow-up means:

1. Verify the seller owns what they sold.
2. Find and cure defects.
3. Assemble the closing package.
4. Record the instruments and notify operators.
5. Confirm that the first payment arrives.

The title tools automate the title-running steps behind step 1. `ptk deal` (section 5.17) follows each signed deal through all five steps, from intake to the first check. The acquisition team's own Landowner CRM (section 5.18) now runs on the same database and hands signed owners to `ptk deal`.

**What a senior title landman wants from software:** thorough and quick, with the judgment calls left to people. The run has seven steps:

| Step | What happens | Kit command |
|---|---|---|
| Plan | Which county, which years, which online index or courthouse, and the parent counties to search before the county was organized | `ptk plan`, `ptk counties` |
| Search | Every grantor/grantee variant of each name, run against the indexes | `ptk names` |
| Map | Parse the legal description. Confirm it lands on a real survey or section of the right size | `ptk legal`, `ptk mapcheck` |
| Read | OCR the instrument images | `ptk ocr` |
| Abstract | AI first-pass abstract into strict JSON, with evidence quotes checked against the OCR | `ptk extract`, `ptk batch`, `ptk smoke-test` |
| Check | Red-flag rules on the runsheet | `ptk check` |
| Chain | Replay conveyances in recording order with exact fractions | `ptk ledger` |
| Decimals | Unit or allocation-well revenue deck. Every tract must add to exactly 1 | `ptk deck`, `ptk calc` |

**Output is landman work product, not a title opinion.** Drilling, division order and acquisition opinions belong to the examining attorney.

---

## 3. Rules that must not change (Spec)

1. **Exact arithmetic.** Interests are `fractions.Fraction` from input to output and are never floats. Round only for display, to 8 places, half-up, using integer math.
2. **Flag; don't decide.** The software flags legal questions and never resolves them. These include Duhig, fixed vs floating NPRI, the meaning of a double fraction, heirship, whether a term interest has expired, NPRI ratification of pooling, and merger. Every flag names the instrument and says what to check.
3. **Nothing changes silently.** If an input can't be applied, it is flagged. This covers an over-conveyance, a wild deed, an unreadable fraction, a reservation row the model can't apply, and a lessor who isn't an owner. The program never drops or "fixes" an input.
4. **AI output is a draft with evidence.**
   - Every extracted fact cites OCR line IDs and quotes them verbatim, and the quote is checked.
   - Refusals and truncated answers are errors, never data.
   - A person reviews every field that changes ownership: fractions and their basis, reservations, parties and capacity, and flags.
5. **Records access.** Never scrape county portals. Use licensed vendor exports and APIs, such as TexasFile, CourthouseDirect, Enverus, county clerk subscriptions and abstract plants.
6. **Privacy.** Redact SSNs, birth dates and account numbers before sending a page to any AI API. Use API terms that don't retain or train on the data.
7. **Credentials** go in environment settings or secrets. Never put them in chat, code, commits or examples.
8. **Owner communications.** Every first contact and every substantive message (curative requests, transfer notices, decimal questions) is drafted for a person to approve. The user decided (Oct 2026) that **routine reminders may go out automatically**, and only under the policy in `ptk/deal/rules.json` `messaging`: email only, only to a party who already received a person-approved message on the same deal, never to a party marked `do_not_contact`, at most `max_reminders` per task, and withdrawn if the task closes first. Don't widen this without the user.
9. **Example data is fictional and labeled** ("FICTIONAL EXAMPLE", "Example", "Doe Stranger"). Never commit real ownership data.
10. **Dependencies.** The core has no third-party dependencies. Every heavier capability is an optional extra. Python 3.10 or later.

---

## 4. Rebuild skeleton (Spec)

```
ptk/
  __init__.py  __main__.py (calls cli.main)  cli.py
  fracs.py     dates.py     ledger.py   doi.py
  legal_tx.py  legal_nm.py  mapcheck.py names.py
  runsheet.py  counties.py  ocr.py      extract.py
  smoke.py     sample.py    xlsx.py    leasehold.py
  schema/instrument.schema.json
  deal/        __init__.py  store.py  rules.py  rules.json  record.py  intake.py  verify.py
               tasks.py  state.py  drafts.py  outbox.py  actions.py  export.py  server.py  cli.py
               titlerun.py  accuracy.py  parser_accuracy.py  parser_corpus.json
               templates/*.md   (conveyances, curative instruments, letters)
               crm.py  web/crm.html  web/icons.svg   (the Landowner CRM on the same store; the icon set)
docs/crm/      INTEGRATION.md  land-crm-schema.json   (the old CRM's notes, kept for reference)
examples/
  takeoff.csv  project.json  runsheet.csv  jobs.example.json  sample_extraction.json
  mapcheck/{tracts.csv, fictional_tx_surveys.geojson, fictional_nm_sections.geojson}
  deals/{tx-mineral.json, nm-leasehold.json, lease_chain.csv, deals.csv, statement.csv}
tests/
  test_math.py test_records.py test_regressions.py test_exports.py
  test_ocr.py  test_mapcheck.py test_smoke.py test_deals.py test_crm.py test_parser_corpus.py
playbook/      built page + src/ (see section 10)
.github/workflows/tests.yml   pyproject.toml   README.md   HANDOFF.md   .gitignore
```

**pyproject.toml.** Uses setuptools.

- name `permian-title-kit`, version 0.1.0, `requires-python >=3.10`, `dependencies = []`
- extras:
  - `ai = [anthropic>=1.0, pillow>=10, jsonschema>=4]`
  - `xlsx = [openpyxl>=3.1]`
  - `ocr = [pillow>=10, boto3>=1.34, rapidocr-onnxruntime>=1.3]`
  - `gis = [pyshp>=2.3]`
  - `test = [pytest>=8, openpyxl, pillow, jsonschema, pyshp]`
- script `ptk = "ptk.cli:main"`
- packages `ptk`, `ptk.deal`; package data `ptk/schema/*.json`, `ptk/deal/rules.json`, `ptk/deal/templates/*.md`, `ptk/deal/web/*.html`

**CI.** Matrix of Python 3.10 and 3.12:

```
sudo apt-get install -y tesseract-ocr poppler-utils
pip install -e ".[test,ai,xlsx]" jsonschema pyshp
python -m pytest -q
ptk deck examples/project.json
```

Tests that need Tesseract, pdftoppm, openpyxl or Pillow skip cleanly when the tool is missing. CI installs all of them, so nothing skips there.

---

## 5. Module specs

Each module is a set of pure functions returning data plus a list of flags. `cli.py` is a thin layer over them.

### 5.1 `fracs.py`: fractions in deed language (Spec)

- **`parse_fraction(text) -> Fraction`** accepts:
  - plain numbers and fractions: `3/16`, `3/16ths`, `0.1875`, `18.75%`, `18.75 percent`
  - mixed numbers and glyphs: `12 1/2%`, `12½%`, `½`, `1⁄8` (fraction slash)
  - trailing words: `3/16 royalty`, `1/16 NPRI`, `seven-eighths (7/8ths) working interest` (royalty, interest, NPRI, ORRI, NRI, WI, working/mineral/net revenue/leasehold interest)
  - leading words: `the usual one-eighth`, `an undivided 1/4`, `the 1/8` (each factor)
  - `.125`, `1-1/2%`, `twenty-five percent`, `twelve and one-half percent`
  - products: `1/2 of 1/8`, `1/2 x 1/8`, `1/2 × 1/8`
  - words: `one-half`, `an undivided one-sixteenth`, `one hundred twenty-eighth`
  - words with numerals: `one-half (1/2)`. If the words and numerals disagree, raise `FractionError`.
- **`read_fraction_text(text) -> FractionReading(text, value, words_value, numeral_value, consistent, problem)`.** The runsheet checker and AI verifier use this to catch "one-fourth (1/8)".
- **Normalization before parsing:**
  - fraction slash → `/`
  - strip commas
  - vulgar-fraction glyphs → `n/d`, with a space inserted after a digit so `12½` becomes `12 1/2`
  - drop trailing "royalty" or "interest"
  - split products on `\s+(?:of|x)\s+|\s*[×*]\s*` and multiply the factors
- **Words:** read each hyphenated group as one number first ("twenty-seven sixty-fourths" = 27/64), then try every split of the phrase into numerator (cardinal) and denominator (ordinal), so "one hundred twenty-eighth" and "three thirty-seconds" both work. Word sequences that aren't a number ("nine two") are refused, and a phrase that still reads as two different proper fractions is refused, never guessed.
- **`fraction_words(value)`** writes deed words with hyphen groups ("one one-hundred-twenty-eighth") and returns None unless they read back to the same value.
- **`to_decimal(value, places=8)`** rounds exactly, half-up, with integer math:
  ```python
  scale = 10**places; n, d = abs(value.numerator), value.denominator
  q = (n*scale*2 + d) // (2*d); whole, frac = divmod(q, scale)
  sign = "-" if value < 0 and q else ""
  return f"{sign}{whole}.{frac:0{places}d}"
  ```
- **`fmt(Fraction) -> "n/d"`.**

### 5.2 `dates.py` (Spec)

- `parse_date` accepts `1948-05-10`, `2019-1-5`, `5/10/1948`, `05-10-1948`, `1948/05/10`, `May 10, 1948`, `May 10,1948`, `May 10th, 1948`, `MAY 10, 1948`, `Sept. 3, 1952`, `10 May 1948`, `10th May, 1948`, `the 10th day of May, 1948` and `June 1, A.D. 1925`. Numeric dates are month-first. It uses explicit patterns and a month table, not `strptime` (which depends on the locale). It refuses a month and year with no day, and impossible dates (`2/30/1950`). It **refuses two-digit years** (`5/10/48`) with `DateError`, because `48` silently becoming 2048 corrupts the chain order.
- `iso()` returns `YYYY-MM-DD`. Every date is converted to ISO before it is sorted.

### 5.3 `ledger.py`: chain replay (Spec; the core of the program)

**Takeoff event**, one CSV row:

```
Event(seq, instrument, recorded, tract, depth, estate, kind, grantor, grantees, shares,
      interest, warranty=False, npri_kind=None, npri_value=None, notes="", npri_burdens="all")
```

| Field | Values |
|---|---|
| `estate` | `MI` (fraction of the whole mineral estate), `NPRI`, or `ALL` (everything the grantor holds, both minerals and NPRIs; for probates, heirship distributions and "all right, title and interest" deeds) |
| `kind` | `root` (patent or base-opinion owners), `convey`, or `reserve` (NPRI only) |
| `interest` | Absolute (`1/4` = 1/4 of the whole estate) or relative (`all`, `1/2 of grantor`, `1/2 of grantor's interest`) |

**State:**

- `Lot(owner, fraction, burdens: frozenset[group_id], source)`: a parcel of minerals plus the set of NPRI groups that burden it.
- `Position(tract, depth, lots, npris)`, with:
  - `minerals`: total per owner, nonzero only
  - `mineral_total`
  - `balance(owner)`
  - `burdened_share(group)`
- `Npri(owner, kind, value, source, group)`.
- `Replay(positions: dict[(tract, depth)] -> Position, flags)`.

**Algorithm**, `replay(events, depths=None, as_of=None)`:

1. **Order and scope.** Sort by `(recorded ISO, seq)`. Drop events recorded after `as_of`. Depth `ALL` fans out to every interval declared for the tract in the depths JSON (`{"T1": ["SURF-BASE_WOLFCAMP", "BELOW_BASE_WOLFCAMP"]}`). Without a depths file, each tract has one interval, `ALL`.
2. **Names.**
   - `name_key`: uppercase, remove `.` and `,`, collapse whitespace.
   - The first spelling seen in replay order is the canonical name.
   - A later variant is mapped to it and flagged `NAME_VARIANT`.
   - Use `dataclasses.replace` and never mutate the input events.
3. **Mineral conveyance (`MI` convey):**
   - Resolve the amount against the grantor's balance.
   - Balance is 0 → `CHAIN_GAP`; nothing moves (a wild deed).
   - Amount is more than the balance → `DUHIG_CANDIDATE` if `warranty=Y`, otherwise `OVER_CONVEYANCE`. Only the balance moves.
   - **Take the amount pro rata from each of the grantor's lots, so NPRI burdens travel with the minerals:**
     ```python
     for lot in [x for x in pos.lots if x.owner == grantor]:
         take = amount * lot.fraction / balance
         lot.fraction -= take
         for g, s in zip(ev.grantees, ev.shares):
             pos.lots.append(Lot(g, take*s, lot.burdens, ev.instrument))
     ```
4. **`MI` reserve → `MI_RESERVE_IGNORED`.** Nothing moves. Model a mineral reservation by conveying less.
5. **NPRI reserve or root:**
   - Create a group with id `f"{instrument}#{seq}"`.
   - Choose the burdened lots with `npri_burdens`:
     - `all`
     - `conveyed`: lots created by this same instrument and not owned by the grantor
     - `retained`: the grantor's lots
     - `owners:A;B`
   - Tag each chosen lot with the group id.
   - Flags: `NPRI_REVIEW` (always: confirm fixed vs floating and the scope), `NPRI_BURDENS_UNREADABLE`, `NPRI_BURDENS_NOTHING`, `NPRI_INCOMPLETE` (missing kind or value).
6. **NPRI convey** moves NPRI holdings; the group id stays the same.
7. **`ALL` estate** moves minerals and NPRIs together. It requires a relative interest; an absolute fraction is flagged `ALL_NEEDS_RELATIVE_INTEREST`.
8. **End of replay:**
   - Consolidate lots.
   - Flag `MERGER_REVIEW` when an owner holds lots burdened by an NPRI group they also own.
   - Flag `MINERALS_NOT_WHOLE` when a position's minerals don't add to 1.
   - Also flagged where they occur: `UNKNOWN_ESTATE`, `UNREADABLE_FRACTION`.

**`load_events(csv)`:**
- Grantees and shares are `;`-separated. Shares default to equal and must add to 1.
- Dates are converted to ISO.
- Errors report the CSV row number.

**`ownership_table`** returns columns `tract, depth, owner, estate, fraction, kind, burdens`.

**Open:**
- Same-name owners, such as a father and son both named John Smith, collapse into one owner.
- Term interests are flagged by the runsheet checker but not modeled in the ledger. The ledger has no reversion events.
- Executive rights, bonus and delay rentals aren't tracked as separate attributes.

### 5.4 `doi.py`: the unit deck (Spec)

**Data types:**
- `Lease(lease_id, tract, lessors, royalty, lessees{name: WI share}, orris{name: fraction of 8/8}, depth="ALL")`. `from_dict` validates that lessee shares add to 1.
- `participation(weights)` normalizes the weights, which are tract acres in the unit or completed lateral feet per tract, into exact factors.
- `REVENUE_TYPES = (RI, NPRI, ORRI, NRI, UMI)`. WI is a cost row and isn't counted toward the revenue total.

**`build_deck(replay, leases, tract_factors, depth="ALL", state="TX", pooled=True, npri_ratified=None)`** works per tract:

1. **Match lessors to owners** by `name_key`.
   - A lessor who isn't an owner → `LESSOR_NOT_OWNER`.
   - An owner who is lessor under two leases → `DOUBLE_LEASE`; the first lease is used.
2. **Group NPRIs** by group id: kind and total value.
3. **Leased owner, `m` minerals, royalty `r`.** For each lot and each burden group on it:
   - `share = lot.fraction` for a fixed group, or `lot.fraction * r` for a floating group
   - add `share` to the group's `base`
   - add `share * group.total` to the owner's burden

   Then:
   - **RI** = `m*r − burden`. A negative result → `NPRI_EXCEEDS_ROYALTY`.
   - **NPRI** row per holder = `npri.value * group.base`.
4. **Unleased owner:**
   - **TX:** `UMI m` plus `WI m`. A Texas unleased owner is a cost-bearing cotenant, not a royalty owner.
   - **NM:** `UMI m/8` (royalty) plus `UMI 7m/8` plus `WI 7m/8`, from the pooling order under NMSA 70-2-17.
   - Flag `UNLEASED`. If NPRIs burden that owner's lots, also flag `NPRI_ON_UNLEASED`; the deck doesn't carve them out.
5. **Per lease, with `m` = minerals the lease covers:**
   - **ORRI** = `o * m` (proportionately reduced).
   - **WI** = `m * share`.
   - **NRI** = `m * share * (1 − r − ORRI total)`.
   - Flags: `BURDENS_EXCEED_LEASE`, `NO_LESSEE`.
6. **Texas pooled unit:** every NPRI holder not listed as ratified in `npri_ratified` → `NPRI_NOT_RATIFIED` (*Montgomery v. Rittersbacher*).
7. **Unit decimal** = tract decimal × participation factor.
   - Each tract's revenue rows must add to exactly 1, or the tract is flagged `DECK_NOT_WHOLE`.
   - Factors must add to 1, or the deck is flagged `PARTICIPATION_NOT_WHOLE`.
   - A tract with no ledger position → `NO_TITLE`.
   - A bad NPRI kind → `NPRI_KIND`.

**Open:**
- The texts disagree on how an NPRI on unleased minerals is paid.
- Lease depth severance is matched only by depth label.
- Lessor royalty may differ by product (oil vs gas). The schema captures this, but the deck uses one royalty per lease.

### 5.5 `legal_tx.py`: Texas abstract, survey, block and section (Spec)

**`parse(text, county=None) -> TxDescription`.** Fields:
- `county`, `abstract`, `survey_code`, `survey_name`, `survey_no`, `block`
- `township` (e.g. `2S`)
- `section`, `sections[]`
- `aliquots` (e.g. `["N2", "SE4"]`)
- `acres`, `save_and_except[]`, `issues[]`

**Keys:**
- `abstract_key()` returns `TX|MIDLAND|A-123`.
- `survey_key()` returns `TX|MIDLAND|T&P|BLK 33|T2S|SEC 12`.
- `survey_keys()` returns one key per section.

**Railroad survey codes:** CCSD&RGNG, GC&SF, GH&SA, H&TC, H&GN, I&GN, T&STL, T&P, W&NW, plus PSL, CSL and UL.

**Traps already handled:**
- The abstract regex `\bA-\s?(\d+[A-Z]?)\b` is **case-sensitive**, so "a 160 acre tract" isn't read as an abstract.
- The block regex has a negative lookahead for of/in/no/on/or/at.
- "Sections 12 and 13" keeps both sections; so do "Sections 1, 2, and 3" and sections from every "Section" marker, cut at BEGINNING/COMMENCING so metes-and-bounds calls aren't read as sections.
- County: "Midland Co.", "County of Midland"; not "County School Land". With several counties named (a recital "recorded in Reeves County"), the Permian county nearest after the first section or survey mention is used, and an issue names them all.
- Lettered PSL blocks ("Block A-38") aren't abstracts; the survey named first wins.
- Aliquots: "North one-half", "North half", "N. 1/2", compact "N2 of", repeats kept ("NW4 NW4"); parenthetical echoes ("(N/2)") ignored. Acres: "160 ac.", "80 1/2 acres". "LESS" alone starts an exception. Common OCR slips ("Sectlon", "B1ock") are corrected.
- "T-2" with no North/South is kept and reported as an issue.

**Open:** a description naming two surveys keeps only the first (and says so in the issues). Metes-and-bounds calls are captured as text and not plotted.

### 5.6 `legal_nm.py`: New Mexico PLSS (Spec)

**`parse(text) -> PlssDescription(township "23S", range "31E", sections[SectionPart], issues)`.**
- `SectionPart` = `(section, whole, quarter_quarters, sub_divisions, lots, aliquot_text, unread)`.
- `keys()` returns `NM|23S|31E|15|NESE`.

**Aliquots:**
- Aliquots are written smallest-to-largest ("N/2 SE/4" means the north half of the SE quarter). **Reverse them before expanding.**
  - "N/2 SE/4" → {NESE, NWSE}
  - "W/2 W/2" → four quarter-quarters
- Divisions finer than a quarter-quarter get labels like `N2NENW`.
- Before reading a token as an aliquot, validate it with a full match (`_ALIQUOT_ONLY`), so letters inside ordinary words are never read as aliquots.
- Word forms are normalized ("north half" → `N/2`).

**Section layouts handled:** "… of Section 6" and "Section 6: …" styles (mixed in one description: each section takes the text before it, or else the text after it up to the next ";"), "Sections 15 and 22", "All of Section 6", lot ranges, "Lots One (1) and Two (2)", compact "10-18S-34E", "Twp."/"Rge.", and parenthetical aliquot echoes. More than one township or range is reported as an issue.

**`regular_area()`** returns the area as a fraction of a regular 640-acre section (¼ per quarter, ½ per half), ignoring lots.

**Open:** lot acreage comes from the BLM survey plat and isn't modeled.

### 5.7 `mapcheck.py` (Spec)

Checks every parsed description against a polygon layer in longitude/latitude: GeoJSON, or shapefile through pyshp.

**Matching:**
- **TX:** by abstract, or by block and section when there's no abstract.
  - Default fields: `ABSTRACT_N`, `LEVEL2_BLO` (alias `LEVLE2_BLO`, a misspelling that appears in the data), `LEVEL3_SUR`.
- **NM:** by CadNSDI `PLSSID` and `FRSTDIVNO`. `plssid("23S","31E")` returns `NM230230S0310E0`.

**Statuses:**

| Status | Meaning |
|---|---|
| `OK` | Matched, and the acreage is within tolerance |
| `NOT_FOUND` | No feature matches |
| `AMBIGUOUS` | Different surveys share the section, or the abstract matches several sections |
| `ACREAGE_MISMATCH` | Layer acres × aliquot fraction differ from the stated acres by more than the tolerance (default 5%). Only checked when acres are stated |
| `OUTSIDE_PERMIAN` | Outside lon −106 to −99.5, lat 29 to 35 |
| `UNPARSED` | The description couldn't be parsed |

**Area:** spherical polygon area (Chamberlain–Duquette), with R = 6,371,008.8 m:

```python
total += radians(lon2-lon1) * (2 + sin(radians(lat1)) + sin(radians(lat2)))
area = abs(total) * R*R / 2
```

Clockwise rings come out positive. Holes are subtracted. Shapefile ring orientation must be handled.

**Other behavior:**
- Projected layers are refused; reproject with `ogr2ogr -t_srs EPSG:4326`.
- `--out` writes MultiPolygon GeoJSON with each tract's status.

**Open:** the default field names come from documentation and haven't been confirmed against downloaded RRC or CadNSDI layers.

### 5.8 `names.py`: index search variants (Spec)

**`search_variants(raw) -> {input, kind, surname_soundex, searches, notes, [parties]}`.**

**Name shapes recognized:**
- et ux, et vir, et al
- capacity words (executor, trustee, …, with a note to also search "signed for X")
- trusts, "Heirs of", "Estate of"
- entities: strip suffixes, drop a leading THE, L.L.C. → LLC
- "Last, First" vs "First Last, Jr."
- couples split on "and wife" or "et ux \<Name\>"
- status phrases removed from the name and kept as notes: "a widow", "a single man", "Deceased", "dealing herein with her sole and separate property", "husband and wife"
- capacity "of X" or "for X" ("Attorney-in-Fact for Mary Jones"), also on a corporate trustee ("Bank of America, N.A., Trustee of the J. Smith Trust")
- surname particles ("Juan de la Cruz" → DE LA CRUZ JUAN); all-caps index order ("SMITH J W")
- "&" in entity names searched three ways: dropped, kept and spelled AND

**Search terms produced:** nickname groups (Jno/John/Jack, Wm/William, Chas/Charles, Mary/Mollie/Polly, …), first initial only, and the surname wildcard `SMITH *`. Soundex is American Soundex.

**Open:** add a learned variant library built from your own index hits, plus a corporate succession table (mergers and name changes).

### 5.9 `runsheet.py`: runsheet and red flags (Spec)

**`COLUMNS`:** row, state, county, record_series, volume, page, instrument_no, instrument_type, instrument_date, filed_date, grantor, grantee, legal_description, interest_conveyed, reservations_exceptions, lease_royalty, lease_primary_term, depth, acknowledged, spouse_joined, image_file, notes, reviewed_by, reviewed_on.

**`image_key(...)`:**
- `TX-REEVES-OPR-2019-012345.pdf` (a `YYYY-NNNN` instrument number is split into year and number)
- `TX-MIDLAND-DR-0512-0033.pdf` (volume and page, zero-padded)

**`RowFlag(row, code, severity ∈ {stop, review, info}, message)`.**

**Text rules** run on interest + reservations + notes + legal description, both raw and with parenthetical numerals removed. That way "one-half (1/2) of the one-eighth (1/8)" still matches.

| Code | Severity | Trigger |
|---|---|---|
| DOUBLE_FRACTION | review | `n/d of [the] [usual] n/d` or "… interest in and to the …", or "one-half of the one-eighth" (*Van Dyke*, 2023, a rebuttable presumption). Not "1/2 of 8/8" (the whole) |
| TERM_INTEREST | review | "for a period/term of … years", or years … "as long thereafter". Not a lease's own primary term, and not a recited lease's term |
| BLANKET_CONVEYANCE | review | "all of my/our/grantor's … interest/lands/minerals … in ___ County", "owned by Grantor in ___ County", "whether or not described". Not when the matched text names a specific tract (section, survey, block, lot, aliquot) |
| MOTHER_HUBBARD | info | strips and gores, "any and all other lands", adjacent or contiguous |
| NPRI_LANGUAGE | review | non-participating, NPRI, non-executive, without the right to lease, executive right, bonus and rentals |
| POWER_OF_ATTORNEY | review | attorney-in-fact |
| FIDUCIARY_CAPACITY | review | executor, administrator, trustee, guardian, receiver, conservator, personal representative, Admx./Admr./Exrx. (grantor column too). Not the trustee named in a deed of trust or mortgage |
| RELINQUISHMENT_ACT | review | "relinquishment act", "mineral classified" |
| CORRECTION | info | correction, corrective, re-recorded (instrument type too) |
| TOP_LEASE | review | top lease (instrument type too); subject to an existing, prior or outstanding lease; a lease made "subject to that certain … lease" |
| DEPTH_LIMITED | info | below/above, base of the ___, stratigraphic equivalent, from the surface to, "below 7,500 feet", "formation", "zone only" |

**Row checks:**

| Area | Flags (severity) |
|---|---|
| Images and recording | `NO_IMAGE` (stop), `IMAGE_MISSING` (stop, with `--images`), `NO_RECORDING_REF` (stop) |
| Dates and fractions | `BAD_DATE` (stop, either date column), `FILED_BEFORE_EXECUTED` (review), `WORD_NUMERAL_MISMATCH` (stop) |
| Instrument type | `QUITCLAIM` (info: no warranty, so no Duhig); `LIEN_OR_LITIGATION` (review: deed of trust, judgment, lis pendens, UCC and similar; releases, satisfactions, discharges and reconveyances excluded) |
| Execution | `UNACKNOWLEDGED` (TX: may be cured under CPRC §16.033(a)(8)/(c) once of record 2 years, examiner confirms; NM: not "of record", §14-8-4); `SPOUSE_NOT_JOINED` (stop, NM only: void if community real property, NMSA 40-3-13, unless a recorded POA §47-1-7 or written ratification) |
| Heirship | `AFFIDAVIT_OF_HEIRSHIP` (TX: info stating the prima facie date, filed + 5 years, Est. Code §203.001; evidence, not a cure; record in the land county and the domicile county; NM: review); `SMALL_ESTATE_AFFIDAVIT` (stop: TX §205.006 homestead only; NM §45-3-1201 personal property, §45-3-1205 residence) |
| Leases | `LEASE_ROYALTY_MISSING` (review), `LEASE_TERM_MISSING` (review) |

Also: `check_file(path, images_dir=None) -> (rows, flags)` (refuses a file missing the row, state, county, instrument_type, grantor or grantee columns) and `write_template(path)`. The takeoff template is written by the CLI from the takeoff columns.

### 5.10 `counties.py`: where the records are (Spec for structure, Open for data)

**Data types:**
- `County(name, state, fips, organized, texasfile_index_from, chd_images_from, chd_historical_from, portal, parents, notes)`
- `Parent(county, until, note, verify)`
- `COLLECTED_ON = "2026-10-01"`

**Functions:**
- `verification_urls(county)` returns the TexasFile page `texas-land-records-coverage/<slug>-county-clerk/` and the CourthouseDirect page `PropertySearch/{Texas|NewMexico}/<Name>`.
- `write_checklist(path)` writes a CSV worksheet.
- `load_overrides(path)` reads the `confirmed_*` columns back in.
- `plan_search(county, start_year, table=None)` returns plain-language lines: which online index covers which years, the biggest online gap, and the parent counties to search.

**Data at handoff.** Years are TexasFile index / CourthouseDirect images / CourthouseDirect historical, then the parent counties. **All of it is unverified:** it was read from search-engine summaries.

| County | Data |
|---|---|
| Reeves | 48389; org. 1884; 1874/1885/1876; parent Pecos |
| Loving | 48301; org. 1931; 1905/1998/1905; parents Reeves before 1931, Tom Green before 1893 (verify) |
| Ward | 48475; org. 1892; 1935/1873/1873 |
| Winkler | 48495; org. 1910; 1883/1965/1883; parent Ward (verify) |
| Pecos | 48371; org. 1875; 1862/–/1884; parent Presidio |
| Culberson | 48109; org. 1911; 1854/–/1882; parent El Paso |
| Midland | 48329; org. 1885; 1884/1979/1884; parent Tom Green |
| Martin | 48317; org. 1884; 1915/1876/1883 |
| Howard | 48227; org. 1882; 1882/1983/1881 |
| Glasscock | 48173; org. 1893; 1893/1918/1877 |
| Reagan | 48383; org. 1903; 1903/2010/1886 |
| Upton | 48461; org. 1910; 1984/1900/1887 |
| Andrews | 48003; org. 1910; 1881/1914/1910 |
| Ector | 48135; org. 1891; 1887/1958/1888; parent Midland |
| Crane | 48103; org. 1927; 1888/2012/1888 |
| Crockett | 48105; org. 1891; TexasFile 1982 only |
| Irion | 48235; org. 1889; 1983/1983/– |
| Dawson | 48115; org. 1905; 1970/1991/1900 |
| Borden | 48033; org. 1891; 1877/–/1880 |
| Gaines | 48165; org. 1905; 1881/2010/1881 |
| Eddy NM | 35015; org. 1889; CourthouseDirect images from 1906 |
| Lea NM | 35025; org. 1917; CourthouseDirect images from 1988; parents Chaves (north) and Eddy (south) before 1917 |
| Chaves NM | 35005; org. 1889 |
| Roosevelt NM | 35041; org. 1903 |

The `portal` field holds each county clerk's search URL where one was found.

**Open:**
- **The user said they will supply their own county data.** Load it through `ptk counties --checklist` and `--overrides`, or replace the table.
- More counties may be needed: Terrell, Schleicher, Sterling, Mitchell, Scurry, Lynn, Terry, Yoakum, Cochran, and NM De Baca.

### 5.11 `ocr.py` (Spec)

**Engines:**

| Engine | Use | How |
|---|---|---|
| Tesseract | Default | CLI `tesseract <img> - --psm 4 tsv`. Group words by (block, par, line); line confidence = mean word confidence / 100 |
| Textract | Optional | boto3 `detect_document_text`. Keep LINE blocks, sorted by relative Top, then Left |
| RapidOCR | Fallback only | Merge boxes into rows (`_merge_rows`): left-to-right within a row, rows top-to-bottom |

**Processing:**
- `clean()` applies NFKC normalization (folds full-width punctuation) and collapses whitespace.
- `split_pdf(pdf, out_dir, dpi=300)` runs `pdftoppm -r <dpi> -gray -png` to the prefix `<stem>-ptk-split`. pdftoppm names its output `-1`, `-01` or `-001` depending on the page count, so rename the files to `<stem>-p<N>.png`.
- `write_page` writes `<stem>.txt`, one OCR line per line, which `extract` reads, and `<stem>.ocr.json` with confidences and boxes.
- `run(inputs, out_dir, engine, pages_dir, dpi)` lists lines below the `--threshold` confidence (default 0.80).

**Measured result:** Tesseract read every line of the rendered sample deed correctly. RapidOCR ran words together ("MINERALDEED") and misread "$10.00".

**Open:** no engine here reads 1880s–1920s clerk handwriting. Send the image straight to the vision model, and review every field.

### 5.12 `extract.py`: AI first-pass abstract (Spec)

**Constants:**

| Constant | Value |
|---|---|
| `DEFAULT_MODEL` | The most capable current Claude model. Check the Anthropic models overview when you rebuild; IDs change |
| `FALLBACK_MODELS` | The models that support server-side refusal fallback |
| `FALLBACK_BETA` | `"server-side-fallback-2026-07-01"` |
| `NO_EFFORT_PREFIXES` | Older models that reject `output_config.effort`. Omit effort for them |
| `MAX_REQUEST_BYTES` | 30 MB (the API limit is 32 MB) |
| `MAX_EDGE` | 2576 px. Downscale larger images with Pillow; an unreadable image raises `ValueError` |

**Request, `build_params(pages, *, doc_id, county, state, model=DEFAULT_MODEL, effort="high", max_tokens=16000)`:**
- `system`: one text block holding `SYSTEM_PROMPT`, with `cache_control: {"type": "ephemeral"}` (prompt caching).
- `user`:
  - one image block per page (base64), or a `document` block for a PDF
  - then the OCR text with stable line IDs (`p1.l1`, `p1.l2`, …)
  - then the task: doc id, county and state
- `output_config = {"effort": effort, "format": {"type": "json_schema", "schema": <instrument schema>}}` (structured outputs).

**Call:** `client.beta.messages.create(**params, betas=[FALLBACK_BETA], fallbacks="default")` for fallback-capable models, otherwise `client.messages.create`. The Batches API doesn't support fallback.

**`SYSTEM_PROMPT`** (rebuild verbatim or improve; keep every rule):
1. Extract only what is on the pages; null or an empty list when a value is absent.
2. Copy names, fractions, legal descriptions and recording references exactly. For fractions, copy both the words and the numerals.
3. Never guess an illegible character. Write `[illegible]` and add an `illegible` flag.
4. Evidence = page number, OCR `line_ids`, and a verbatim quote from those lines. With no OCR, leave `line_ids` empty and quote the image.
5. Fractions are factors ("1/2 of 1/8" = two factors) plus the basis exactly as stated. `fixed_or_floating_signal` reports only what the wording suggests. Never decide fixed vs floating, Duhig or heirship.
6. Flag:
   - word/numeral mismatch
   - double fractions
   - term language
   - blanket county conveyances
   - spouse not joined
   - unclear signing capacity
   - interlineations
   - missing pages
   - no acknowledgment
   - marginal notations
   - possible over-conveyance
7. List every reservation, exception, save-and-except and subject-to clause separately with its full text.
8. Extract only the named instrument when a page holds parts of two. Flag text that belongs to another instrument.
9. Party IDs are P1, P2, …, used consistently in `party_refs`.

**Checks on every answer**, in `verify(extraction, lines, threshold=0.85)`:
- `UNKNOWN_LINE_ID`
- `EVIDENCE_NOT_CITED`: a quote with no line IDs when OCR existed
- `QUOTE_NOT_IN_CITED_LINES`: difflib longest-match ratio below 0.85 against the cited lines, whitespace-normalized
- `WORD_NUMERAL_MISMATCH`: re-read with `read_fraction_text`
- local JSON Schema validation through `schema_errors`, when jsonschema is installed

**Result shape:** `_result` returns `{doc_id, model, stop_reason, usage}` plus either `error`, or `extraction`, `schema_errors` and `checks`. A refusal, a `max_tokens` cut-off, a missing text block and invalid JSON each become `error`, never data. `ptk extract` exits 1 on `error` and warns when `checks` is non-empty.

**Batch:**
- `submit_batch(client, jobs)` returns the batch id. Each request's `custom_id(doc_id)` replaces anything outside `[A-Za-z0-9_-]` with `_` and caps it at 64 characters.
- `collect_batch(client, batch_id, lines_by_id)` re-runs `verify` on every result.
- Batches cost half the price and return within 24 hours.

**The schema**, `ptk/schema/instrument.schema.json`, is **strict**:
- `additionalProperties: false` on every object, and every property is listed in `required` (nullable where optional).
- No top-level `$schema` key.
- 56 nullable fields.

Top level: `doc_id`, `instrument_type` (an enum of 33 values, e.g. `mineral_deed`, `oil_gas_lease`, `affidavit_of_heirship`, `ratification`, `production_sharing_agreement`), `instrument_title_verbatim`, `recording`, `execution`, `parties`, `conveyances`, `reservations_exceptions`, `subject_to`, `legal_descriptions`, `lease_terms`, `references`, `flags`.

`$defs`:

| Definition | Fields |
|---|---|
| `evidence` | `page`, `line_ids`, `quote` |
| `fraction` | `text_verbatim`; `factors[{num, den}]`; `basis` (`of_whole_mineral_estate`, `of_grantors_interest`, `of_royalty`, `of_production_8_8`, `of_leasehold_8_8`, `of_working_interest`, `of_lease_royalty_as_reserved`, `unstated`); `all_of_grantors_interest`; `numeral_word_consistent`; `fixed_or_floating_signal` |
| `depth`, `term` | — |
| `party` | role, entity type, capacity, marital descriptor, spouse, signed |
| `instrument_ref` | — |
| `legal_description` | `tx_survey`, `plss`, `metes_and_bounds`, stated acres, save-and-except |
| `lease_terms` | royalty oil/gas, valuation, cost-free language, Pugh clause, depth limitation, retained acreage, pooling, shut-in, cessation days, … |

`examples/sample_extraction.json` validates with 0 errors and is the reference answer for the sample deed.

### 5.13 `sample.py` and `smoke.py`: one-command live check (Spec)

**`sample.py`**
- Renders a fictional 1948 Reeves County mineral deed: 1700×2200 px, Liberation Mono 30 pt, 52 px line spacing.
- The page is stamped "SAMPLE DOCUMENT - FICTIONAL - FOR SOFTWARE TESTING ONLY".
- It contains: Vol. 88 Pg. 77; Adams Example to Baker Example; "an undivided one-half (1/2)" of the minerals; a reservation of "one-half (1/2) of the one-eighth (1/8)" royalty for 15 years and as long thereafter; Section 12, Block 33, T-2-S, T&P RR Co. Survey, A-123.

**`smoke.py`**
- `run_smoke(client, work_dir, model, effort)` renders the page, OCRs it, extracts it, validates the answer against the schema and checks 8 facts:
  1. type `mineral_deed`
  2. Vol 88 Pg 77
  3. parties ADAMS EXAMPLE and BAKER EXAMPLE
  4. a 1/2 mineral conveyance
  5. reservation factors {1/2, 1/8}
  6. term interest
  7. `double_fraction` flag
  8. Sec 12, Blk 33, abstract ending 123
- Returns `(ok, report_lines)`.
- `ptk smoke-test` exits 2 when there are no credentials.

**Chat route (no API key).** `extract.chat_prompt(pages, doc_id, county, state)` returns the same instructions and numbered OCR lines the API request carries (`ptk abstract-prompt`); `extract.check_answer(extraction, pages, doc_id, source="chat")` runs the schema, quote and fraction checks on JSON written in a Claude chat (`ptk abstract-check`). `ptk smoke-test --render-only` writes the sample page for a chat run and `--answer FILE` grades it with `smoke.grade_answer` (no API call). A chat abstract passed 8 of 8 in Oct 2026. The API is needed only for unattended or batch abstracting.

### 5.14 `xlsx.py` (Spec)

**`export_runsheet(rows, flags, out, ...)`**
- Sheets: Runsheet, Flags, About.
- Each image file name is a hyperlink relative to the workbook.
- Each row is colored by its worst flag: stop `F4CCCC`, review `FCE8B2`, info `D9EAD3`.
- Header is frozen and auto-filtered.

**`export_deck(deck, weights, out, title="Unit deck")`**
- **By owner:** `SUMIFS` over By tract, a revenue total, and the check `=IF(ROUND(C{n},8)=1,"OK","NOT WHOLE")`.
- **Participation:** the weights are **live inputs**, blue on yellow; factor `=B{i}/SUM($B$2:$B${n+1})`.
- **By tract:** owner, type, tract, depth, tract decimal (a value from the title work), the exact fraction as text, the factor through INDEX/MATCH, and unit decimal = tract decimal × factor.
- **Flags** and **About** sheets.
- `fullCalcOnLoad=True`, Arial, number format `0.00000000`.
- Verified in headless LibreOffice: 59 formulas, 0 errors, values equal to the exact results.

### 5.15 `cli.py` (Spec)

| Command | Arguments | Exit code |
|---|---|---|
| `calc` | `--acres --owned --royalty [--unit a/b] [--npri-fixed] [--npri-floating]` | 0 |
| `ledger` | `takeoff [--depths] [--as-of] [--out]` | 0 |
| `deck` | `project [--out csv] [--xlsx]` | **2** if any tract or the participation isn't whole |
| `legal` | `text [--state] [--county]` (JSON out) | 0 |
| `names` | `names...` (JSON out) | 0 |
| `plan` | `--county --from YEAR [--overrides]` | 0 |
| `counties` | `[--checklist csv]` | 0 |
| `check` | `runsheet [--images] [--out] [--xlsx]` | **1** if any stop flag |
| `template` | `path [--kind runsheet\|takeoff]` | 0 |
| `mapcheck` | `tracts --layer (repeatable) [--fields json] [--tolerance 0.05] [--out]` | **1** if any tract isn't OK |
| `ocr` | `inputs... --out [--engine] [--pages-dir] [--dpi 300] [--threshold 0.8]` | 0 |
| `extract` | `pages... --doc-id --county --state [--ocr-dir] [--model] [--effort] [--max-tokens] [--out]` | 1 on error |
| `smoke-test` | `[--model] [--effort] [--work-dir]` | **2** without credentials; 1 if a fact fails |
| `batch` | `submit jobs.json` / `collect <id> --jobs jobs.json [--out]` | |

Bad input (`FractionError`, `ValueError`, `KeyError`, `RuntimeError`) prints `error: …` and exits 1 instead of a traceback. Reset SIGPIPE to the default so `ptk … | head` doesn't print a traceback either. Credentials count as present when `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` is set, or when `ant auth status` succeeds.

### 5.16 `leasehold.py`: lease chains for lease and wellbore buys (Spec)

A lease buy is verified against the lease's own chain, not the mineral ledger's. The math is the ledger's math, so the module translates rows and reuses `ledger.replay`:

| Lease row `kind` | Ledger equivalent |
|---|---|
| `root` (the lease; `royalty` column = lessor royalty) | `MI` root: the lessees take 8/8 of the WI |
| `assign` | `MI` convey (pro rata from each WI piece, so ORRI burdens travel) |
| `orri` | `NPRI` reserve, `npri_kind=fixed`, `npri_value=orri_value`; burdens default to `conveyed` for a reservation, `retained` for a grant to `assignees` |
| `orri_assign` | `NPRI` convey |
| `all` | `ALL` convey |

- An ORRI is a fraction of 8/8 of production from the WI it burdens (proportionately reduced), exactly like a fixed NPRI on the minerals it burdens.
- **NRI(owner)** = Σ over the owner's WI pieces of `WI × (1 − royalty − Σ ORRI totals of the groups on that piece)`.
- Flags are renamed to leasehold terms: `NPRI_REVIEW→ORRI_REVIEW`, `NPRI_INCOMPLETE→ORRI_INCOMPLETE`, `NPRI_BURDENS_*→ORRI_BURDENS_*`, `MINERALS_NOT_WHOLE→WI_NOT_WHOLE`; other codes keep their names with "minerals" reworded to "working interest".
- Added: `ROYALTY_MISSING` (no royalty on the root row; NRI isn't computed from a guess), `BURDENS_EXCEED_LEASE` (an owner's NRI < 0), and `LEASE_STATUS_REVIEW` once per lease naming it (is it still in force?).
- Interests are of the lease (8/8 of the lease). **Open:** a lease covering less than all of the minerals; depth severance by label only.

### 5.17 `ptk deal`: deal follow-up pipeline (Spec)

Follows each signed deal from intake to the first check. Built Oct 2026 from the user's answers to the 12.1 questions:

| Question | Answer | What it changed |
|---|---|---|
| Deal type | Varies: mineral buys, lease buys with horizontal wells, or both; Texas and New Mexico run title very differently | A deal holds any mix of assets: `mineral`, `royalty`, `orri`, `leasehold`, `wellbore`. Checklists are per state and asset kind |
| Where deals live | A basic local CRM today; wants something more official | Deals live in an append-only, hash-chained SQLite log with a local dashboard; CSV/JSON in, CSV/workbook out. A CRM import is still open (12.1) |
| Owner follow-up | Auto-send routine reminders | Section 3 rule 8 and `rules.json` `messaging` |
| Attorney threshold | Only flagged deals | `attorney.mode = "flagged"`: an opinion is required only when a legal-question task was raised and not resolved by later data |

**Store (`store.py`).** One table `events(id, deal_id, ts, actor, type, payload JSON, prev_hash, hash)`. SQLite triggers abort any UPDATE or DELETE. `hash = sha256(prev_hash ⏎ id ⏎ deal_id ⏎ ts ⏎ actor ⏎ type ⏎ canonical payload)`; `Store.verify()` (`ptk deal verify-log`) recomputes the chain. Writes take `BEGIN IMMEDIATE` plus an in-process lock (the dashboard is multi-threaded); a payload can be a function of the open transaction, which is how T#/M#/OP# are numbered (`next_number`) and how a task or a send attempt is claimed exactly once (`has_event`), even with two processes on one file. Fractions are stored as `"n/d"` strings, money as decimal strings. Default path `$PTK_DEALS_DB` or `./deals.db`; actor `--by`, `$PTK_USER` or the login name.

**State (`state.py`).** `fold(events)` rebuilds a `DealState` (record, tasks, verification results, opinions, package, recordings, notices, division orders, expected payments, payments, messages, drafts). Nothing is cached, so status can't drift from the log. Amending the deal drops the verification of every asset that changed (all of them if the effective date, buyer or sellers changed), and the expected payments of removed assets; intake resolves the verify tasks of removed assets. `evaluate()` returns the six stages with blockers:

| Stage | Done when |
|---|---|
| 1 intake | Intake checks ran and no open stop/review task has stage `intake` |
| 2 verify | Every asset verified OK, or every ownership task on it (`SELLER_SHORTFALL`, `NRI_SHORTFALL`, `SELLER_NOT_IN_CHAIN`, `SELLER_CONVEYED_AFTER_EFFECTIVE`) decided by a person; no open verify-stage task |
| 3 curative | No open curative task; if an opinion is required, one is recorded and every legal-question task is closed |
| 4 closing | Package drafted, approved, signed, funds released |
| 5 record | Recorded in every (state, county) of the deal; every well operator and payor notified; no open record-stage task (agency filings) |
| 6 payment | Every expected payment has a non-suspense payment from that payor for that well; no open payment-stage task. With no wells listed, nothing to track |

`funds_gate()` lists everything between the deal and releasing the price, including any open legal-question task at any stage; `ptk deal closing release` refuses while it is non-empty, and refuses a second release.

**Rules (`rules.json`, overridable by `ptk-rules.json` beside the database or `--rules`; objects merge, everything else replaces).**
- `flags`: per code `severity`, `party` (`landman`, `attorney`, `acquisitions`, `seller`, `operator`, `third_party`), `requires_attorney` (bool, or a list of severities), `stage`, `due_days`, `template`, `requirement`. A source severity (runsheet) is used unless the rule sets one.
- `checklists`: items with `states`, `kinds`, `when` (`land_status`, `survey_code`, `has_wells`, `operated`, `seller_married_individual`), `stage`, `severity`, `party`, `requirement` (`{county}`, `{tract}` filled in), `authority`. Identical requirement text from two assets on one tract opens one task. `per: "deal"` opens one per deal.
- `attorney`, `messaging`, `payment` (watch days after the transfer notice, decimal tolerance 1e-8).
- Texas items: county run and bring-down, RRC well list, school land (PSL/CSL → GLO), University Lands (stop for mineral/royalty buys), tax certificate, homestead joinder, consents and preferential rights, RRC P-4 for operated wells. New Mexico items: land status, county run, federal or State minerals (stop for mineral/royalty buys: a private deed passes nothing), BLM transfer (3000-3/3000-3a, approval required), NMSLO assignment approval, spousal joinder, OCD pooling orders, consents, OCD C-145. Every citation says "confirm with counsel".

**Tasks (`tasks.py`).** Every flag becomes a task with a fingerprint `sha1(source|asset|code|ref|party)`. Re-running a check never duplicates: an existing task is left alone; a task whose flag is gone becomes `resolved` (with a note); a resolved task whose flag returns is reopened. Info flags never become tasks; they're kept as notes on the verification result. Statuses: `open`, `requested`, `received`, `cleared`, `waived`, `resolved` (program only).
- A **legal-question task** (`requires_attorney`) can be cleared only by an opinion whose `covers` lists it (`ptk deal opinion --covers T#,T#|all`; citing a different opinion is refused) or waived only with `--attorney NAME`. Waivers always need `--reason`.
- An opinion's requirements (`--requirement`, repeatable) become `OPINION_REQUIREMENT` tasks.

**Intake (`intake.py`).** Structure errors raise `ValueError` (`record.normalize`): bad ids, unknown kind or state, no seller, no buyer. Content problems become intake tasks: `INTAKE_LEGAL_UNREADABLE`/`_REVIEW` (legal_tx / legal_nm issues), `INTAKE_COUNTY_MISMATCH`/`_UNKNOWN`, `INTAKE_INTEREST`, `INTAKE_NMA_MISMATCH` (interest × gross ≠ NMA at 4 places), `INTAKE_ACRES_MISMATCH` (>5%), `INTAKE_LAND_STATUS` (required in New Mexico), `INTAKE_MARITAL_STATUS` and `INTAKE_SPOUSE_MISSING` (stop when the seller has a New Mexico asset), `INTAKE_SELLER_CONTACT`, `INTAKE_NO_AGREEMENT`, `INTAKE_EFFECTIVE_DATE`, `INTAKE_PRICE_MISSING`, `INTAKE_WELL_API` (TX 42-, NM 30-), `INTAKE_SELLER_REF`, and `INTAKE_MAPCHECK` when `--layer` is given. Amending an existing deal needs `--reason`.

**Verify (`verify.py`).**
- *Minerals and royalties:* replay the takeoff as of the effective date, and again with everything recorded since except the deal's own conveyance to the buyer (the bring-down). Compare the sellers' holdings with the interest sold (`all` = whatever they hold; a royalty buy's `interest` is in the takeoff's `npri_value` terms, a fraction of production from the minerals the NPRI burdens): `SELLER_NOT_IN_CHAIN`, `SELLER_SHORTFALL`, `SELLER_CONVEYED_AFTER_EFFECTIVE` (a later conveyance to anyone but the buyer: possible double sale; attorney), `NMA_DIFFERS`, `NPRI_KIND_DIFFERS`.
- *Leases, wellbores, ORRIs:* the lease chain. A lease or ORRI buy depends on its own lease only (a well crossing other leases just sets that lease's factor); a wellbore buy depends on every lease in its wells' participation. No lease named → `LEASE_NOT_NAMED`; a wellbore buy with no wells → `WELLBORE_NO_WELLS` (both stop, NO_TITLE). A lease with no royalty gets no NRI (`LeaseholdPosition.nri` returns None), no NRI comparison and no expected payment; its `ROYALTY_MISSING` task holds the deal. A wellbore's WI and NRI are `Σ factor × lease WI/NRI`, with factors from the well's `participation` weights (lateral feet per lease). `NRI_SHORTFALL` when the buyer's NRI from title is below what the seller represented.
- *Relevance:* a ledger or chain flag becomes a task only when its instrument is in the sellers' chain (walk back from the sellers through every grantor that conveyed into the chain). An NPRI/ORRI flag (`NPRI_REVIEW`, `NPRI_NOT_RATIFIED`, `MERGER_REVIEW`, `NPRI_EXCEEDS_ROYALTY`, `NPRI_ON_UNLEASED`, `NPRI_KIND`, `ORRI_REVIEW`) becomes a task only when that burden sits on, or is held by, the sellers' interest. Everything else on the tract is kept as a note. Runsheet rows in other counties are skipped (counted in a note).
- *Expected payments:* for each well with a deck `project`, replay the takeoff with the deal's conveyance added at the effective date, add the buyer once as lessor on the sellers' leases (the buyer takes subject to them; a seller is dropped when, by `name_key`, it keeps nothing), build the deck, and take the buyer's revenue rows. Lease and wellbore buys use `nri_bought = seller NRI × min(sold, owned)/owned`, times the lease's factor in each well. Payments are reconciled against the **sum** for that payor, well and type, because an operator pays one decimal per owner per well.

**Drafts (`drafts.py`).** Markdown templates with `${field}`; a missing field raises, and every unknown fact is a visible `[bracketed blank]`. The package groups assets into one conveyance per (state, form, seller group): Mineral and Royalty Deed (mineral, royalty), Assignment and Bill of Sale (leasehold, wellbore), Assignment of ORRI. Texas uses § venue and acknowledgments; New Mexico uses ") ss." venue and names a married individual's spouse as grantor and signer. Exhibit A states each interest in words and numerals (`fracs.fraction_words`, used only when the words read back to the same value) and adds a **Title note** when verification wasn't OK. The closing statement shows a title shortfall as a **PROPOSED** adjustment (`price × (sold − owned)/sold`, exact to the cent) and both totals. A manifest holds each file's SHA-256. Redrafting an approved package needs `--reason` and resets approval; after funds are released the package can't be redrafted. Curative drafts: affidavit of heirship (TX/NM note), ratification of pooled unit, spousal ratification, correction instrument, release, affidavit of identity.

**Outbox (`outbox.py`).** Templates: substantive `curative_request`, `third_party_request`, `document_request`, `transfer_notice`, `decimal_query`; routine `curative_reminder`, `third_party_reminder`, `document_reminder`, `do_followup`, `payment_followup`. `may_auto_send()` applies section 3 rule 8. `run_reminders()` (`ptk deal remind`): for each open task with an outside party, and for each recipient whose first message was delivered, draft the next reminder every `cadence_days` (14) up to `max_reminders` (3), then open `NO_RESPONSE` for a person to call. No reminders once a task is `received`, and none after a person rejects the latest reminder until someone writes again; for each expected payment with no payment `watch_days_after_notice` (120) after the transfer notice, open `PAYMENT_OVERDUE` and draft a `payment_followup`. Sending: SMTP when `PTK_SMTP_HOST` is set (`PTK_SMTP_PORT`, `PTK_SMTP_USER`, `PTK_SMTP_PASSWORD`, `PTK_SMTP_TLS` = starttls|ssl|none, `PTK_SMTP_FROM`); otherwise `.eml` files in `outbox/` beside the database, marked `exported`, not `sent`. Each delivery attempt is claimed (`message.claimed`) inside the write transaction before sending, so two sends at once deliver once; a failed send is retried on the next run. Letters by mail, and exported email sent by hand, are marked delivered with `outbox mark-mailed` (dates read strictly; two-digit years refused). A delivered transfer notice logs the notice; a delivered request marks its task `requested`. Closing a task withdraws its pending routine reminders.

**Actions (`actions.py`).** `update_task`, `add_task`, `record_opinion`, `closing` (approve → sign → release, gated), `log_recording` (county must be in the deal; image key from `runsheet.image_key`), `notify` (one transfer notice per operator and payor, held until the county is recorded unless `--before-recording`; opens `DIVISION_ORDER_PENDING`), `division_order` (decimal off by more than the tolerance → `DO_DECIMAL_MISMATCH` stop and a `decimal_query` draft; clears the pending task only on a match, otherwise marks it `received` with the reason), `expect`, `payment` (`PAY_DECIMAL_MISMATCH`, `PAYEE_NAME_VARIANT`, `PAYMENT_IN_SUSPENSE`; a paid line resolves `PAYMENT_OVERDUE`), `reconcile` (statement CSV; lines for the buyer matched by `name_key` or difflib ratio ≥ 0.85).

**Dashboard (`server.py`, `ptk deal serve`, port 8780).** Standard library. Deal list, deal page (stage stepper, blockers by stage, assets with verification notes, curative tracker with inline status/note/waiver forms, messages with approve/reject, recordings/notices/payments, audit log), approval queue with "send approved". Listens only on 127.0.0.1/localhost/::1 (`serve` refuses any other host: it has no sign-in), refuses other Host headers and cross-origin posts, puts a per-run token in every form, and redirects after every POST. Every action calls the same functions as the CLI.

**Title run (`titlerun.py`, `ptk deal title-run`, the deal page's *Start title run*).** `start_title_run(store, rules, deal_id, actor, asset_ids=None, start_year=1850)` writes, per asset, `deals/<id>/title/<asset>/`: `SEARCH-PLAN.md` (the county plan from `counties.py`, the legal keys, every name variant for each seller from `names.py`, the state and kind checklist, and the steps: pull, OCR, abstract by API or chat, runsheet and `ptk check`, takeoff or lease chain, verify), a blank `runsheet.csv`, `takeoff.csv` (minerals and royalties) or `lease_chain.csv` (leases, wellbores, ORRIs), and `images/`, `ocr/`, `abstracts/`. Existing files are never overwritten. The new files are linked on the deal through `intake_deal(reason="title run started …")` and a `title.run_started` event is logged.

**Accuracy (`accuracy.py`, `ptk audit [--only areas] [--json] [--out]`, `/audit`).** `run_audit(store, rules, today, areas)` returns a report of checks (PASS, WARN, FAIL, INFO) in six areas: Engine (the 8.1 and 8.2 outputs, lease chain, fractions, dates, rounding, fraction words, and the parser corpus scored by `parser_accuracy.score_all()`: a silently wrong reading fails, a refusal or noise flag warns), Records (hash chain), Deals (verification re-run in memory against the stored result, title files present, package and draft SHA-256 against their manifests, released funds still pass the gate, open legal questions, overdue tasks), CRM (stages, field formats, links, files), Rules (every flag code in the source has a rule, unique checklist ids, templates exist and render, citations say to confirm), Setup (API key, OCR tools, SMTP: information only). Exit 1 on any FAIL.

**Desk (`ptk desk init FOLDER [--crm backup.json] [--by NAME]`).** Creates `deals.db`, a minimal `ptk-rules.json` to edit, `Start Land Desk.cmd` (Windows) and `start-land-desk.sh`, which run `ptk deal serve --open` against that folder. `--crm` imports the CRM backup.

**Look.** One stylesheet for the deal pages and the CRM: a sidebar (Acquisition: Dashboard, Owners, Pipeline, Call queue, Outreach; Title: Deals, Approval queue, Accuracy), line icons from an inline SVG sprite (`web/icons.svg`, injected into the CRM at `<!--PTK_ICONS-->`), no emoji, light and dark themes. Old CRM tags that start with an emoji still display.

**CLI.** `ptk deal intake | import | list | status | verify | title-run | tasks | task | task-add | opinion | draft | request | package | closing | record | notify | division-order | expect | payment | reconcile | remind [--send] | outbox list|show|approve|reject|edit|send|mark-mailed | note | log | verify-log | export | serve [--open] | template`; `ptk audit`; `ptk desk init`. Common options `--db`, `--by`, `--rules`. Exit codes: `verify` 1 unless every asset verified and the verify stage is clear; `division-order`, `payment` and `reconcile` 1 on a decimal mismatch; `outbox send` 1 on a failed send; `verify-log` 1 on a broken chain; bad input or a refused action prints `error: …` and exits 1.

### 5.18 The Landowner CRM on the deal desk (Spec; merged Oct 2026, refinement pending)

The user's earlier CRM (a single-file app with a pluggable storage seam; its notes and schema are in `docs/crm/`) is the acquisition side. It was merged, not rewritten:

**Store (`crm.py`).** CRM records share the deals' append-only log as streams: `crm:<customer id>` (`crm.customer.saved` with the whole record exactly as the CRM holds it, `crm.customer.deleted`, `crm.email.delivered`, `crm.file.added`, `crm.file.removed`) and `crm:settings` (`crm.settings.saved`: `email`, `tagColors`, `knownTags`, `lastBy`, `lastBuyer`). `Store.deal_ids()` skips `crm:` streams. Current state is the latest saved record per id; saving an unchanged record appends nothing. `import_backup` / `export_backup` use the CRM's backup shape; `--replace` also deletes owners missing from the file (each delete is an event, so earlier versions stay in the log).

**Page (`web/crm.html`).** The original app with only its integration points changed:
- `save()` diffs `data.customers` against the last-saved snapshot (the app's own `flushCloud` pattern) and calls `PUT /api/crm/customers/<id>`, `DELETE /api/crm/customers/<id>`, `PUT /api/crm/settings`, debounced 400 ms; `initStore()` loads `GET /api/crm/state`; a 20-second refresh picks up changes from the CLI or another window when nothing is unsaved or open. Leaving with unsaved changes warns. Sample data is never seeded into the database.
- The server injects a per-run token (`<meta name="ptk-token">`); every API call sends it as `X-PTK-Token` (reads too). Host and Origin are checked as for the deal pages.
- Email: every message is previewed (the webhook auto-send path is off on the desk); Send calls `POST /api/crm/send`, which uses the outbox transport (SMTP or an .eml file, status `sent` or `exported`) and refuses owners marked Dead or tagged `do-not-contact`. The CRM logs the step with `via: "ptk"` and shows "Saved as .eml · not emailed" for exported mail.
- Attachments: `POST /api/crm/customers/<id>/documents` (raw body, `X-Filename`) stores the file in `crm-files/` as `<id>-<12 hex>.<ext>` (PDF, PNG/JPEG/GIF/WebP, CSV, JSON, TXT, Markdown; no SVG; 20 MB); served at `/files/<key>` with a sandbox CSP; `DELETE /api/crm/files/<key>` moves it to `crm-files/.removed`.
- *Title follow-up* section on each owner: with no deal, buyer and state fields and a *Start title follow-up* button (`POST /api/crm/customers/<id>/deal`); with a deal, its stage, open and stop tasks, opinion flag and next step, linked to `/deal/<id>`. Owners with a deal get a briefcase icon in the table and pipeline; the sidebar links to /deals (badge = deals with stop tasks) and /outbox. `/crm#c=<id>` opens an owner.

**Hand-off (`start_deal`).** Seller = the owner (`crm_id` kept); one asset per holding with kind from `asset_kind` (working → wellbore, ORRI/overriding → orri, NPRI/non-participating → royalty, anything else, including "Royalty", → mineral), state from the county table (else the user picks TX or NM; never guessed), county, legal description = CRM location, gross acres = CRM acres, wells = the holding's well and case id, and the CRM fields kept under `crm`. Price, interest, tract and effective date are left blank on purpose, so intake raises them. A `CRM_HANDOFF_CONFIRM` stop task (intake, acquisitions) lists every mapping and the exhibit percentages, which are shown but never used as title decimals. A draft `deals/<id>/deal.json` is written for completing the record and re-running `ptk deal intake --reason`. One deal per owner (a second hand-off is refused).

**Routes.** `/` redirects to `/crm`; deal pages moved from `/` to `/deals` (`/deal/<id>` unchanged).

**Open (refinement):** edit the deal record in the browser instead of the draft JSON; decide whether Under Contract should start title follow-up automatically and whether a released deal should move the owner to Closed; per-field merges instead of last-write-wins; whether outreach emails should also go through the approval queue; noise from intake on CRM-sourced records (one `INTAKE_LEGAL_UNREADABLE` per parse issue); the CRM's own call-queue and email cadence vs `ptk deal remind`.

---

## 6. Data formats (Spec)

- **Takeoff CSV**, one row per event: `seq, instrument, recorded, tract, depth, estate, kind, grantor, grantees, shares, interest, warranty, npri_kind, npri_value, notes, npri_burdens`. See 5.3 and README "Formats".
- **Depths JSON:** `{tract: [interval, ...]}`.
- **Project JSON:**
  ```json
  {"well": "...", "state": "TX", "takeoff": "takeoff.csv", "produce_depth": "ALL", "pooled": true,
   "npri_ratified": {"Owner": true},
   "participation": {"method": "completed lateral feet per tract", "weights": {"SEC12": 4800, "SEC13": 5200}},
   "leases": [{"id": "L-100", "tract": "SEC12", "lessors": ["..."], "royalty": "1/4",
               "lessees": {"Example Operating LLC": "1"}, "orris": {"Example ORRI Partners": "1/40"}}]}
  ```
  Paths are relative to the JSON file. Fractions are strings. Optional keys: `depths` (as in the depths JSON) and `as_of`.
- **Runsheet CSV:** `runsheet.COLUMNS` (5.9).
- **Map-check tracts CSV:** `tract, state, county, legal_description, stated_acres`. **Fields JSON:** `{"TX": {"abstract": "...", "block": "...", "section": "..."}, "NM": {"plssid": "...", "section": "..."}}`.
- **Batch jobs JSON:** `{"jobs": [{"doc_id", "county", "state", "pages": [...], "ocr_dir", "model", "effort"}]}`.
- **County checklist CSV:** one row per county with the vendor URLs and blank `confirmed_*` columns for the years.
- **Deal JSON** (`examples/deals/*.json`, `ptk deal template deal.json`): `deal_id`, `name`, `buyer`, `buyer_address`, `effective_date`, `signed_date`, `price`, `sellers[{id, name, kind?, marital_status, spouse, email, phone, address, do_not_contact, channel}]`, `assets[{id, kind, state, county, tract, legal_description, gross_acres, interest | nma, npri_kind, lease{id, …}, wi, nri, land_status, depth, operated, price, seller | sellers[], title{takeoff, depths, runsheet, images, lease_chain, lease_depths}, wells[{api, name, operator, payor, project, participation{lease: weight}}]}]`, `contacts[{name, role, email, address, do_not_contact}]`, `documents[{type, file, received}]`. Paths are relative to the JSON file.
- **Deal sheet CSV** (`ptk deal template deals.csv --kind deal-csv`): one row per asset, grouped by `deal_id`; one well per row (more wells need the JSON form). Columns in `ptk.deal.record.DEAL_CSV_COLUMNS`.
- **Lease chain CSV** (`--kind lease-chain`): `seq, instrument, recorded, lease, depth, kind, assignor, assignees, shares, interest, warranty, orri_value, orri_burdens, royalty, notes` (5.16).
- **Operator statement CSV** (`--kind statement`): `payor, well, owner, decimal, type, amount, check_date, period, status` (`paid` or `suspense`).
- **Rules JSON:** `ptk/deal/rules.json` (5.17).
- **CRM backup JSON** (`ptk crm import/export`): `{customers[], tagColors, knownTags, lastBy, lastBuyer, email}`; record shapes in `docs/crm/land-crm-schema.json` (strings as entered for money, acres and %, local YYYY-MM-DD dates).

---

## 7. Ownership rules the math applies (defaults to confirm with the examiner)

| Topic | Default the deck applies |
|---|---|
| NPRI scope | Burdens only the minerals the ledger tags. It comes out of those owners' royalty in proportion to their burdened minerals. This is the kit's modelling choice, not a rule of law: *Wenske v. Ealy* (2017) turned on that deed's words |
| NPRI types | Fixed = fraction of production. Floating = fraction of the owner's royalty |
| ORRI | Fraction of 8/8, proportionately reduced to the minerals the lease covers; burdens the lessees |
| Unleased owner, TX | Cost-bearing cotenant: share of net proceeds, not royalty |
| Unleased owner, NM | Pooled: 7/8 WI (subject to the risk charge) plus 1/8 royalty (NMSA 70-2-17) |
| TX NPRI in a unit or allocation well | Must ratify pooling, or it is flagged (*Montgomery v. Rittersbacher*, pooled units; allocation wells unsettled; the NPRI instrument may grant pooling authority). Ratifying the lease doesn't reduce a fixed NPRI (*ConocoPhillips v. Hahn*, 2024) |
| Double fractions | Flagged only. After *Van Dyke* (2023), "1/2 of 1/8" in an older instrument is presumed to be a floating 1/2; the presumption is rebuttable (rebutted in *Clifton v. Johnson*, 2026: a fixed 1/128), and mineral and royalty grants can come out differently. The examiner decides. Also see *Hysaw* |
| Duhig | A warranty deed that over-conveys is flagged as a candidate; only the grantor's balance moves |
| NM community property | If the property is community real property, a conveyance without both spouses is void (NMSA 40-3-13), unless a recorded POA (§47-1-7) or written ratification |
| TX affidavit of heirship | Prima facie evidence 5 years after recording (Est. Code §203.001), in the land county and the domicile county; evidence, not a cure |
| Rounding | Exact throughout; 8 places half-up for display only |

Have counsel confirm pin cites and any 2025–26 changes before relying on these in an opinion.

---

## 8. Acceptance spec: exact outputs a rebuild must reproduce

All example data is fictional: an allocation well across two Texas sections, built to trigger each kind of flag.

### 8.1 `ptk ledger examples/takeoff.csv`

```
tract,depth,owner,estate,fraction,kind,burdens
SEC12,ALL,Adams Heir Three,MI,1/6,,
SEC12,ALL,Adams Heir Two,MI,1/6,,
SEC12,ALL,Baker Example,MI,1/2,,
SEC12,ALL,Carter Example,MI,1/6,,
SEC12,ALL,Adams Heir One,NPRI,1/48,fixed,1/2
SEC12,ALL,Adams Heir Two,NPRI,1/48,fixed,1/2
SEC12,ALL,Adams Heir Three,NPRI,1/48,fixed,1/2
SEC13,ALL,Fox Example,MI,1/2,,
SEC13,ALL,Hill Minerals LLC,MI,1/2,,
3 flag(s):
  [NPRI_REVIEW] SEC12/ALL DR Vol 88 Pg 77: fixed NPRI of 1/16 burdening 1/2 of the minerals (conveyed) ...
  [DUHIG_CANDIDATE] SEC12/ALL DR Vol 140 Pg 22: Adams Heir One conveyed 1/4 but held 1/6; moved 1/6
  [CHAIN_GAP] SEC12/ALL DR Vol 141 Pg 9: Doe Stranger holds no minerals here on this date; nothing moved
```

### 8.2 `ptk deck examples/project.json` (exit 0)

```
SEC12: 0.48000000 (12/25)      SEC13: 0.52000000 (13/25)
Example Operating LLC   WI    0.68000000  (17/25)
Example Operating LLC   NRI   0.52825000  (2113/4000)
Hill Minerals LLC       UMI   0.26000000  (13/50)
Hill Minerals LLC       WI    0.26000000  (13/50)
Example Non-Op LP       WI    0.06000000  (3/50)
Fox Example             RI    0.05200000  (13/250)
Example Non-Op LP       NRI   0.04875000  (39/800)
Baker Example           RI    0.04500000  (9/200)
Adams Heir Three        RI    0.01500000  (3/200)
Adams Heir Two          RI    0.01500000  (3/200)
Carter Example          RI    0.01500000  (3/200)
Example ORRI Partners   ORRI  0.00600000  (3/500)
Adams Heir One          NPRI  0.00500000  (1/200)
Adams Heir Three        NPRI  0.00500000  (1/200)
Adams Heir Two          NPRI  0.00500000  (1/200)
Revenue interests total: 1.00000000 (1)
Flags: NPRI_REVIEW, DUHIG_CANDIDATE, CHAIN_GAP, LESSOR_NOT_OWNER (Adams Heir One on L-101),
       NPRI_NOT_RATIFIED (Adams Heir Three), UNLEASED (Hill Minerals LLC, 1/2 of SEC13)
```

Hand checks:
- **Baker RI:** ½ × (¼ − 1/16) × 12/25 = 9/200 = 0.045.
- **Each heir's NPRI:** 1/48 × ½ × 12/25 = 1/200.

### 8.3 `ptk check examples/runsheet.csv` (exit 1)

5 rows: 3 stop, 7 review, 2 info.
- **Row 1:** DOUBLE_FRACTION.
- **Row 2:** WORD_NUMERAL_MISMATCH ("one-fourth (1/8)").
- **Row 3:** NO_IMAGE, and AFFIDAVIT_OF_HEIRSHIP with prima facie date 2028-03-01.
- **Row 4:** TOP_LEASE, DEPTH_LIMITED, LEASE_TERM_MISSING.
- **Row 5:** FILED_BEFORE_EXECUTED, BLANKET_CONVEYANCE, FIDUCIARY_CAPACITY, UNACKNOWLEDGED, SPOUSE_NOT_JOINED (stop).

### 8.4 Other commands

- `ptk calc --acres 640 --owned 1/16 --royalty 1/4 --unit 4800/9600` prints NMA 40.0000, NRA 80.0000, participation 1/2, decimal **0.00781250 (1/128)**.
- `ptk legal "SE/4 of NW/4 and W/2 W/2 of Section 6, T24S, R32E, NMPM"` gives Section 6 quarter-quarters {NWNW, NWSW, SENW, SWNW, SWSW}, regular acres 200.00.
- `ptk plan --county Irion --from 1905` says the TexasFile index starts 1983, so use the courthouse or a plant for 1905–1982.
- `ptk mapcheck examples/mapcheck/tracts.csv --layer …tx… --layer …nm…` gives SEC12-N2 OK (640 × ½ = 320), SEC13 ACREAGE_MISMATCH (+33.3% vs 480), NM15-NE4 OK (640 × ¼ = 160), NM16 NOT_FOUND. Exit 1.
- `ptk names "John Smith and wife, Mary Smith"` gives kind `multiple`, Soundex S530, two parties with nickname and initial variants and `SMITH *`.

### 8.5 Deal pipeline (`examples/deals/`, fictional)

- `ptk deal intake examples/deals/tx-mineral.json`: 5 tasks (TX-RUN-COUNTY, TX-RRC-WELLS and TX-TAX-CERTIFICATE once for the shared tract; TX-HOMESTEAD for Baker only, the married seller; ALL-W9 once). No intake problems; stage `verify`.
- `ptk deal verify D-2026-001` (exit 1):
  - A1 Baker: **OK**, held 1/2, sold 1/2. Tasks for her chain: NPRI_REVIEW (DR Vol 88 Pg 77), DOUBLE_FRACTION, WORD_NUMERAL_MISMATCH, NO_IMAGE, TOP_LEASE, LEASE_TERM_MISSING (runsheet rows 1–4), NPRI_NOT_RATIFIED (party Adams Heir Three). The DUHIG_CANDIDATE and CHAIN_GAP are notes, not tasks; 5 runsheet flags on the Eddy County row are skipped.
  - A2 Carter: **SHORTFALL**, held 1/6, sold 1/4 (short 1/12). Tasks: SELLER_SHORTFALL (acquisitions), DUHIG_CANDIDATE (DR Vol 140 Pg 22).
  - Expected payments from Example Operating LLC, Example Unit 12-13 1H: A1 RI **9/200 = 0.04500000**, A2 RI **3/200 = 0.01500000**; the transfer notice states **RI 0.06000000**.
  - Opinion required: DOUBLE_FRACTION, DUHIG_CANDIDATE, NPRI_REVIEW, TOP_LEASE, WORD_NUMERAL_MISMATCH.
- `ptk deal package D-2026-001`: `01-TX-mineral-deed-baker-example.md` ("an undivided one-half (1/2) interest … being 320.0000 net mineral acres"), `02-TX-mineral-deed-carter-example.md` (with a Title note), closing statement: price 432000.00, PROPOSED title adjustment A2 **−48000.00**, due seller if approved **384000.00**.
- `ptk deal intake examples/deals/nm-leasehold.json` then `verify D-2026-002` (exit 1): A1 lease NM-FEE-10 **OK** (WI 1, NRI 3/4). A2 wellbore Example Federal 15-22 1H: seller WI 3/4, NRI **19/32 = 0.59375000** (½ × 3/4 + ½ × ½ × 7/8) against 0.60 represented → NRI_SHORTFALL; the federal lease assignment recorded 2026-10-01 → SELLER_CONVEYED_AFTER_EFFECTIVE (attorney). ORRI_REVIEW on NM-FEE-15; LEASE_STATUS_REVIEW once per lease (3); NM-BLM-TRANSFER at the record stage.
- `ptk deal import examples/deals/deals.csv`: D-2026-003 stops at intake with NM-FEDERAL-MINERALS (stop, acquisitions) and INTAKE_SPOUSE_MISSING (stop).
- `ptk deal reconcile D-2026-001 examples/deals/statement.csv` after verify: 1 line for the buyer, 2 other owners skipped, 0 mismatches, payee name variant "EXAMPLE MINERALS FUND, L.P.".

- CRM (`tests/test_crm.py`, fictional backup): importing the backup twice adds 3 then changes nothing; a hand-off of an Eddy County owner with Royalty and Working Interest holdings gives assets (mineral, NM, Eddy) and (wellbore, NM, Eddy), a `CRM_HANDOFF_CONFIRM` stop task naming both mappings and the exhibit %, and no expected payments; a Yellowstone County owner needs `--state`; outreach to a Dead/do-not-contact owner is refused; without SMTP the send is `exported`.

### 8.6 Test inventory (151 tests)

A rebuild should have equivalents for each of these.

**test_math.py**
- numeric_forms, products, words
- word_numeral_mismatch_is_caught
- rounding_is_exact_half_up
- simple_chain_sums_to_one
- duhig_candidate_moves_only_what_grantor_held
- quitclaim_over_conveyance_is_not_duhig
- chain_gap_moves_nothing
- as_of_date_stops_the_replay
- depth_severance
- missing_minerals_are_flagged
- worked_example_fixed_npri_allocation_well
- floating_npri_follows_each_lease_royalty
- unleased_new_mexico_owner_is_seven_eighths_wi_plus_one_eighth_royalty
- unratified_npri_is_flagged_in_texas_units
- npri_larger_than_royalty_is_flagged
- example_project_deck_is_whole

**test_records.py**
- Texas parsing: full_modern_description, old_style_abbreviations, school_land_and_missing_abstract, lowercase_a_is_not_an_abstract, save_and_except
- New Mexico parsing: section_first_style_with_lots, of_style_and_right_to_left, multiple_sections_and_compact_aliquots, words_and_finer_divisions, unreadable_text_is_reported
- names: person_variants_include_abbreviations_and_initials, entities_and_capacity, soundex
- runsheet and counties: example_runsheet_flags, image_key, late_index_county_sends_you_to_the_courthouse, parent_county_before_organization
- extraction: request_shape_and_checks, good_quote_passes_and_haiku_skips_fallbacks, refusal_and_truncation_are_not_parsed, batch_submit_and_collect, schema_is_strict
- CLI: deck_command_exits_zero_on_whole_deck, calc_command

**test_regressions.py** (one per bug in section 9)
- parsing: mixed_numbers_and_glyphs, two_digit_years_are_refused, common_forms
- takeoff loading: takeoff_sorts_by_real_date_not_text, takeoff_rejects_unreadable_dates
- ledger names and rows: names_match_across_case_and_punctuation, mineral_reserve_rows_are_flagged_not_silently_dropped, probate_moves_npri_as_well_as_minerals, all_estate_needs_relative_interest, input_events_are_not_mutated, lessor_names_match_ledger_names_loosely
- NPRI scope: conveyed_half_only, all_minerals_is_the_old_behavior, retained_and_named_owners, floating_npri_on_conveyed_half, unreadable_or_empty_burden_is_flagged, merger_review_when_owner_holds_burdened_minerals_and_the_npri
- name parsing: trailing_suffix_after_comma, and_wife_splits_into_two_people, heirs_of
- runsheet rules: double_fraction_with_numerals_in_parentheses, short_term_clause, bad_dates_on_either_column, release_is_not_a_lien
- legal descriptions: texas_multiple_sections, new_mexico_all_of_section, new_mexico_multiple_sections
- counties: checklist_round_trip_overrides_years
- extraction: haiku_gets_no_effort, uncited_evidence_is_flagged_when_ocr_exists, oversized_request_fails_with_a_clear_message
- math audit (Oct 2026): absolute_npri_conveyance_is_taken_pro_rata_across_pieces, relative_all_estate_conveyance_moves_the_same_share_of_npri, compound_numerator_words_are_not_a_false_mismatch

**test_parser_corpus.py** (ratchet)
- corpus: every case in `parser_corpus.json` is correct unless listed in `KNOWN_FAILURES` with a reason (empty today); a listed case that starts passing must be removed
- case_ids_are_unique

**test_exports.py**
- runsheet_links_colors_and_flags
- deck_has_live_formulas

**test_ocr.py**
- rows_merge_left_to_right_and_sort_top_to_bottom
- full_width_punctuation_is_folded
- textract_lines_with_fake_client
- sample_deed_reads_and_feeds_extract
- pdf_is_split_into_pages

**test_mapcheck.py**
- area_of_a_square_mile_is_640_acres
- plssid
- texas_matches_and_acreage
- new_mexico_sections_and_aliquots
- outside_the_permian_is_flagged
- projected_layers_are_refused
- shapefile_with_a_hole
- geojson_output

**test_smoke.py**
- expected_facts_hold_for_the_sample_answer
- full_run_passes_with_a_correct_answer
- wrong_answer_fails

**test_deals.py**
- fraction words: words_read_back_exactly, words_are_withheld_when_they_would_not_read_back (1/10^6 is withheld; 2113/4000 now reads back through hyphen groups and is allowed)
- leasehold: nri_carries_the_orri_reserved_out_of_the_assignment, orri_follows_a_partial_assignment, missing_royalty_is_flagged_not_assumed
- store: log_is_append_only_and_tamper_evident
- intake: texas_mineral_deal_opens_state_checklists_once_per_tract, new_mexico_sheet_deal_goes_back_to_acquisitions, content_problems_are_tasks_but_structure_problems_are_errors
- verify: texas_seller_shortfall_and_chain_relevance, new_mexico_wellbore_nri_and_later_assignment, rerunning_verify_never_duplicates_and_fixing_data_resolves
- curative and closing: legal_questions_need_the_attorney, funds_stay_held_until_the_gate_clears, package_documents, new_mexico_spouse_signs, curative_draft
- messaging: first_contact_is_never_automatic, routine_reminder_auto_approves_only_after_an_approved_contact, mail_and_do_not_contact_are_never_automatic, reminder_limit_then_a_person_calls, sending_uses_the_transport_and_withdraws_stale_reminders, without_smtp_messages_are_exported_not_sent
- record and pay: notice_waits_for_recording_and_names_every_seller, division_order_and_statement_reconcile_against_title, payment_overdue_after_the_watch_window
- CLI and dashboard: commands_and_exit_codes, dashboard_guards_and_renders, export
- CRM (test_crm.py): backup_imports_once_and_round_trips, every_version_is_kept_and_deletes_are_recoverable, replace_removes_owners_missing_from_the_file, interest_types_map_to_asset_kinds, owner_becomes_a_deal_and_intake_lists_what_is_missing, state_and_buyer_are_never_guessed, outreach_goes_through_the_mailer_and_is_logged, documents_are_kept_beside_the_database, crm_page_and_api, guards, import_list_handoff_export
- review regressions: amending_a_deal_invalidates_its_verification, an_opinion_clears_only_the_tasks_it_covers, a_reminder_a_person_rejected_stays_rejected, a_received_task_gets_no_reminders, each_recipient_on_a_task_gets_reminders, two_sends_at_once_deliver_once_and_ids_never_collide, no_redraft_or_second_release_after_funds_go_out, a_missing_lease_royalty_never_becomes_an_nri, a_wellbore_buy_without_wells_and_an_orri_without_a_lease_are_reported, a_lease_buy_whose_well_crosses_another_lease_still_verifies, mark_mailed_dates_are_read_strictly, co_sellers_on_one_lease_add_the_buyer_once, a_mismatched_division_order_keeps_waiting, a_legal_question_at_any_stage_holds_funds, the_dashboard_only_listens_on_this_computer

AI tests use a fake client object that records the request and returns canned responses. No test makes a network call.

---

## 9. Mistakes already made: don't repeat them

| Bug | Fix |
|---|---|
| Floats for decimals, or `round()` on floats | Integer half-up rounding (5.1) |
| "12 1/2%", "½", "1⁄8" didn't parse | Normalize glyphs and slashes, space after a digit |
| Two-digit year `48` became 2048 | Refuse two-digit years |
| Non-ISO dates sorted as text (`5/10/1948` < `12/1/1931`) | Convert to ISO before sorting |
| `MI` reserve rows silently dropped | `MI_RESERVE_IGNORED` flag |
| "ADAMS EXAMPLE" vs "Adams Example" created a false chain gap | `name_key` matching plus a `NAME_VARIANT` flag |
| Replay mutated the caller's events | `dataclasses.replace` |
| A probate left the dead owner still paid on their NPRI | `ALL` estate moves minerals and NPRIs together |
| `ALL` with an absolute fraction gave nonsense | `ALL_NEEDS_RELATIVE_INTEREST` |
| An NPRI burdened the whole tract when it was reserved from the half conveyed | Lot-based burden groups that travel pro rata |
| "John Smith, Jr." and "John Smith and wife, Mary" parsed wrong; "Heirs of" was ignored | Explicit name-shape handling (5.8) |
| Double fraction with numerals in parentheses was missed | Run the rules on text with parentheticals removed too |
| Short term clause ("for 15 years and as long thereafter") was missed | Broadened `TERM_INTEREST` |
| Releases were flagged as liens | Exclude releases |
| Bad instrument dates were not flagged | `BAD_DATE` on both columns |
| "a 160 acre tract" was read as an abstract | Case-sensitive `A-` |
| "Sections 12 and 13" lost a section (TX); "All of Section 6" and "Sections 15 and 22" misread (NM) | Multi-section and whole-section patterns |
| Letters inside words were read as NM aliquots | Full-match aliquot validation |
| Sent `effort` to a model that rejects it | `NO_EFFORT_PREFIXES` |
| A quote with no line IDs passed unchecked | `EVIDENCE_NOT_CITED` |
| A huge request failed opaquely | 30 MB guard with a clear message |
| Invalid JSON raised an exception | Reported as an error result |
| Fake-PNG tests broke once Pillow was installed | Unreadable image → `ValueError`; tests use a tiny real base64 PNG |
| RapidOCR was poor on typescript | Tesseract is the default |
| pdftoppm page-number padding broke page names | Rename to `-p<N>` |
| Textract lines were out of order | Sort by relative Top, then Left |
| Shapefile ring orientation gave negative areas | Handle orientation; subtract holes |
| `ptk … \| head` raised BrokenPipe | Reset SIGPIPE |
| `pkill -f soffice` killed its own shell during formula checks | Use the pattern `[s]office.bin` |
| Python 3.10/3.11 rejects backslashes inside f-string expressions | Move the regex out of the f-string. `ruff check --target-version py310` catches it (and reused quotes) |
| NPRI flags landed on the wrong seller when relevance followed the chain (a probate in Carter's chain made Baker's NPRI look like Carter's) | NPRI/ORRI flags follow the burden (`verify.burden_refs`), chain flags follow the chain |
| Two interests bought in one well were each checked against the operator's single decimal | Reconcile against the sum per payor, well and type |
| The transfer notice named only the first asset's seller | Messages to an operator cover every asset whose wells it operates or pays |
| A third party got the seller's "thank you for selling" letter | `third_party_request` / `third_party_reminder` |
| An auto-approved reminder could still go out after its task closed | Closing a task withdraws pending routine reminders; `send` re-checks |
| The CLI left the SQLite file open, so Windows couldn't delete it | Every command closes its store |
| Re-running verification would have duplicated tasks | Fingerprints; missing flags resolve, returning flags reopen |
| Independent review, Oct 2026: amending a deal kept the old verification, so the gate could pass on stale title | The fold drops the verification of changed assets |
| Review: a legal-question task could be cleared by citing an opinion that didn't cover it | The opinion's `covers` must list the task |
| Review: a reminder a person rejected came back auto-approved the next day; reminders continued after a task was `received`; only the last recipient was reminded | Rejection by a person stops reminders until someone writes again; `received` stops them; reminders run per recipient |
| Review: two sends at once delivered the same email twice; ids from `len()+1` could collide across processes | Claim each attempt and number ids inside the write transaction; a lock around the shared connection |
| Review: redrafting after funds were released erased the release; a second release was allowed | Both refused |
| Review: a missing lease royalty was computed as 0, giving NRI 1 | NRI is None without a royalty; nothing is compared or expected |
| Review: a lease buy whose well crossed another lease could never verify; a wellbore buy with no wells verified OK; an ORRI with no lease crashed | Lease dependencies by asset kind; `WELLBORE_NO_WELLS`, `LEASE_NOT_NAMED` |
| Review: two co-sellers on one lease added the buyer as lessor twice (a false DOUBLE_LEASE) | Add the buyer once; compare sellers by `name_key` |
| Review: a division order with the wrong decimal cleared the pending task | Clear only on a match |
| Review: an attorney question at the record or payment stage didn't hold funds | The gate counts every open legal question |
| Review: a non-ISO `mark-mailed` date crashed the reminder run for every deal; `serve --host 0.0.0.0` exposed an unauthenticated dashboard | Strict date parsing; loopback only |
| Math audit, Oct 2026: an absolute NPRI conveyance ("1/32") from an owner holding the NPRI in two pieces took 1/32 from each piece | Taken pro rata against the total held, computed once; over-conveyance flagged once |
| Math audit: an `ALL`-estate conveyance of "1/2 of grantor's" wasn't read as relative, so the NPRI didn't move | `relative_share()` reads "all", "all of grantor's", "n/d of grantor's" and similar |
| Math audit: "twenty-seven sixty-fourths (27/64)" was split as "twenty / seven sixty-fourths", a false mismatch | Hyphen groups are read as one number first; non-numbers like "nine two" are refused |
| Parser audit, Oct 2026: 184 of 264 labelled record forms correct | 264 of 264 after fixes in every parser; `test_parser_corpus.py` keeps it there |
| Parser audit: removing duplicate aliquots broke "NW4 NW4" (a quarter-quarter) | Keep repeats; only parenthetical echoes are dropped. Check every fix against the whole corpus |
| Parser audit: the first county named was taken as the land's county, even in a recital ("recorded in Reeves County") | The Permian county nearest after the section or survey; an issue names all of them |
| Parser audit: runsheet flags fired on recitals (a lease's primary term, a deed of trust's trustee, a specific tract "situated in Midland County") and missed "Admx.", "Personal Representative", "NPRI", "(Top Lease)" | Recital checks per flag; abbreviations and the instrument type are read |
| Dates were parsed with `strptime`, which depends on the locale and missed "Sept.", "A.D." and "the 10th day of" | Explicit patterns and a month table |
| Legal review, Oct 2026: the NMSLO assignment cite was §19-10-12 (it's §19-10-13) and ORRIs were on the approval checklist; *Van Dyke* was written as a rule (it's a rebuttable presumption); *Wenske* was called the default; an unacknowledged deed was "cured" by §16.033 | Corrected in `rules.json`, `runsheet.py`, `doi.py`, templates, README and section 7. Every citation still says to confirm with counsel |
| Bash heredocs turned `\b` in regexes into a backspace character | Edit code with the editor tool or a script file, and check for stray control characters |

---

## 10. The Permian Title Playbook (companion page)

**What it is.** A single-page operating manual for the land department, aimed at a senior title landman. Sections:

1. Start here (thesis)
2. Five title products
3. The run, step by step (SOP)
4. County finder (an interactive table of 24 counties)
5. Texas (GLO, Relinquishment Act, University Lands, railroad surveys, case law)
6. New Mexico (PLSS, NMSLO, BLM MLRS, OCD pooling, community property)
7. Red flags
8. Decimal calculator (BigInt exact rationals in `calc.js`; no floats)
9. Automation (pipeline, extraction schema, review tiers)
10. Tool stack
11. QC and KPIs
12. People and costs
13. First 90 days
14. Sources

**Build.**
- `cd playbook/src && python build.py` writes `../permian-title-playbook.html`. Rebuilding reproduces the committed file byte for byte.
- The output is an artifact-style fragment (no `<!doctype>`); claude.ai artifacts add the document skeleton when published.
- Fonts: IBM Plex from Google Fonts. Light and dark themes come from CSS tokens.
- `shot.js` is a Playwright check: screenshots at desktop and phone widths, a horizontal-overflow test and a console-error test.

**Open:**
- Sync the county data with `ptk/counties.py`. Today the same data lives in two places (`counties_tx.js` and `counties_nm.js` in the page).
- The page's schema (`src/schema.json`) is an earlier, looser version than the kit's enforced schema.

---

## 11. Environment notes

**Setup:**
- **System packages:** `tesseract-ocr`, `poppler-utils`. Add `libreoffice-calc` only to verify spreadsheet formulas headlessly:
  ```
  soffice --headless --convert-to xlsx --outdir out deck.xlsx
  ```
  Then reopen the converted file with openpyxl `data_only=True` and compare the values.
- **Secrets:** set `ANTHROPIC_API_KEY` in the environment's secrets or settings. AWS credentials are needed only for Textract. Outgoing email for `ptk deal` needs `PTK_SMTP_HOST` (and `PTK_SMTP_USER`, `PTK_SMTP_PASSWORD`, `PTK_SMTP_FROM`) in the environment; without it nothing leaves the machine.
- **Windows:** the deal pipeline was built and tested on Windows 11 with Python 3.14 (Tesseract and pdftoppm not installed, so 3 OCR tests skip). On Windows, set `PYTHONIOENCODING=utf-8` when piping output, so § and ½ survive.

**Network:**
- The original cloud session could reach PyPI, apt and the Anthropic API.
- It **could not** reach `gis.blm.gov`, `www.rrc.texas.gov`, `www.texasfile.com` or `www.courthousedirect.com`. Add them to the environment's network allowlist if you need live checks of layers or coverage pages.

**GitHub:** the integration could not create repositories (403), so the user created the repo by hand.

**Not yet done:**
- **No live API call has been made.** Run `ptk smoke-test` first.
- Then run a gold set of 300–500 already-abstracted instruments, stratified by county, era and instrument type, and measure field accuracy before production use.
- When rebuilding, check the current Anthropic docs for structured outputs, prompt caching, the Batches API, effort, and the server-side fallback beta. These change faster than this document.

---

## 12. Open for improvement

These are listed roughly in priority order. Each one gives a starting design. Change it if you find a better one, but keep section 3 intact and keep section 8 passing.

### 12.1 Deal follow-up pipeline: built (5.17); what's still open

The six-stage pipeline in the original plan (intake, verify, curative, closing package, record and notify, track payment) is built as `ptk deal`, with the user's answers recorded in 5.17. Still open, roughly in priority order:

1. **The CRM merge (first pass done, 5.18).** The user supplied the old CRM (Oct 2026) and asked to merge first and refine after. The refinement list is at the end of 5.18.
2. **Schedule the reminders.** `ptk deal remind --send` is meant to run daily (Windows Task Scheduler or cron), with SMTP configured. Set it up only with the user's go-ahead, since it sends email.
3. **Multi-user.** The dashboard is a single-user desk tool on 127.0.0.1. For a team, put the same functions behind a hosted app with real sign-in, and keep the append-only log as the record.
4. **Leases taken** (new leases from mineral owners: bonus, delay rentals, lease form). The user's deals buy existing leases; taking new leases isn't modeled.
5. **E-recording and agency filings.** Recording, BLM, NMSLO, RRC and OCD filings are checklist tasks logged by hand. Vendor e-recording APIs or form generation could fill them in.
6. **Owner portal.** Sellers sign and return curative instruments by mail today.
7. **Automated checkups.** A daily `ptk audit` (and `ptk deal remind`) by Windows Task Scheduler or a scheduled Claude task. Set it up only with the user's go-ahead.
8. **Closing statement for lease and wellbore shortfalls.** A leasehold or wellbore NRI shortfall (or a `NOT_IN_CHAIN` asset) proposes no price adjustment today (e.g. the fictional D-2026-002 NRI shortfall should propose −10,416.67). Also from the math audit: per-line vs total rounding on the closing statement; Exhibit A wording "1 (1.00000000) of the working interest"; spreadsheet floats vs section 3 rule 1; `MERGER_REVIEW` repeats per NPRI piece; `ownership_table` lists NPRI pieces separately instead of one total per owner.
9. **Legal points for counsel to confirm.** The Oct 2026 legal review's corrections are applied, but each needs counsel's confirmation, especially the newest case cites (*Clifton v. Johnson*, 2026; *ConocoPhillips v. Hahn*, 2024) and the agency rules (19.2.100 NMAC, 43 CFR 3106, 19.15.9 NMAC).

### 12.2 Backlog

| Improvement | Why | Starting idea |
|---|---|---|
| **Abstract-to-takeoff bridge** (highest value) | Today a person retypes the extracted JSON into the takeoff CSV | Map `conveyances` and `reservations_exceptions` to takeoff events. Every inferred field (relative vs absolute interest, NPRI burden scope, fixed vs floating) carries a confidence and lands in a review queue. Never auto-commit an NPRI kind |
| **Pay-deck reconciliation** (partly built) | Catch operator decimal errors and suspense | Built for the buyer's own lines (`ptk deal division-order`, `payment`, `reconcile`). Still open: reconcile a whole operator pay deck against `ptk deck` for every owner |
| **Heirship and intestacy calculator** | Heirship is the most common Permian curative | TX and NM intestacy tables by date of death (community vs separate property; the law changes over time), output as `ALL` takeoff rows. Always flag for attorney review |
| **Index-export import and chain walking** | Faster search | Import grantor/grantee index exports (vendor CSV), walk back from the current owner with `names` variants, and propose the instrument list |
| **Appraisal-roll cross-check** | Finds owners the chain missed | Compare ledger owners with county appraisal district mineral rolls |
| **Lease status from production** | Has a lease expired? | RRC PDQ (TX) and OCD (NM) production by lease or well against the primary term and cessation clause |
| **Allocation factors from lateral geometry** | Removes hand-entered weights | Intersect RRC or OCD directional survey laterals with tract polygons; completed feet per tract |
| **Dual-read on critical tokens** | Catch OCR and vision misreads | Compare OCR and the vision model on fractions, volume and page, names, and section numbers; disagreement → review |
| **Automatic PII redaction** | Section 3 rule 6, automated | Regex plus OCR boxes to black out SSNs, DOBs and account numbers in the image before upload |
| **Title register** | Avoid duplicate runs | SQLite: tract → runs, certified-through date, opinions, curative items |
| **Review screen** | Faster human review | A small web page: image on the left, fields with evidence highlights on the right, accept or edit, writes back JSON |
| **Land-system exports** | Fit existing tools | MineralSoft, Quorum or TotaLand import formats |
| **Metes-and-bounds plotting** | Check closure and area | Parse calls, plot them, report closure error and area |
| **Two-survey TX descriptions; same-name owners** | Known gaps (5.3, 5.5) | Keep a list of surveys; give owners an identity key (a name plus a distinguishing fact) |
| **Term interests and reversions in the ledger** | They're flagged today but not modeled | A `term` event with an expiry condition; the ledger reports both branches until production proves which applies |

Add rows here as you find new gaps. When you finish one, move its behavior into section 5 and its proof into section 8.

---

## 13. Working agreements

- **Tests first for any math change.** The example deck must still total exactly `1.00000000`, and section 8 must still hold. When a bug is found, add a regression test named after the behavior.
- **Keep README.md (user guide) and this file (builder guide) in step** with the code.
- **Message style:** say what to do, not just what is wrong. Example: "Filed before it was signed. Check both dates against the image."
- **Flag codes are API.** Don't rename them, because spreadsheets and downstream tools key on them. Add new ones instead.
- **When unsure about a legal effect, add a flag**, cite the authority in the message, and leave the decision to a person.
