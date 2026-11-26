#!/usr/bin/env bash
# Read-only health check for the portfolio advisor. Touches nothing; prints no secrets.
cd /home/ubuntu/portfolio_advisor || { echo "FATAL: app dir missing"; exit 1; }
echo "=== PORTFOLIO ADVISOR HEALTH ($(TZ=Asia/Kolkata date '+%F %T %a')) ==="
echo "git  : $(git rev-parse --short HEAD)"
for s in openalgo portfolio-advisor portfolio-dashboard; do
  printf '  %-20s %s\n' "$s" "$(systemctl is-active "$s" 2>/dev/null)"
done
echo "restarts: $(systemctl show portfolio-advisor -p NRestarts --value)"
if [ -f data/snapshot.json ]; then
  python3 - <<'PY'
import json
s = json.load(open("data/snapshot.json"))
print(f"snapshot: {s['as_of']}  positions={s['n_positions']}  alerts={len(s['alerts'])}  no_quote={len(s['no_quote'])}")
PY
else
  echo "snapshot: none yet (expected before the first market-hours cycle)"
fi
[ -f data/holdings.xlsx ] && echo "holdings: $(stat -c '%y' data/holdings.xlsx | cut -c1-16)" || echo "holdings: MISSING (upload data/holdings.xlsx)"
[ -f data/memory.json ]   && echo "memory  : $(stat -c '%y' data/memory.json | cut -c1-16)"   || echo "memory  : MISSING (upload data/memory.json)"
echo "dashboard /: HTTP $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5041/)  (403 = token gate working)"
tail -n 3 advisor.log 2>/dev/null
