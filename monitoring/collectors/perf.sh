#!/usr/bin/env bash
set -euo pipefail

pid=${1:?usage: perf.sh PID DURATION_SECONDS OUTPUT}
duration=${2:?usage: perf.sh PID DURATION_SECONDS OUTPUT}
output=${3:?usage: perf.sh PID DURATION_SECONDS OUTPUT}

exec perf stat \
    --pid "$pid" \
    --event cycles,instructions,branches,branch-misses,cache-references,cache-misses,context-switches,cpu-migrations,page-faults \
    --output "$output" \
    sleep "$duration"
