#!/bin/bash
# THE DEVELOPMENT WATCHDOG, the host's half (operator, 2026-11-07: "when the
# limit of usage approaches, and you are going to be frozen for more than 2
# hours, put all the developments on hold and resume the last working state
# before freezing"). An agent cannot see its own usage limit, so the node
# keeps the watch: an agent records the working state and beats
# (`louise node checkpoint save|beat|close`, app/working_state.py); when no
# beat has come for AFTER, this stops the development's host units — which
# a container cannot — and the node restores its state.
#
#   louise-watchdog.sh        on the HOST, as root; louise-watchdog.timer runs it every 10 min
#
#   1. `checkpoint watch --dry-run --json`: 0 nothing to do (exit 0, silent); 11 expired
#   2. `systemctl stop` each dev unit it names — only `louise-…` names, never the watchdog's
#      own; anything else is refused and logged
#   3. `checkpoint watch --json` again, which stops the steps recorded INSIDE the container
#      (`checkpoint run`: a unit's `docker exec` stopped leaves its process running there; SIGTERM,
#      up to 300 s, SIGKILL) and restores (10) — NOT `restore`: a beat that came
#      meanwhile means the agent is back, and `watch` sees it where `restore` would not. Units
#      first, restore second: a run killed between the two finds the checkpoint still open
#      and does both again (stopping a stopped unit is nothing), so no unit is left running
#      behind a closed checkpoint
#
#   exit 0   nothing to do, or the units stopped and the state restored
#   exit 1   the worker container is not running (apps are never started from here), docker
#            failed, a unit was refused or would not stop, or the restore failed (the
#            checkpoint stays open; the next run tries again)
#
#   CONTAINER  the worker (ix-louise-rag-worker-1)   AFTER  2h   LOG  /root/measure/watchdog.log
set -u
C=${CONTAINER:-ix-louise-rag-worker-1}
AFTER=${AFTER:-2h}
LOG=${LOG:-/root/measure/watchdog.log}
mkdir -p "$(dirname "$LOG")"
say() { echo "== $(date -u +%FT%TZ) $*" | tee -a "$LOG"; }
checkpoint() { docker exec "$C" python -m app.louise node checkpoint "$@" 2>>"$LOG"; }
field() { python3 -c 'import json, sys
d = json.loads(sys.argv[1] or "{}")
v = d.get(sys.argv[2])
print("\n".join(v) if isinstance(v, list) else ("" if v is None else v))' "$1" "$2" 2>/dev/null; }

running=$(docker inspect -f '{{.State.Running}}' "$C" 2>/dev/null)
if [ "$running" != "true" ]; then
    say "the worker container $C is not running (${running:-no such container}): nothing checked, nothing stopped; apps are not started from here"
    exit 1
fi

out=$(checkpoint watch --after "$AFTER" --dry-run --json); code=$?
case $code in
    0) exit 0 ;;
    11) ;;
    3) say "watch deferred (exit 3): another checkpoint command holds the record; the next run asks again"; exit 0 ;;
    *) say "watch --dry-run exit $code: $out"; exit 1 ;;
esac
say "EXPIRED: $(field "$out" says) (saved $(field "$out" saved_at): $(field "$out" note))"

bad=0
while IFS= read -r u; do
    [ -n "$u" ] || continue
    if ! [[ $u =~ ^louise-[A-Za-z0-9@._:-]+$ ]] || [[ $u == louise-watchdog || $u == louise-watchdog.* ]]; then
        say "REFUSED unit '$u': the watchdog stops louise-… development units only, never itself"
        bad=1
        continue
    fi
    systemctl stop -- "$u"; rc=$?
    case $rc in
        0) say "stopped $u" ;;
        5) say "$u: not loaded, nothing to stop" ;;
        *) say "$u: systemctl stop exit $rc"; bad=1 ;;
    esac
done <<< "$(field "$out" units)"

out=$(checkpoint watch --after "$AFTER" --json); code=$?
case $code in
    10) say "restored: $(printf '%s' "$out" | python3 -c 'import json, sys
r = (json.load(sys.stdin).get("restored") or {})
print("; ".join(" ".join(str(x) for x in (c.get("collection"), c.get("what"), "->", c.get("to")) if x is not None)
                for c in r.get("changed") or []) or "nothing differed")' 2>/dev/null)" ;;
    0) say "not restored: $(field "$out" says)" ;;
    *) say "restore exit $code: $out"; bad=1 ;;
esac
exit $bad
