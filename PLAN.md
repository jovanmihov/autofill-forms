# autofill-forms: FINKI Moodle auto-form script

## Context
Course forms show up on courses.finki.ukim.mk (Moodle, with CAS login at `https://cas.finki.ukim.mk/cas/login?service=https%3A%2F%2Fcourses.finki.ukim.mk%2Flogin%2Findex.php`). The site's home page can be viewed without logging in and has a login button. The notification for each form arrives by email in **classic Outlook desktop** (Office16 is installed on this PC). There are two kinds of form to fill in automatically:
1. **Midterm attendance** ("will you attend the колоквиум?"). Always answer **Yes** if it's for the user's study program.
2. **Lab exercise term**. Pick a time slot that doesn't clash with the user's weekly schedule, which is kept in a `.txt` file.

What the user decided:
- It runs **fully automatically** (it submits).
- It **checks every few minutes** through Task Scheduler.
- Language is Python 3.13, run with `py`.
- **Playwright code lives in its own module(s), separate from the script and the decision logic.**
- The project will be **public on GitHub**. Personal data (account, schedule, program, logs, browser session) must never be committed. Example files with fake data get committed in their place.

## Layout
```
main.py                     CLI entry point / orchestration only (no Playwright imports)
config.yaml                 generic settings (Moodle/CAS URLs, lookback days, keywords, headless): committed
preferences.example/        committed, fake data
  profile.yaml              program aliases, other FINKI programs, Outlook account filter, lab rules
  schedule.txt              e.g. "Понеделник 08:00-10:00 Веб програмирање"
preferences/                GITIGNORED, the user's real copy of preferences.example/
autoform/
  outlook_reader.py         pywin32 COM: scan the Inbox of every Outlook account, extract Moodle links
  classifier.py             midterm / lab / ignore (email keywords, then confirmed by the shape of the options)
  schedule.py               parse schedule.txt + slot labels, check for overlaps (pure, no I/O)
  decide.py                 pick the option for midterm/lab forms (pure, works on plain Option dataclasses)
  models.py                 dataclasses shared by the modules (Email, Option, Decision…)
  state.py                  state.json of processed emails/activities (so nothing is submitted twice)
  notify.py                 Windows toast + log line
  credentials.py            keyring get/set for the CAS username + password
  browser/                  ALL Playwright code lives here, nowhere else
    session.py              launch_persistent_context("browser-profile/"), CAS login
    moodle_form.py          open activity, read options -> list[Option], select, submit, verify, screenshot
tests/                      pytest for schedule/classifier/decide, fake data only
install_task.ps1            registers the scheduled task (every 3 min, pythonw, only when logged on)
requirements.txt            pywin32, playwright, pyyaml, keyring, win11toast, pytest (already exists)
.gitignore                  preferences/, state.json, logs/, browser-profile/, .venv/, __pycache__/, .pytest_cache/
README.md                   setup: venv, copy preferences.example -> preferences, set-password, scan, dry-run, install task
```

## Keeping personal data private
- The CAS username and password are stored only in Windows Credential Manager (`keyring`). They are never kept in a file.
- `preferences/` (program, schedule, Outlook account address) is gitignored. If it's missing, `main.py` exits with "copy preferences.example to preferences and fill it in".
- `browser-profile/` holds the Moodle/CAS session cookies, so it is gitignored. `logs/` and `state.json` hold email subjects, course names and screenshots, so they are gitignored too.
- Tests and example files use only made-up data.
- Before the first commit, check with `git status --ignored` that nothing personal gets staged.

## How it works (`py main.py run`, which is what the scheduled task calls)
1. **Read mail** (`outlook_reader.py`). `Dispatch("Outlook.Application").GetNamespace("MAPI")`, then go through `ns.Stores` and take each store's `GetDefaultFolder(6)` Inbox. This matters because the student account is probably not the default one; the account filter is set in `profile.yaml`. Sort by `[ReceivedTime]` descending and stop once past `lookback_days` (default 3). Avoid `Restrict` date strings because they depend on the locale. Keep only emails that contain a `courses.finki.ukim.mk` link and haven't been processed before (tracked by EntryID in state.json).
2. **Extract the target link.** Prefer `/mod/(choice|feedback|questionnaire|groupselect|scheduler)/view.php?id=N`. If the email only links to a forum post (`/mod/forum/discuss.php`), open that post (in `browser/`) and take the activity link from inside it.
3. **Classify** (`classifier.py`). Look for keywords in the subject and body, in both Cyrillic and Latin: midterm = колоквиум/kolokvium/midterm/парцијал; lab = лабораториск/лаб. вежби/термин/lab. Once the page is open, confirm using the options themselves: options that parse as day+time slots mean **lab**, Да/Не-style options mean **midterm**. If the email keywords and the options disagree, skip it and notify.
4. **Browser** (`browser/session.py`). Use Playwright `launch_persistent_context("browser-profile/")`, headless by default, so the session is reused between runs. Open the activity URL. If it redirects to `cas.finki.ukim.mk/cas/login`, fill `#username`/`#password` with credentials from keyring, submit, then go back to the activity URL. If login fails, stop, notify, and **don't retry**, so the account doesn't get locked.
5. **Read options** (`browser/moodle_form.py`). Moodle Choice uses `input[name=answer]` / `input[name^="answer["]` with `label[for=id]`. Options that are disabled or marked "(Full)/(Полно)" are dropped. As a fallback, collect the radio/checkbox inputs in `#region-main` together with their labels. Return plain `Option` dataclasses to the caller. If the user has already answered, record it in state and skip.
6. **Decide** (`decide.py`, pure).
   - **Midterm:** If the page or email names one of `other_programs` (whole-word Unicode match) and none of the user's `program_aliases`, skip it as not their program. Otherwise pick the option that matches a yes-word (да/yes/da/ќе присуствувам/присуствувам) and no no-word (не/no/нема). If some options carry program names, pick the one with the user's program.
   - **Lab:** Parse each label for a weekday (MK/EN names and abbreviations, or a `dd.mm.yyyy` date converted to a weekday) and a time range (`10:00-12:00`, `10-12`, `10:15–12:00`). Drop options that overlap `schedule.txt` (with `min_gap_minutes` added). Order the rest by `prefer` (default: earliest) and pick the first.
   - If there are 0 candidates, or the result is ambiguous, **don't submit**. Send a toast notification "needs you: <link>" instead.
7. **Submit and verify** (`browser/moodle_form.py`). Click the form's submit button ("Save my choice"/"Зачувај…"), reload the page, and confirm that the chosen option is shown or checked. Save a screenshot to `logs/`, write state.json, and show a toast like "Lab: Среда 12:00-14:00 booked for <course>".

## CLI (`main.py`)
- `py main.py set-password`: the **user** types their CAS username and password themselves (getpass), and they're stored in Credential Manager. Claude never handles the password.
- `py main.py scan [--lookback-days 60]`: lists the matching emails (date, subject, type, link) without opening a browser.
- `py main.py run [--dry-run] [--headed] [--lookback-days N]`: `--dry-run` does everything except clicking submit, and saves the page HTML and a screenshot to `logs/`.
- `py main.py open <moodle-url> --dry-run`: tests against a link you paste in.

## Implementation order
1. `git init`, `.gitignore`, then `py -3.13 -m venv .venv`, `.venv\Scripts\python -m pip install -r requirements.txt`, and `.venv\Scripts\python -m playwright install chromium`.
2. `models.py`, `schedule.py`, `classifier.py`, `decide.py`, plus pytest tests using realistic fake labels ("Понеделник 08:00-10:00 Лаб 215", "12.11.2026 (Среда) 10-12", "Да, ќе присуствувам", "Не").
3. `outlook_reader.py` + `scan`. Run it to confirm that the user's past course-form email is found and classified.
4. `browser/` + `run`/`open` with `--dry-run`. If the real markup differs from standard Moodle Choice, adjust the selectors using the HTML saved in `logs/`.
5. `preferences.example/` with fake data and `README.md`. The user copies the example files to `preferences/` and fills in their real data.
6. `install_task.ps1`: `schtasks /Create /SC MINUTE /MO 3 /TN "autofill-forms" /TR "<.venv pythonw> <main.py> run" /IT`. Only registered after the dry run passes, and only when the user says so.

## Verification
- `.venv\Scripts\python -m pytest`: parsers, overlap logic, yes/program pickers, and classifier.
- `py main.py scan --lookback-days 60`: the past course-form email shows up with the right type and link. This only reads local Outlook data.
- The user runs `set-password`, then `main.py run --dry-run --headed --lookback-days 60`: watch it log in through CAS, open the form, and log the option it would choose without submitting. Check the screenshot and HTML in `logs/`. (Past forms may be closed or already answered. In that case use the saved HTML and/or `open <url>` on a currently open form.)
- `git status --ignored`: `preferences/`, `logs/`, `state.json`, `browser-profile/` and `.venv/` are listed as ignored, not untracked.
- After the task is installed: check `logs/autoform.log` for a run every ~3 minutes and make sure no errors appear.

## Known risks
- If Outlook shows a "program is trying to access email" security prompt, Windows Defender must be shown as active for Outlook to trust the script.
- Notification emails could be digests, or come in a format other than Moodle's. The `scan` step on the past email checks this before anything is submitted.
- Lab slots are first-come-first-served, so polling every 3 minutes plus Outlook's own sync delay can lose a race. The interval can be changed in `install_task.ps1`.
