# Real Project Gutenberg RDF records

Copied byte for byte, read-only, from the node's Gutenberg mirror
(`/mnt/tank/media/public/books/project_gutenberg/<N>/pg<N>.rdf`,
the gutenberg collection's corpus) on 2026-11-26, for the
fiction-vs-fact classifier's tests (architecture's read of 12b31c4d).

The ids architecture named: 1170, 674, 3623, 656, 7148, 3176, 1974
(Aristotle's Poetics), 30, 10, 17 (Book of Mormon), 216 (Tao Te Ching),
2017 (Dhammapada), 5, 686, 89, 14, 732, 566, 7, 4763 (Game of Logic),
1962, 5428, 151, 201 (Flatland) and 2600 (War and Peace).

Added for shelf-only cases (Category shelves with no FORM shelf and an
LCSH heading ending in "-- Fiction"):
- 1874 The Railway Children: Children & Young Adult Reading, Adventure,
  British Literature;
- 119 A Tramp Abroad: Humour, Travel Writing, American Literature
  (a travel book whose LCSH says "-- Fiction");
- 1194 The Adventures of Louis de Rougemont: Biographies, Adventure.

Over the 79,833 RDF records the node holds, 75,698 carry a
pgterms:bookshelf and 75,259 carry at least one "Category: ..." shelf.
