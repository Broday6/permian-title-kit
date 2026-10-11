"""Deal follow-up: from a signed deal to a recorded, paid interest.

The acquisition team hands over a signed deal; ``ptk deal`` follows it through
six stages, each built on the kit's title tools:

1. intake    ``intake.py``   parse and map every tract, check the record, open the TX/NM checklists
2. verify    ``verify.py``   ledger / lease-chain replay: seller owns what it sold, as of the effective date and today
3. curative  ``tasks.py``    every flag becomes a tracked task; ``drafts.py`` drafts the cure; opinions clear legal questions
4. closing   ``drafts.py``   conveyances in each state's form, Exhibit A, closing statement; ``actions.closing`` gates funds
5. record    ``actions.py``  recordings per county, agency filings, transfer notices to operators and payors
6. payment   ``actions.py``  division orders and checks reconciled against the title decimal

State lives in an append-only, hash-chained SQLite log (``store.py``) and is
rebuilt from it on every read (``state.py``), so status is always auditable.
Messages to owners and operators go through ``outbox.py``: substantive ones
wait for a person; routine reminders go out on their own only under the
policy in ``rules.json``. ``server.py`` is a local dashboard over the same
functions the CLI uses.
"""
