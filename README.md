# UptimeMonitor — web app

A browser front end for the uptime monitor, replacing the Streamlit dashboard.
It reads the same `uptime.db` and reuses the same `monitor.py` and `auth.py`
functions, so nothing about your checking logic changes.

## Files

| File | What it does |
| --- | --- |
| `api.py` | Flask backend. Session login, server CRUD, one dashboard endpoint, serves the page. |
| `index.html` | The whole front end. No build step, no npm, no CDN. |

Drop `api.py` in the same folder as `monitor.py`, `dashboard.py`, `auth.py` and
`alerts.py`, and put `index.html` in a `web/` subfolder next to it:

```
your-project/
  monitor.py
  dashboard.py
  auth.py
  alerts.py
  uptime.db
  api.py          <- new
  index.html      <- new
```

## Running it

Install Flask once:

```
pip install flask
```

Then two terminals in VS Code:

```
python monitor.py      # terminal 1 — performs the checks
python api.py          # terminal 2 — serves the dashboard
```

Open http://127.0.0.1:5000 and sign in with your existing account — it's the
same `auth.py` user table the Streamlit app uses.

The Streamlit dashboard still works; both can read the same database. Once
you're happy with the web version you can stop using `dashboard.py`.

## What's in the interface

- A headline that says the one thing that matters: everything is fine, or N services are down.
- Per-service rows with a strip of the last 48 checks — green for a normal
  response, amber at or above 1000 ms, red for a failure. Patterns like
  "fails every night around 2am" are visible at a glance.
- Expand any row for uptime over the selected window, a response-time chart,
  7- and 30-day figures, and inline editing.
- Check now (single service or all), pause/resume, maintenance mode, delete.
- Search, filter by state, history window from 1 hour to 30 days, auto-refresh.
- Incident log with durations, and the monitoring-gap notes your engine writes.

## API endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/signup` | Create an account |
| POST | `/api/login` | Sign in, sets session cookie |
| POST | `/api/logout` | Sign out |
| GET | `/api/me` | Current session |
| GET | `/api/dashboard?hours=24` | Everything the page renders |
| POST | `/api/servers` | Add a service |
| PUT | `/api/servers/<id>` | Edit a service |
| POST | `/api/servers/<id>/active` | Pause or resume |
| DELETE | `/api/servers/<id>` | Delete with history |
| POST | `/api/servers/<id>/check` | Check one service now |
| POST | `/api/check-all` | Check every active service |

Every endpoint scopes by the signed-in `user_id`, the same way the Streamlit
version does.

## Notes before you put this anywhere shared

`app.run(debug=True)` and binding to `127.0.0.1` are right for local
development only. For anything beyond your own machine you'd want debug off, a
real WSGI server (waitress on Windows, gunicorn elsewhere), HTTPS, and
`SESSION_COOKIE_SECURE = True`.

`.flask_secret` is generated on first run and signs session cookies. Add it to
`.gitignore` — don't commit it.
