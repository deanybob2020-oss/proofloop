# ProofLoop v0.1

MVP to validate one core question:

Can ProofLoop prove one thing that helps a user within 14 days?

## Scope implemented

- Email register/login
- Focus area selection: Energy, Sleep, Focus, Stress
- Experiment selection
- Daily check-in:
  - score (1-10)
  - followed experiment (yes/no)
  - optional note
- Results page:
  - average score when followed
  - average score when not followed
  - impact percentage
  - confidence level (low/medium/high)
- Admin validation page (`/admin/metrics`) with:
  - total users
  - active users today
  - users with 1+ check-in
  - users with 5+ check-ins
  - users completed 7 days
  - experiments started/completed
  - average score improvement
  - retention day 2 / day 7 / day 14
- CSV export for metrics and check-ins
- Daily reminder email (CLI command + admin trigger)

## Experiment library (Phase 2 starter)

Energy experiments seeded by default:

1. Morning walk
2. No caffeine after 2pm
3. 10 minutes morning sunlight
4. Drink water before first coffee
5. No phone first 30 minutes
6. Bed before fixed time
7. Protein breakfast
8. 5-minute afternoon reset

All experiments are configured for 7 days.

## Run locally

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
flask --app app.py init-db
flask --app app.py run
```

Open http://127.0.0.1:5000

## Phase 4 validation tracking

- Metrics dashboard: `/admin/metrics`
- CSV exports:
  - `/admin/export/metrics.csv`
  - `/admin/export/checkins.csv`

Admin access behavior:

- `ADMIN_EMAILS` must be set (comma-separated emails), otherwise all admin routes are blocked.
- Only listed users can access admin routes.

## Daily reminder email

Reminder target:

- users with active experiment
- no check-in submitted today

Environment variables:

- `SMTP_HOST`
- `SMTP_PORT` (default `587`)
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `SMTP_FROM_EMAIL`
- `SMTP_USE_TLS` (`true` by default)
- `APP_BASE_URL` (default `http://127.0.0.1:5000`)

Run reminders manually:

```bash
flask --app app.py send-daily-reminders --dry-run
flask --app app.py send-daily-reminders
```

Also available from admin page:

- dry-run
- send now

## Deploy online (simple path)

Recommended MVP deploy target: Render Web Service + managed Postgres later (SQLite is fine for earliest test).

1. Push this repo to GitHub.
2. Create new Render Web Service from repo.
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn app:app`
5. Set env vars:
  - `SECRET_KEY` (required in production)
  - `ADMIN_EMAILS`
  - `DATABASE_URL` or `SQLITE_PATH`
  - SMTP vars from section above
  - `APP_BASE_URL` to deployed URL
6. Redeploy and run `flask --app app.py init-db` once in shell.

Included deploy helpers:

- `.env.example` for required environment variables
- `render.yaml` for one-click Render service setup

Production config behavior:

- `FLASK_DEBUG` defaults to `false`.
- If `FLASK_DEBUG` is off and `SECRET_KEY` is missing, app startup fails.
- Debug is not forced on in code.
- DB URL comes from `DATABASE_URL`, otherwise SQLite uses `SQLITE_PATH` (default `proofloop.db`).

## Security hardening in place

- Passwords are stored as hashes (`generate_password_hash` / `check_password_hash`), never plain text.
- Session only stores `user_id`; all user pages resolve data from logged-in user on each request.
- Check-ins enforce score validation (1-10) at app and DB levels.
- Duplicate check-ins are blocked per experiment/day by DB uniqueness + route handling.

## Pre-recruit smoke test (2-3 fake users)

Before inviting real testers:

1. Create 3 fake accounts.
2. Start an experiment for each.
3. Submit daily check-ins (including one duplicate submit on same day to confirm block).
4. Verify:
   - user A cannot see user B data
   - admin route blocked when `ADMIN_EMAILS` is missing
   - admin route works for configured admin email only
   - metrics and CSV exports load
5. Run reminder dry-run:

```bash
flask --app app.py send-daily-reminders --dry-run
```

## Recruit 20 testers (14-day run)

1. Source 20 users from personal network + 2 focused communities.
2. Give each tester one simple brief: choose one experiment, log daily for 14 days.
3. Send day 0, day 3, day 7, day 12 nudges.
4. At day 14, ask:
  - Was this useful?
  - Would you pay 5-10 GBP/month?
5. Track success criteria from roadmap:
  - 60% complete one experiment
  - 30% active after 14 days
  - 5+ users would pay

## Next from roadmap (not implemented intentionally)

- Payments
- AI suggestions
- Wearables

## Notes on confidence

- `low`: not enough spread or volume
- `medium`: at least 4 total check-ins and at least 2 in both followed/not-followed groups
- `high`: at least 7 total check-ins and at least 3 in both groups

## Validation metrics to track manually

- Signed up users
- Logged day 1
- Logged 5+ days
- Completed one experiment
- Reported useful
- Would pay 5-10 GBP/month
