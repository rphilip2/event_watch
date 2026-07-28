# event-watch

Polls the Ticketmaster Discovery API on a schedule, emails you when an event
matching your rules appears or goes on sale. Runs entirely on GitHub Actions —
no server, no cost.

```
watches.yml  ->  monitor.py  ->  state.json (dedupe)  ->  Resend  ->  your inbox
```

## Setup (about 15 minutes)

### 1. Get a Ticketmaster API key
Register at [developer.ticketmaster.com](https://developer.ticketmaster.com/).
A default app is created for you on signup; its **Consumer Key** is your API key.
The public tier allows 5,000 requests/day at 2 requests/second — plenty here.

### 2. Get an email sender
Sign up at [resend.com](https://resend.com) (free tier: 100 emails/day) and
create an API key. For testing you can send from `onboarding@resend.dev` to
your own address without verifying a domain. To send anywhere else, verify a
domain and set `ALERT_FROM` to an address on it.

AWS SES works too if you'd rather — swap out `send_email()`.

### 3. Create the repo
Push these files to a **public** GitHub repo. Public repos get unlimited
Actions minutes; private repos are capped at 2,000 min/month, which a
15-minute cron will burn through. Your keys live in encrypted secrets, not in
the code, so a public repo is fine — just don't paste keys into `watches.yml`.

### 4. Add secrets
Repo **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
| --- | --- |
| `TICKETMASTER_API_KEY` | your Consumer Key |
| `RESEND_API_KEY` | `re_...` |
| `ALERT_EMAIL` | where alerts go |
| `ALERT_FROM` | optional; defaults to `onboarding@resend.dev` |

### 5. Edit your watches
Open `watches.yml` and replace the examples. Each entry is one query.
To find a `venueId`, search the Discovery API for the venue and copy its `id`.

### 6. Run it
Go to **Actions → event-watch → Run workflow**. The first run seeds
`state.json` with everything currently matching and deliberately sends no
email — otherwise you'd get a hundred results at once. Every run after that
only alerts on what's changed.

## Local testing

```bash
pip install -r requirements.txt
export TICKETMASTER_API_KEY=...
python monitor.py --dry-run     # prints matches, sends nothing
```

Delete `state.json` to reset and re-seed.

## What triggers an alert

- An event newly matches one of your watches
- A known event flips from `offsale` to `onsale`

Events you've already been told about stay quiet. State is keyed per
watch, so the same event can alert once for each watch it matches.

## Things worth knowing

- **Cron is approximate.** GitHub queues scheduled workflows and drops them
  under heavy load. Fine for "a tour was announced," not for beating a
  presale queue. If you need sub-minute reliability, this needs a real server.
- **Discovery covers listings, not seats.** It tells you an event exists and
  its price range. Live seat-level inventory is a partner-tier API, so this
  won't catch "4 seats just got released in section 112."
- **Scheduled workflows auto-disable** after 60 days without repo activity —
  but committing `state.json` each run counts as activity, so this stays alive.
- **Pagination is not implemented.** Each watch returns up to 100 events. If a
  watch is that broad, narrow it rather than paging.

## Adding more sources

`fetch_events()` is the only Ticketmaster-specific part. To add SeatGeek,
write a second fetcher that returns the same shape as `summarize()` and the
diffing, dedupe, and email all work unchanged.
