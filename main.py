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
    sys.exit(subprocess.call([str(_VENV / "Scripts" / _exe), __file__, *sys.argv[1:]]))

import argparse
import getpass
import logging
import msvcrt
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

from autoform import credentials
from autoform.classifier import describe_hints, email_hints
from autoform.config import (
    LOGS_DIR, STATE_FILE, Config, SetupError, check_not_example, load_config, load_profile, load_schedule,
)
from autoform.decide import decide_form
from autoform.links import activity_links, is_choice, pick_target
from autoform.models import Action, FormType
from autoform.notify import notify
from autoform.state import RETRY_SECONDS, State

log = logging.getLogger("autoform")

FORMS_DIR = LOGS_DIR / "forms"
LOCK_FILE = LOGS_DIR / "browser.lock"
OUTLOOK_RELOAD_SECONDS = 30 * 60
MAX_ERRORS_IN_A_ROW = 5


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


class Runner:
    """One browser with two tabs: Outlook on the web and Moodle."""

    def __init__(self, config: Config, context, dry_run: bool):
        self.config = config
        self.dry_run = dry_run
        self.state = State(STATE_FILE)
        self.timeout = config.page_timeout_seconds * 1000
        self.outlook = context.pages[0] if context.pages else context.new_page()
        self.moodle = context.new_page()
        for page in (self.outlook, self.moodle):
            page.set_default_timeout(self.timeout)
        self.profile = None
        self.reload_preferences()

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

    def tell(self, title: str, message: str, link: str | None = None) -> None:
        if self.dry_run:
            print(f"  [dry run] would notify: {title}: {message}")
        else:
            notify(title, message, link)

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

        outlook_web.open_inbox(self.outlook, self.config.outlook_url, self.timeout)

    def check_mail(self, since: datetime, max_scrolls: int) -> None:
        from autoform.browser import outlook_web

        items = outlook_web.list_moodle_emails(self.outlook, self.config.moodle_sender, since, max_scrolls)
        log.debug("%d Moodle email(s) in the Inbox list", len(items))
        for item in items:
            if self.state.email_done(item.conv_id) or not email_hints(item.label, self.config.keywords):
                continue  # done before, or not about a form (left unopened, so it stays unread)
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

        email = outlook_web.read_email(self.outlook, item, self.timeout)
        text = f"{email.subject}\n{email.body}"
        hints = email_hints(text, self.config.keywords) or email_hints(item.label, self.config.keywords)
        keywords = [k for hint in hints for k in self.config.keywords[hint.value]]
        kind, url = pick_target(email.links, self.config.moodle_url, keywords)
        if kind == "forum":
            self.goto_moodle(url)
            kind, url = pick_target(moodle_form.post_links(self.moodle), self.config.moodle_url, keywords)
        log.info("email %r: %s, %s %s", email.subject, describe_hints(hints), kind, url or "")
        if self.dry_run:
            when = f"{email.received:%Y-%m-%d %H:%M}" if email.received else "?"
            print(f"\n{when}  {email.subject}\n  type: {describe_hints(hints)}, link: {url or kind}")

        if kind == "several":
            self.tell("Needs you", f"Several forms are linked in: {email.subject}")
            status = "needs_user"
        elif kind != "activity":
            status = "skipped"
        else:
            status = self.process_activity(url, hints, text)
        if not self.dry_run:
            self.state.record_email(item.conv_id, status, subject=email.subject, url=url)

    # --- Moodle form -------------------------------------------------------

    def process_activity(self, url: str, hints: frozenset[FormType], email_text: str) -> str:
        from autoform.browser import moodle_form

        if self.state.activity_done(url):
            return "skipped"  # handled through another email
        if not self.state.activity_due(url):
            return "retry"
        if not is_choice(url):
            self.tell("Needs you", "This form isn't a Moodle Choice, so fill it in yourself.", url)
            if not self.dry_run:
                self.state.record_activity(url, "needs_user", reason="not a Choice activity")
            return "needs_user"

        self.goto_moodle(url)
        page = moodle_form.read_form(self.moodle)
        decision = decide_form(hints, page, email_text, self.profile, self.busy)
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
                print(f"    - {option.label}{marks}")
            verdict = f"would choose: {choice}" if decision.action is Action.SUBMIT else decision.action.value
            print(f"  [dry run] {kind}: {verdict} ({decision.reason})")
            for note in decision.notes:
                print(f"    {note}")
            print(f"  saved {shot} and .html")
            return "dry_run"

        info = {"title": page.title, "type": kind, "reason": decision.reason}
        if decision.action is Action.SUBMIT:
            saved = moodle_form.submit_choice(self.moodle, url, decision.option)
            shot = moodle_form.save_evidence(self.moodle, FORMS_DIR, f"submitted-{page.title}")
            if saved:
                notify(f"{kind.title()}: {choice}", f"saved on {page.title}", url)
                status = "submitted"
            else:
                notify("Needs you", f"Saved {choice!r} on {page.title}, but Moodle doesn't show it. Please check.", url)
                status = "needs_user"
            self.state.record_activity(url, status, choice=choice, screenshot=str(shot), **info)
            return status
        if decision.action is Action.RETRY:
            if self.state.record_retry(url, **info):
                notify("Waiting for a form", f"{page.title} isn't open yet; checking every {RETRY_SECONDS // 60} min", url)
            return "retry"
        if decision.action is Action.NEEDS_USER:
            notify("Needs you", f"{page.title}: {decision.reason}", url)
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
        items = outlook_web.list_moodle_emails(page, config.moodle_sender, since, max_scrolls=30)
        state = State(STATE_FILE)
        print(f"{len(items)} email(s) from {config.moodle_sender} since {since:%Y-%m-%d}.\n")
        for item in items:
            hints = email_hints(item.label, config.keywords)
            status = state.email_status(item.conv_id)
            when = f"{item.received:%Y-%m-%d %H:%M}" if item.received else "?"
            if not hints:
                print(f"{when}  {'ignore':<12} {item.label[:90]}")
                continue
            email = outlook_web.read_email(page, item, timeout)
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
            status = runner.process_activity(url, frozenset(), "")
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
        "set-password": cmd_set_password, "browse": cmd_browse, "scan": cmd_scan,
        "run": cmd_run, "watch": cmd_watch, "open": cmd_open,
    }
    try:
        return commands[args.command](args)
    except SetupError as e:
        log.error("%s", e)  # the console handler shows it too
        if args.command in ("run", "watch") and not getattr(args, "dry_run", False):
            notify("autofill-forms needs you", str(e))
        # The scheduled task restarts watch when it fails; this needs the user, so exit "cleanly".
        return 0 if args.command == "watch" else 2


if __name__ == "__main__":
    sys.exit(main())
