# The node's model files, as they ran on 2026-11-25 08:1xZ

Copied byte for byte from the node's `/mnt/ssd5/apps/localai/models/` (the model
server's own directory) for every file that has a counterpart in
`deploy/vega/stage/models/` on main 6033dfe1. LocalAI rewrites a file it loads
with its keys sorted, so key order is LocalAI's.

Why: main's staged files were found NOT to be what runs (14 of 43 identical as
YAML values, 21 different, 8 not on the node) — the live encoders carry
`context_on_demand` and `context_canary:true`, `vega-qwen3-embedding-4b` a batch
of 2048, `louise-translate-madlad` a context of 2048 with four slots and no
`fit:off`, and the engine is `d531d5d804f1`. Until main's staged files are these,
nobody runs the stage from main: it would change model files and, for the
encoders, their run ids.

Not here: the node's other model files (candidates and tools with no staged
counterpart) and every `qual-*` copy made for a measurement.
