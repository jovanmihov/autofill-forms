# autofill-forms

Reads your FINKI course emails and answers the Moodle (courses.finki.ukim.mk) forms they announce:

- **Midterm attendance** ("ќе присуствувате на колоквиумот?"): answers *Yes* if the form is for your study program.
- **Lab term selection**: picks a slot that doesn't clash with your weekly schedule.
- **Everything else** from a course (events, schedule changes, announcements): a one-line summary on your phone.

It reads Outlook on the web in its own Microsoft Edge window (headless) and notices new emails from any
`@courses.*` address within about 30 seconds. **Gemini** (free tier) is asked two things:

1. **The email**: each new course email on its own. Gemini says what it is about (lab form, midterm form, other
   form, event, schedule change, announcement, other) and writes the one-line phone message.
2. **The form**: when the email links to a Moodle form that is open, Gemini gets the form's contents (title,
   description, options), the email, your weekly schedule and your study program, and decides which option to
   choose: the lab term that fits your schedule, or the midterm answer.

The script then checks Gemini's choice against your rules, saves it, checks that Moodle shows it, and tells you
on the PC (Windows notification) and on your phone (Discord). Anything it can't decide safely is left to you,
with a "Needs you" message.

## Setup

Windows with Microsoft Edge and Python 3.13.

```
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

Copy `preferences.example\` to `preferences\` and fill in your own data (it is gitignored):

- `preferences\profile.yaml`: your study program (`program_aliases`), the other programs, lab rules, and
  `private_words`: your name in every spelling (Cyrillic and Latin; Moodle writes "Hi Име Презиме,"), removed
  before anything goes to Gemini.
- `preferences\schedule.txt`: your weekly classes, one per line, e.g. `Понеделник 08:00-10:00 Веб програмирање`.
  Any other time you're not free goes in the same way, at any hour and for any length
  (`Понеделник 00:00-09:00 Спиење`, `Вторник 08:00-21:00 Зафатен`); a night goes on two lines.

Then:

```
py main.py set-password          # your CAS username + password -> Windows Credential Manager
py main.py set-gemini-key        # your Gemini API key -> Windows Credential Manager (see below)
py main.py set-discord-webhook   # your Discord webhook -> Windows Credential Manager; sends a test message
py main.py browse                # log in to Outlook once in the window that opens ("Stay signed in": Yes), then close it
py main.py scan                  # lists course emails and the form each one links to
py main.py run --dry-run --headed --lookback-days 7   # everything except saving and sending
```

`py main.py ...` hands itself over to `.venv` automatically.

### Gemini API key (free)

1. Open https://aistudio.google.com/apikey with your Google account (you must be 18 or older) and create a key.
   No billing account is needed; don't enable billing on the project if you want to be sure nothing is charged.
2. `py main.py set-gemini-key` and paste it. The script checks the key right away.

The models are in `config.yaml` (`gemini.models`): `gemini-3.5-flash-lite`, then `gemini-3.8-flash` if the first
one is rate limited or fails. (Flash-Lite goes first because Flash often timed out or was overloaded.) Without a saved key, the keyword classifier is used instead (see Safety).

### Phone messages (Discord)

1. In the Discord app, create a server just for yourself (+ > Create My Own > For me and my friends).
2. On its text channel: Edit Channel > Integrations > Webhooks > New Webhook > Copy Webhook URL.
3. `py main.py set-discord-webhook` and paste the URL. A test message should arrive on your phone.
4. On the phone, allow Discord notifications and set that server to *All Messages*. Discord holds back phone
   notifications while you're active in Discord on the PC (User Settings > Notifications > Push Notification
   Inactive Timeout).

Each new course email gives one message. Its title says what kind of email it is, the course (the short name
Moodle puts at the start of the subject, e.g. `Аипс`) and who sent it, when the email has them:

```
Lab · Аипс · Georgiev Pece: Lab term survey. I chose Среда 12:00-14:00 for you.
Event · Krajchevska Evgenija: Free AI workshop on Nov 18 at 17:00
Schedule change · Лаип · Georgiev Pece: Lecture moved to Wed 17:00
Needs you · БП · Stojcheva Marija: Midterm survey: Gemini suggests 'Да', the checks say skip (for other program(s): КН)
Course email: New login to your Moodle account
```

## Commands

| Command | What it does |
|---|---|
| `set-password` | Stores your CAS login in Windows Credential Manager. Also unblocks logins after a failed one. |
| `set-gemini-key` | Stores your Gemini API key in Windows Credential Manager and checks it. |
| `set-discord-webhook` | Stores your Discord webhook URL in Windows Credential Manager and sends a test message. |
| `browse [url]` | Opens the script's browser so you can log in; saves each page you stay on to `logs/browse/`. |
| `scan [--lookback-days N]` | Lists course emails, their type (by keywords) and form link. Opens only form-related emails; no Gemini, no messages. |
| `run [--dry-run] [--headed]` | Checks the Inbox once: every new course email is read, summed up, and its form answered. |
| `watch [--dry-run] [--headed]` | Keeps checking every `poll_seconds` (30). This is what the scheduled task runs. |
| `open <moodle-url> [--dry-run]` | Answers one form from its link. |

A dry run asks Gemini (so you see its answers), saves the form's screenshot and HTML to `logs/forms/`, and prints
what it would choose and which message it would send, without saving anything in Moodle or sending messages.
Add `-v` (`py main.py -v run --dry-run ...`) to also print each request exactly as it goes to Gemini.

The first real run handles every course email of the last `lookback_days` (3) that it hasn't seen yet, so expect a
burst of phone messages once. Use `--lookback-days 1` to keep it short.

## Run it automatically

After a dry run did the right thing:

```
powershell -ExecutionPolicy Bypass -File install_task.ps1
```

This starts `main.py watch` hidden whenever you log on. The log is `logs/autoform.log`.
Remove it with `Unregister-ScheduledTask -TaskName autofill-forms -Confirm:$false`.

## Safety

- Gemini's answers are only data. They must fit a JSON schema, the category must be one of a fixed list, and the
  option it chooses must be one of the options on the page. Email and form text is sent as untrusted data
  inside delimiters, and links are removed from the messages it writes.
- Gemini's choice is submitted only if it also passes the same checks as before: the form is open and not
  answered yet, the options look like the form type (day/time slots for a lab, yes/no for a midterm), the option
  isn't full or disabled, a lab slot doesn't clash with your schedule (with `min_gap_minutes`), and a midterm
  "yes" is for your program. If Gemini fails, is rate limited, or its pick doesn't pass: nothing is submitted
  and you get "Needs you".
- Without a saved Gemini key, the keyword classifier and the rules decide alone (the behavior before Gemini).
- Nothing is submitted twice (`state.json`), and forms whose type, program or options are unclear are left to you.
- A failed CAS login is never retried, so the account can't get locked; run `set-password` to try again.
- Only Moodle **Choice** forms are filled in. Other form types get a notification.
- Phone messages can't ping anyone (`@everyone` is switched off) and don't load link previews.
- If Outlook asks for a login again, you get a notification: run `py main.py browse` and log in, then
  `Start-ScheduledTask autofill-forms`.

## Read / unread

The script opens every course email, and Outlook marks an opened email as read. With `keep_unread: true`
(config.yaml) an email that was unread is marked unread again right after it has been read, so your Outlook looks
untouched; emails you had already read stay read. This works in any Outlook language. If your Outlook is set to
mark emails read only "when the selection changes", an email can still end up read later.

## What goes to Google (Gemini)

- **Every new course email** (one request each): its subject and text, up to 8000 characters. Nothing else:
  no schedule, no program.
- **An open Moodle form** (one more request): the form's title, description and options, the email again, and
  your rules: your study program names, the other programs' names, your schedule as days and hours only
  (`Every Monday: 08:00-10:00, ...`; the labels in schedule.txt, like course names, are not sent),
  `min_gap_minutes` and `prefer`.

Each email is asked about once; a form that isn't open yet is asked about when it opens.

Before sending, everything in `private_words` (your name) becomes `[student]`, and for anyone: email addresses
become `[email]`, IP addresses `[ip]` and 6-digit numbers (index numbers, which are also Moodle usernames)
`[index]`. Other people's names in an email (teachers, classmates in a signature, students named in a faculty
notice) and phone numbers are still sent.

On the free tier, Google may use what you send to improve its products, and human reviewers may read it
(not linked to your account). This applies in North Macedonia; in the EEA, Switzerland and the UK it doesn't.
Google asks not to send sensitive or confidential information to the free tier. If that's not acceptable,
don't save a key: the script then works with keywords only.

Discord gets the short message and the Moodle link of each email.

## Privacy (this repo is public)

Your data never goes into git: `preferences/`, `state.json`, `logs/` (screenshots, emails) and
`browser-profile/` (Outlook/Moodle cookies) are gitignored, and the CAS password, the Gemini key and the
Discord webhook live only in Windows Credential Manager. `preferences.example/` and the tests use made-up
data, and the tests never reach the network or Credential Manager. Check `git status --ignored` before
committing.

## Layout

```
main.py                CLI and orchestration (no Playwright here)
config.yaml            generic settings: URLs, sender domain, Gemini models, keywords, poll interval
autoform/              schedule.py, classifier.py, decide.py, links.py: pure logic, unit tested
  gemini.py            Gemini client: the email and form prompts, redaction, schema checks (only module calling Gemini)
  discord_webhook.py   phone messages (the only module that calls Discord)
  notify.py            Windows notification + phone message
  browser/             all Playwright code: session (profile, CAS), outlook_web, moodle_form, inspect
tests/                 pytest (the browser tests run headless Edge against fake Outlook/Moodle pages)
install_task.ps1       registers the scheduled task
```
