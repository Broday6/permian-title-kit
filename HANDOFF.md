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
| Acceptance tests | `tests/` (91 tests, all passing at handoff) |
| CI | `.github/workflows/tests.yml` (Python 3.10 and 3.12) |

**With repo access:**
1. Clone the repo.
2. Run `pip install -e ".[test,ai,xlsx]" && python -m pytest -q`. You should see `91 passed`.
3. Read sections 2, 9 and 12 of this document before changing anything.

**Without repo access:** sections 3–8 are the rebuild spec, and section 8 is the acceptance test.

---

## 2. What the program is for

**The business.** The user runs a minerals-and-land company in the Permian Basin (Texas and New Mexico). An existing team handles acquisition: it finds land, identifies owners and contacts them to make deals. **Each signed deal is then handed to Claude for title follow-up.** Follow-up means:

1. Verify the seller owns what they sold.
2. Find and cure defects.
3. Assemble the closing package.
4. Record the instruments and notify operators.
5. Confirm that the first payment arrives.

The kit already automates the title-running steps behind step 1. Steps 2–5 are the next build (section 12.1).

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
8. **Owner communications** are drafted for human approval and are not sent automatically, unless the user explicitly decides otherwise (an open question; see 12.1).
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
  smoke.py     sample.py    xlsx.py
  schema/instrument.schema.json
examples/
  takeoff.csv  project.json  runsheet.csv  jobs.example.json  sample_extraction.json
  mapcheck/{tracts.csv, fictional_tx_surveys.geojson, fictional_nm_sections.geojson}
tests/
  test_math.py test_records.py test_regressions.py test_exports.py
  test_ocr.py  test_mapcheck.py test_smoke.py
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
- package data `ptk/schema/*.json`

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
  - trailing words: `3/16 royalty`
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
- **Words:** try every split of the phrase into numerator (cardinal) and denominator (ordinal), so "one hundred twenty-eighth" and "three thirty-seconds" both work.
- **`to_decimal(value, places=8)`** rounds exactly, half-up, with integer math:
  ```python
  scale = 10**places; n, d = abs(value.numerator), value.denominator
  q = (n*scale*2 + d) // (2*d); whole, frac = divmod(q, scale)
  sign = "-" if value < 0 and q else ""
  return f"{sign}{whole}.{frac:0{places}d}"
  ```
- **`fmt(Fraction) -> "n/d"`.**

### 5.2 `dates.py` (Spec)

- `parse_date` accepts `1948-05-10`, `5/10/1948`, `1948/05/10`, `May 10, 1948`, `May 10th, 1948` and `10 May 1948`. It **refuses two-digit years** (`5/10/48`) with `DateError`, because `48` silently becoming 2048 corrupts the chain order.
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
- "Sections 12 and 13" keeps both sections.

**Open:** a description naming two surveys keeps only the first. Metes-and-bounds calls are captured as text and not plotted.

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

**Section layouts handled:** "… of Section 6" and "Section 6: …" styles, "Sections 15 and 22", "All of Section 6", and lot ranges.

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
| DOUBLE_FRACTION | review | `n/d of [the] [usual] n/d`, or "one-half of the one-eighth" (*Van Dyke*, 2023) |
| TERM_INTEREST | review | "for a period/term of … years", or years … "as long thereafter" |
| BLANKET_CONVEYANCE | review | "all of my/our/grantor's … interest/lands/minerals … in ___ County" |
| MOTHER_HUBBARD | info | strips and gores, "any and all other lands", adjacent or contiguous |
| NPRI_LANGUAGE | review | non-participating, without the right to lease, executive right, bonus and rentals |
| POWER_OF_ATTORNEY | review | attorney-in-fact |
| FIDUCIARY_CAPACITY | review | executor, administrator, trustee, guardian, receiver |
| RELINQUISHMENT_ACT | review | "relinquishment act", "mineral classified" |
| CORRECTION | info | correction, corrective, re-recorded |
| TOP_LEASE | review | top lease; subject to an existing, prior or outstanding lease |
| DEPTH_LIMITED | info | below/above, base of the ___, stratigraphic equivalent, from the surface to |

**Row checks:**

| Area | Flags (severity) |
|---|---|
| Images and recording | `NO_IMAGE` (stop), `IMAGE_MISSING` (stop, with `--images`), `NO_RECORDING_REF` (stop) |
| Dates and fractions | `BAD_DATE` (stop, either date column), `FILED_BEFORE_EXECUTED` (review), `WORD_NUMERAL_MISMATCH` (stop) |
| Instrument type | `QUITCLAIM` (info: no warranty, so no Duhig); `LIEN_OR_LITIGATION` (review: deed of trust, judgment, lis pendens, UCC and similar; releases excluded) |
| Execution | `UNACKNOWLEDGED` (TX: cured by limitations after 2 years, CPRC §16.033; NM: not "of record", §14-8-4); `SPOUSE_NOT_JOINED` (stop, NM only, NMSA 40-3-13) |
| Heirship | `AFFIDAVIT_OF_HEIRSHIP` (TX: info stating the prima facie date, filed + 5 years, Est. Code §203.001; NM: review); `SMALL_ESTATE_AFFIDAVIT` (stop) |
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

---

## 7. Ownership rules the math applies (defaults to confirm with the examiner)

| Topic | Default the deck applies |
|---|---|
| NPRI scope | Burdens only the minerals the ledger tags. It comes out of those owners' royalty in proportion to their burdened minerals (the *Wenske v. Ealy* default when the burden is spread across the estate) |
| NPRI types | Fixed = fraction of production. Floating = fraction of the owner's royalty |
| ORRI | Fraction of 8/8, proportionately reduced to the minerals the lease covers; burdens the lessees |
| Unleased owner, TX | Cost-bearing cotenant: share of net proceeds, not royalty |
| Unleased owner, NM | Pooled: 7/8 WI (subject to the risk charge) plus 1/8 royalty (NMSA 70-2-17) |
| TX NPRI in a unit or allocation well | Must ratify pooling, or it is flagged (*Montgomery v. Rittersbacher*) |
| Double fractions | Flagged only. After *Van Dyke* (2023), "1/2 of 1/8" is usually a floating 1/2 under a rebuttable presumption, but the examiner decides. Also see *Hysaw* and *Hahn* (2024) |
| Duhig | A warranty deed that over-conveys is flagged as a candidate; only the grantor's balance moves |
| NM community property | A conveyance without both spouses is void (NMSA 40-3-13) |
| TX affidavit of heirship | Prima facie evidence 5 years after recording (Est. Code §203.001) |
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

### 8.5 Test inventory (91 tests)

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
| Python 3.10/3.11 rejects backslashes inside f-string expressions | Move the regex out of the f-string |

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
- **Secrets:** set `ANTHROPIC_API_KEY` in the environment's secrets or settings. AWS credentials are needed only for Textract.

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

### 12.1 Next build: deal follow-up pipeline

**The flow.** The acquisition team hands over a signed deal. The pipeline follows it to a recorded, paid interest:

| Stage | What happens | Kit pieces it uses | Output |
|---|---|---|---|
| 1. Intake | Deal record: deal id, seller(s), tracts and legal descriptions, interest bought (NMA or NRA, mineral or royalty), price, effective date, documents received | `legal`, `mapcheck`, `names` | Validated deal record. Any parse or map problem goes back to acquisitions |
| 2. Verify ownership | Run or bring down title on each tract to the effective date; confirm the seller owns at least what they sold | `plan`, `ocr`, `extract`, `check`, `ledger --as-of` | Ownership report: what's owned vs sold; shortfalls flagged |
| 3. Curative | Turn every stop or review flag into a curative item: owner, requirement, due date, status. Draft affidavits of heirship, ratifications, correction deeds and releases from templates | flags from `check`, `ledger`, `deck` | Curative tracker; drafts for attorney review |
| 4. Closing package | Conveyance with the exact legal description and interest; exhibits; closing statement; funds release only when curative is cleared or waived | ledger + deal record | Package for signature |
| 5. Record and notify | Record in every county the minerals sit in; send transfer packets to each operator and purchaser (division order requests) | `image_key`, operator list from RRC or OCD | Recording log; notice log |
| 6. Track payment | Watch for the first check or revenue statement; reconcile decimals against the deck; chase suspense | `deck` (pay-deck reconciliation, 12.2) | Paid-status dashboard; exceptions list |

**Questions to ask the user first.** The answers change the design.
1. **Deal type:** minerals and royalties purchased, leases taken, or both?
2. **Where deals live:** a spreadsheet, a CRM, Asana, Notion, or something else? The pipeline should read from and write back to that system.
3. **Owner follow-up:** draft for approval (the current rule) or auto-send for routine reminders?
4. **Attorney threshold:** which deals need a title opinion (by price, interest size or flag type) and which close on landman work alone?

**Suggested shape:**
- A `ptk deal` command group: `intake`, `status`, `curative`, `package`, `notify`.
- A deal record stored as JSON or SQLite.
- Every stage writes an append-only event log, so status is always derivable and auditable.

### 12.2 Backlog

| Improvement | Why | Starting idea |
|---|---|---|
| **Abstract-to-takeoff bridge** (highest value) | Today a person retypes the extracted JSON into the takeoff CSV | Map `conveyances` and `reservations_exceptions` to takeoff events. Every inferred field (relative vs absolute interest, NPRI burden scope, fixed vs floating) carries a confidence and lands in a review queue. Never auto-commit an NPRI kind |
| **Pay-deck reconciliation** | Catch operator decimal errors and suspense | Import the operator's division order or revenue decimals (CSV); match owners by `name_key` and fuzzy matching; flag differences larger than 1e-8 |
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
