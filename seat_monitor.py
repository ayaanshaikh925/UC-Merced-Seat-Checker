#!/usr/bin/env python3
"""
UC Merced seat monitor
======================
Watches one or more CRNs on UC Merced's Banner class search
(reg-prod.ec.ucmerced.edu) and emails you when a seat opens up.

No third-party packages needed - Python 3.8+ standard library only.

Quick start
-----------
    # 1. Create config.json next to this script (or write it by hand)
    python3 seat_monitor.py --init-config

    # 2. Edit config.json: your CRNs, the email to notify, and the Gmail
    #    address + App Password that sends the alert

    # 3. Run it
    python3 seat_monitor.py

Anything given on the command line (e.g. --crn 10036) overrides config.json.
Set "email_every_minutes_while_open" in config.json (e.g. 15) to keep getting
emails while a seat stays open; 0 sends one email per opening.
Output is quiet: only changes in open/max seats are printed (--verbose for all).
Anything missing from both is asked for interactively.

Run `python3 seat_monitor.py --help` for all options.
"""

import argparse
import getpass
import html
import http.cookiejar
import json
import os
import random
import re
import smtplib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from email.message import EmailMessage

BASE_URL = os.environ.get(
    "BANNER_BASE_URL", "https://reg-prod.ec.ucmerced.edu/StudentRegistrationSsb/ssb"
)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
)
PAGE_SIZE = 500


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# --------------------------------------------------------------------------
# Banner session handling
# --------------------------------------------------------------------------
class BannerClient:
    """Mimics what the browser does on the class search page.

    Banner 9 will only return search results after the session has:
      1. loaded termSelection?mode=search  -> JSESSIONID + AWSALB cookies,
         and a synchronizer token in a <meta> tag
      2. POSTed the chosen term to term/search?mode=search
    After that, searchResults/searchResults returns JSON. Between different
    searches the form must be reset via classSearch/resetDataForm.
    """

    def __init__(self, term, timeout=30, verbose=False):
        self.term = term
        self.verbose = verbose
        self.timeout = timeout
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies)
        )
        self.sync_token = None
        self.unique_session_id = None
        self.ready = False

    # -- low-level helpers -------------------------------------------------
    def _request(self, path, data=None, ajax=True, referer=None):
        url = f"{BASE_URL}/{path}"
        headers = {
            "User-Agent": USER_AGENT,
            "Accept-Language": "en",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
        if ajax:
            headers["Accept"] = "application/json, text/javascript, */*; q=0.01"
            headers["X-Requested-With"] = "XMLHttpRequest"
            if self.sync_token:
                headers["X-Synchronizer-Token"] = self.sync_token
        else:
            headers["Accept"] = "text/html,application/xhtml+xml,*/*;q=0.8"
        if referer:
            headers["Referer"] = referer
        body = None
        if data is not None:
            body = urllib.parse.urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        req = urllib.request.Request(url, data=body, headers=headers)
        with self.opener.open(req, timeout=self.timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")

    # -- session setup -----------------------------------------------------
    def start_session(self):
        self.cookies.clear()
        self.sync_token = None
        # Same shape as the id the site generates in JS: 5 random chars + ms epoch
        alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
        self.unique_session_id = "".join(random.choice(alphabet) for _ in range(5)) + str(
            int(time.time() * 1000)
        )

        page = self._request("term/termSelection?mode=search", ajax=False)
        m = re.search(r'name="synchronizerToken"\s+content="([^"]+)"', page)
        if m:
            self.sync_token = m.group(1)

        self._request(
            "term/search?mode=search",
            data={
                "term": self.term,
                "studyPath": "",
                "studyPathText": "",
                "startDatepicker": "",
                "endDatepicker": "",
                "uniqueSessionId": self.unique_session_id,
            },
            referer=f"{BASE_URL}/term/termSelection?mode=search",
        )
        # Visiting the class search page mirrors the browser and is harmless.
        self._request("classSearch/classSearch", ajax=False,
                      referer=f"{BASE_URL}/term/termSelection?mode=search")
        self.ready = True
        cookie_names = sorted(c.name for c in self.cookies)
        if self.verbose:
            log(f"Session started for term {self.term} (cookies: {', '.join(cookie_names) or 'none'})")

    def _reset_form(self):
        try:
            self._request(
                "classSearch/resetDataForm",
                data={},
                referer=f"{BASE_URL}/classSearch/classSearch",
            )
        except urllib.error.HTTPError:
            pass  # not fatal

    # -- searching ---------------------------------------------------------
    def search_subject(self, subject):
        """Return every section (dict) for a subject in the session's term."""
        if not self.ready:
            self.start_session()
        self._reset_form()

        sections, offset = [], 0
        while True:
            params = {
                "txt_subject": subject,
                "txt_term": self.term,
                "startDatepicker": "",
                "endDatepicker": "",
                "uniqueSessionId": self.unique_session_id,
                "pageOffset": offset,
                "pageMaxSize": PAGE_SIZE,
                "sortColumn": "subjectDescription",
                "sortDirection": "asc",
            }
            raw = self._request(
                "searchResults/searchResults?" + urllib.parse.urlencode(params),
                referer=f"{BASE_URL}/classSearch/classSearch",
            )
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                raise SessionExpired("Got HTML instead of JSON - session expired")
            if not payload.get("success") or payload.get("data") is None:
                raise SessionExpired("Search returned no data - term not set on session")

            page = payload["data"]
            sections.extend(page)
            total = payload.get("totalCount") or 0
            offset += len(page)
            if not page or offset >= total:
                break
        return sections


class SessionExpired(Exception):
    pass


# --------------------------------------------------------------------------
# Email
# --------------------------------------------------------------------------
def send_email(to_addr, subject, body):
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    sender = os.environ.get("SMTP_FROM", user)
    if not user or not password:
        raise RuntimeError("SMTP_USER and SMTP_PASSWORD must be set to send email")

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)

    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=30) as s:
            s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls()
            s.login(user, password)
            s.send_message(msg)


def describe(sec):
    title = html.unescape(sec.get("courseTitle") or "")
    return (
        f"{sec.get('subject')} {sec.get('courseNumber')}-{sec.get('sequenceNumber')} "
        f"({sec.get('scheduleTypeDescription')}) {title} | CRN {sec.get('courseReferenceNumber')}"
    )


def seat_line(sec):
    line = (
        f"{sec.get('seatsAvailable')} open of {sec.get('maximumEnrollment')} "
        f"(enrolled {sec.get('enrollment')})"
    )
    if sec.get("waitCapacity"):
        line += f", waitlist {sec.get('waitCount')}/{sec.get('waitCapacity')}"
    return line


def build_alert(sec, term):
    term_desc = sec.get("termDesc") or term
    subject = f"Seat open: {sec.get('subject')} {sec.get('courseNumber')} CRN {sec.get('courseReferenceNumber')}"
    body = (
        f"A seat just opened up!\n\n"
        f"{describe(sec)}\n"
        f"Term: {term_desc}\n"
        f"Seats: {seat_line(sec)}\n\n"
        f"Register now: https://reg-prod.ec.ucmerced.edu/StudentRegistrationSsb/ssb/registration\n"
        f"\nChecked at {datetime.now():%Y-%m-%d %H:%M:%S}\n"
    )
    return subject, body


# --------------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------------
CONFIG_TEMPLATE = {
    "crns": ["10640"],
    "notify_email": "you@ucmerced.edu",
    "subject": "CSE",
    "term": "202710",
    "interval_seconds": 120,
    "email_every_minutes_while_open": 0,
    "smtp_user": "yourname@gmail.com",
    "smtp_password": "your 16-char app password",
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
}


def load_config(path):
    """Read settings from a JSON config file. Returns {} if it doesn't exist."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except json.JSONDecodeError as e:
        sys.exit(f"Could not read {path}: {e}")
    if not isinstance(cfg, dict):
        sys.exit(f"{path} must contain a JSON object {{ ... }}")
    # Allow "crns" as a list, a single number, or a comma-separated string.
    crns = cfg.get("crns", cfg.get("crn"))
    if isinstance(crns, (str, int)):
        crns = [c.strip() for c in str(crns).split(",") if c.strip()]
    cfg["crns"] = [str(c).strip() for c in (crns or [])]
    for key in CONFIG_TEMPLATE:
        if isinstance(cfg.get(key), str) and cfg[key].startswith(("you@", "yourname@", "your 16")):
            cfg[key] = None  # still the template placeholder
    return cfg


def apply_config(args, cfg):
    """Fill in anything not given on the command line from the config file.
    Precedence: command line > config file > environment variables > prompt."""
    args.crn = args.crn or cfg.get("crns") or None
    args.email = args.email or cfg.get("notify_email")
    args.subject = (args.subject or cfg.get("subject") or "").upper() or None
    args.term = args.term or str(cfg.get("term") or "202710")
    args.interval = args.interval or int(cfg.get("interval_seconds") or 120)
    if args.email_every is None:
        # "remind_every_minutes" is the older name for the same setting
        args.email_every = float(cfg.get("email_every_minutes_while_open")
                                 or cfg.get("remind_every_minutes") or 0)
    for key, env in (("smtp_user", "SMTP_USER"), ("smtp_password", "SMTP_PASSWORD"),
                     ("smtp_host", "SMTP_HOST"), ("smtp_port", "SMTP_PORT"),
                     ("smtp_from", "SMTP_FROM")):
        if cfg.get(key):
            os.environ[env] = str(cfg[key])


def prompt_if_missing(args):
    if not args.crn:
        raw = input("Which CRN(s) do you want to monitor? (comma-separated, e.g. 10640,10036): ")
        args.crn = [c.strip() for c in raw.split(",") if c.strip()]
    if not args.crn:
        sys.exit("No CRN given.")
    if not args.email:
        args.email = input("Email address to notify when a seat opens: ").strip()
    if not args.email:
        sys.exit("No email given.")
    if not args.subject:
        args.subject = (input("Subject code for these CRNs [CSE]: ").strip() or "CSE").upper()
    if not os.environ.get("SMTP_USER"):
        os.environ["SMTP_USER"] = input("Sender email account (SMTP login, e.g. Gmail address): ").strip()
    if not os.environ.get("SMTP_PASSWORD"):
        os.environ["SMTP_PASSWORD"] = getpass.getpass("Sender password / app password: ")


def format_change(sec, old, new):
    """Describe what changed between two (seatsAvailable, maximumEnrollment) pairs."""
    parts = []
    if old[0] != new[0]:
        parts.append(f"open seats {old[0]} -> {new[0]}")
    if old[1] != new[1]:
        parts.append(f"max seats {old[1]} -> {new[1]}")
    return f"CRN {sec.get('courseReferenceNumber')} {sec.get('subject')} {sec.get('courseNumber')}-" \
           f"{sec.get('sequenceNumber')}: {', '.join(parts)}  [{seat_line(sec)}]"


def main():
    default_config = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    ap = argparse.ArgumentParser(
        description="Email me when a UC Merced class seat opens. Settings are read from "
                    "config.json next to this script; command-line options override it.")
    ap.add_argument("--config", default=default_config,
                    help=f"path to the JSON config file (default: {default_config})")
    ap.add_argument("--init-config", action="store_true",
                    help="write a template config.json to fill in, then exit")
    ap.add_argument("--crn", nargs="+", help="CRN(s) to watch, e.g. --crn 10640 10036")
    ap.add_argument("--email", help="address to notify")
    ap.add_argument("--subject", help="subject code the CRNs belong to (default CSE)")
    ap.add_argument("--term", help="Banner term code (default 202710 = Spring 2027)")
    ap.add_argument("--interval", type=int,
                    help="seconds between checks (default 120; please don't go below 60)")
    ap.add_argument("--once", action="store_true", help="check once and exit")
    ap.add_argument("--test-email", action="store_true", help="send a test email and exit")
    ap.add_argument("--email-every", "--remind-every", dest="email_every", type=float,
                    help="while a seat is open, email again every N minutes "
                         "(default 0 = only one email each time a seat opens)")
    ap.add_argument("--verbose", action="store_true",
                    help="print every check, not just changes")
    args = ap.parse_args()

    if args.init_config:
        if os.path.exists(args.config):
            sys.exit(f"{args.config} already exists - edit it directly.")
        with open(args.config, "w", encoding="utf-8") as f:
            json.dump(CONFIG_TEMPLATE, f, indent=2)
        print(f"Wrote {args.config}. Fill in your CRNs, email and app password, then run again.")
        return

    cfg = load_config(args.config)
    apply_config(args, cfg)
    prompt_if_missing(args)
    crns = sorted({c.strip() for c in args.crn})

    if args.test_email:
        send_email(args.email, "UC Merced seat monitor test", "Email alerts are working.")
        log(f"Test email sent to {args.email}")
        return

    client = BannerClient(args.term, verbose=args.verbose)
    last_seen = {}       # crn -> (seatsAvailable, maximumEnrollment)
    last_alert = {}      # crn -> time of last successful email for the current opening
    reported_missing = set()
    failures = 0
    listed = False

    repeat = (f"every {args.email_every:g} min while open" if args.email_every
              else "once each time a seat opens")
    log(f"Seat monitor started{' (settings from ' + args.config + ')' if cfg else ''}")
    print(f"  Term {args.term}, subject {args.subject}, checking every {args.interval}s")
    print(f"  Alerts to {args.email}, {repeat}")
    print("  Only changes in open seats or max seats will be printed. Ctrl+C to stop.")

    while True:
        try:
            try:
                sections = client.search_subject(args.subject)
            except (SessionExpired, urllib.error.HTTPError) as e:
                if args.verbose:
                    log(f"{e} - starting a fresh session")
                client.ready = False
                sections = client.search_subject(args.subject)

            if failures:
                log(f"Connection restored after {failures} failed check(s).")
            failures = 0
            by_crn = {s.get("courseReferenceNumber"): s for s in sections}

            # First successful check: list what's being monitored.
            if not listed:
                print("\nMonitored courses:")
                for crn in crns:
                    sec = by_crn.get(crn)
                    if sec:
                        print(f"  {describe(sec)}\n      {seat_line(sec)}")
                    else:
                        print(f"  CRN {crn}: NOT FOUND in {args.subject} for term {args.term} "
                              f"- check the CRN and subject")
                        reported_missing.add(crn)
                print()
                listed = True

            now = time.time()
            for crn in crns:
                sec = by_crn.get(crn)
                if sec is None:
                    if crn not in reported_missing:
                        log(f"CRN {crn} is no longer listed in {args.subject} for term {args.term}.")
                        reported_missing.add(crn)
                    last_seen.pop(crn, None)
                    continue
                current = (sec.get("seatsAvailable") or 0, sec.get("maximumEnrollment") or 0)
                previous = last_seen.get(crn)
                if crn in reported_missing:
                    log(f"CRN {crn} now appears: {describe(sec)}  [{seat_line(sec)}]")
                    reported_missing.discard(crn)
                elif previous is not None and previous != current:
                    log(format_change(sec, previous, current))
                elif args.verbose:
                    log(f"{describe(sec)} -> {seat_line(sec)}")
                last_seen[crn] = current

                is_open = current[0] > 0
                if not is_open:
                    last_alert.pop(crn, None)   # next opening gets a fresh alert
                    continue

                sent_before = last_alert.get(crn)
                due = sent_before is None or (
                    args.email_every and now - sent_before >= args.email_every * 60)
                if due:
                    subj, body = build_alert(sec, args.term)
                    if sent_before is not None:
                        subj = "Still open: " + subj.split(": ", 1)[1]
                    try:
                        send_email(args.email, subj, body)
                        last_alert[crn] = now
                        if sent_before is None:
                            log(f"*** Seat open for CRN {crn} - email sent to {args.email}")
                        elif args.verbose:
                            log(f"Reminder email sent for CRN {crn}")
                    except Exception as e:
                        log(f"!!! Seat open for CRN {crn} but email failed: {e} (will retry)")
        except KeyboardInterrupt:
            raise
        except Exception as e:
            failures += 1
            client.ready = False
            # Report the first failure and then only occasionally, to keep output quiet.
            if failures == 1 or failures % 10 == 0 or args.verbose:
                log(f"Check failed ({failures} in a row): {e}")

        if args.once:
            break
        # back off on repeated failures, and add jitter so requests aren't robotic
        delay = args.interval * min(2 ** max(failures - 1, 0), 8) if failures else args.interval
        time.sleep(delay + random.uniform(0, min(15, delay * 0.2)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Stopped.")