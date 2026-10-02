#!/usr/bin/env python3
"""Weekly privacy-safe public activity digest for roster GitHub accounts.

Parses README.md for github.com/<login> links, fetches public repos/events
via the GitHub API, and writes docs/index.html + docs/index.md + docs/latest.json.

Privacy rules:
  - Only GitHub @handles and public repo names/URLs/dates
  - Never include real names, hospitals, bios, or commit messages
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
README_PATH = ROOT / "README.md"
DOCS_DIR = ROOT / "docs"
WINDOW_DAYS = int(os.environ.get("DIGEST_WINDOW_DAYS", "7"))
API_BASE = "https://api.github.com"
USER_AGENT = "awesome-tw-physician-engineer-weekly-digest/1.0"

LOGIN_RE = re.compile(
    r"https?://github\.com/([A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38})"
    r"(?:[/\s)\]\"'<>]|$)",
    re.IGNORECASE,
)

SKIP_LOGINS = {
    "settings",
    "orgs",
    "organizations",
    "marketplace",
    "explore",
    "topics",
    "collections",
    "events",
    "sponsors",
    "about",
    "pricing",
    "login",
    "join",
    "features",
    "security",
    "enterprise",
    "customer-stories",
    "team",
    "enterprise-plan",
    "pulls",
    "issues",
    "codespaces",
    "notifications",
    "new",
    "apps",
    "gist",
    "site",
    "git-guides",
    "readme",
    "awesome",
}


def parse_roster_logins(readme_text: str) -> list[str]:
    """Extract unique GitHub logins from README links, preserving order."""
    seen: set[str] = set()
    logins: list[str] = []
    for match in LOGIN_RE.finditer(readme_text):
        login = match.group(1)
        key = login.lower()
        if key in SKIP_LOGINS or key in seen:
            continue
        seen.add(key)
        logins.append(login)
    return logins


def github_request(path: str, token: str | None, params: dict[str, Any] | None = None) -> Any:
    """GET a GitHub API path; returns parsed JSON or None on 404/403."""
    url = f"{API_BASE}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            remaining = resp.headers.get("X-RateLimit-Remaining")
            if remaining is not None and int(remaining) < 20:
                time.sleep(2)
            return json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 403):
            err = exc.read().decode("utf-8", errors="replace")
            print(f"warn: HTTP {exc.code} for {path}: {err[:200]}", file=sys.stderr)
            return None
        raise


def fetch_non_fork_repos(login: str, token: str | None, since: datetime) -> list[dict[str, Any]]:
    """Public non-fork repos created or pushed within the window."""
    repos: list[dict[str, Any]] = []
    page = 1
    while page <= 5:
        data = github_request(
            f"/users/{urllib.parse.quote(login)}/repos",
            token,
            {
                "type": "owner",
                "sort": "pushed",
                "direction": "desc",
                "per_page": "100",
                "page": str(page),
            },
        )
        if not data or not isinstance(data, list):
            break
        stop_paging = False
        for repo in data:
            if repo.get("fork") or repo.get("private"):
                continue
            name = repo.get("name") or ""
            html_url = repo.get("html_url") or f"https://github.com/{login}/{name}"
            created_at = _parse_gh_time(repo.get("created_at"))
            pushed_at = _parse_gh_time(repo.get("pushed_at"))
            updated_at = _parse_gh_time(repo.get("updated_at"))
            activity = pushed_at or updated_at
            if activity and activity < since and (not created_at or created_at < since):
                stop_paging = True
                continue
            is_new = bool(created_at and created_at >= since)
            recently_pushed = bool(pushed_at and pushed_at >= since)
            if not is_new and not recently_pushed:
                continue
            repos.append(
                {
                    "name": name,
                    "url": html_url,
                    "created_at": _fmt(created_at),
                    "pushed_at": _fmt(pushed_at),
                    "is_new": is_new,
                    "recently_pushed": recently_pushed,
                }
            )
        if stop_paging or len(data) < 100:
            break
        page += 1
        time.sleep(0.05)
    return repos


def fetch_recent_push_events(
    login: str, token: str | None, since: datetime
) -> list[dict[str, Any]]:
    """Public PushEvents in the window (handles + repo only; no commit msgs)."""
    pushes: list[dict[str, Any]] = []
    seen_repos: set[str] = set()
    page = 1
    while page <= 3:
        data = github_request(
            f"/users/{urllib.parse.quote(login)}/events/public",
            token,
            {"per_page": "100", "page": str(page)},
        )
        if not data or not isinstance(data, list):
            break
        stop = False
        for event in data:
            created = _parse_gh_time(event.get("created_at"))
            if created and created < since:
                stop = True
                break
            if event.get("type") != "PushEvent":
                continue
            repo_obj = event.get("repo") or {}
            full_name = repo_obj.get("name") or ""
            if not full_name or full_name in seen_repos:
                continue
            seen_repos.add(full_name)
            pushes.append(
                {
                    "repo": full_name,
                    "url": f"https://github.com/{full_name}",
                    "pushed_at": _fmt(created),
                }
            )
        if stop or len(data) < 100:
            break
        page += 1
        time.sleep(0.05)
    return pushes


def _parse_gh_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _fmt(dt: datetime | None) -> str | None:
    if not dt:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_digest(logins: list[str], token: str | None, now: datetime) -> dict[str, Any]:
    since = now - timedelta(days=WINDOW_DAYS)
    entries: list[dict[str, Any]] = []
    for i, login in enumerate(logins):
        print(f"[{i + 1}/{len(logins)}] @{login}", flush=True)
        try:
            repos = fetch_non_fork_repos(login, token, since)
            pushes = fetch_recent_push_events(login, token, since)
        except Exception as exc:  # noqa: BLE001
            print(f"warn: failed for @{login}: {exc}", file=sys.stderr)
            repos, pushes = [], []
        if not repos and not pushes:
            continue
        entries.append(
            {
                "login": login,
                "profile_url": f"https://github.com/{login}",
                "repos": repos,
                "recent_pushes": pushes,
            }
        )
        time.sleep(0.1)
    return {
        "generated_at": _fmt(now),
        "window_days": WINDOW_DAYS,
        "window_start": _fmt(since),
        "roster_count": len(logins),
        "active_count": len(entries),
        "privacy_note": (
            "Handles and public repository names/URLs/dates only. "
            "No real names, hospitals, bios, or commit messages."
        ),
        "entries": entries,
    }


def render_index_md(digest: dict[str, Any]) -> str:
    lines: list[str] = [
        "# Weekly public digest",
        "",
        "Privacy-safe snapshot of **public** GitHub activity from roster accounts "
        "listed in the [README](https://github.com/erichuang777777/awesome-tw-physician-engineer).",
        "",
        f"- Generated (UTC): `{digest['generated_at']}`",
        f"- Window: last **{digest['window_days']}** days "
        f"(from `{digest['window_start']}`)",
        f"- Roster accounts scanned: **{digest['roster_count']}**",
        f"- Accounts with public activity in window: **{digest['active_count']}**",
        "",
        "> **Privacy:** only `@handles` and public repository titles / URLs / dates. "
        "No real names, hospitals, bios, or commit contents.",
        "",
        "Machine-readable: [latest.json](./latest.json)",
        "",
        "---",
        "",
    ]
    entries = digest.get("entries") or []
    if not entries:
        lines.extend(
            [
                "## No public activity in this window",
                "",
                "No new/updated non-fork public repos or recent public pushes were "
                "detected for roster accounts in the lookback window.",
                "",
            ]
        )
        return "\n".join(lines)

    lines.append("## Activity by handle")
    lines.append("")
    for entry in entries:
        login = entry["login"]
        lines.append(f"### [@{login}]({entry['profile_url']})")
        lines.append("")
        repos = entry.get("repos") or []
        if repos:
            lines.append("**Repos (non-fork, public)**")
            lines.append("")
            for repo in repos:
                flags: list[str] = []
                if repo.get("is_new"):
                    flags.append("new")
                if repo.get("recently_pushed"):
                    flags.append("pushed")
                flag_s = f" ({', '.join(flags)})" if flags else ""
                date_bits = []
                if repo.get("created_at") and repo.get("is_new"):
                    date_bits.append(f"created {repo['created_at'][:10]}")
                if repo.get("pushed_at"):
                    date_bits.append(f"pushed {repo['pushed_at'][:10]}")
                date_s = f" — {'; '.join(date_bits)}" if date_bits else ""
                lines.append(f"- [{repo['name']}]({repo['url']}){flag_s}{date_s}")
            lines.append("")
        pushes = entry.get("recent_pushes") or []
        if pushes:
            listed = {f"{login}/{r['name']}" for r in repos}
            extra = [p for p in pushes if p["repo"] not in listed]
            if extra:
                lines.append("**Recent public pushes**")
                lines.append("")
                for p in extra:
                    day = (p.get("pushed_at") or "")[:10]
                    day_s = f" — {day}" if day else ""
                    lines.append(f"- [{p['repo']}]({p['url']}){day_s}")
                lines.append("")
    lines.extend(
        [
            "---",
            "",
            "Source repo: "
            "[erichuang777777/awesome-tw-physician-engineer]"
            "(https://github.com/erichuang777777/awesome-tw-physician-engineer)",
            "",
            "Regenerated weekly by `.github/workflows/weekly-digest.yml`.",
            "",
        ]
    )
    return "\n".join(lines)


def render_index_html(digest: dict[str, Any]) -> str:
    """Static HTML for GitHub Pages artifact deploy (no Jekyll required)."""
    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8"/>',
        '<meta name="viewport" content="width=device-width, initial-scale=1"/>',
        "<title>Weekly public digest — TW clinician-engineers</title>",
        "<style>",
        "body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;"
        "max-width:52rem;margin:2rem auto;padding:0 1rem;line-height:1.5;color:#1a1a1a}",
        "h1,h2,h3{line-height:1.25} a{color:#0969da}",
        "code{background:#f6f8fa;padding:.1em .3em;border-radius:4px}",
        ".meta{color:#57606a;font-size:.95rem}",
        ".note{background:#fff8c5;border:1px solid #d4a72c;padding:.75rem 1rem;border-radius:6px}",
        "ul{padding-left:1.25rem}",
        "hr{border:none;border-top:1px solid #d0d7de;margin:1.5rem 0}",
        "</style>",
        "</head>",
        "<body>",
        "<h1>Weekly public digest</h1>",
        "<p>Privacy-safe snapshot of <strong>public</strong> GitHub activity from roster "
        'accounts listed in the <a href="https://github.com/erichuang777777/awesome-tw-physician-engineer">'
        "README</a>.</p>",
        '<p class="meta">',
        f"Generated (UTC): <code>{html.escape(digest.get('generated_at') or '')}</code><br/>",
        f"Window: last <strong>{digest['window_days']}</strong> days "
        f"(from <code>{html.escape(digest.get('window_start') or '')}</code>)<br/>",
        f"Roster accounts scanned: <strong>{digest['roster_count']}</strong><br/>",
        f"Accounts with public activity in window: <strong>{digest['active_count']}</strong>",
        "</p>",
        '<p class="note"><strong>Privacy:</strong> only <code>@handles</code> and public '
        "repository titles / URLs / dates. No real names, hospitals, bios, or commit contents.</p>",
        '<p>Machine-readable: <a href="./latest.json">latest.json</a> · '
        '<a href="./index.md">index.md</a></p>',
        "<hr/>",
    ]
    entries = digest.get("entries") or []
    if not entries:
        parts.extend(
            [
                "<h2>No public activity in this window</h2>",
                "<p>No new/updated non-fork public repos or recent public pushes were "
                "detected for roster accounts in the lookback window.</p>",
            ]
        )
    else:
        parts.append("<h2>Activity by handle</h2>")
        for entry in entries:
            login = html.escape(entry["login"])
            profile = html.escape(entry["profile_url"])
            parts.append(f'<h3><a href="{profile}">@{login}</a></h3>')
            repos = entry.get("repos") or []
            if repos:
                parts.append("<p><strong>Repos (non-fork, public)</strong></p><ul>")
                for repo in repos:
                    flags: list[str] = []
                    if repo.get("is_new"):
                        flags.append("new")
                    if repo.get("recently_pushed"):
                        flags.append("pushed")
                    flag_s = f" ({', '.join(flags)})" if flags else ""
                    date_bits = []
                    if repo.get("created_at") and repo.get("is_new"):
                        date_bits.append(f"created {repo['created_at'][:10]}")
                    if repo.get("pushed_at"):
                        date_bits.append(f"pushed {repo['pushed_at'][:10]}")
                    date_s = f" — {'; '.join(date_bits)}" if date_bits else ""
                    name = html.escape(repo["name"])
                    url = html.escape(repo["url"])
                    parts.append(
                        f'<li><a href="{url}">{name}</a>'
                        f"{html.escape(flag_s)}{html.escape(date_s)}</li>"
                    )
                parts.append("</ul>")
            pushes = entry.get("recent_pushes") or []
            if pushes:
                listed = {f"{entry['login']}/{r['name']}" for r in repos}
                extra = [p for p in pushes if p["repo"] not in listed]
                if extra:
                    parts.append("<p><strong>Recent public pushes</strong></p><ul>")
                    for p in extra:
                        day = (p.get("pushed_at") or "")[:10]
                        day_s = f" — {day}" if day else ""
                        rname = html.escape(p["repo"])
                        rurl = html.escape(p["url"])
                        parts.append(
                            f'<li><a href="{rurl}">{rname}</a>{html.escape(day_s)}</li>'
                        )
                    parts.append("</ul>")
    parts.extend(
        [
            "<hr/>",
            '<p>Source repo: <a href="https://github.com/erichuang777777/awesome-tw-physician-engineer">'
            "erichuang777777/awesome-tw-physician-engineer</a></p>",
            "<p>Regenerated weekly by <code>.github/workflows/weekly-digest.yml</code>.</p>",
            "</body></html>",
            "",
        ]
    )
    return "\n".join(parts)


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not README_PATH.is_file():
        print(f"error: missing {README_PATH}", file=sys.stderr)
        return 1
    readme = README_PATH.read_text(encoding="utf-8")
    logins = parse_roster_logins(readme)
    if not logins:
        print("error: no GitHub logins found in README", file=sys.stderr)
        return 1
    print(f"Parsed {len(logins)} roster logins", flush=True)
    now = datetime.now(timezone.utc)
    digest = build_digest(logins, token, now)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = DOCS_DIR / "latest.json"
    md_path = DOCS_DIR / "index.md"
    html_path = DOCS_DIR / "index.html"
    json_path.write_text(
        json.dumps(digest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(render_index_md(digest), encoding="utf-8")
    html_path.write_text(render_index_html(digest), encoding="utf-8")
    print(f"Wrote {md_path}", flush=True)
    print(f"Wrote {html_path}", flush=True)
    print(f"Wrote {json_path}", flush=True)
    print(f"Active accounts: {digest['active_count']}/{digest['roster_count']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
