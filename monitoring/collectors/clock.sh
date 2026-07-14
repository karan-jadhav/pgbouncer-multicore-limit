#!/usr/bin/env bash
set -euo pipefail

date --iso-8601=ns
timedatectl show --property=NTPSynchronized --property=Timezone 2>/dev/null || true
chronyc tracking 2>/dev/null || true
