# The closed-list sweep for the derived-reading kind (2026-11-28)

*A record. The derived-reading kind ([derived-reading-amendment.md](derived-reading-amendment.md))
was SUPERSEDED the same day by the operator's proposal: the Met collection
carries only metadata and images, and its descriptions become an annotations
collection with embeddings ([image-collections.md](image-collections.md)).
The sweep found 77 places in docs and code that enumerate what may exist.
What still binds the annotations route is below.*

## Findings that bind ANY reading of figures into a collection

1. **Every `native_id` is an integer.** The text index (`app/fts.py` `book_id`),
   the vector table (`app/embeddings.py` `upsert_book(book_id: int)`) and the
   chunk-id regex (`_CHUNK_ID`, one colon, numeric) all assume it. A uid such as
   `reading:<uid>:<hex>` is refused at upsert. Legal plugins hash to an int
   (`app/plugins/eli.py:139`). Annotations' `annotations:<n>` is already fine.
2. **`origin_hash` is BLAKE2b-128** (09 §9.5, `app/hold/records.py`
   `refuse_without_origin_hash`), not sha256. A figure's sha256 goes elsewhere.
3. **`title` is required** in every record line (`app/pack/records.py`
   `RECORD_KEYS`); "absent" means `""`.
4. **`origin` is a string locator** (09 §9.5, `app/hold/check.py`).
5. **Codex versions check with `>=`** (`app/hold/codex.py`): a new version
   inherits `space.exactness` and `space.document` as required.
6. **Annotations have no space anywhere in code**: `_validate_annotations`,
   `space` null, no worker stage, `cascade.py` edges `annotates | input`. An
   annotations collection WITH a space touches the validator, the worker's
   stage selection (an annotations collection is skipped), `copy_state.py`'s
   origin-check case, and ask-time ranking (05 §5.6).
7. **Tests asserting "unsupported Codex version 13"**:
   `tests/hold/test_codex.py:356-366`, `tests/hold/test_backend_engine.py:192-194`;
   and `deploy/16f/deploy-louise-madlad-t5.sh:223` greps the exact
   `CODEX_VERSIONS` line.
8. **Licence is one class and one terms hash** (`app/licensing.py`): "the
   stricter of two" needs a precedence function.

Stale, outside the amendment: 09 §9.18 still says "record line | 2", while
`record_line` 3 exists (09-formats.md:2512).

**Binding on the annotations route** (Architecture, 2026-11-28, via München):
- uids are `annotations:<n>`;
- `origin_hash` is BLAKE2b-128 (the sha256 stays in the receipt);
- `title` is `""` when there is none;
- `origin` is a string locator naming the Met record and the figure's sha256;
- the new annotations Codex version states the `>=` inheritance for
  `exactness` and the document instruction.

The build's scope is item 6: the validator, stage selection, the
`copy_state` origin check and ask-time ranking, each with tests. The stale
§9.18 row is fixed on main (#1012).
