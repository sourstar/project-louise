# deploy/watchdog — the development watchdog

The operator, 2026-11-07: *"when the limit of usage approaches, and you are
going to be frozen for more than 2 hours, put all the developments on hold
and resume the last working state before freezing."* An agent cannot see its
own usage limit, so the node keeps the watch. The node's half is `louise node
checkpoint` (docs/07 §7.3, app/working_state.py); this directory is the
host's half, because a container cannot stop a host systemd unit.

## Install (on the node, as root)

```bash
S=/mnt/ssd4/apps/louise-rag/src/deploy/watchdog     # the deployed tree
install -d /root/louise-watchdog /root/measure
install -m 755 $S/louise-watchdog.sh /root/louise-watchdog/   # a private copy: a deploy replaces src/
install -m 644 $S/louise-watchdog.service $S/louise-watchdog.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now louise-watchdog.timer
systemctl list-timers louise-watchdog.timer                    # the next run
```

`/etc/systemd/system` is reset by a TrueNAS **update** (not by a reboot):
after one, run the last four lines again. Uninstall: `systemctl disable --now
louise-watchdog.timer`, then remove the two unit files.

## What a run does

Every 10 minutes (`louise-watchdog.sh`): if the worker container
`ix-louise-rag-worker-1` is not running it logs that and exits 1 — it never
starts an app. Otherwise it asks `checkpoint watch --dry-run`; when a
checkpoint is open and no beat has come for 2 h (and no declared step is
still running), it `systemctl stop`s each dev unit — `louise-…` names only,
never `louise-watchdog`, anything else refused and logged — and then
`checkpoint watch` stops the steps recorded inside the container (below),
restores the node's mode, its collections' pauses and their embed settings
to the checkpoint, and closes it. Freezes are never changed.

## The log

`/root/measure/watchdog.log` holds every action with its UTC time (a run
with nothing to do writes nothing); `journalctl -u louise-watchdog` holds
every run. `docker exec ix-louise-rag-worker-1 python -m app.louise node
checkpoint status` says what is open, the deadline and what a restore would
change.

## How an agent uses it

```bash
L="docker exec ix-louise-rag-worker-1 python -m app.louise node checkpoint"
$L save --note "Arc engine A/B, this session" --dev-unit louise-engine-ab5   # BEFORE changing anything
$L beat                          # at least every hour while working
$L beat --dev-unit louise-onednn-arms   # a unit started later
$L beat --for 5h                 # before a known-long unattended step (at most 24 h)
$L close                         # done, and the node is in a good state

# A PASS WITH A PARSE POOL (`hold records build`, `hold collection new`, snapshot, check,
# vectors build): its OWN container, cap and supervisor, in a louise-… unit — never
# `docker exec` in the worker, whose 24 GiB cap it would share (2026-11-08: pubmed9's
# records build, OOM-killed there with the worker, 3,914 s lost; deploy/supervise-pass.sh)
systemd-run --unit louise-pubmed9-records -p KillMode=mixed -p TimeoutStopSec=330 \
    /root/louise-rag/deploy/supervise-pass.sh pubmed9-records --memory 16g \
    --volumes-from ix-louise-rag-worker-1 --network container:ix-louise-rag-worker-1 \
    --env-file <the worker's env> -w /srv <image> python -m app.louise hold records build ...
$L beat --dev-unit louise-pubmed9-records

# A SHORT STEP INSIDE THE CONTAINER (a fetch, a measurement, `hold embedder check`):
docker exec ix-louise-rag-worker-1 python -m app.louise node checkpoint run --note "pubmed gate" -- \
    python -m app.louise hold embedder check pubmed
# one started some other way: record its pid as the worker container sees it
$L beat --dev-pid 4242
```

**Why `run`** (München, 2026-11-07): `systemctl stop` of a unit whose command
is `docker exec … python -m app.louise …` does NOT stop the process in the
container — the docker CLI does not forward the signal. `run` records the
step's pid, command line and start time in the checkpoint, forwards SIGTERM
and SIGINT to it and exits with its code; the restore SIGTERMs each recorded
step still running (the same pid, command line and start time: a reused pid
is never signalled), waits up to 300 s for it to end its unit, then SIGKILLs
what is left, and says each — before it changes any state. `run` does not
beat: the agent's beat is the liveness signal. Run steps in the WORKER
container, where `watch` runs: a pid of another container is said and
skipped. A supervised pass is stopped by its unit instead: the supervisor
takes the unit's SIGTERM, holds itself and `docker stop`s the pass, which
ends its unit and exits 130 (deploy/supervise-pass.sh). `--network
container:` gives it the worker's names for the model servers (`new`'s
calibration and gate).

`save` records the state as it is NOW, so it comes first; a second `save`
while one is open is refused unless `--replace` (the current state is the
working state; the open one's units and processes are carried). `restore`
puts the checkpoint back by hand; `restore --dry-run` and `status` show what
it would change and stop.
