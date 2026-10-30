"""autofill-forms: answer FINKI Moodle midterm/lab forms announced by email.

CLI entry point and orchestration only. Browser code lives in autoform/browser/.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# "py main.py ..." starts the system Python: hand over to the project's .venv, which has the packages.
_VENV = Path(__file__).resolve().parent / ".venv"
if (_VENV / "Scripts" / "python.exe").exists() and Path(sys.prefix).resolve() != _VENV:
    _exe = "pythonw.exe" if Path(sys.executable).name.lower() == "pythonw.exe" else "python.exe"
    try:
        sys.exit(subprocess.call([str(_VENV / "Scripts" / _exe), __file__, *sys.argv[1:]]))
    except KeyboardInterrupt:  # Ctrl+C reaches the .venv process too, which stops on its own
        sys.exit(130)

import argparse
import getpass
import logging
import msvcrt
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

from autoform import credentials, discord_webhook
from autoform.classifier import course_code, describe_hints, email_hints, keyword_analysis
from autoform.config import (
    LOGS_DIR, STATE_FILE, Config, SetupError, check_not_example, load_config, load_profile, load_schedule,
)
from autoform.decide import check_suggestion, decide_form, page_decision
from autoform.gemini import FORM_KINDS, Gemini, GeminiError
from autoform.links import activity_links, is_choice, pick_target
from autoform.models import Action, Analysis, Category, Decision, Email, FormPage, FormType
from autoform.notify import notify
from autoform.state import RETRY_SECONDS, State

log = logging.getLogger("autoform")

FORMS_DIR = LOGS_DIR / "forms"
LOCK_FILE = LOGS_DIR / "browser.lock"
OUTLOOK_RELOAD_SECONDS = 30 * 60
MAX_ERRORS_IN_A_ROW = 5

# Notification titles for emails without a form to answer.
TITLES = {
    Category.LAB_FORM: "Lab form", Category.MIDTERM_FORM: "Midterm form", Category.OTHER_FORM: "Form",
    Category.EVENT: "Event", Category.SCHEDULE_CHANGE: "Schedule change",
    Category.ANNOUNCEMENT: "Announcement", Category.OTHER: "Course email",
}


def setup_logging(verbose: bool = False) -> None:
    LOGS_DIR.mkdir(exist_ok=True)
    log.setLevel(logging.DEBUG)
    file_handler = RotatingFileHandler(LOGS_DIR / "autoform.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    file_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    log.addHandler(file_handler)
    if sys.stderr is not None:  # None under pythonw
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        console.setLevel(logging.DEBUG if verbose else logging.WARNING)
        log.addHandler(console)


@contextmanager
def single_browser():
    """Only one process may use browser-profile/ at a time (Edge refuses a second one)."""
    LOGS_DIR.mkdir(exist_ok=True)
    handle = open(LOCK_FILE, "a+")
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        raise SetupError("the script's browser is already in use (is 'watch' running?)") from None
    try:
        yield
    finally:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        handle.close()


@contextmanager
def browser(config: Config, headed: bool):
    from autoform.browser.session import open_context

    with single_browser(), open_context(headless=config.headless and not headed, channel=config.browser_channel) as context:
        yield context


class StopMoodle(Exception):
    """No usable CAS login: leave Moodle alone until the user runs set-password."""


def make_gemini(config: Config) -> Gemini | None:
    """Gemini with the saved API key, or None (keyword classifier) when no key is saved."""
    key = credentials.load_secret(credentials.GEMINI_KEY)
    if not key or not config.gemini_models:
        log.info("no Gemini API key saved, using the keyword classifier (py main.py set-gemini-key)")
        return None
    return Gemini(key, config.gemini_models, config.gemini_timeout_seconds, config.gemini_min_interval_seconds)


class Runner:
    """One browser with two tabs: Outlook on the web and Moodle."""

    def __init__(self, config: Config, context, dry_run: bool):
        self.config = config
        self.dry_run = dry_run
        self.state = State(STATE_FILE)
        self.timeout = config.page_timeout_seconds * 1000
        self.context = context
        self.outlook = context.pages[0] if context.pages else context.new_page()
        self.moodle = context.new_page()
        for page in (self.outlook, self.moodle):
            page.set_default_timeout(self.timeout)
        self.profile = None
        self.reload_preferences()
        self.gemini = make_gemini(config)
        if dry_run:
            print(f"Gemini: {', '.join(config.gemini_models)}" if self.gemini else "Gemini: no key saved, using keywords")

    def reload_preferences(self) -> None:
        """Read preferences/ again, so edits apply without restarting watch."""
        try:
            profile, busy = load_profile(), load_schedule()
            try:
                check_not_example()
            except SetupError as e:
                if not self.dry_run:
                    raise
                if self.profile is None:
                    print(f"WARNING: {e} (fine for a dry run, but the choices below use the example data)")
        except SetupError:
            if self.profile is None:
                raise
            log.exception("preferences/ has an error; keeping the previous version")
            return
        self.profile, self.busy = profile, busy

    def reopened(self, page):
        """The same tab, or a new one if it was closed (e.g. in the --headed window)."""
        if not page.is_closed():
            return page
        log.info("a browser tab was closed; opening a new one")
        page = self.context.new_page()
        page.set_default_timeout(self.timeout)
        return page

    def tell(self, title: str, message: str, link: str | None = None, email: Email | None = None) -> None:
        """With email, the title also says which course and who it is from: "Lab · Аипс · Georgiev Pece"."""
        if email:
            title = " · ".join(filter(None, (title, course_code(email.subject), email.sender)))
        if self.dry_run:
            print(f"  [dry run] would notify (PC + phone): {title}: {message}{f'  {link}' if link else ''}")
        else:
            notify(title, message, link)

    # --- what an email is about ---------------------------------------------

    def show(self, what: str, analysis: Analysis) -> None:
        if self.dry_run:
            option = f", option {analysis.option_value}" if analysis.option_value else ""
            reason = f" ({analysis.reason})" if analysis.reason else ""
            print(f"  {analysis.source} ({what}): {analysis.category.value}{option}: {analysis.message}{reason}")

    def read_email(self, email: Email, hints: frozenset[FormType], has_form: bool) -> Analysis:
        """What the email is about: Gemini's answer, else (no key, or Gemini failed) the keywords'."""
        analysis = None
        if self.gemini:
            try:
                analysis = self.gemini.read_email(email, self.profile.private_words)
            except GeminiError as e:
                log.warning("Gemini gave no usable answer about the email: %s", e)
                if self.dry_run:
                    print(f"  Gemini failed on the email: {e}")
        if analysis is None:
            analysis = keyword_analysis(email.subject, hints, has_form)
        self.show("email", analysis)
        return analysis

    # --- Moodle login ------------------------------------------------------

    def cas_credentials(self) -> tuple[str, str]:
        if self.state.login_blocked:
            raise StopMoodle(f"CAS login is blocked ({self.state.login_blocked}); run  py main.py set-password")
        saved = credentials.load()
        if not saved:
            self.state.block_login("no saved CAS password")
            self.state.save()
            notify("CAS password needed", "Run: py main.py set-password")
            raise StopMoodle("no saved CAS password")
        return saved

    def goto_moodle(self, url: str) -> None:
        from autoform.browser.session import LoginFailed, goto_moodle

        self.moodle = self.reopened(self.moodle)
        try:
            goto_moodle(self.moodle, url, self.config.moodle_url, self.config.cas_login_url, self.cas_credentials)
        except LoginFailed as e:
            self.state.block_login(str(e))
            self.state.save()
            notify("CAS login failed", f"{e}. Not retrying. Run: py main.py set-password")
            raise StopMoodle(str(e)) from e

    # --- Outlook -----------------------------------------------------------

    def open_outlook(self) -> None:
        from autoform.browser import outlook_web

        self.outlook = self.reopened(self.outlook)
        outlook_web.open_inbox(self.outlook, self.config.outlook_url, self.timeout)

    def check_mail(self, since: datetime, max_scrolls: int) -> None:
        from autoform.browser import outlook_web

        items = outlook_web.list_course_emails(self.outlook, self.config.sender_domain_prefix, since, max_scrolls)
        log.debug("%d course email(s) in the Inbox list", len(items))
        for item in items:
            if self.state.email_done(item.conv_id) or self.state.email_waiting(item.conv_id):
                continue  # done before, or its form isn't due for another look yet
            try:
                self.process_email(item)
            except StopMoodle as e:
                log.warning("%s", e)
                break
            except Exception as e:
                log.exception("email %s failed", item.conv_id)
                if not self.dry_run and self.state.record_failure(item.conv_id, str(e)):
                    notify("autofill-forms needs you", "A course email kept failing; see logs/autoform.log")
            finally:
                self.state.save()

    def process_email(self, item) -> None:
        from autoform.browser import moodle_form, outlook_web

        email = outlook_web.read_email(self.outlook, item, self.timeout, keep_unread=self.config.keep_unread)
        text = f"{email.subject}\n{email.body}"
        hints = email_hints(text, self.config.keywords) or email_hints(item.label, self.config.keywords)
        keywords = [k for hint in hints for k in self.config.keywords[hint.value]]
        kind, url = pick_target(email.links, self.config.moodle_url, keywords)
        if self.dry_run:
            when = f"{email.received:%Y-%m-%d %H:%M}" if email.received else "?"
            print(f"\n{when}  {email.subject}\n  keywords: {describe_hints(hints)}, link: {url or kind}")
        # Asked once per email: a form that isn't open yet brings the email back every few minutes.
        summary = self.state.email_summary(item.conv_id) or self.read_email(email, hints, kind in ("activity", "several"))
        link = url
        # Gemini read the whole email; keywords also match a "колоквиум" mentioned in passing.
        about_a_form = summary.category in FORM_KINDS if summary.source == "gemini" else bool(hints)
        if kind == "forum" and about_a_form:
            # A form email that only links to its forum post: the form link is in the post.
            self.goto_moodle(url)
            kind, url = pick_target(moodle_form.post_links(self.moodle), self.config.moodle_url, keywords)
            link = url or link
        log.info("email %r from %s: %s (%s), %s %s", email.subject, item.sender, summary.category.value,
                 summary.source, kind, url or "")

        if kind == "activity":
            status = self.process_activity(url, email, hints, summary)
        else:
            status = self.inform(summary, kind, link, email)
        if not self.dry_run:
            self.state.record_email(
                item.conv_id, status, subject=email.subject, url=url if kind == "activity" else None,
                category=summary.category.value, message=summary.message, source=summary.source,
            )

    def inform(self, summary: Analysis, kind: str, link: str | None, email: Email) -> str:
        """An email without one Moodle form to answer: tell the user what it is about."""
        if kind == "several":
            self.tell("Needs you", f"{summary.message} (it links several forms: answer them yourself)", link, email)
            return "needs_user"
        self.tell(TITLES[summary.category], summary.message, link, email)
        return "informed"

    # --- Moodle form -------------------------------------------------------

    def decide(self, page: FormPage, email: Email | None, hints: frozenset[FormType]) -> tuple[Decision, Analysis | None]:
        """With Gemini, its choice must pass decide.py's checks. Without a key, decide.py picks alone."""
        email_text = f"{email.subject}\n{email.body}" if email else ""
        if self.gemini is None:
            return decide_form(hints, page, email_text, self.profile, self.busy), None
        if before := page_decision(page):
            return before, None  # answered, closed or not open yet: nothing to ask Gemini
        try:
            choice = self.gemini.choose_option(page, email, self.profile, self.busy)
        except GeminiError as e:
            log.warning("Gemini gave no usable answer about the form: %s", e)
            if self.dry_run:
                print(f"  Gemini failed on the form: {e}")
            return Decision(Action.NEEDS_USER, reason="Gemini gave no usable answer, so nothing was submitted"), None
        self.show("form", choice)
        return check_suggestion(choice, page, email_text, self.profile, self.busy), choice

    def report(self, decision: Decision, what: str, url: str, email: Email | None, saved: bool = True) -> None:
        """The notification (PC + phone) for a form's outcome."""
        kind = decision.form_type.value.title() if decision.form_type else "Form"
        choice = decision.option.label if decision.option else ""
        if decision.action is Action.SUBMIT and saved:
            self.tell(kind, f"{what}. I chose {choice} for you.", url, email)
        elif decision.action is Action.SUBMIT:
            self.tell("Needs you", f"{what}: saved {choice!r}, but Moodle doesn't show it. Please check.", url, email)
        elif decision.action is Action.RETRY:
            self.tell("Waiting for a form", f"{what}: not open yet, checking every {RETRY_SECONDS // 60} min", url, email)
        elif decision.action is Action.NEEDS_USER:
            self.tell("Needs you", f"{what}: {decision.reason}", url, email)
        else:
            self.tell(kind, f"{what}: nothing to do ({decision.reason})", url, email)

    def process_activity(self, url: str, email: Email | None, hints: frozenset[FormType],
                         summary: Analysis | None = None) -> str:
        from autoform.browser import moodle_form

        if self.state.activity_done(url):
            return "skipped"  # handled through another email
        if not self.state.activity_due(url):
            return "retry"
        if not is_choice(url):
            what = summary.message if summary else "A form"
            self.tell("Needs you", f"{what} (not a Moodle Choice, so fill it in yourself)", url, email)
            if not self.dry_run:
                self.state.record_activity(url, "needs_user", reason="not a Choice activity")
            return "needs_user"

        self.goto_moodle(url)
        page = moodle_form.read_form(self.moodle)
        decision, analysis = self.decide(page, email, hints)
        what = next((a.message for a in (analysis, summary) if a and a.message), page.title)
        kind = decision.form_type.value if decision.form_type else "form"
        choice = decision.option.label if decision.option else ""
        log.info("%s %r: %s %s (%s)", kind, page.title, decision.action.value, choice, decision.reason)
        for note in decision.notes:
            log.info("  %s", note)

        if self.dry_run:
            shot = moodle_form.save_evidence(self.moodle, FORMS_DIR, f"dryrun-{page.title}")
            print(f"  form: {page.title} ({page.status.value}{', already answered: ' + page.selection if page.already_answered else ''})")
            for option in page.options:
                marks = "".join(m for m, on in (("  [disabled]", option.disabled), ("  [checked]", option.checked)) if on)
                print(f"    - {option.value}: {option.label}{marks}")
            verdict = f"would choose: {choice}" if decision.action is Action.SUBMIT else decision.action.value
            print(f"  [dry run] {kind}: {verdict} ({decision.reason})")
            for note in decision.notes:
                print(f"    {note}")
            print(f"  saved {shot} and .html")
            self.report(decision, what, url, email)
            return "dry_run"

        info = {"title": page.title, "type": kind, "reason": decision.reason}
        if analysis:
            info["gemini"] = analysis.category.value
        if decision.action is Action.SUBMIT:
            saved = moodle_form.submit_choice(self.moodle, url, decision.option)
            shot = moodle_form.save_evidence(self.moodle, FORMS_DIR, f"submitted-{page.title}")
            self.report(decision, what, url, email, saved)
            status = "submitted" if saved else "needs_user"
            self.state.record_activity(url, status, choice=choice, screenshot=str(shot), **info)
            return status
        if decision.action is Action.RETRY:
            if self.state.record_retry(url, **info):
                self.report(decision, what, url, email)
            return "retry"
        self.report(decision, what, url, email)
        status = "needs_user" if decision.action is Action.NEEDS_USER else "skipped"
        self.state.record_activity(url, status, **info)
        return status


# --- commands --------------------------------------------------------------

def cmd_set_password(args) -> int:
    print("Your CAS login is stored in Windows Credential Manager, not in any file.")
    username = input("CAS username: ").strip()
    password = getpass.getpass("CAS password (not shown): ")
    if not username or not password or password != getpass.getpass("Again: "):
        print("Empty or not the same twice. Nothing saved.")
        return 1
    credentials.save(username, password)
    state = State(STATE_FILE)
    state.unblock_login()
    state.save()
    print("Saved.")
    return 0


def cmd_set_gemini_key(args) -> int:
    print("Get a free API key at https://aistudio.google.com/apikey (needs a Google account; you must be 18+).")
    print("It is stored in Windows Credential Manager, not in any file.")
    key = getpass.getpass("Gemini API key (not shown): ").strip()
    if not key or any(c.isspace() for c in key):
        print("Empty or has spaces in it. Nothing saved.")
        return 1
    credentials.save_secret(credentials.GEMINI_KEY, key)
    config = load_config()
    print("Saved. Checking it with Google...")
    try:
        name = Gemini(key, config.gemini_models, config.gemini_timeout_seconds).check()
    except (GeminiError, ValueError) as e:
        print(f"The key is saved, but the check failed: {e}")
        return 1
    print(f"OK: {name} answers with this key.")
    return 0


def cmd_set_discord_webhook(args) -> int:
    print("In your own Discord server: Edit Channel > Integrations > Webhooks > New Webhook > Copy Webhook URL.")
    print("Anyone with the URL can post to that channel, so it is stored in Windows Credential Manager.")
    url = getpass.getpass("Webhook URL (not shown): ").strip()
    if not discord_webhook.is_webhook_url(url):
        print("That isn't a Discord webhook URL (https://discord.com/api/webhooks/<id>/<token>). Nothing saved.")
        return 1
    credentials.save_secret(credentials.DISCORD_WEBHOOK, url)
    try:
        discord_webhook.send(url, "autofill-forms is connected: course emails will show up here.")
    except discord_webhook.PhoneError as e:
        print(f"Saved, but the test message failed: {e}")
        return 1
    print("Saved, and a test message was sent: check your phone.")
    return 0


def cmd_browse(args) -> int:
    from autoform.browser.inspect import browse

    out_dir = LOGS_DIR / "browse"
    print("A browser window opens. Log in and click around; every page you stop on is saved")
    print(f"to {out_dir} (login pages are not saved). Close the window when you're done.")
    config = load_config()
    with single_browser():
        count = browse(args.url or config.outlook_url, out_dir, config.browser_channel)
    print(f"Saved {count} page(s).")
    return 0


def cmd_scan(args) -> int:
    from autoform.browser import outlook_web

    config = load_config()
    load_profile()  # fail early if preferences/ is missing
    since = datetime.now() - timedelta(days=args.lookback_days or config.lookback_days)
    timeout = config.page_timeout_seconds * 1000
    with browser(config, args.headed) as context:
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(timeout)
        outlook_web.open_inbox(page, config.outlook_url, timeout)
        items = outlook_web.list_course_emails(page, config.sender_domain_prefix, since, max_scrolls=30)
        state = State(STATE_FILE)
        print(f"{len(items)} email(s) from *@{config.sender_domain_prefix}* since {since:%Y-%m-%d}.\n")
        for item in items:
            hints = email_hints(item.label, config.keywords)
            status = state.email_status(item.conv_id)
            when = f"{item.received:%Y-%m-%d %H:%M}" if item.received else "?"
            if not hints:
                print(f"{when}  {'ignore':<12} {item.label[:90]}")
                continue
            email = outlook_web.read_email(page, item, timeout, keep_unread=config.keep_unread)
            hints = email_hints(f"{email.subject}\n{email.body}", config.keywords) or hints
            keywords = [k for hint in hints for k in config.keywords[hint.value]]
            kind, url = pick_target(email.links, config.moodle_url, keywords)
            target = {"activity": url, "forum": f"forum post {url}", "several": "several form links"}.get(kind, "no form link")
            print(f"{when}  {describe_hints(hints):<12} {email.subject}")
            print(f"{'':18}{target}{f'  [{status}]' if status else ''}")
    return 0


def cmd_run(args) -> int:
    config = load_config()
    since = datetime.now() - timedelta(days=args.lookback_days or config.lookback_days)
    with browser(config, args.headed) as context:
        runner = Runner(config, context, args.dry_run)
        runner.open_outlook()
        runner.check_mail(since, max_scrolls=10)
    return 0


def cmd_watch(args) -> int:
    config = load_config()
    lookback = timedelta(days=args.lookback_days or config.lookback_days)
    with browser(config, args.headed) as context:
        runner = Runner(config, context, args.dry_run)
        runner.open_outlook()
        opened, scrolls, errors = time.monotonic(), 10, 0
        log.info("watching for course emails every %ds%s", config.poll_seconds, " (dry run)" if args.dry_run else "")
        while True:
            try:
                runner.reload_preferences()
                runner.check_mail(datetime.now() - lookback, scrolls)
                scrolls, errors = 0, 0  # after the first pass, new mail is at the top
            except SetupError:
                raise
            except Exception:
                errors += 1
                log.exception("check failed (%d in a row)", errors)
                if errors >= MAX_ERRORS_IN_A_ROW:
                    raise
            runner.outlook.wait_for_timeout(config.poll_seconds * 1000)
            if errors or time.monotonic() - opened > OUTLOOK_RELOAD_SECONDS:
                runner.open_outlook()
                opened = time.monotonic()


def cmd_open(args) -> int:
    config = load_config()
    url = next(iter(activity_links([(args.url, "")], config.moodle_url)), args.url)
    with browser(config, args.headed) as context:
        runner = Runner(config, context, args.dry_run)
        try:
            status = runner.process_activity(url, None, frozenset())
        except StopMoodle as e:
            print(e)
            return 1
        runner.state.save()
    print(f"result: {status}")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(prog="main.py", description=__doc__.splitlines()[0])
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("set-password", help="store your CAS username and password in Windows Credential Manager")
    sub.add_parser("set-gemini-key", help="store your Gemini API key in Windows Credential Manager")
    sub.add_parser("set-discord-webhook", help="store the Discord webhook for phone messages and send a test")
    browse = sub.add_parser("browse", help="open the script's browser to log in and save page snapshots")
    browse.add_argument("url", nargs="?", help="start page (default: Outlook on the web)")
    scan = sub.add_parser("scan", help="list course emails and their form links (opens matching emails)")
    run = sub.add_parser("run", help="check the Inbox once and answer new forms")
    watch = sub.add_parser("watch", help="keep checking for new course emails (what the scheduled task runs)")
    open_ = sub.add_parser("open", help="answer one form from its Moodle link")
    open_.add_argument("url")
    for command in (scan, run, watch, open_):
        command.add_argument("--headed", action="store_true", help="show the browser window")
    for command in (scan, run, watch):
        command.add_argument("--lookback-days", type=int)
    for command in (run, watch, open_):
        command.add_argument("--dry-run", action="store_true", help="do everything except pressing save")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)
    commands = {
        "set-password": cmd_set_password, "set-gemini-key": cmd_set_gemini_key,
        "set-discord-webhook": cmd_set_discord_webhook, "browse": cmd_browse, "scan": cmd_scan,
        "run": cmd_run, "watch": cmd_watch, "open": cmd_open,
    }
    try:
        return commands[args.command](args)
    except KeyboardInterrupt:
        print("Stopped.")
        return 130
    except SetupError as e:
        log.error("%s", e)  # the console handler shows it too
        if args.command in ("run", "watch") and not getattr(args, "dry_run", False):
            notify("autofill-forms needs you", str(e))
        # The scheduled task restarts watch when it fails; this needs the user, so exit "cleanly".
        return 0 if args.command == "watch" else 2


if __name__ == "__main__":
    sys.exit(main())
