#!/usr/bin/env python3
"""繁中 GitHub Pages：本週創作者動態摘要 + 全公開非 fork 專案資料庫。

Privacy:
  - Only GitHub @handles and public repo names/URLs/descriptions/topics
  - Never invent real names, hospitals, or private bios
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
DATA_DIR = DOCS_DIR / "data"
CACHE_DIR = ROOT / "research"
WINDOW_DAYS = int(os.environ.get("DIGEST_WINDOW_DAYS", "7"))
API_BASE = "https://api.github.com"
USER_AGENT = "awesome-tw-physician-engineer-pages/2.0"
SKIP_REPO_DB = os.environ.get("SKIP_REPO_DB", "").lower() in {"1", "true", "yes"}

LOGIN_RE = re.compile(
    r"https?://github\.com/([A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38})"
    r"(?:[/\s)\]\"\'<>]|$)",
    re.IGNORECASE,
)
TABLE_LOGIN_RE = re.compile(
    r"\[@([A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38})\]"
    r"\(https://github\.com/\1\)",
    re.IGNORECASE,
)

SKIP_LOGINS = {
    "settings", "orgs", "organizations", "marketplace", "explore", "topics",
    "collections", "events", "sponsors", "about", "pricing", "login", "join",
    "features", "security", "enterprise", "customer-stories", "team",
    "enterprise-plan", "pulls", "issues", "codespaces", "notifications", "new",
    "apps", "gist", "site", "git-guides", "readme", "awesome",
}

THEME_RULES: list[tuple[list[str], str]] = [
    (['fhir', 'hl7'], 'FHIR／醫療資料互通'),
    (['dicom', 'pacs', 'radiolog', '醫學影像', 'x-ray', 'mri'], '醫學影像／放射'),
    (['ecg', 'ekg', 'eeg', 'edf', 'waveform', 'vital'], '生理訊號／波形'),
    (['ehr', 'emr', '電子病歷', 'openemr', 'cpoe'], '電子病歷／臨床資訊系統'),
    (['nhi', '健保', 'icd', '診斷碼', 'billing'], '健保／編碼與申報'),
    (['pharmacy', '藥', 'drug', 'pill', 'medication', 'tfda', '藥師'], '藥學／藥品資訊'),
    (['oncolog', 'cancer', '腫瘤', 'breast', 'her2', 'hema'], '腫瘤／血液相關'),
    (['emergency', '急診', 'trauma', 'triage', 'er-ref', 'er-quiz', 'er-todo', 'em-pulse'], '急診／急重症'),
    (['opthalm', 'ophthalm', '眼', 'retina', 'glaucoma'], '眼科'),
    (['dental', '牙'], '牙醫／口腔'),
    (['nurse', '護理', 'nursing'], '護理'),
    (['clinic', 'clinical', '臨床', 'guideline', 'nccn'], '臨床指引／路徑'),
    (['trial', '臨床試驗', 'pubmed', 'literature', 'citation', 'manuscript'], '文獻／臨床試驗'),
    (['llm', 'gpt', 'claude', 'langchain', 'rag', 'openai', 'gemini'], '生成式 AI／LLM'),
    (['deep-learning', 'pytorch', 'tensorflow', 'sklearn', 'machine-learning'], '機器學習'),
    (['nlp', 'ner', 'text-mining', 'tokenizer'], '自然語言處理'),
    (['dashboard', 'visuali', 'plotly', 'grafana'], '資料視覺化／儀表板'),
    (['bot', 'line-', 'telegram', 'chatbot', 'messenger'], '聊天機器人／通訊整合'),
    (['docker', 'kubernetes', 'k8s', 'devops', 'terraform'], 'DevOps／基礎建設'),
    (['dotfiles', 'nvim', 'vim', 'zsh'], '開發環境／dotfiles'),
    (['github.io', 'portfolio', 'blog', 'hugo', 'jekyll'], '個人網站／部落格'),
    (['slide', 'reveal', 'slidev', '簡報', 'presentation'], '簡報／教材'),
    (['course', 'tutorial', '學習', 'exam', 'quiz', 'edu'], '教育／學習資源'),
    (['sdk', 'library', 'package', 'npm', 'pypi'], '函式庫／API'),
    (['android', 'ios', 'flutter', 'react-native', 'mobile'], '行動應用'),
    (['game', 'unity', 'godot'], '遊戲／互動'),
    (['security', 'oauth', 'auth0'], '資安／身份驗證'),
    (['dataset', 'etl', 'scrap', 'crawler', '爬蟲'], '資料集／資料處理'),
]


def parse_roster_logins(readme_text: str) -> list[str]:
    seen: set[str] = set()
    logins: list[str] = []
    for match in TABLE_LOGIN_RE.finditer(readme_text):
        login = match.group(1)
        key = login.lower()
        if key in SKIP_LOGINS or key in seen:
            continue
        seen.add(key)
        logins.append(login)
    if logins:
        return logins
    for match in LOGIN_RE.finditer(readme_text):
        login = match.group(1)
        key = login.lower()
        if key in SKIP_LOGINS or key in seen:
            continue
        seen.add(key)
        logins.append(login)
    return logins


def github_request(
    path: str, token: str | None, params: dict[str, Any] | None = None
) -> Any:
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
        with urllib.request.urlopen(req, timeout=45) as resp:
            body = resp.read().decode("utf-8")
            remaining = resp.headers.get("X-RateLimit-Remaining")
            if remaining is not None and int(remaining) < 30:
                time.sleep(3)
            return json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 403):
            err = exc.read().decode("utf-8", errors="replace")
            print(f"warn: HTTP {exc.code} for {path}: {err[:200]}", file=sys.stderr)
            return None
        raise


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


def _fmt_taipei(iso_or_dt: Any) -> str:
    if isinstance(iso_or_dt, str):
        dt = _parse_gh_time(iso_or_dt)
    else:
        dt = iso_or_dt
    if not dt:
        return ""
    tw = timezone(timedelta(hours=8))
    return dt.astimezone(tw).strftime("%Y-%m-%d %H:%M") + "（台北時間）"


def _has_cjk(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text)


def _first_sentence(text: str, max_len: int = 80) -> str:
    text = re.sub(r"\s+", " ", text.strip())
    for sep in ("。", "！", "？", ". ", "! ", "? "):
        if sep in text:
            part = text.split(sep)[0].strip()
            if sep[0] in "。！？":
                part += sep[0]
            if part:
                text = part
                break
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text


def detect_theme(name: str, description: str, topics: list[str]) -> str | None:
    blob = " ".join(
        [name.lower(), (description or "").lower(), " ".join(t.lower() for t in topics)]
    )
    for keys, label in THEME_RULES:
        if any(k in blob for k in keys):
            return label
    return None


def synthesize_zh_intro(
    name: str,
    description: str | None,
    topics: list[str],
    language: str | None,
) -> str:
    desc = (description or "").strip()
    theme = detect_theme(name, desc, topics)
    low = name.lower()

    if desc and _has_cjk(desc):
        return _first_sentence(desc, 90)

    if low.endswith(".github.io") or low in {"homepage", "blog", "site"}:
        return "個人或專案的公開 GitHub Pages／網站內容。"

    if low in {"dotfiles", "dot-files"}:
        return "個人開發環境與 shell／編輯器設定檔集合。"

    if desc:
        short = _first_sentence(desc, 72)
        if theme:
            return "「" + theme + "」相關公開專案：" + short
        lang_bit = ("（主要語言：" + language + "）") if language else ""
        return "公開專案「" + name + "」" + lang_bit + "：" + short

    topic_zh = "、".join(topics[:3]) if topics else ""
    if theme and topic_zh:
        return "「" + theme + "」主題的公開倉庫，標籤含 " + topic_zh + "。"
    if theme:
        lang_bit = ("，主要語言為 " + language) if language else ""
        return "「" + theme + "」相關的公開倉庫" + lang_bit + "。"
    if topic_zh:
        return "公開倉庫，主題標籤包含 " + topic_zh + "。"
    if language:
        return "公開的 " + language + " 專案倉庫（尚無說明文字）。"
    return "公開倉庫（尚無說明文字；依名稱列出）。"


def fetch_all_non_fork_repos(
    login: str, token: str | None
) -> tuple[list[dict[str, Any]], str | None]:
    repos: list[dict[str, Any]] = []
    page = 1
    while page <= 20:
        data = github_request(
            f"/users/{urllib.parse.quote(login)}/repos",
            token,
            {
                "type": "owner",
                "sort": "updated",
                "direction": "desc",
                "per_page": "100",
                "page": str(page),
            },
        )
        if data is None:
            return repos, f"API 失敗或帳號不可用（page {page}）"
        if not isinstance(data, list):
            break
        if not data:
            break
        for repo in data:
            if repo.get("fork") or repo.get("private"):
                continue
            name = repo.get("name") or ""
            description = repo.get("description")
            topics = list(repo.get("topics") or [])
            language = repo.get("language")
            html_url = repo.get("html_url") or f"https://github.com/{login}/{name}"
            intro_zh = synthesize_zh_intro(name, description, topics, language)
            repos.append(
                {
                    "name": name,
                    "full_name": repo.get("full_name") or f"{login}/{name}",
                    "url": html_url,
                    "description": description,
                    "topics": topics,
                    "language": language,
                    "stars": int(repo.get("stargazers_count") or 0),
                    "forks": int(repo.get("forks_count") or 0),
                    "created_at": repo.get("created_at"),
                    "pushed_at": repo.get("pushed_at"),
                    "updated_at": repo.get("updated_at"),
                    "archived": bool(repo.get("archived")),
                    "theme": detect_theme(name, description or "", topics),
                    "intro_zh": intro_zh,
                }
            )
        if len(data) < 100:
            break
        page += 1
        time.sleep(0.05)
    repos.sort(key=lambda r: (r.get("pushed_at") or ""), reverse=True)
    return repos, None


def fetch_window_repos(
    all_repos: list[dict[str, Any]], since: datetime
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for repo in all_repos:
        created_at = _parse_gh_time(repo.get("created_at"))
        pushed_at = _parse_gh_time(repo.get("pushed_at"))
        is_new = bool(created_at and created_at >= since)
        recently_pushed = bool(pushed_at and pushed_at >= since)
        if not is_new and not recently_pushed:
            continue
        out.append(
            {
                "name": repo["name"],
                "url": repo["url"],
                "created_at": _fmt(created_at),
                "pushed_at": _fmt(pushed_at),
                "is_new": is_new,
                "recently_pushed": recently_pushed,
                "intro_zh": repo.get("intro_zh"),
                "theme": repo.get("theme"),
            }
        )
    return out


def fetch_recent_push_events(
    login: str, token: str | None, since: datetime
) -> list[dict[str, Any]]:
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


NOTABLE_THEMES = {
    "FHIR／醫療資料互通",
    "醫學影像／放射",
    "生理訊號／波形",
    "電子病歷／臨床資訊系統",
    "健保／編碼與申報",
    "藥學／藥品資訊",
    "腫瘤／血液相關",
    "急診／急重症",
    "眼科",
    "牙醫／口腔",
    "護理",
    "臨床指引／路徑",
    "文獻／臨床試驗",
}

SOFT_NOISE_THEMES = {
    "個人網站／部落格",
    "開發環境／dotfiles",
    "遊戲／互動",
}

NOISE_NAME_RE = re.compile(
    r"(nihongo|lunch|dotfiles?|portfolio|homepage|\.github\.io$)",
    re.IGNORECASE,
)


def summarize_entry_zh(entry: dict[str, Any]) -> str:
    login = entry["login"]
    repos = entry.get("repos") or []
    pushes = entry.get("recent_pushes") or []
    new_repos = [r for r in repos if r.get("is_new")]
    updated = [r for r in repos if r.get("recently_pushed") and not r.get("is_new")]
    bits: list[str] = []
    if new_repos:
        names = "、".join("「" + r["name"] + "」" for r in new_repos[:5])
        extra = f" 等 {len(new_repos)} 個" if len(new_repos) > 5 else ""
        bits.append("新建公開倉庫 " + names + extra)
    if updated:
        names = "、".join("「" + r["name"] + "」" for r in updated[:5])
        extra = f" 等 {len(updated)} 個" if len(updated) > 5 else ""
        bits.append("更新倉庫 " + names + extra)
    listed = {f"{login}/{r['name']}" for r in repos}
    extra_pushes = [p for p in pushes if p["repo"] not in listed]
    if extra_pushes and not bits:
        names = "、".join(p["repo"].split("/")[-1] for p in extra_pushes[:4])
        bits.append("有公開推送活動（含 " + names + "）")
    elif extra_pushes and bits:
        bits.append(f"另有 {len(extra_pushes)} 處公開推送")
    if not bits:
        return "@" + login + " 本週有公開動態。"
    return "@" + login + " 本週" + "；".join(bits) + "。"


def _repo_score(repo: dict[str, Any]) -> int:
    theme = repo.get("theme")
    name = repo.get("name") or ""
    score = 0
    if repo.get("is_new"):
        score += 5
    if theme in NOTABLE_THEMES:
        score += 10
    elif theme and theme not in SOFT_NOISE_THEMES:
        score += 3
    if theme in SOFT_NOISE_THEMES:
        score -= 5
    if NOISE_NAME_RE.search(name):
        score -= 4
    if repo.get("recently_pushed"):
        score += 1
    intro = repo.get("intro_zh") or ""
    if any(k in intro for k in ("臨床", "藥", "醫", "急診", "腫瘤", "健保", "TFDA", "NCCN", "FHIR")):
        score += 2
    return score


def _format_repo_group(repos: list[dict[str, Any]], limit: int = 3) -> str:
    """Compact labels; mention a notable theme once when shared."""
    show = repos[:limit]
    themes = [r.get("theme") for r in show if r.get("theme") in NOTABLE_THEMES]
    shared = themes[0] if themes and all(t == themes[0] for t in themes) else None
    names = "、".join("「" + (r.get("name") or "") + "」" for r in show)
    extra = f" 等 {len(repos)} 個" if len(repos) > limit else ""
    if shared and len(show) > 1:
        return f"{shared}{names}{extra}"
    parts: list[str] = []
    for r in show:
        theme = r.get("theme")
        name = r.get("name") or ""
        if theme and theme in NOTABLE_THEMES:
            parts.append(f"{theme}「{name}」")
        else:
            parts.append(f"「{name}」")
    return "、".join(parts) + extra


def _is_clinically_notable(repo: dict[str, Any]) -> bool:
    if repo.get("theme") in NOTABLE_THEMES:
        return True
    intro = repo.get("intro_zh") or ""
    name = repo.get("name") or ""
    blob = intro + " " + name
    keys = ("臨床", "藥", "醫", "急診", "腫瘤", "健保", "TFDA", "NCCN", "FHIR", "DICOM", "病歷", "護理")
    return any(k in blob for k in keys)


def curate_weekly_highlights(entries: list[dict[str, Any]], limit: int = 10) -> dict[str, Any]:
    """Pick quality-over-quantity weekly highlights; one handle per bullet."""
    scored: list[tuple[int, dict[str, Any], list[dict[str, Any]]]] = []
    soft: list[dict[str, Any]] = []

    for entry in entries:
        repos = list(entry.get("repos") or [])
        # Score each owned window repo; ignore bare push-only noise unless no repos
        if not repos:
            # push-only: soft noise unless we know nothing else
            soft.append(entry)
            continue
        notable = [
            r for r in repos
            if _repo_score(r) >= 4 and _is_clinically_notable(r)
        ]
        # Keep high-signal non-noise even if theme missed (e.g. strong medical keywords already gated)
        if not notable:
            notable = [r for r in repos if _repo_score(r) >= 8]
        noise_only = [r for r in repos if r not in notable]
        if notable:
            best = max(_repo_score(r) for r in notable)
            # Prefer more medical signal + volume
            total = sum(max(0, _repo_score(r)) for r in notable)
            scored.append((best * 10 + total + len(notable), entry, notable))
        elif noise_only:
            soft.append(entry)
        else:
            soft.append(entry)

    scored.sort(key=lambda t: t[0], reverse=True)
    top = scored[:limit]
    leftover_scored = scored[limit:]

    highlights: list[dict[str, Any]] = []
    for _score, entry, notable in top:
        new_ones = [r for r in notable if r.get("is_new")]
        updated = [r for r in notable if not r.get("is_new")]
        bits: list[str] = []
        if new_ones:
            bits.append("新建 " + _format_repo_group(new_ones))
        if updated:
            bits.append("更新了 " + _format_repo_group(updated))
        text_zh = "；".join(bits) if bits else "有值得關注的公開動態"
        highlights.append(
            {
                "login": entry["login"],
                "profile_url": entry["profile_url"],
                "text_zh": text_zh,
                "repo_names": [r["name"] for r in notable[:5]],
            }
        )

    soft_count = len(soft) + len(leftover_scored)
    soft_note = ""
    if soft_count:
        soft_note = (
            f"另有 {soft_count} 個帳號本週僅有個人網站、學習筆記或其他非臨床向公開推送，"
            "未列入上方精選。"
        )

    return {
        "items": highlights,
        "soft_note_zh": soft_note,
        "soft_count": soft_count,
        "featured_count": len(highlights),
    }


def make_overview_zh(digest_like: dict[str, Any], curated: dict[str, Any]) -> str:
    return (
        f"近 {digest_like['window_days']} 天掃描 {digest_like['roster_count']} 個名冊帳號，"
        f"精選 {curated['featured_count']} 則值得追蹤的臨床／醫工動態"
        + (f"；另略過 {curated['soft_count']} 則較安靜或非臨床向更新。" if curated.get("soft_count") else "。")
    )


def attach_curation(digest: dict[str, Any]) -> dict[str, Any]:
    curated = curate_weekly_highlights(digest.get("entries") or [])
    digest["highlights"] = curated["items"]
    digest["soft_note_zh"] = curated.get("soft_note_zh") or ""
    digest["featured_count"] = curated["featured_count"]
    digest["overview_zh"] = make_overview_zh(digest, curated)
    return digest


def build_all(logins: list[str], token: str | None, now: datetime) -> tuple[dict[str, Any], dict[str, Any]]:
    since = now - timedelta(days=WINDOW_DAYS)
    digest_entries: list[dict[str, Any]] = []
    db_accounts: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    total_repos = 0

    for i, login in enumerate(logins):
        print(f"[{i + 1}/{len(logins)}] @{login}", flush=True)
        err = None
        all_repos: list[dict[str, Any]] = []
        try:
            if SKIP_REPO_DB:
                page_repos, err = fetch_all_non_fork_repos(login, token)
                all_repos = page_repos
            else:
                all_repos, err = fetch_all_non_fork_repos(login, token)
            pushes = fetch_recent_push_events(login, token, since)
        except Exception as exc:  # noqa: BLE001
            print(f"warn: failed for @{login}: {exc}", file=sys.stderr)
            all_repos, pushes, err = [], [], str(exc)

        if err and not all_repos:
            skipped.append({"login": login, "reason": err})

        window_repos = fetch_window_repos(all_repos, since)
        if window_repos or pushes:
            entry = {
                "login": login,
                "profile_url": f"https://github.com/{login}",
                "repos": window_repos,
                "recent_pushes": pushes,
            }
            entry["summary_zh"] = summarize_entry_zh(entry)
            digest_entries.append(entry)

        db_accounts.append(
            {
                "login": login,
                "profile_url": f"https://github.com/{login}",
                "repo_count": len(all_repos),
                "repos": all_repos,
                "error": err,
            }
        )
        total_repos += len(all_repos)
        time.sleep(0.08)

    digest = {
        "generated_at": _fmt(now),
        "generated_at_taipei": _fmt_taipei(now),
        "window_days": WINDOW_DAYS,
        "window_start": _fmt(since),
        "roster_count": len(logins),
        "active_count": len(digest_entries),
        "privacy_note": "僅列出 @帳號 與公開倉庫名稱／網址／說明；不含真實姓名、院所或 commit 內容。",
        "overview_zh": "",
        "entries": digest_entries,
        "skipped": skipped,
    }
    attach_curation(digest)

    repo_db = {
        "generated_at": _fmt(now),
        "generated_at_taipei": _fmt_taipei(now),
        "roster_count": len(logins),
        "repo_count": total_repos,
        "privacy_note": digest["privacy_note"],
        "accounts": [
            {
                "login": a["login"],
                "profile_url": a["profile_url"],
                "repo_count": a["repo_count"],
                "error": a.get("error"),
                "repos": [
                    {
                        "name": r["name"],
                        "full_name": r["full_name"],
                        "url": r["url"],
                        "description": r.get("description"),
                        "topics": r.get("topics") or [],
                        "language": r.get("language"),
                        "stars": r.get("stars", 0),
                        "pushed_at": r.get("pushed_at"),
                        "theme": r.get("theme"),
                        "intro_zh": r.get("intro_zh"),
                        "archived": r.get("archived", False),
                    }
                    for r in a["repos"]
                ],
            }
            for a in db_accounts
        ],
        "skipped": skipped,
    }
    return digest, repo_db


def render_index_md(digest: dict[str, Any], repo_db: dict[str, Any]) -> str:
    lines: list[str] = [
        "# 台灣臨床醫事工程師 — 本週值得追蹤與專案資料庫",
        "",
        "隱私優先：僅使用 GitHub `@帳號` 與公開倉庫中繼資料。",
        "",
        f"- 產生時間：`{digest.get('generated_at_taipei') or digest.get('generated_at')}`",
        f"- 動態視窗：近 **{digest['window_days']}** 天（自 `{digest['window_start']}`）",
        f"- 名冊帳號：**{digest['roster_count']}**",
        f"- 本週精選：**{digest.get('featured_count', digest.get('active_count'))}**",
        f"- 公開非 fork 倉庫總數：**{repo_db['repo_count']}**",
        "",
        f"> **隱私：** {digest['privacy_note']}",
        "",
        "機器可讀：[latest.json](./latest.json) · [data/repos.json](./data/repos.json)",
        "",
        "---",
        "",
        "## 本週值得追蹤",
        "",
        digest.get("overview_zh") or "",
        "",
    ]
    highlights = digest.get("highlights") or []
    if not highlights:
        lines.extend(["本週暫無特別值得追蹤的臨床／醫工公開動態。", ""])
    else:
        for h in highlights:
            lines.append(
                f"- [@{h['login']}]({h['profile_url']}) — {h.get('text_zh') or ''}"
            )
        lines.append("")
        if digest.get("soft_note_zh"):
            lines.append(f"_{digest['soft_note_zh']}_")
            lines.append("")

    lines.extend(
        [
            "---",
            "",
            "## 專案資料庫（公開非 fork）",
            "",
            "依作者分組；每位作者帳號只出現一次，其下為緊湊「倉庫名 — 一句繁中」。",
            "",
            "完整列表見 [repos.md](./repos.md) 或網頁搜尋介面。",
            "",
            f"共 **{repo_db['repo_count']}** 個倉庫、**{repo_db['roster_count']}** 個帳號。",
            "",
            "來源：[erichuang777777/awesome-tw-physician-engineer]"
            "(https://github.com/erichuang777777/awesome-tw-physician-engineer)",
            "",
            "由 `.github/workflows/weekly-digest.yml` 每週重建。",
            "",
        ]
    )
    return "\n".join(lines)


def render_repos_md(repo_db: dict[str, Any]) -> str:
    lines: list[str] = [
        "# 公開專案資料庫",
        "",
        f"產生時間：`{repo_db.get('generated_at_taipei') or repo_db.get('generated_at')}`",
        f"帳號 **{repo_db['roster_count']}** · 倉庫 **{repo_db['repo_count']}**",
        "",
        f"> {repo_db.get('privacy_note')}",
        "",
        "[返回首頁](./index.md) · [repos.json](./data/repos.json)",
        "",
        "## 目錄",
        "",
    ]
    for acc in repo_db.get("accounts") or []:
        lines.append(f"- [@{acc['login']}](#{acc['login'].lower()})（{acc['repo_count']}）")
    lines.append("")
    for acc in repo_db.get("accounts") or []:
        login = acc["login"]
        lines.append(f"## [@{login}]({acc['profile_url']}) {{#{login.lower()}}}")
        lines.append("")
        if acc.get("error") and not acc.get("repos"):
            lines.append(f"_略過：{acc['error']}_")
            lines.append("")
            continue
        if not acc.get("repos"):
            lines.append("_此帳號目前沒有公開非 fork 倉庫。_")
            lines.append("")
            continue
        for r in acc["repos"]:
            arch = "（已封存）" if r.get("archived") else ""
            intro = r.get("intro_zh") or ""
            lines.append(f"- [{r['name']}]({r['url']}){arch} — {intro}")
        lines.append("")
    return "\n".join(lines)


def _css() -> str:
    return """
:root{--bg:#f4f6f8;--card:#fff;--text:#1f2328;--muted:#656d76;--accent:#0969da;--border:#d0d7de;--chip:#eaf2ff;--ok:#1a7f37;--warn:#9a6700;--row:#eef1f4}
*{box-sizing:border-box}
body{margin:0;font-family:"Noto Sans TC",system-ui,-apple-system,"Segoe UI",Roboto,"PingFang TC","Microsoft JhengHei",sans-serif;background:var(--bg);color:var(--text);line-height:1.5;font-size:16px}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:900px;margin:0 auto;padding:1rem .85rem 2.5rem}
header.hero{background:linear-gradient(135deg,#1f6feb 0%,#054da7 100%);color:#fff;padding:1.25rem 0 1.1rem;margin-bottom:1rem}
header.hero .wrap{padding-top:0;padding-bottom:0}
header.hero h1{margin:0 0 .3rem;font-size:1.45rem;font-weight:700;letter-spacing:.02em}
header.hero p{margin:.15rem 0;opacity:.95;font-size:.95rem}
nav.toc{display:flex;flex-wrap:wrap;gap:.4rem;margin:.75rem 0}
nav.toc a{background:var(--card);border:1px solid var(--border);border-radius:999px;padding:.2rem .65rem;font-size:.82rem}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:.85rem .9rem;margin:0 0 .85rem;box-shadow:0 1px 2px rgba(0,0,0,.03)}
.card h2{margin:0 0 .45rem;font-size:1.15rem}
.meta{color:var(--muted);font-size:.86rem}
.note{background:#fff8c5;border:1px solid #d4a72c;border-radius:8px;padding:.55rem .75rem;margin:.6rem 0;font-size:.86rem}
ul.highlights{margin:.4rem 0 0;padding-left:1.15rem}
ul.highlights li{margin:.28rem 0;font-size:.95rem}
ul.highlights .handle{font-weight:600}
.soft-note{margin:.55rem 0 0;color:var(--muted);font-size:.86rem}
.filters{display:flex;flex-wrap:wrap;gap:.45rem;margin:.55rem 0 .7rem;align-items:center}
.filters input,.filters select{font:inherit;padding:.4rem .55rem;border:1px solid var(--border);border-radius:8px;min-width:0;font-size:.9rem}
.filters input{flex:1 1 200px}
.filters select{flex:0 1 170px}
.stats{color:var(--muted);font-size:.84rem;margin:0 0 .45rem}
.author-block{margin:0 0 .75rem;padding:0}
.author-head{font-size:.92rem;font-weight:650;margin:0;padding:.3rem 0 .25rem;border-bottom:1px solid var(--border);background:var(--card);color:var(--text)}
.author-head .count{color:var(--muted);font-weight:500;font-size:.8rem;margin-left:.35rem}
.repo-list{margin:0;padding:0;list-style:none}
.repo-row{display:block;padding:.32rem 0;border-bottom:1px dotted var(--row);font-size:.88rem;line-height:1.4}
.repo-row:last-child{border-bottom:none}
.repo-row .rname{font-weight:600;word-break:break-word}
.repo-row .sep{color:var(--muted);margin:0 .2rem}
.repo-row .intro{color:#333;font-weight:400}
.repo-row .arch{color:var(--warn);font-size:.75rem;margin-left:.3rem}
footer{margin-top:1.5rem;color:var(--muted);font-size:.82rem}
.hidden{display:none !important}
@media (max-width:640px){
  body{font-size:15px}
  .wrap{padding:.75rem .65rem 2rem}
  header.hero{padding:1rem 0 .9rem;margin-bottom:.75rem}
  header.hero h1{font-size:1.28rem}
  .card{padding:.7rem .7rem;border-radius:8px;margin-bottom:.7rem}
  .card h2{font-size:1.05rem}
  ul.highlights{padding-left:1rem}
  ul.highlights li{font-size:.9rem;margin:.22rem 0}
  .repo-row{font-size:.84rem;padding:.26rem 0}
  .author-head{font-size:.88rem;padding:.25rem 0 .2rem}
  .filters input,.filters select{padding:.35rem .5rem;font-size:.86rem}
  nav.toc a{font-size:.78rem;padding:.15rem .55rem}
}
"""


def render_index_html(digest: dict[str, Any], repo_db: dict[str, Any]) -> str:
    gen = html.escape(digest.get("generated_at_taipei") or digest.get("generated_at") or "")
    featured = digest.get("featured_count", len(digest.get("highlights") or []))
    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="zh-Hant">',
        "<head>",
        '<meta charset="utf-8"/>',
        '<meta name="viewport" content="width=device-width, initial-scale=1"/>',
        "<title>台灣臨床醫事工程師 — 本週值得追蹤</title>",
        '<meta name="description" content="名冊帳號本週值得追蹤的公開 GitHub 動態精選，與依作者分組的專案資料庫（繁體中文）"/>',
        "<style>" + _css() + "</style>",
        "</head>",
        "<body>",
        '<header class="hero"><div class="wrap">',
        "<h1>台灣臨床醫事工程師</h1>",
        "<p>本週值得追蹤 · 依作者分組的公開專案資料庫</p>",
        f'<p class="meta" style="opacity:.9">產生時間：{gen}</p>',
        "</div></header>",
        '<div class="wrap">',
        '<nav class="toc">',
        '<a href="#weekly">本週值得追蹤</a>',
        '<a href="#database">專案資料庫</a>',
        '<a href="./repos.md">Markdown 資料庫</a>',
        '<a href="./data/repos.json">repos.json</a>',
        '<a href="./latest.json">latest.json</a>',
        '<a href="https://github.com/erichuang777777/awesome-tw-physician-engineer">GitHub 名冊</a>',
        "</nav>",
        f'<p class="note"><strong>隱私：</strong>{html.escape(digest.get("privacy_note") or "")}</p>',
        '<section id="weekly" class="card">',
        "<h2>本週值得追蹤</h2>",
        f'<p class="meta">視窗：近 <strong>{digest["window_days"]}</strong> 天'
        f' · 名冊 <strong>{digest["roster_count"]}</strong>'
        f' · 精選 <strong>{featured}</strong></p>',
        f'<p>{html.escape(digest.get("overview_zh") or "")}</p>',
    ]

    highlights = digest.get("highlights") or []
    if not highlights:
        parts.append("<p>本週暫無特別值得追蹤的臨床／醫工公開動態。</p>")
    else:
        parts.append('<ul class="highlights">')
        for h in highlights:
            login = html.escape(h["login"])
            profile = html.escape(h["profile_url"])
            text = html.escape(h.get("text_zh") or "")
            parts.append(
                f'<li><a class="handle" href="{profile}">@{login}</a> — {text}</li>'
            )
        parts.append("</ul>")
        if digest.get("soft_note_zh"):
            parts.append(
                f'<p class="soft-note">{html.escape(digest["soft_note_zh"])}</p>'
            )
    parts.append("</section>")

    parts.extend(
        [
            '<section id="database" class="card">',
            "<h2>專案資料庫</h2>",
            f'<p class="meta">依<strong>作者分組一次</strong>：帳號標題下為緊湊「倉庫名 — 一句繁中」。'
            f'共 <strong>{repo_db["repo_count"]}</strong> 個倉庫、'
            f'<strong>{repo_db["roster_count"]}</strong> 個帳號。</p>',
            '<div class="filters">',
            '<input type="search" id="q" placeholder="搜尋倉庫、說明、主題、帳號…" autocomplete="off"/>',
            '<select id="account"><option value="">全部帳號</option></select>',
            "</div>",
            '<p class="stats" id="stats"></p>',
            '<div id="repo-root"><p class="meta">載入資料中…</p></div>',
            "</section>",
            "<footer>",
            '<p>來源：<a href="https://github.com/erichuang777777/awesome-tw-physician-engineer">'
            "erichuang777777/awesome-tw-physician-engineer</a> · "
            "由 <code>scripts/weekly_digest.py</code>／"
            "<code>.github/workflows/weekly-digest.yml</code> 每週重建。</p>",
            "</footer>",
            "</div>",
            "<script>",
            JS_BOOTSTRAP,
            "</script>",
            "</body></html>",
            "",
        ]
    )
    return "\n".join(parts)


JS_BOOTSTRAP = r"""
(async function () {
  const root = document.getElementById('repo-root');
  const stats = document.getElementById('stats');
  const q = document.getElementById('q');
  const sel = document.getElementById('account');
  let data;
  try {
    const res = await fetch('./data/repos.json', { cache: 'no-cache' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    data = await res.json();
  } catch (e) {
    root.innerHTML = '<p>無法載入 <code>data/repos.json</code>：' + e + '</p>';
    return;
  }
  const accounts = data.accounts || [];
  for (const a of accounts) {
    const opt = document.createElement('option');
    opt.value = a.login;
    opt.textContent = '@' + a.login + '（' + (a.repo_count || 0) + '）';
    sel.appendChild(opt);
  }

  function norm(s) { return (s || '').toString().toLowerCase(); }

  function render() {
    const query = norm(q.value);
    const only = sel.value;
    let shownRepos = 0, shownAccounts = 0;
    const frag = document.createDocumentFragment();
    for (const acc of accounts) {
      if (only && acc.login !== only) continue;
      const repos = (acc.repos || []).filter(function (r) {
        if (!query) return true;
        const blob = [acc.login, r.name, r.full_name, r.description, r.intro_zh, r.theme, r.language]
          .concat(r.topics || []).join(' ').toLowerCase();
        return blob.indexOf(query) !== -1;
      });
      if (!repos.length) continue;
      shownAccounts += 1;
      shownRepos += repos.length;

      const block = document.createElement('div');
      block.className = 'author-block';
      block.dataset.login = acc.login;

      const head = document.createElement('div');
      head.className = 'author-head';
      const link = document.createElement('a');
      link.href = acc.profile_url;
      link.textContent = '@' + acc.login;
      head.appendChild(link);
      const count = document.createElement('span');
      count.className = 'count';
      count.textContent = repos.length + ' 個倉庫';
      head.appendChild(count);
      block.appendChild(head);

      const ul = document.createElement('ul');
      ul.className = 'repo-list';
      for (const r of repos) {
        const li = document.createElement('li');
        li.className = 'repo-row';
        const a = document.createElement('a');
        a.className = 'rname';
        a.href = r.url;
        a.target = '_blank';
        a.rel = 'noopener';
        a.textContent = r.name;
        li.appendChild(a);
        if (r.archived) {
          const arch = document.createElement('span');
          arch.className = 'arch';
          arch.textContent = '已封存';
          li.appendChild(arch);
        }
        const sep = document.createElement('span');
        sep.className = 'sep';
        sep.textContent = ' — ';
        li.appendChild(sep);
        const intro = document.createElement('span');
        intro.className = 'intro';
        intro.textContent = r.intro_zh || '';
        li.appendChild(intro);
        ul.appendChild(li);
      }
      block.appendChild(ul);
      frag.appendChild(block);
    }
    root.innerHTML = '';
    if (!shownRepos) {
      root.innerHTML = '<p class="meta">沒有符合條件的倉庫。</p>';
    } else {
      root.appendChild(frag);
    }
    stats.textContent = '顯示 ' + shownRepos + ' 個倉庫／' + shownAccounts + ' 個帳號（資料庫共 ' +
      (data.repo_count || 0) + ' 個倉庫）';
  }
  q.addEventListener('input', render);
  sel.addEventListener('change', render);
  render();
})();
"""


def write_outputs(digest: dict[str, Any], repo_db: dict[str, Any]) -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    (DOCS_DIR / "latest.json").write_text(
        json.dumps(digest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (DATA_DIR / "repos.json").write_text(
        json.dumps(repo_db, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (CACHE_DIR / "repos.json").write_text(
        json.dumps(repo_db, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (DOCS_DIR / "index.md").write_text(render_index_md(digest, repo_db), encoding="utf-8")
    (DOCS_DIR / "repos.md").write_text(render_repos_md(repo_db), encoding="utf-8")
    (DOCS_DIR / "index.html").write_text(render_index_html(digest, repo_db), encoding="utf-8")


def main() -> int:
    from_cache = (
        "--from-cache" in sys.argv
        or os.environ.get("REGEN_FROM_CACHE", "").lower() in {"1", "true", "yes"}
    )

    if from_cache:
        latest_path = DOCS_DIR / "latest.json"
        repos_path = DATA_DIR / "repos.json"
        if not latest_path.is_file() or not repos_path.is_file():
            print("error: --from-cache requires docs/latest.json and docs/data/repos.json", file=sys.stderr)
            return 1
        digest = json.loads(latest_path.read_text(encoding="utf-8"))
        repo_db = json.loads(repos_path.read_text(encoding="utf-8"))
        attach_curation(digest)
        write_outputs(digest, repo_db)
        print(
            f"Regenerated from cache: featured {digest.get('featured_count')} / "
            f"active {digest.get('active_count')} / repos {repo_db.get('repo_count')}",
            flush=True,
        )
        return 0

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
    digest, repo_db = build_all(logins, token, now)
    write_outputs(digest, repo_db)

    print(f"Active accounts: {digest['active_count']}/{digest['roster_count']}", flush=True)
    print(f"Featured highlights: {digest.get('featured_count')}", flush=True)
    print(f"Repo DB: {repo_db['repo_count']} repos across {repo_db['roster_count']} accounts", flush=True)
    if digest.get("skipped"):
        print(f"Skipped/partial: {digest['skipped']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
