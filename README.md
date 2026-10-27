# autofill-forms

Answers FINKI Moodle (courses.finki.ukim.mk) forms that are announced by email:

- **Midterm attendance** ("ќе присуствувате на колоквиумот?"): answers *Yes* if the form is for your study program.
- **Lab term selection**: picks the earliest slot that doesn't clash with your weekly schedule.

It reads Outlook on the web in its own Microsoft Edge window (headless), notices new course emails within
about 30 seconds, opens the Moodle Choice form, decides, saves your answer, checks Moodle shows it, and
shows a Windows notification. Anything it can't decide safely is left to you, with a notification.

## Setup

Windows with Microsoft Edge and Python 3.13.

```
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

Copy `preferences.example\` to `preferences\` and fill in your own data (it is gitignored):

- `preferences\profile.yaml`: your study program (`program_aliases`), the other programs, lab rules.
- `preferences\schedule.txt`: your weekly classes, one per line, e.g. `Понеделник 08:00-10:00 Веб програмирање`.

Then:

```
py main.py set-password        # your CAS username + password -> Windows Credential Manager
py main.py browse              # log in to Outlook once in the window that opens ("Stay signed in": Yes), then close it
py main.py scan                # lists course emails and the form each one links to
py main.py run --dry-run --headed --lookback-days 7   # everything except pressing save
```

`py main.py ...` hands itself over to `.venv` automatically.

## Commands

| Command | What it does |
|---|---|
| `set-password` | Stores your CAS login in Windows Credential Manager. Also unblocks logins after a failed one. |
| `browse [url]` | Opens the script's browser so you can log in; saves each page you stay on to `logs/browse/`. |
| `scan [--lookback-days N]` | Lists course emails, their type and form link. Opens only form-related emails (Outlook marks those read). |
| `run [--dry-run] [--headed]` | Checks the Inbox once and answers new forms. |
| `watch [--dry-run] [--headed]` | Keeps checking every `poll_seconds` (30). This is what the scheduled task runs. |
| `open <moodle-url> [--dry-run]` | Answers one form from its link. |

A dry run saves the form's screenshot and HTML to `logs/forms/` and prints what it would choose.

## Run it automatically

After a dry run did the right thing:

```
powershell -ExecutionPolicy Bypass -File install_task.ps1
```

This starts `main.py watch` hidden whenever you log on. The log is `logs/autoform.log`.
Remove it with `Unregister-ScheduledTask -TaskName autofill-forms -Confirm:$false`.

## Safety

- Nothing is submitted twice (`state.json`), and forms whose type, program or options are unclear are left to you.
- A failed CAS login is never retried, so the account can't get locked; run `set-password` to try again.
- Only Moodle **Choice** forms are filled in. Other form types get a notification.
- If Outlook asks for a login again, you get a notification: run `py main.py browse` and log in, then
  `Start-ScheduledTask autofill-forms`.

## Privacy (this repo is public)

Your data never goes into git: `preferences/`, `state.json`, `logs/` (screenshots, emails) and
`browser-profile/` (Outlook/Moodle cookies) are gitignored, and the CAS password lives only in Windows
Credential Manager. `preferences.example/` and the tests use made-up data. Check `git status --ignored`
before committing.

## Layout

```
main.py                CLI and orchestration (no Playwright here)
config.yaml            generic settings: URLs, keywords, poll interval
autoform/              schedule.py, classifier.py, decide.py, links.py: pure logic, unit tested
  browser/             all Playwright code: session (profile, CAS), outlook_web, moodle_form, inspect
tests/                 pytest (the browser tests run headless Edge against fake Moodle pages)
install_task.ps1       registers the scheduled task
```
