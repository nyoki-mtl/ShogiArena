# CSA event-log contract fixture

`csa-event-log-contract.jsonl` is a **vendored artifact**. It is produced by
`rsshogi-csa`, copied here byte for byte, and never edited in this repository.

Why bytes rather than a shared description: the two sides of this contract are
in different repositories and different languages, and the last drift between
them — a producer that emitted a time-control dialect the consumer did not
parse — passed every assertion on both sides, because each side was checking its
own description of the format. Agreeing on one artifact is what turns that class
of drift into a failing test.

Why vendored rather than read from a sibling checkout: a test that reaches
outside its own repository only passes on a machine where both are present, at
whatever revisions happen to be checked out. The bytes travel by being committed.

## Provenance

`PROVENANCE.json` records the producing commit and the SHA-256 of the bytes.
`tests/unit/test_csa_contract_fixture.py` verifies the digest, so a copy that was
edited here — or vendored without updating its provenance — fails.

## Updating

1. In `rsshogi-csa`, make the schema change and regenerate:
   `cargo test -p rsshogi-csa-bridge --test contract_fixture -- --ignored`
2. Run `cargo test -p rsshogi-csa-bridge` there; the producer asserts its own
   invariants against the same file.
3. Commit in `rsshogi-csa` first — the consumer must be able to name a commit
   that exists.
4. Copy the file here, regenerate `PROVENANCE.json`, and run the consumer tests.

The reader-before-writer rule from task 0012 still applies to *deployment*: a new
record type is taught to this repository before a bridge starts emitting it.
