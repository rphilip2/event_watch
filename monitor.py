#!/usr/bin/env python3
"""
event-watch: poll the Ticketmaster Discovery API for events matching your rules,
and email you when something new appears or goes on sale.

State lives in state.json so you only get alerted once per event.
Run locally with --dry-run to see what it would send without emailing.
"""

import argparse
import datetime as dt
import json
import os
import pathlib
import sys
import time

import requests
import yaml

ROOT = pathlib.Path(__file__).parent
STATE_PATH = ROOT / "state.json"
WATCHES_PATH = ROOT / "watches.yml"

TM_URL = "https://app.ticketmaster.com/discovery/v2/events.json"
RESEND_URL = "https://api.resend.com/emails"

# Ticketmaster public tier: 2 req/sec, 5000 req/day. Stay well under.
REQUEST_PAUSE = 0.6


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------

def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"seen": {}, "initialized": False}
    with STATE_PATH.open() as fh:
        return json.load(fh)


def save_state(state: dict) -> None:
    with STATE_PATH.open("w") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
        fh.write("\n")


# --------------------------------------------------------------------------
# fetching
# --------------------------------------------------------------------------

def fetch_events(watch: dict, api_key: str) -> list:
    """Query the Discovery API for one watch. Returns a list of raw event dicts."""
    params = {
        "apikey": api_key,
        "size": watch.get("size", 100),
        "sort": "date,asc",
    }

    # Optional filters -- only send the ones actually configured.
    for key in ("keyword", "city", "stateCode", "countryCode",
                "classificationName", "postalCode", "radius", "unit", "venueId"):
        if watch.get(key) is not None:
            params[key] = watch[key]

    if watch.get("days_ahead"):
        now = dt.datetime.now(dt.timezone.utc)
        end = now + dt.timedelta(days=int(watch["days_ahead"]))
        params["startDateTime"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        params["endDateTime"] = end.strftime("%Y-%m-%dT%H:%M:%SZ")

    resp = requests.get(TM_URL, params=params, timeout=30)
    time.sleep(REQUEST_PAUSE)

    if resp.status_code != 200:
        print(f"  ! HTTP {resp.status_code} for watch '{watch['name']}': {resp.text[:200]}",
              file=sys.stderr)
        return []

    return resp.json().get("_embedded", {}).get("events", [])


def summarize(event: dict) -> dict:
    """Flatten the parts of a Discovery API event we actually care about."""
    venues = event.get("_embedded", {}).get("venues", [{}])
    venue = venues[0] if venues else {}
    prices = event.get("priceRanges") or []

    return {
        "id": event.get("id"),
        "name": event.get("name"),
        "url": event.get("url"),
        "date": event.get("dates", {}).get("start", {}).get("localDate"),
        "time": event.get("dates", {}).get("start", {}).get("localTime"),
        "status": event.get("dates", {}).get("status", {}).get("code"),
        "venue": venue.get("name"),
        "city": venue.get("city", {}).get("name"),
        "price_min": prices[0].get("min") if prices else None,
        "price_max": prices[0].get("max") if prices else None,
    }


def passes_filters(ev: dict, watch: dict) -> bool:
    """Client-side rules the API can't express."""
    max_price = watch.get("max_price")
    if max_price is not None:
        # No price data yet usually means it hasn't gone on sale -- keep it.
        if ev["price_min"] is not None and ev["price_min"] > max_price:
            return False

    if watch.get("onsale_only") and ev["status"] != "onsale":
        return False

    exclude = [w.lower() for w in watch.get("exclude_keywords", [])]
    if exclude and ev["name"]:
        if any(word in ev["name"].lower() for word in exclude):
            return False

    return True


# --------------------------------------------------------------------------
# diffing
# --------------------------------------------------------------------------

def diff_against_state(watch_name: str, events: list, state: dict) -> list:
    """Return the events worth alerting on, and update state in place."""
    hits = []
    seen = state["seen"]

    for ev in events:
        key = f"{watch_name}::{ev['id']}"
        previous = seen.get(key)

        if previous is None:
            reason = "New match"
        elif previous.get("status") != ev["status"] and ev["status"] == "onsale":
            reason = f"Now on sale (was {previous.get('status')})"
        else:
            # Already known and nothing meaningful changed.
            seen[key]["status"] = ev["status"]
            seen[key]["last_seen"] = dt.datetime.now(dt.timezone.utc).isoformat()
            continue

        seen[key] = {
            "status": ev["status"],
            "name": ev["name"],
            "first_seen": dt.datetime.now(dt.timezone.utc).isoformat(),
            "last_seen": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        hits.append({**ev, "watch": watch_name, "reason": reason})

    return hits


# --------------------------------------------------------------------------
# email
# --------------------------------------------------------------------------

def build_html(hits: list) -> str:
    rows = []
    for h in hits:
        price = ""
        if h["price_min"] is not None:
            price = f"${h['price_min']:.0f}"
            if h["price_max"] and h["price_max"] != h["price_min"]:
                price += f"&ndash;${h['price_max']:.0f}"

        when = h["date"] or "TBD"
        if h["time"]:
            when += f" at {h['time'][:5]}"

        where = " &middot; ".join(x for x in (h["venue"], h["city"]) if x)

        rows.append(f"""
        <tr>
          <td style="padding:12px 0;border-bottom:1px solid #e5e5e5;">
            <a href="{h['url']}" style="font-size:16px;font-weight:600;
               color:#1a1a1a;text-decoration:none;">{h['name']}</a>
            <div style="color:#666;font-size:14px;margin-top:4px;">
              {when}{' &middot; ' + where if where else ''}
              {' &middot; ' + price if price else ''}
            </div>
            <div style="color:#999;font-size:12px;margin-top:4px;">
              {h['watch']} &middot; {h['reason']}
            </div>
          </td>
        </tr>""")

    return f"""<html><body style="font-family:-apple-system,BlinkMacSystemFont,
      'Segoe UI',sans-serif;max-width:600px;margin:0 auto;padding:24px;">
      <h2 style="font-size:18px;margin:0 0 16px;">
        {len(hits)} new match{'es' if len(hits) != 1 else ''}
      </h2>
      <table style="width:100%;border-collapse:collapse;">{''.join(rows)}</table>
      <p style="color:#999;font-size:12px;margin-top:24px;">
        Sent by event-watch
      </p></body></html>"""


def send_email(hits: list, api_key: str, to_addr: str, from_addr: str) -> None:
    subject = f"{len(hits)} new event match{'es' if len(hits) != 1 else ''}"
    if len(hits) == 1:
        subject = f"Available: {hits[0]['name']}"

    resp = requests.post(
        RESEND_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "from": from_addr,
            "to": [to_addr],
            "subject": subject,
            "html": build_html(hits),
        },
        timeout=30,
    )
    if resp.status_code >= 300:
        print(f"! Email failed: {resp.status_code} {resp.text[:300]}", file=sys.stderr)
        sys.exit(1)
    print(f"  -> emailed {to_addr}")


# --------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="print matches instead of emailing")
    args = parser.parse_args()

    tm_key = os.environ.get("TICKETMASTER_API_KEY")
    if not tm_key:
        sys.exit("TICKETMASTER_API_KEY is not set")

    with WATCHES_PATH.open() as fh:
        watches = yaml.safe_load(fh)["watches"]

    state = load_state()
    first_run = not state.get("initialized")
    all_hits = []

    for watch in watches:
        raw = fetch_events(watch, tm_key)
        events = [summarize(e) for e in raw]
        events = [e for e in events if passes_filters(e, watch)]
        hits = diff_against_state(watch["name"], events, state)
        print(f"  {watch['name']}: {len(events)} matching, {len(hits)} new")
        all_hits.extend(hits)

    state["initialized"] = True
    save_state(state)

    if first_run:
        print(f"First run: seeded {len(all_hits)} events into state, no email sent.")
        return

    if not all_hits:
        print("Nothing new.")
        return

    if args.dry_run:
        for h in all_hits:
            print(f"  [{h['reason']}] {h['name']} -- {h['date']} -- {h['url']}")
        return

    send_email(
        all_hits,
        os.environ["RESEND_API_KEY"],
        os.environ["ALERT_EMAIL"],
        os.environ.get("ALERT_FROM", "onboarding@resend.dev"),
    )


if __name__ == "__main__":
    main()
