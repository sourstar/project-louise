# NeuRIS fixtures — provenance

**What these are.** LegalDocML.de files copied verbatim from the German
federal legal information portal's own public source repository,
`github.com/digitalservicebund/ris-search` at commit `81b58f82`
(licence GPL-3.0, the repository's):

| here | there |
|---|---|
| `eli/bund/bgbl-1/2000/s1016/…`, `2020/s1234/…` (four expressions), `2025/130/…`, `2025/999/…` | `backend/e2e-data/norm/eli/…` |
| `eli/bund/bgbl-1/1991/s101/…` (with its annex, two images and a PDF, all three empty in the source) | `backend/src/testFixtures/resources/data/LDML/norm/eli/…` |

**They are the portal's TEST data**: lorem ipsum, "Fiktive" acts, invented
GUIDs (`a1111111-…`), dates as far as 2920. The FORMAT is real — the
namespaces, the FRBR identification, the lifecycle and temporalData, the
`ris:` metadata, the annex files, the ELI layout of the paths — and that is
what the parser is tested against. No number measured on them says anything
about the real corpus.

**What is NOT the origin's.**

- `handmade/regelungstext-1.xml` — the portal's `2025/130` act with a
  `ris:verweise` block, an `akn:analysis` block (active modifications) and one
  `akn:ref` ADDED BY HAND, marked in an XML comment at its head. The
  portal's fixtures carry none of these; whether NeuRIS fills them is owed
  from the node. It tests the relations and closures code only.
- The API's JSON (the listing item and the per-expression answer) is not in
  the repository as data. `tests/neuris_support.py` builds it in the shape
  of `frontend/src/public/openapi.json` (`LegislationExpressionSchema`,
  `LegislationExpressionSearchSchema`, `ChangelogResponse`) at the same
  commit, its values read from the XML beside it.
- The manifestation zips are built by the tests from these files, members
  named by the expression's ELI path as the portal's `ZipManager` names its
  S3 keys.

## Hand-made, in tests/test_neuris.py (2026-11-08)

- `RECHTSETZUNGSDOKUMENT`: a `rechtsetzungsdokument-1.xml` in the shape
  `norm-rechtsetzungsdokument.xsd` (ris-search@81b58f82) fixes, with invented
  GUIDs. The node's probe found one in every manifestation zip it opened
  (München, 2026-11-08, `/root/legal-probe-20261108.log`); none is in
  ris-search's test data.
