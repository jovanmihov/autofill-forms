"""Moodle (4.5) Choice activities: read the options, select one, save, check it was saved."""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Page

from ..models import FormPage, Option, PageStatus
from ..schedule import normalize

log = logging.getLogger("autoform")

MAIN = "#region-main"
_FULL_RE = re.compile(r"\((full|полно)\)\s*$", re.IGNORECASE)

_READ_FORM_JS = """() => {
  const main = document.querySelector('#region-main') || document.body;
  let inputs = [...main.querySelectorAll('input[name="answer"], input[name="answer[]"]')];
  if (!inputs.length) inputs = [...main.querySelectorAll('form input[type=radio], form input[type=checkbox]')];
  const labelOf = i => {
    const byFor = i.id && main.querySelector(`label[for="${CSS.escape(i.id)}"]`);
    return (byFor || i.closest('label') || i.parentElement).innerText;
  };
  const form = inputs.length ? inputs[0].form : null;
  const text = sel => [...document.querySelectorAll(sel)].map(e => e.innerText.trim()).filter(Boolean).join('\\n');
  return {
    options: inputs.map(i => ({value: i.value, name: i.name, label: labelOf(i), disabled: i.disabled, checked: i.checked})),
    hasSubmit: !!(form && form.querySelector('input[type=submit], button[type=submit]')),
    selection: text('#yourselection'),
    notOpenYet: !!document.querySelector('.notopenyet'),
    title: text('#region-main h2') || text('h2') || document.title,
    intro: text('.activity-description, #intro'),
  };
}"""

_POST_LINKS_JS = """() => [...document.querySelectorAll('.post-content-container a[href]')]
  .map(a => [a.href, a.innerText.trim()])"""


def _first_line(text: str) -> str:
    # Moodle puts "Responses: n / Limit: m" on extra lines under the option text.
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def read_form(page: Page) -> FormPage:
    data = page.evaluate(_READ_FORM_JS)
    options = []
    for raw in data["options"]:
        label = _first_line(raw["label"])
        options.append(Option(
            value=raw["value"],
            label=label,
            input_name=raw["name"],
            disabled=raw["disabled"] or bool(_FULL_RE.search(label)),
            checked=raw["checked"],
        ))
    if data["notOpenYet"]:
        status = PageStatus.NOT_OPEN_YET
    elif options and data["hasSubmit"]:
        status = PageStatus.OPEN
    elif options:
        status = PageStatus.NOT_OPEN_YET  # preview before it opens, or every slot full: look again later
    else:
        status = PageStatus.CLOSED
    selection = re.sub(r"^[^:]*:\s*", "", data["selection"]) if data["selection"] else ""
    return FormPage(
        url=page.url,
        title=data["title"].splitlines()[0] if data["title"] else "",
        intro=data["intro"],
        options=tuple(options),
        already_answered=bool(selection) or any(o.checked for o in options),
        selection=selection,
        status=status,
    )


def submit_choice(page: Page, url: str, option: Option) -> bool:
    """Select option, press the form's save button, then reload and check Moodle shows it."""
    main = page.locator(MAIN)
    selector = f'input[name="{option.input_name}"][value="{option.value}"]'
    main.locator(selector).first.check()
    # has= is matched inside the form, so it takes a bare selector, not one starting at #region-main.
    button = main.locator("form", has=page.locator(selector)).locator('input[type="submit"], button[type="submit"]').first
    with page.expect_navigation():
        button.click()
    page.goto(url)
    after = read_form(page)
    saved = normalize(option.label) in normalize(after.selection) or any(
        o.checked and o.value == option.value for o in after.options
    )
    log.info("submitted %r on %s: %s", option.label, url, "confirmed" if saved else "NOT confirmed")
    return saved


def post_links(page: Page) -> list[tuple[str, str]]:
    """Links inside forum post bodies (not the course menu, which links every activity)."""
    return [tuple(link) for link in page.evaluate(_POST_LINKS_JS)]


def save_evidence(page: Page, folder: Path, tag: str) -> Path:
    """Screenshot + HTML in logs/, named by time and tag. Returns the screenshot path."""
    folder.mkdir(parents=True, exist_ok=True)
    base = folder / f"{datetime.now():%Y%m%d-%H%M%S}-{re.sub(r'[^0-9A-Za-z_-]+', '-', tag)[:60]}"
    base.with_suffix(".html").write_text(page.content(), encoding="utf-8")
    page.screenshot(path=str(base.with_suffix(".png")), full_page=True)
    return base.with_suffix(".png")
