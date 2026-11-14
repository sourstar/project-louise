#!/bin/bash
# THE SUPERVISOR FOR AN OPERATOR PASS (operator, 2026-10-30: a process the
# kernel kills recovers by itself, "not involving this agent. This applies
# to any worker"). Every pass is resumable (invariant 2: records build,
# snapshot, check, vectors build, relocate), so a pass that dies is started
# again until it COMPLETES:
#
#   deploy/supervise-pass.sh <name> <docker run arguments...>
#
#   exit 0                     done
#   exit 130 or 143            STOPPED ON PURPOSE: a `docker stop` (TrueNAS's
#                              stop included) sends SIGTERM, and a pass ends
#                              its unit and exits 130 (07 §7.7), or 143 if it
#                              did not catch it. Not restarted: the operator
#                              stops a pass to fix something, and a pass that
#                              came back by itself would undo the stop
#                              (operator, 2026-10-31). The kernel's OOM kill
#                              is SIGKILL, 137, and still comes back
#   a hold file present        stopped on purpose: `touch $HOLD` before a
#                              deliberate stop (deploy, measurement)
#   exit 2                     REFUSED (07 §7.7: "a rule stood in the way
#                              before anything was done"): not restarted, since
#                              nothing changes by itself. München, 2026-11-02:
#                              pubmed's CPU reference was refused (VeGa states
#                              no run for an unmeasured tree) and came back
#                              every minute, holding the night's chain
#   a VERDICT                  not restarted: the pass ANSWERED. A gate's exit 1
#                              (`hold embedder check`, `hold collection new`) is
#                              "this run does not reproduce itself" (invariant
#                              12), and asking again until it says yes would
#                              admit the engine it failed (2026-11-08). Case F's
#                              exit 4 (`collection new --re-create`: the swap
#                              waits for the operator's `forget`) is one too: it
#                              answers the same until the operator acts
#                              (München, 2026-11-13: legi F was run three times
#                              and called "a defect in the pass"). VERDICTS lists
#                              the codes; unless set it is "1 4" for `collection
#                              new`, "1" for `embedder check`, none otherwise
#   exit 137 or 141            again in 60 s with PARSE_PROCESSES halved
#                              (12 -> 6 -> 3 -> 2): an OOM loop backs off.
#                              141 is SIGPIPE: until 2026-10-30 a pool child the
#                              kernel killed took its parent with it
#   any other exit             again in 60 s, at most MAX_RESTARTS times — but
#                              FAST_FAILS (3) in a row each within FAST_FAIL_S
#                              (120 s) of the run's start is a DEFECT of the pass,
#                              not a crash (2026-11-09: the legal pilot's `pool`,
#                              an AttributeError at startup, ran 20 times in 19
#                              min): not restarted; fix it and run it again —
#                              and the SAME ERROR TWICE IN A ROW (the same exit
#                              and the same last line of output, however long
#                              each run took) is one too: not restarted
#                              (2026-11-14: a paragraph measurement died on one
#                              record's LegalChunkerError after ~27 min, 20
#                              times in 9 h). An OOM kill never counts, and a
#                              run that printed nothing has no last line to
#                              compare. The last run's output is kept in
#                              $LAST (default /root/supervise/<name>.last.log)
#
# Every pass carries its own memory cap (`--memory`; refused without one)
# and runs FIRST IN LINE for the kernel's OOM killer (`--oom-score-adj`,
# default 500), under the ceiling on every container together
# (deploy/docker-memory-ceiling.sh, applied before each run): the caps of
# what runs at once may add up to more than the host, and when they do the
# kernel kills inside Docker's cgroup — a pass, which comes back — never on
# the host at large. Kubernetes does the same with kubepods.slice and its
# QoS classes' oom_score_adj.
#
# A STOP OF THE SUPERVISOR IS A STOP OF THE PASS: SIGTERM or SIGINT to this
# script (`systemctl stop` of the `louise-<name>` unit it runs in, the
# watchdog's included, deploy/watchdog/) touches the hold file and `docker
# stop`s the pass with STOP_GRACE seconds (default 300) to end its unit and
# exit 130. The docker CLI forwards nothing (`--sig-proxy=false`), so the
# pass is told once. Start it as
#   systemd-run --unit louise-<name> -p KillMode=mixed -p TimeoutStopSec=330 \
#       deploy/supervise-pass.sh <name> ...
# (mixed: the SIGTERM to this script alone, so the stop is this one).
#
# The standard alternative is Docker's own `--restart on-failure` (no
# --rm), which also survives a node reboot but cannot back off the pool on
# an OOM; this is München's /root/wikidata/build-supervisor.sh, generalised.
set -u
NAME=$1; shift
HOLD=${HOLD:-/root/supervise/$NAME.hold}
LAST=${LAST:-$(dirname "$HOLD")/$NAME.last.log}
MAX_RESTARTS=${MAX_RESTARTS:-20}
FAST_FAIL_S=${FAST_FAIL_S:-120}
FAST_FAILS=${FAST_FAILS:-3}
PAUSE=${PAUSE:-60}
PROCS=${PARSE_PROCESSES:-}
CEILING=${CEILING:-$(dirname "$0")/docker-memory-ceiling.sh}
capped=
for a in "$@"; do
    case $a in -m|--memory|--memory=*|-m?*) capped=1 ;; esac
done
[ -n "$capped" ] || { echo "== $NAME: no --memory cap; every pass carries one (CLAUDE.md, \"Louise survives a crash\")"; exit 2; }
# the codes that are a pass's answer, unless VERDICTS says otherwise: a
# gate's exit 1, and case F's EXIT_SWAP_WAITS (app/louise.py, 4)
gate=
for ((i = 1; i < $#; i++)); do
    a=${!i}; j=$((i + 1)); b=${!j}
    case "$a $b" in "embedder check") gate=${gate:-1} ;; "collection new") gate="1 4" ;; esac
done
VERDICTS=${VERDICTS-$gate}
mkdir -p "$(dirname "$HOLD")"
rm -f "$HOLD"
on_stop() {
    interrupted=1
    touch "$HOLD"
    echo "== $(date -u +%FT%TZ) $NAME: stop asked; docker stop, ${STOP_GRACE:-300} s to end its unit"
    docker stop -t "${STOP_GRACE:-300}" "$NAME" >/dev/null 2>&1
}
trap on_stop TERM INT
n=0
fast=0
prev=
while :; do
    [ -x "$CEILING" ] && "$CEILING"
    env_args=()
    [ -n "$PROCS" ] && env_args=(-e "PARSE_PROCESSES=$PROCS")
    echo "== $(date -u +%FT%TZ) $NAME: run $((n + 1))${PROCS:+, PARSE_PROCESSES=$PROCS}"
    # in the background, so a stop asked meanwhile is handled now (bash runs
    # a trap only between commands, and `wait` is one it interrupts)
    started=$SECONDS
    # the pass's output to the journal as before AND to $LAST, so its last
    # line can be compared with the previous run's; its exit status through
    # a file, since a pipeline's status is tee's
    rm -f "$LAST.code"
    ( docker run --rm --sig-proxy=false --name "$NAME" --oom-score-adj "${OOM_SCORE_ADJ:-500}" \
        "${env_args[@]}" "$@" 2>&1; echo $? > "$LAST.code" ) | tee "$LAST" &
    pid=$!
    # THE PASS'S OWN STATUS, never the interrupted wait's. A trap that
    # interrupts `wait` leaves 128 + the signal (143); the trap says so
    # (`interrupted`), and waiting again returns the child's own status,
    # whether it still runs or bash already reaped it (bash keeps an ended
    # background child's status). Until 2026-11-12 the second wait was asked
    # only while `kill -0` still saw the child: a pass that ended during the
    # trap's `docker stop` (load 30 on the node) was reported 143, not 130
    interrupted=
    wait "$pid"
    while [ -n "$interrupted" ]; do interrupted=; wait "$pid"; done
    code=$(cat "$LAST.code" 2>/dev/null); code=${code:-1}
    last=$(grep -v '^[[:space:]]*$' "$LAST" 2>/dev/null | tail -1)
    echo "== $(date -u +%FT%TZ) $NAME: exit $code"
    [ "$code" -eq 0 ] && exit 0
    [ -e "$HOLD" ] && { echo "== $NAME: held ($HOLD), not restarted"; exit "$code"; }
    for v in $VERDICTS; do
        [ "$code" -eq "$v" ] && { echo "== $NAME: exit $code is the pass's verdict (VERDICTS=\"$VERDICTS\"), not restarted"; exit "$code"; }
    done
    if [ "$code" -eq 2 ]; then
        echo "== $NAME: refused (exit 2), not restarted: a rule stood in the way; fix it and run it again"
        exit 2
    fi
    if [ "$code" -eq 130 ] || [ "$code" -eq 143 ]; then
        echo "== $NAME: stopped on purpose (SIGTERM, exit $code), not restarted; run it again to resume"
        exit "$code"
    fi
    if [ "$code" -ne 137 ] && [ "$code" -ne 141 ] && [ -n "$last" ] && [ "$code $last" = "$prev" ]; then
        echo "== $NAME: exit $code with the same last line twice in a row (\"$last\"): a defect in the pass, not a crash; not restarted — fix it and run it again ($LAST)"
        exit "$code"
    fi
    if [ "$code" -eq 137 ] || [ "$code" -eq 141 ]; then prev=; else prev="$code $last"; fi
    if [ "$code" -ne 137 ] && [ "$code" -ne 141 ] && [ $((SECONDS - started)) -lt "$FAST_FAIL_S" ]; then
        fast=$((fast + 1))
        if [ "$fast" -ge "$FAST_FAILS" ]; then
            echo "== $NAME: exit $code within ${FAST_FAIL_S} s of its start $fast times in a row: a defect in the pass, not a crash; not restarted — fix it and run it again"
            exit "$code"
        fi
    else
        fast=0
    fi
    n=$((n + 1))
    [ "$n" -ge "$MAX_RESTARTS" ] && { echo "== $NAME: $n restarts, giving up"; exit "$code"; }
    if [ "$code" -eq 137 ] || [ "$code" -eq 141 ]; then
        cur=${PROCS:-$(nproc)}
        PROCS=$(( cur / 2 > 2 ? cur / 2 : 2 ))
    fi
    sleep "$PAUSE"
done
