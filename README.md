# UC Merced Seat Monitor

Get an email the moment a seat opens in a full UC Merced class.

`seat_monitor.py` checks UC Merced's public class search
(`reg-prod.ec.ucmerced.edu`) every couple of minutes for the CRNs you choose. When a
section goes from full to open, it emails you right away. You can also have it keep
emailing you until you register or the seat fills.

- **No dependencies.** It's a single Python file that uses only the standard library.
- **No login needed.** It uses the public class search, the same one you can open without
  signing in, and gets its own session cookies automatically.
- **Quiet output.** It prints only when a course's open seats or max seats change.

```
[2026-11-04 12:00:01] Seat monitor started (settings from config.json)
  Term 202710, subject CSE, checking every 120s
  Alerts to you@ucmerced.edu, every 15 min while open
  Only changes in open seats or max seats will be printed. Ctrl+C to stop.

Monitored courses:
  CSE 022-01 (Lecture) Introduction to Programming | CRN 10640
      0 open of 30 (enrolled 30)

[2026-11-04 14:37:12] CRN 10640 CSE 022-01: open seats 0 -> 1  [1 open of 30 (enrolled 29)]
[2026-11-04 14:37:12] *** Seat open for CRN 10640 - email sent to you@ucmerced.edu
```

---

## Requirements

- Python 3.8 or newer ([python.org/downloads](https://www.python.org/downloads/))
- An email account that can send mail over SMTP. Gmail with an
  [App Password](https://myaccount.google.com/apppasswords) is the easiest choice.

## Setup

### 1. Download the script

```bash
git clone https://github.com/<your-username>/<your-repo>.git
cd <your-repo>
```

Or download `seat_monitor.py` and `config.example.json` into a folder.

### 2. Create a Gmail App Password

The script sends the alert emails from a Gmail account, and Gmail requires an App Password
for this. Your normal password won't work.

1. Turn on [2-Step Verification](https://myaccount.google.com/signinoptions/two-step-verification)
   for the Gmail account.
2. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords),
   create a password (name it anything, e.g. "seat monitor"), and copy the 16-character code.

### 3. Create your config file

Copy the example and fill it in:

```bash
cp config.example.json config.json      # Windows: copy config.example.json config.json
```

Or let the script write a blank one for you:

```bash
python seat_monitor.py --init-config
```

Then edit `config.json`:

```json
{
  "crns": ["10640", "10036"],
  "notify_email": "you@ucmerced.edu",
  "subject": "CSE",
  "term": "202710",
  "interval_seconds": 120,
  "email_every_minutes_while_open": 15,
  "smtp_user": "yourname@gmail.com",
  "smtp_password": "abcdefghijklmnop",
  "smtp_host": "smtp.gmail.com",
  "smtp_port": 587
}
```

> ⚠️ `config.json` holds your app password in plain text. It's listed in `.gitignore`, so
> **never commit it**. Only `config.example.json` belongs in the repo.

### 4. Test your email

```bash
python seat_monitor.py --test-email
```

You should get an email saying "Email alerts are working." If you don't, see
[Troubleshooting](#troubleshooting).

### 5. Run it

```bash
python seat_monitor.py
```

On macOS and Linux, use `python3` instead of `python`. Stop the script with **Ctrl+C**.

---

## Configuration

| Setting | What it does | Default |
|---|---|---|
| `crns` | The CRNs to watch. Use a list (`["10640", "10036"]`) or a string (`"10640, 10036"`). | *(asks you)* |
| `notify_email` | The address that receives the alerts. | *(asks you)* |
| `subject` | The subject code the CRNs belong to, e.g. `CSE`, `MATH`, `PHYS`. All CRNs in one config must share a subject. | `CSE` |
| `term` | The Banner term code (see [Finding your term code](#finding-your-term-code)). | `202710` (Spring 2027) |
| `interval_seconds` | Seconds between checks. Please don't go below 60. | `120` |
| `email_every_minutes_while_open` | While a seat is open, email again every N minutes. `0` sends one email each time a seat opens. | `0` |
| `smtp_user` | The login for the account that sends the alerts. | *(asks you)* |
| `smtp_password` | That account's password or App Password. | *(asks you)* |
| `smtp_host` / `smtp_port` | The mail server. Port 587 uses STARTTLS and port 465 uses SSL. | `smtp.gmail.com` / `587` |
| `smtp_from` | Optional "From" address, if it differs from `smtp_user`. | `smtp_user` |

When a setting is missing from the config, the script asks for it at startup. You can also
set the SMTP settings as environment variables (`SMTP_USER`, `SMTP_PASSWORD`, `SMTP_HOST`,
`SMTP_PORT`, `SMTP_FROM`), but values in `config.json` take priority over them.

### Command-line options

Command-line options override `config.json` for that run.

| Option | Description |
|---|---|
| `--crn 10640 10036` | The CRNs to watch. |
| `--email you@ucmerced.edu` | The address to notify. |
| `--subject CSE` | The subject code. |
| `--term 202710` | The term code. |
| `--interval 120` | Seconds between checks. |
| `--email-every 15` | Email every N minutes while a seat stays open. |
| `--config path/to/file.json` | Use a different config file. |
| `--init-config` | Write a template `config.json` and exit. |
| `--test-email` | Send a test email and exit. |
| `--once` | Check once and exit. |
| `--verbose` | Print every check, not just changes. |

Example of watching a different class without editing the config:

```bash
python seat_monitor.py --crn 10346 --subject CSE --email-every 10
```

---

## Finding CRNs, subject and term codes

### Finding a CRN

1. Open the [UC Merced class search](https://reg-prod.ec.ucmerced.edu/StudentRegistrationSsb/ssb/term/termSelection?mode=search).
2. Pick a term and search for the subject.
3. The **CRN** column shows the 5-digit number for each section. Lab and discussion
   sections have their own CRNs, and the script can watch those too.

### Finding your term code

The term code is a 6-digit number such as `202710` (Spring 2027). To find the code for
another term:

1. Open the class search in Chrome or Edge and press **F12** to open DevTools.
2. Go to the **Network** tab, pick the term and search.
3. Click the `searchResults` request. The URL contains `txt_term=XXXXXX`, and that number
   is your term code.

---

## How it works

The script follows the same steps your browser takes on the class search page:

1. It loads `term/termSelection?mode=search` to get a session cookie (`JSESSIONID`) and the
   page's synchronizer token.
2. It POSTs the term to `term/search?mode=search`.
3. It calls `searchResults/searchResults?txt_subject=...&txt_term=...` and reads the JSON
   response, paging through the results if needed.
4. For each CRN you're watching, it compares `seatsAvailable` and `maximumEnrollment` with
   the previous check.

When the session expires, the script starts a new one automatically. When the site is
down, it waits longer between retries, up to 8× the normal interval.

### When you get emails

| Situation | Email? |
|---|---|
| A seat opens (open seats goes from 0 to more than 0) | ✅ "Seat open: …" right away |
| The seat stays open and `email_every_minutes_while_open` is above 0 | ✅ "Still open: …" every N minutes |
| The seat stays open and the setting is `0` | — |
| The section fills, then opens again | ✅ A new "Seat open" email |
| The section is already open when you start the script | ✅ One email, so you know right away |

---

## Running it 24/7

The script only checks while it's running. Some ways to keep it running:

- **Your own computer:** leave a terminal open. On Windows, turn off sleep in power settings.
- **A Raspberry Pi or a spare laptop:** run
  `nohup python3 seat_monitor.py > monitor.log 2>&1 &`.
- **A small cloud server** (DigitalOcean, Hetzner, Oracle Cloud's free tier, etc.): copy
  `seat_monitor.py` and `config.json` to the server and run the same `nohup` command, or set
  it up as a `systemd` service so it restarts after reboots.

Before leaving the script running on a new machine, run `python3 seat_monitor.py --once`
there. Some sites block requests from cloud servers, so make sure your course is listed
with its seat count.

---

## Troubleshooting

**`SMTPAuthenticationError` / "Username and Password not accepted"**
Gmail needs an App Password, not your normal password, and 2-Step Verification must be on
before you can create one. Spaces in the app password are fine.

**"CRN XXXXX: NOT FOUND"**
Check that the CRN belongs to the `subject` in your config and that the `term` is right. The
script only searches one subject.

**"Check failed … HTTP Error 403" or timeouts**
The registration site may be down for maintenance, or it may be blocking your network. The
script keeps retrying. If the error persists on a cloud server, try running the script from
home instead.

**Nothing is printing**
Nothing printing is normal: the script only prints when seats change. Run it with
`--verbose` to see every check.

---

## Be considerate

This tool reads a public page that many students use, especially during registration. Keep
`interval_seconds` at 60 or higher, watch only the CRNs you need, and stop the script once
you've registered.

## Disclaimer

This is an unofficial personal project. It isn't affiliated with or endorsed by the
University of California, Merced. It can only tell you that a seat opened; you still have
to register yourself. The site can change at any time and break the script, so don't rely
on it as your only way to get into a class. Use it at your own risk and in line with UC
Merced's acceptable-use policies.
