#!/bin/bash
# THE RELEASE OF A PASS HELD BY A MEMORY KILL (operator, 2026-11-19; CLAUDE.md,
# "An OOM kill is not relaunched until its cause is known"):
#
#   deploy/release-oom.sh <name> --cause TEXT        the cause determined (what it
#                                                    was processing, the measured peak)
#   deploy/release-oom.sh <name> --diagnostic TEXT   the diagnostic added that will
#                                                    name it the next time
#
# The hold supervise-pass.sh wrote ($OOM_HOLD, beside its hold file) is kept,
# with the release, in oom-holds.log in the same directory; the pass may then
# be started again. The worker's own hold is released by
# `louise node release-oom` (app/oom_hold.py).
set -u
NAME=${1:?usage: release-oom.sh <name> --cause TEXT | --diagnostic TEXT}
KIND=${2:-}
TEXT=${3:-}
case "$KIND" in --cause|--diagnostic) ;; *) echo "release-oom: --cause TEXT or --diagnostic TEXT"; exit 2 ;; esac
[ -n "$TEXT" ] || { echo "release-oom: say it in words"; exit 2; }
HOLD=${HOLD:-/root/supervise/$NAME.hold}
OOM_HOLD=${OOM_HOLD:-$(dirname "$HOLD")/$NAME.oom-hold}
[ -e "$OOM_HOLD" ] || { echo "release-oom: no memory hold for $NAME ($OOM_HOLD)"; exit 2; }
LOG=$(dirname "$OOM_HOLD")/oom-holds.log
{ echo "== released $(date -u +%FT%TZ) $NAME ${KIND#--}: $TEXT"; cat "$OOM_HOLD"; } >> "$LOG"
rm -f "$OOM_HOLD"
echo "== $NAME released (${KIND#--}: $TEXT); its hold kept in $LOG"
