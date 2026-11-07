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
`checkpoint watch` restores the node's mode, its collections' pauses and
their embed settings to the checkpoint, and closes it. Freezes are never
changed.

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
```

`save` records the state as it is NOW, so it comes first; a second `save`
while one is open is refused unless `--replace` (the current state is the
working state; the open one's units are carried). `restore` puts the
checkpoint back by hand; `restore --dry-run` and `status` show what it would
change.
