#!/usr/bin/env bash
set -euo pipefail

output=${1:?usage: host.sh OUTPUT.csv}
running=1
trap 'running=0' TERM INT

echo "timestamp_unix,cpu_user,cpu_nice,cpu_system,cpu_idle,cpu_iowait,cpu_irq,cpu_softirq,cpu_steal,load1,load5,load15,run_queue,context_switches,mem_total_kb,mem_available_kb,swap_total_kb,swap_free_kb,network_rx_bytes,network_tx_bytes,network_rx_packets,network_tx_packets,network_rx_drops,network_tx_drops,tcp_established,tcp_retransmits,disk_read_sectors,disk_write_sectors" >"$output"

while (( running )); do
    read -r _ user nice system idle iowait irq softirq steal _ < /proc/stat
    read -r load1 load5 load15 _ < /proc/loadavg
    run_queue=$(awk '/^procs_running / {print $2}' /proc/stat)
    context_switches=$(awk '/^ctxt / {print $2}' /proc/stat)
    mem_total=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)
    mem_available=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
    swap_total=$(awk '/^SwapTotal:/ {print $2}' /proc/meminfo)
    swap_free=$(awk '/^SwapFree:/ {print $2}' /proc/meminfo)
    read -r rx_bytes tx_bytes rx_packets tx_packets rx_drops tx_drops < <(
        awk -F '[: ]+' 'NR > 2 {rxb += $3; rxp += $4; rxd += $6; txb += $11; txp += $12; txd += $14} END {print rxb + 0, txb + 0, rxp + 0, txp + 0, rxd + 0, txd + 0}' /proc/net/dev
    )
    read -r tcp_established tcp_retransmits < <(
        awk '$1 == "Tcp:" {line++; if (line == 2) print $10, $13}' /proc/net/snmp
    )
    disk_read_sectors=0
    disk_write_sectors=0
    while read -r _ _ device _ _ read_sectors _ _ _ write_sectors _; do
        if [[ -d "/sys/block/$device" && "$device" != loop* && "$device" != ram* ]]; then
            disk_read_sectors=$((disk_read_sectors + read_sectors))
            disk_write_sectors=$((disk_write_sectors + write_sectors))
        fi
    done </proc/diskstats
    echo "$(date +%s),$user,$nice,$system,$idle,$iowait,$irq,$softirq,$steal,$load1,$load5,$load15,$run_queue,$context_switches,$mem_total,$mem_available,$swap_total,$swap_free,$rx_bytes,$tx_bytes,$rx_packets,$tx_packets,$rx_drops,$tx_drops,$tcp_established,$tcp_retransmits,$disk_read_sectors,$disk_write_sectors" >>"$output"
    sleep 1 &
    wait $! || true
done
