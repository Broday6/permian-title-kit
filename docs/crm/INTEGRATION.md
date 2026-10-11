# Landowner CRM — Integration & Handoff Guide

This package hands the CRM off so it can be folded into an existing tool. The app is
deliberately built around a **single pluggable storage seam**: swap a couple of
functions and the whole UI runs on your backend and mailer instead of the built-in ones.

## Files in this handoff

| File | What it is |
|---|---|
| `land-crm.html` | The complete app — UI, logic, styling — in one file (~105 KB, no framework, no build step, no external dependencies). |
| `land-crm-schema.json` | JSON-Schema definitions for every record type (Customer, Interest, Call, Email, Document, Settings). |
| `land-crm-data-export.json` | The current data, in the app's own backup format — restorable in the app and usable as seed/sample for your tool. |
| `INTEGRATION.md` | This guide. |

## 1. Architecture in one paragraph

Everything the UI shows lives in a single in-memory object called `data`:

```js
data = {
  customers: [ /* Customer records, see schema */ ],
  tagColors: { "<tag>": 0..8 },   // tag -> color index
  knownTags: [ "<tag>", ... ],    // tags created but not yet used
  lastBy:    "<name>",            // remembered 'called by'
  email:     { from, sig, webhook, steps:[{day,subject,body}] }
}
```

The UI **renders from `data`**; every mutation ends by calling **`save()`**. On
startup **`initStore()`** fills `data` and calls `renderAll()`. That's the whole
contract. To integrate, you replace how `data` is loaded and saved — nothing in the
UI code has to change.

The app currently ships with two storage backends behind that seam:
- **Local** (opened as a plain file): `localStorage`, single user.
- **Cloud** (published as a shared app): a hosted document store + blob store, multi-user, live-synced.

You add a third: **your tool's backend.**

## 2. The storage seam — the functions to replace

All of these are near the top of the `<script>` block, in one contiguous section
labelled `STORAGE`. Replace the bodies; keep the names.

| Function | Replace it to… |
|---|---|
| `load()` / `loadCloud()` | Fetch your data into `data` on startup (GET customers + settings), then `normalize()`. |
| `save()` | Persist `data` to your backend. Debounce if you like (current cloud mode debounces 400 ms). |
| `subscribeCloud()` + `applyCustSnap()` / `applyMetaSnap()` | OPTIONAL live multi-user sync. If you poll or don't need realtime, make these no-ops. |

**Minimal integration** (no realtime) is two functions:

```js
async function load() {
  const [custs, settings] = await Promise.all([
    fetch('/api/customers').then(r => r.json()),
    fetch('/api/settings').then(r => r.json())
  ]);
  data.customers = custs;
  Object.assign(data, settings);   // {email, tagColors, knownTags, lastBy}
  normalize();
}

let t;
function save() {                  // called after every change
  clearTimeout(t);
  t = setTimeout(() => {
    fetch('/api/state', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
  }, 400);
}
```

Then, at the bottom of the script, the init routine calls `initStore()`; point it at
your `load()` (or just call `load().then(renderAll)`).

### Per-record writes (recommended for multi-user)

Each `Customer` has a stable `id`. The cloud backend already shards by it — **one
document per customer** in a `customers` collection, plus **one settings document**
(`meta/app`). That maps cleanly onto a relational or document store:

- `customers` table/collection, primary key = `customer.id`, body = the Customer JSON.
- `settings` single row/doc, body = `{ email, tagColors, knownTags, lastBy }`.

If you want per-record writes instead of saving the whole blob, look at `flushCloud()`
in the source — it diffs `data.customers` against a last-synced snapshot and writes
only the changed/added/deleted customer records. Reuse that pattern against your API:
`PUT /api/customers/:id`, `DELETE /api/customers/:id`, `PUT /api/settings`.

## 3. Data model

Full field definitions and types are in **`land-crm-schema.json`**. Summary:

- **Customer** — the core record (owner). Nested arrays: `interests[]`, `calls[]`,
  `emails[]`, `documents[]`. Note: money/percent/acre fields are stored as **strings
  as entered**; the UI parses them. Dates are local `YYYY-MM-DD` strings.
- **Interest** — one mineral/royalty/working-interest holding (maps 1:1 to a row of
  Case ID / Well / Interest Type / % / Buy Signal / Source PDF / exhibit).
- **Call** — one logged call (date, outcome, success, by, notes, follow-up).
- **Email** — one sent/logged follow-up email (step, date, subject, status, via).
- **Document** — a pointer to an attached file (see §5).
- **Settings** — `email` cadence/config, `tagColors`, `knownTags`, `lastBy`.

Lead stages (pipeline order): `New → Contacted → Interested → Negotiating → Under
Contract → Closed`, plus `Dead` (off-pipeline). Defined in the `STATUSES` / `PIPELINE`
arrays near the top of the script.

## 4. Email follow-ups

Config lives in `data.email`: `{ from, sig, webhook, steps:[{day, subject, body}] }`.
Merge fields usable in subject/body: `{{name}} {{firstName}} {{county}} {{well}}
{{from}} {{sig}}`.

Sending is itself pluggable:
- **With a webhook** (`data.email.webhook` set): when an email is due, the app POSTs
  JSON to that URL and marks the step sent. The payload is:

  ```json
  { "to": "...", "from": "...", "subject": "...", "body": "...",
    "name": "<owner>", "customerId": "<id>", "step": 1 }
  ```

  Point it at your tool's mailer endpoint (or a Zapier/Make hook → Gmail/Outlook).
- **Without a webhook**: the app composes the email for manual send and logs it.

To wire sending straight into your tool instead, replace `postWebhook()` (fire the
send) and/or `sendDue()` (decides the next step and schedules the follow-up). The
cadence/scheduling logic is backend-agnostic and can stay as-is.

## 5. File attachments

A `Customer.documents[]` entry is just a pointer: `{ id, name, type, size, date }`.
The **bytes live in blob/object storage**, never in the record (that's what keeps
records tiny). In the shipped cloud build, `id` is a blob id and the file is served
at `"/_blob/" + id`.

To use your own storage: on upload, push the file to your bucket (S3/GCS/your DB blob)
and store the returned key as `document.id`; change the link builder in `docsHTML()`
(currently `/_blob/${id}`) to your file URL scheme. The upload entry point is
`uploadDocs()`; deletion is `removeDoc()`. Supported types in the current build: PDF,
images, and csv/json/txt (Office files should be converted to PDF first) — adjust the
`assetType()` allow-list for your storage's limits.

## 6. Ways to embed it

1. **Host as-is** — serve `land-crm.html` from your app and replace `load()`/`save()`
   (and optionally the file + email hooks). Fastest path; the whole UI comes along.
2. **Iframe** — drop the page into your tool in an `<iframe>` and have it talk to your
   API through the same seam. Good isolation.
3. **Lift the model only** — take the schema and the per-section render/logic functions
   (`renderTable`, `renderBoard`, `renderQueue`, `openDetail`, the import wizard in
   `openImport`/`doImport`, the email engine) into your own front end.

Everything is vanilla JS with one `<style>` and one `<script>`; there are no build
tools, imports, or runtime dependencies to reconcile.

## 7. Importing the existing data

`land-crm-data-export.json` is the current dataset in the app's backup shape
(`{ customers, tagColors, knownTags, lastBy, email }`). To load it into your tool,
read `customers[]` into your customers table and the rest into settings. To load it
back into the app itself, use the ⋯ menu → **Restore backup**.

## 8. Notes / gotchas

- IDs are client-generated short strings; keep them as the primary key so call/email/
  document history stays attached.
- Numbers are strings by design — parse at read time (`parseFloat`) if your DB column
  is numeric.
- Dates are **local** `YYYY-MM-DD` (the app intentionally avoids UTC to prevent an
  off-by-one at day boundaries). Preserve that if you reformat.
- There are no server-side computations — all derived values (pipeline counts,
  "due" flags, pipeline value, email scheduling) are computed in the client from the
  records, so your backend only needs to store and return the JSON.
