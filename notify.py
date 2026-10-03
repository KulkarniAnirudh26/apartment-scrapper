"""Email the run report: the recommendation shortlist as the body, a few
rent-history charts inline, and explorer.html attached.

Opt-in, via `scraper.py --visualize --email` (or `visualize.py --email` to
re-send what is already on disk without scraping again). Credentials live in
.env, same as every other secret here:

    GMAIL_USER=you@gmail.com
    GMAIL_APP_PASSWORD=abcd efgh ijkl mnop     # myaccount.google.com/apppasswords
    EMAIL_TO=you@gmail.com                     # optional; defaults to GMAIL_USER

A normal Gmail password will not work - Google requires an App Password for
SMTP, which in turn requires 2-step verification on the account.

Nothing here raises: a send that fails prints a WARNING and returns False, so a
run that already wrote its CSVs and charts is never lost to a mail problem.

Also runnable on its own, to test the mail step without scraping or rebuilding
any charts - it just uses whatever is in plots/ already:

    python notify.py --dry-run   # write email-preview.eml/.html, send nothing
    python notify.py --check     # verify the credentials log in, send nothing
    python notify.py             # send it

Stdlib only (smtplib + email); no new dependency.
"""
from __future__ import annotations

import argparse
import base64
import collections
import datetime as dt
import html
import os
import re
import smtplib
from email.message import EmailMessage

try:
    from dotenv import load_dotenv
    _HAVE_DOTENV = True
except ImportError:                     # dotenv is in requirements, but don't
    _HAVE_DOTENV = False                # let a missing one break the run

    def load_dotenv(*_a, **_k):
        return False

# Communities whose rent-history chart rides along with the report. Matched
# loosely (case/punctuation-insensitive substring) against the PNG filenames,
# because those come from the *scraped* community name and drift between runs -
# Encasa's chart is "Encasaliving.png", not "Encasa-Living.png".
WATCH = ["Monticello", "Encasa", "Mylo", "1250 Lakeside"]

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
INK = "#1a1d21"
MUTED = "#5f6b7a"
RULE = "#e3e7ec"


# --------------------------------------------------------------------------- #
# credentials
# --------------------------------------------------------------------------- #
def _creds(to: str | None = None) -> tuple[str, str, str]:
    """(user, password, recipient). Recipient: argument > EMAIL_TO > user.

    Calls load_dotenv() itself - visualize.py never does, so `visualize.py
    --email` would otherwise see no credentials at all.
    """
    load_dotenv()
    user = (os.getenv("GMAIL_USER") or "").strip()
    # Google shows app passwords in groups of four; paste-with-spaces is the
    # single most likely way to get this wrong.
    password = (os.getenv("GMAIL_APP_PASSWORD") or "").replace(" ", "").strip()
    rcpt = ((to or "").strip() or (os.getenv("EMAIL_TO") or "").strip() or user)
    return user, password, rcpt


def available(to: str | None = None) -> tuple[bool, str]:
    """Can we send? Returns (ok, reason-if-not), like llm_extract.available()."""
    user, password, rcpt = _creds(to)
    if not user or not password:
        # The system Python has no python-dotenv, so load_dotenv() above was the
        # no-op shim and .env was never opened at all. Say that, rather than
        # blaming keys the user can see are filled in.
        if not _HAVE_DOTENV and os.path.exists(".env"):
            return False, (".env exists but python-dotenv isn't installed, so it was "
                           "never read - run this with venv\\Scripts\\python.exe, "
                           "not the system Python")
        return False, "no GMAIL_USER / GMAIL_APP_PASSWORD in .env"
    if not rcpt:
        return False, "no recipient (set EMAIL_TO in .env or pass --email ADDR)"
    return True, ""


# --------------------------------------------------------------------------- #
# picking the charts
# --------------------------------------------------------------------------- #
def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _find_plots(hist_dir: str, watch: list[str]) -> tuple[list[tuple], list[str]]:
    """([(watch_term, png_path)], [watch terms with no chart this run]).

    First match wins, and a file is only claimed once, so one term cannot
    swallow the chart a later term wanted.
    """
    try:
        files = sorted(f for f in os.listdir(hist_dir) if f.lower().endswith(".png"))
    except OSError:
        files = []

    found: list[tuple] = []
    missing: list[str] = []
    taken: set[str] = set()
    for term in watch:
        key = _norm(term)
        hit = next((f for f in files
                    if f not in taken and key and key in _norm(os.path.splitext(f)[0])),
                   None)
        if hit:
            taken.add(hit)
            found.append((term, os.path.join(hist_dir, hit)))
        else:
            missing.append(term)
    return found, missing


# --------------------------------------------------------------------------- #
# markdown -> html
# --------------------------------------------------------------------------- #
# Only as much markdown as write_recommendations() actually emits: h1/h3, pipe
# tables, "- " bullets, **bold**, *em*, `code`, [text](url). Every style is
# inline because Gmail strips <style> blocks.
def _inline(text: str) -> str:
    t = html.escape(text or "", quote=False)
    t = re.sub(r"`([^`]+)`",
               r'<code style="background:#f1f3f5;border-radius:3px;'
               r'padding:1px 4px;font-size:92%">\1</code>', t)
    t = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)",
               r'<a href="\2" style="color:#1558d6;text-decoration:none">\1</a>', t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", t)
    return t


def _cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_rule(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", c or "") for c in cells)


def _aligns(cells: list[str]) -> list[str]:
    out = []
    for c in cells:
        if c.startswith(":") and c.endswith(":"):
            out.append("center")
        elif c.endswith(":"):
            out.append("right")
        else:
            out.append("left")
    return out


def _table(block: list[str]) -> str:
    rows = [_cells(ln) for ln in block]
    if not rows:
        return ""
    head, body = rows[0], rows[1:]
    aligns = ["left"] * len(head)
    if body and _is_rule(body[0]):
        aligns = _aligns(body[0])
        body = body[1:]
    aligns += ["left"] * (len(head) - len(aligns))

    def align(i: int) -> str:
        return aligns[i] if i < len(aligns) else "left"

    cell = (f"padding:7px 10px;border-bottom:1px solid {RULE};"
            f"font:13px/1.45 {FONT};color:{INK}")
    out = ['<table role="presentation" cellspacing="0" cellpadding="0" '
           'style="border-collapse:collapse;width:100%;margin:0 0 18px">']
    out.append("<tr>" + "".join(
        f'<th style="{cell};text-align:{align(i)};font-weight:600;color:{MUTED};'
        f'border-bottom:2px solid {RULE};white-space:nowrap">{_inline(c)}</th>'
        for i, c in enumerate(head)) + "</tr>")
    for r in body:
        # A plan name or address containing "|" makes a ragged row. Render it
        # ragged rather than dropping data - the source markdown is just as
        # broken, and nothing here should crash over a scraped string.
        r = r + [""] * (len(head) - len(r))
        out.append("<tr>" + "".join(
            f'<td style="{cell};text-align:{align(i)}">{_inline(c)}</td>'
            for i, c in enumerate(r)) + "</tr>")
    out.append("</table>")
    return "\n".join(out)


def _md_to_html(md: str) -> str:
    lines = md.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip():
            i += 1
            continue

        if line.startswith("### "):
            out.append(f'<h3 style="font:600 15px/1.3 {FONT};color:{INK};'
                       f'margin:26px 0 10px">{_inline(line[4:])}</h3>')
            i += 1
        elif line.startswith("## "):
            out.append(f'<h2 style="font:600 17px/1.3 {FONT};color:{INK};'
                       f'margin:26px 0 10px">{_inline(line[3:])}</h2>')
            i += 1
        elif line.startswith("# "):
            out.append(f'<h1 style="font:600 21px/1.3 {FONT};color:{INK};'
                       f'margin:0 0 12px">{_inline(line[2:])}</h1>')
            i += 1
        elif line.lstrip().startswith("- "):
            items = []
            while i < len(lines) and lines[i].lstrip().startswith("- "):
                items.append(_inline(lines[i].lstrip()[2:]))
                i += 1
            out.append(f'<ul style="margin:0 0 18px;padding-left:20px;'
                       f'font:13px/1.6 {FONT};color:{INK}">'
                       + "".join(f'<li style="margin:0 0 7px">{it}</li>' for it in items)
                       + "</ul>")
        elif line.lstrip().startswith("|"):
            block = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                block.append(lines[i])
                i += 1
            out.append(_table(block))
        else:
            para = []
            while (i < len(lines) and lines[i].strip()
                   and lines[i].lstrip()[0] not in "#-|"):
                para.append(lines[i].strip())
                i += 1
            out.append(f'<p style="font:13px/1.6 {FONT};color:{INK};margin:0 0 16px">'
                       f'{_inline(" ".join(para))}</p>')
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# assembling the message
# --------------------------------------------------------------------------- #
def _today() -> str:
    return dt.date.today().strftime("%d %b %Y").lstrip("0")


def _subject(md: str) -> str:
    """Put the headline number in the subject so the inbox list is useful."""
    base = f"Apartment rents - {_today()}"
    try:
        matched = re.search(r"(\d+) floor plans matched", md)
        row = re.search(r"^\|\s*1\s*\|(.+)$", md, re.M)
        if not row:
            return base
        cells = _cells("|" + row.group(1))
        name = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", cells[0]).strip()
        rent = next((c for c in cells if re.fullmatch(r"\$[\d,]+", c)), "")
        tail = " ".join(b for b in (rent, name) if b)
        if matched:
            tail = (f"{tail} ({matched.group(1)} matches)" if tail
                    else f"{matched.group(1)} matches")
        return f"{base} - {tail}" if tail else base
    except Exception:                   # a subject line is never worth failing over
        return base


def _html_body(md: str, charts: list[tuple], missing: list[str],
               has_explorer: bool) -> str:
    parts = ['<div style="max-width:860px;margin:0 auto;padding:22px 18px;'
             'background:#ffffff">', _md_to_html(md)]

    if charts:
        parts.append(f'<h3 style="font:600 15px/1.3 {FONT};color:{INK};'
                     f'margin:30px 0 6px">Rent history</h3>')
        for idx, (term, _path) in enumerate(charts):
            parts.append(
                f'<p style="margin:0 0 20px">'
                f'<img src="cid:chart{idx}" alt="{html.escape(term)} rent history" '
                f'style="width:100%;max-width:820px;height:auto;'
                f'border:1px solid {RULE};border-radius:6px"></p>')
    if missing:
        parts.append(f'<p style="font:13px/1.6 {FONT};color:{MUTED};margin:0 0 16px">'
                     f'No chart this run for: {html.escape(", ".join(missing))} '
                     f'(nothing priced was scraped for it).</p>')
    if has_explorer:
        parts.append(f'<p style="font:13px/1.6 {FONT};color:{MUTED};margin:0 0 16px">'
                     f'<strong>explorer.html</strong> is attached - save it and open '
                     f'it in a browser to filter and sort every floor plan.</p>')

    parts.append(f'<p style="font:11px/1.5 {FONT};color:{MUTED};'
                 f'border-top:1px solid {RULE};padding-top:12px;margin:26px 0 0">'
                 f'apartment-scraper, {_today()}</p></div>')
    return "\n".join(parts)


def build_message(plots_dir: str = "plots", sender: str = "", rcpt: str = "",
                  watch: list[str] | None = None):
    """(message, matched charts, missing watch terms) - or (None, [], []) if there
    is no report to send.

    Pure assembly: no SMTP, and no credential lookup, so --dry-run can build the
    real message with placeholder addresses and an empty .env.
    """
    watch = watch if watch is not None else WATCH

    md_path = os.path.join(plots_dir, "recommendations.md")
    try:
        with open(md_path, "r", encoding="utf-8") as f:
            md = f.read()
    except OSError as e:
        print(f"WARNING: no recommendations at {md_path} ({e})")
        return None, [], []

    charts, missing = _find_plots(os.path.join(plots_dir, "history"), watch)
    explorer_path = os.path.join(plots_dir, "explorer.html")
    has_explorer = os.path.exists(explorer_path)

    msg = EmailMessage()
    msg["Subject"] = _subject(md)
    msg["From"] = sender
    msg["To"] = rcpt
    msg.set_content(md)                                     # text/plain fallback
    msg.add_alternative(_html_body(md, charts, missing, has_explorer), subtype="html")

    html_part = msg.get_body(preferencelist=("html",))
    kept = []
    for idx, (term, path) in enumerate(charts):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            print(f"  (skipping chart {path}: {e})")
            continue
        # inline for the body, but still named so the client offers a download
        html_part.add_related(data, maintype="image", subtype="png",
                              cid=f"<chart{idx}>", filename=os.path.basename(path),
                              disposition="inline")
        kept.append((term, path))

    if has_explorer:
        try:
            with open(explorer_path, "rb") as f:
                # octet-stream, not text/html: Gmail then offers a download
                # instead of trying to preview a 100 KB page
                msg.add_attachment(f.read(), maintype="application",
                                   subtype="octet-stream", filename="explorer.html")
        except OSError as e:
            print(f"  (skipping explorer.html: {e})")

    return msg, kept, missing


def send_report(plots_dir: str = "plots", to: str | None = None,
                watch: list[str] | None = None) -> bool:
    """Mail the shortlist + charts + explorer.html. Never raises; returns sent?"""
    user, password, rcpt = _creds(to)
    ok, why = available(to)
    if not ok:
        print(f"WARNING: email not sent: {why}")
        return False

    msg, charts, missing = build_message(plots_dir, user, rcpt, watch)
    if msg is None:
        print("WARNING: email not sent (nothing to report).")
        return False
    has_explorer = os.path.exists(os.path.join(plots_dir, "explorer.html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)
    except Exception as e:              # auth, DNS, TLS, timeout, refusal
        print(f"WARNING: email not sent: {type(e).__name__}: {e}")
        return False

    note = f"{len(charts)} chart(s)"
    if missing:
        note += f", no chart for {', '.join(missing)}"
    if has_explorer:
        note += ", explorer.html"
    print(f"Emailed the report to {rcpt} ({note})")
    return True


def parse_watch(raw: str | None) -> list[str]:
    """Comma-separated --email-plots value -> watch list (blank means default)."""
    terms = [t.strip() for t in (raw or "").split(",") if t.strip()]
    return terms or list(WATCH)


def send_failure(log_path: str = "run.log", code: int | None = None,
                 lines: int = 50, to: str | None = None,
                 died: bool = False) -> bool:
    """Alert that a scheduled run failed, with the tail of its log.

    Two cases. `died=False` is a run that exited non-zero and run.bat lived to
    notice. `died=True` is the harder one: the run never reached run.bat's own
    finish line at all - killed by the scheduler's ExecutionTimeLimit, or lost
    to a reboot - so nothing could check an exit code and nothing was sent.
    The next run detects the leftover marker file and calls this.

    Deliberately plain text with no attachments and no dependency on plots/ -
    a failed run is exactly when the charts may be missing or half-written, and
    the alert has to get through anyway.
    """
    user, password, rcpt = _creds(to)
    ok, why = available(to)
    if not ok:
        print(f"WARNING: failure alert not sent: {why}")
        return False

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            tail = "".join(collections.deque(f, maxlen=lines))
    except OSError as e:
        tail = f"(could not read {log_path}: {e})"

    exit_note = f" (exit {code})" if code is not None else ""
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if died:
        subject = f"Apartment scraper run DIED - {_today()}"
        head = (f"A previous run never finished. Detected at {now}, when the next\n"
                f"run found the in-progress marker still in place.\n\n"
                f"Most likely the scheduler's ExecutionTimeLimit terminated it, or the\n"
                f"machine rebooted. Either way it sent no report and could not send a\n"
                f"failure alert of its own, because the process that checks the exit\n"
                f"code was killed with it.\n\n"
                f"Nothing was written for that run: the scraper holds every row in\n"
                f"memory and writes the CSVs only after all sites finish.\n")
    else:
        subject = f"Apartment scraper FAILED - {_today()}{exit_note}"
        head = (f"The apartment scraper failed{exit_note} at {now}.\n\n"
                f"No report was sent for this run. The rent history is unchanged\n"
                f"unless the run got far enough to append to it.\n")

    body = (f"{head}\n"
            f"Last {lines} lines of {log_path}:\n"
            f"{'-' * 68}\n{tail}")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = rcpt
    msg.set_content(body)

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)
    except Exception as e:
        print(f"WARNING: failure alert not sent: {type(e).__name__}: {e}")
        return False

    print(f"Emailed a failure alert to {rcpt}")
    return True


# --------------------------------------------------------------------------- #
# testing without sending
# --------------------------------------------------------------------------- #
def check_login(to: str | None = None) -> bool:
    """Connect, STARTTLS, log in, hang up. Sends nothing. Returns ok?"""
    user, password, _rcpt = _creds(to)
    ok, why = available(to)
    if not ok:
        print(f"FAILED: {why}")
        return False
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(user, password)
    except smtplib.SMTPAuthenticationError as e:
        print(f"FAILED: Gmail rejected the login ({e.smtp_code}).\n"
              f"  GMAIL_APP_PASSWORD must be a 16-character App Password from\n"
              f"  https://myaccount.google.com/apppasswords - not your account\n"
              f"  password - and 2-step verification has to be on for the account.")
        return False
    except (smtplib.SMTPServerDisconnected, OSError) as e:
        print(f"FAILED: could not reach {SMTP_HOST}:{SMTP_PORT} "
              f"({type(e).__name__}: {e}).\n"
              f"  Check the network, a VPN, or a firewall blocking port 587.")
        return False
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")
        return False
    print(f"OK: logged in to {SMTP_HOST} as {user}. Credentials work.")
    return True


def _mime_tree(msg) -> str:
    lines = []

    def walk(part, depth=0):
        line = "  " * depth + "- " + part.get_content_type()
        if not part.is_multipart():
            n = len(part.get_payload(decode=True) or b"")
            line += f"  [{n:,} B]"
            if part.get_filename():
                line += f"  {part.get_filename()}"
            if part.get("Content-ID"):
                line += f"  cid={part.get('Content-ID')}"
        lines.append(line)
        if part.is_multipart():
            for p in part.get_payload():
                walk(p, depth + 1)

    walk(msg)
    return "\n".join(lines)


def write_preview(msg, charts: list[tuple], plots_dir: str = "plots") -> list[str]:
    """Write email-preview.eml and email-preview.html. Sends nothing.

    The .eml is the exact bytes that would go over the wire, so opening it in a
    mail client is the most faithful check short of actually sending. The .html
    is the body alone with the charts inlined as data: URIs, for a quick look in
    a browser.
    """
    written = []

    eml = os.path.join(plots_dir, "email-preview.eml")
    with open(eml, "wb") as f:
        f.write(msg.as_bytes())
    written.append(eml)

    body = msg.get_body(preferencelist=("html",)).get_payload(decode=True).decode("utf-8")
    for idx, (_term, path) in enumerate(charts):
        try:
            with open(path, "rb") as f:
                uri = "data:image/png;base64," + base64.b64encode(f.read()).decode()
        except OSError:
            continue
        body = body.replace(f"cid:chart{idx}", uri)

    page = os.path.join(plots_dir, "email-preview.html")
    with open(page, "w", encoding="utf-8") as f:
        f.write("<!doctype html><meta charset=utf-8><title>Email preview</title>"
                "<body style='background:#eef1f4;margin:0;padding:24px 0'>"
                f"<div style=\"max-width:900px;margin:0 auto 14px;"
                f"font:13px/1.5 {FONT};color:{MUTED}\">Subject: "
                f"<strong style='color:{INK}'>{html.escape(str(msg['Subject']))}"
                f"</strong></div>"
                "<div style='max-width:900px;margin:0 auto;background:#fff;"
                "border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,.12)'>"
                + body + "</div>")
    written.append(page)
    return written


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Email the report that is already in the plots folder. "
                    "Builds nothing - run scraper.py --visualize or visualize.py "
                    "first if the charts are stale.")
    parser.add_argument("--plots-dir", default="plots",
                        help="Folder holding recommendations.md, explorer.html "
                             "and history/ (default: plots).")
    parser.add_argument("--to", metavar="ADDR",
                        help="Override the recipient (default: EMAIL_TO from .env).")
    parser.add_argument("--email-plots", metavar="NAMES",
                        help="Comma-separated communities whose rent-history chart "
                             f"is attached (default: {', '.join(WATCH)}).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="Only verify the credentials log in to Gmail. Sends nothing.")
    mode.add_argument("--dry-run", action="store_true",
                      help="Build the message and write email-preview.eml / .html "
                           "next to the charts. Sends nothing; needs no credentials.")
    mode.add_argument("--failure", metavar="LOG",
                      help="Send a plain-text alert that a run failed, quoting the "
                           "tail of LOG. Used by run.bat when the scrape exits "
                           "non-zero; no charts needed.")
    parser.add_argument("--code", type=int,
                        help="Exit code to quote in the --failure alert.")
    parser.add_argument("--died", action="store_true",
                        help="With --failure: word the alert for a run that was "
                             "killed outright (scheduler time limit, reboot) "
                             "rather than one that exited with an error.")
    parser.add_argument("--lines", type=int, default=50,
                        help="How many trailing log lines the --failure alert "
                             "carries (default: 50).")
    args = parser.parse_args()

    if args.check:
        return 0 if check_login(args.to) else 1

    if args.failure:
        return 0 if send_failure(args.failure, args.code, args.lines, args.to,
                                 died=args.died) else 1

    watch = parse_watch(args.email_plots)

    if args.dry_run:
        # placeholder addresses: a dry run must work with an empty .env
        user, _pw, rcpt = _creds(args.to)
        msg, charts, missing = build_message(args.plots_dir,
                                             user or "you@example.com",
                                             rcpt or "you@example.com", watch)
        if msg is None:
            return 1
        print(f"Subject: {msg['Subject']}")
        print(f"To:      {msg['To']}")
        for term, path in charts:
            print(f"  chart   {term:16} -> {os.path.basename(path)}")
        for term in missing:
            print(f"  chart   {term:16} -> (none this run)")
        print(f"\n{_mime_tree(msg)}")
        size = len(msg.as_bytes())
        print(f"\ntotal wire size: {size:,} bytes ({size / 1024 / 1024:.2f} MB)")
        for p in write_preview(msg, charts, args.plots_dir):
            print(f"Wrote {p}")
        print("\nNothing was sent. Open the .eml in a mail client to see the real thing.")
        return 0

    return 0 if send_report(args.plots_dir, args.to, watch) else 1


if __name__ == "__main__":
    raise SystemExit(main())
