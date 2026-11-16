# EuroVoc and the EU authority tables: byte-exact excerpts

Taken on the node, 2026-11-16, from the Publications Office's downloads in
/mnt/tank/louise-measure/euvoc-2026-11-16 (SHA256SUMS there):

- `eurovoc_in_skos_core_concepts.zip` (EuroVoc 4.24, SKOS core);
- `corporatebodies-skos.rdf`, `subject-matter-skos.rdf` (20260923-0);
- `dir-eu-legal-act-skos.rdf` (20171004-0).

`<name>.head.rdf` is each file from its first byte to the end of its third
complete concept. `<name>.<id>.rdf` is one concept found by its id.
`<name>.dcat.json` is the data.europa.eu DCAT record's rights fields
(api/hub/search/datasets/<id>). `counts.json` gives the counts per file.
The excerpts are not converted (`.gitattributes`: -text).
