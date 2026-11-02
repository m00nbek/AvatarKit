#!/bin/bash
# Deletes this pod after $1 hours, so a forgotten clothes pod stops billing (pod.py starts it).
# One per pod: a second start finds the first still waiting and leaves.
[ -f /tmp/deadman.pid ] && kill -0 "$(cat /tmp/deadman.pid)" 2>/dev/null && exit 0
echo $$ > /tmp/deadman.pid
export $(tr '\0' '\n' < /proc/1/environ | grep -E '^RUNPOD_(API_KEY|POD_ID)=' | xargs)
sleep $(( $1 * 3600 ))
runpodctl remove pod "$RUNPOD_POD_ID"
