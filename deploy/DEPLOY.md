# Deploying portfolio_advisor (same server and method as AI Trader v2)

Deploy only when the owner asks, outside market hours (09:15-15:30 IST). `main` must pass
`pytest tests -q` and `python advisor.py --replay tests/fixtures/day.json` first.
Set `KEY` and `HOST` as in the AI Trader v2 deployment checklist (not stored in this repo).

## One-time setup (creates NEW services only; touches nothing of v1 or v2)
```bash
ssh -i "$KEY" ubuntu@$HOST '
  cd ~ && git clone https://github.com/actlceo/portfolioadvisor.git portfolio_advisor &&
  cd portfolio_advisor && python3 -m venv venv && venv/bin/pip install -q -r requirements.txt &&
  mkdir -p data && cp .env.example .env && chmod 600 .env &&
  sudo cp deploy/portfolio-advisor.service deploy/portfolio-dashboard.service /etc/systemd/system/ &&
  sudo systemctl daemon-reload && cp deploy/healthcheck.sh ~/pm_healthcheck.sh && chmod +x ~/pm_healthcheck.sh'
```
Then, on the server, edit `~/portfolio_advisor/.env` yourself:
- `OPENALGO_API_KEY` (same key v2 uses; do NOT point this service at v2's `.env`, which also holds
  Zerodha credentials this project must never see), `OPENALGO_BASE_URL=http://127.0.0.1:5000`.
- `SMTP_USER`, `SMTP_PASS` (app password), `ALERT_TO`.
- `PM_DASH_TOKEN` = a long random string (the dashboard refuses to serve on 0.0.0.0 without it).

Upload the two data files (they are not in git):
```bash
scp -i "$KEY" holdings.xlsx ubuntu@$HOST:~/portfolio_advisor/data/holdings.xlsx
scp -i "$KEY" memory.json   ubuntu@$HOST:~/portfolio_advisor/data/memory.json
```
Open port 5041 in the Lightsail firewall (console) if you want the dashboard from outside.

Start:
```bash
ssh -i "$KEY" ubuntu@$HOST 'sudo systemctl enable --now portfolio-advisor portfolio-dashboard && ~/pm_healthcheck.sh'
```

## Update (every later change)
```bash
ssh -i "$KEY" ubuntu@$HOST 'cd ~/portfolio_advisor && git pull --ff-only origin main && git log --oneline -1'
ssh -i "$KEY" ubuntu@$HOST 'sudo systemctl restart portfolio-advisor portfolio-dashboard && ~/pm_healthcheck.sh'
```
Restart ONLY `portfolio-advisor` and `portfolio-dashboard`. Never touch `trading-bot`, `dashboard-v2`,
`openalgo` or v1 units.

## Refreshing holdings after you trade
Download the new broker export and `scp` it over `data/holdings.xlsx` (same name). The next cycle
(<= 15 min) uses it. `memory.json` is refreshed from the weekly/monthly research.

## Verify
`~/pm_healthcheck.sh`: services `active`, restarts `0`, snapshot recent in market hours,
dashboard `/` returns 403 without the token.

## Rollback
`git checkout <prev-good-commit>` on the server, restart the two units, or
`sudo systemctl disable --now portfolio-advisor portfolio-dashboard` to stop everything.
