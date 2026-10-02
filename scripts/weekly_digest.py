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
                # Lightweight weekly-only path: still need window repos via API
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
        "overview_zh": (
            f"本週掃描 {len(logins)} 個名冊帳號，其中 {len(digest_entries)} 個帳號"
            f"在近 {WINDOW_DAYS} 天有公開新建／推送動態。"
        ),
        "entries": digest_entries,
        "skipped": skipped,
    }

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
        "# 台灣臨床醫事工程師 — 公開動態與專案資料庫",
        "",
        "隱私優先：僅使用 GitHub `@帳號` 與公開倉庫中繼資料。",
        "",
        f"- 產生時間：`{digest.get('generated_at_taipei') or digest.get('generated_at')}`",
        f"- 動態視窗：近 **{digest['window_days']}** 天（自 `{digest['window_start']}`）",
        f"- 名冊帳號：**{digest['roster_count']}**",
        f"- 本週有動態：**{digest['active_count']}**",
        f"- 公開非 fork 倉庫總數：**{repo_db['repo_count']}**",
        "",
        f"> **隱私：** {digest['privacy_note']}",
        "",
        "機器可讀：[latest.json](./latest.json) · [data/repos.json](./data/repos.json)",
        "",
        "---",
        "",
        "## 本週動態摘要",
        "",
        digest.get("overview_zh") or "",
        "",
    ]
    entries = digest.get("entries") or []
    if not entries:
        lines.extend(["本週名冊帳號未偵測到公開新建／推送動態。", ""])
    else:
        for entry in entries:
            lines.append(f"### [@{entry['login']}]({entry['profile_url']})")
            lines.append("")
            lines.append(entry.get("summary_zh") or "")
            lines.append("")
            for repo in entry.get("repos") or []:
                flags = []
                if repo.get("is_new"):
                    flags.append("新建")
                if repo.get("recently_pushed"):
                    flags.append("有推送")
                flag_s = f"（{'／'.join(flags)}）" if flags else ""
                day = (repo.get("pushed_at") or repo.get("created_at") or "")[:10]
                day_s = f" — {day}" if day else ""
                lines.append(f"- [{repo['name']}]({repo['url']}){flag_s}{day_s}")
                if repo.get("intro_zh"):
                    lines.append(f"  - {repo['intro_zh']}")
            lines.append("")

    lines.extend(["---", "", "## 專案資料庫（公開非 fork）", "",
                  "完整列表見 [repos.md](./repos.md) 或網頁搜尋介面。", "",
                  f"共 **{repo_db['repo_count']}** 個倉庫、**{repo_db['roster_count']}** 個帳號。", "",
                  "來源：[erichuang777777/awesome-tw-physician-engineer]"
                  "(https://github.com/erichuang777777/awesome-tw-physician-engineer)", "",
                  "由 `.github/workflows/weekly-digest.yml` 每週重建。", ""])
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
            theme = f" · {r['theme']}" if r.get("theme") else ""
            lang = f" · {r['language']}" if r.get("language") else ""
            lines.append(f"### [{r['name']}]({r['url']}){arch}")
            lines.append("")
            lines.append(r.get("intro_zh") or "")
            lines.append("")
            meta = []
            if r.get("description"):
                meta.append(f"原始說明：{r['description']}")
            if r.get("topics"):
                meta.append("主題：" + ", ".join(r["topics"][:8]))
            meta.append(f"星標 {r.get('stars', 0)}{lang}{theme}")
            for m in meta:
                lines.append(f"- {m}")
            lines.append("")
    return "\n".join(lines)

def _css() -> str:
    return """
:root{--bg:#f6f8fa;--card:#fff;--text:#1f2328;--muted:#656d76;--accent:#0969da;--border:#d0d7de;--chip:#ddf4ff;--ok:#1a7f37;--warn:#9a6700}
*{box-sizing:border-box}
body{margin:0;font-family:"Noto Sans TC",system-ui,-apple-system,"Segoe UI",Roboto,"PingFang TC","Microsoft JhengHei",sans-serif;background:var(--bg);color:var(--text);line-height:1.6}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:960px;margin:0 auto;padding:1.25rem 1rem 3rem}
header.hero{background:linear-gradient(135deg,#1f6feb 0%,#054da7 100%);color:#fff;padding:1.75rem 0 1.5rem;margin-bottom:1.25rem}
header.hero .wrap{padding-top:0;padding-bottom:0}
header.hero h1{margin:0 0 .4rem;font-size:1.65rem;font-weight:700}
header.hero p{margin:.25rem 0;opacity:.95}
nav.toc{display:flex;flex-wrap:wrap;gap:.5rem;margin:1rem 0}
nav.toc a{background:var(--card);border:1px solid var(--border);border-radius:999px;padding:.25rem .75rem;font-size:.9rem}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:1rem 1.1rem;margin:0 0 1rem;box-shadow:0 1px 2px rgba(0,0,0,.04)}
.card h2{margin:0 0 .6rem;font-size:1.25rem}
.meta{color:var(--muted);font-size:.92rem}
.note{background:#fff8c5;border:1px solid #d4a72c;border-radius:8px;padding:.7rem .9rem;margin:0.75rem 0;font-size:.92rem}
.summary{border-left:4px solid var(--accent);padding:.15rem 0 .15rem .85rem;margin:.6rem 0}
.summary h3{margin:0 0 .25rem;font-size:1.05rem}
.chip{display:inline-block;background:var(--chip);color:#0550ae;border-radius:999px;padding:.05rem .5rem;font-size:.78rem;margin-right:.25rem}
.chip.new{background:#dafbe1;color:var(--ok)}
.chip.push{background:#fff8c5;color:var(--warn)}
ul.tight{margin:.35rem 0;padding-left:1.2rem}
.filters{display:flex;flex-wrap:wrap;gap:.6rem;margin:0.75rem 0 1rem;align-items:center}
.filters input,.filters select{font:inherit;padding:.45rem .65rem;border:1px solid var(--border);border-radius:8px;min-width:0}
.filters input{flex:1 1 220px}
.filters select{flex:0 1 200px}
.stats{color:var(--muted);font-size:.9rem;margin-bottom:.5rem}
.account{margin:0 0 1.25rem}
.account h3{margin:0 0 .5rem;font-size:1.1rem;position:sticky;top:0;background:var(--bg);padding:.4rem 0;z-index:1}
.repo{border:1px solid var(--border);border-radius:10px;padding:.7rem .85rem;margin:0 0 .5rem;background:#fff}
.repo .name{font-weight:600}
.repo .intro{margin:.25rem 0 0;color:#333}
.repo .desc{margin:.2rem 0 0;color:var(--muted);font-size:.88rem}
footer{margin-top:2rem;color:var(--muted);font-size:.88rem}
.hidden{display:none !important}
"""


def render_index_html(digest: dict[str, Any], repo_db: dict[str, Any]) -> str:
    gen = html.escape(digest.get("generated_at_taipei") or digest.get("generated_at") or "")
    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="zh-Hant">',
        "<head>",
        '<meta charset="utf-8"/>',
        '<meta name="viewport" content="width=device-width, initial-scale=1"/>',
        "<title>台灣臨床醫事工程師 — 本週動態與專案資料庫</title>",
        '<meta name="description" content="名冊帳號的公開 GitHub 動態摘要與非 fork 專案資料庫（繁體中文）"/>',
        "<style>" + _css() + "</style>",
        "</head>",
        "<body>",
        '<header class="hero"><div class="wrap">',
        "<h1>台灣臨床醫事工程師</h1>",
        "<p>公開 GitHub 動態週摘要 · 全名冊公開專案資料庫</p>",
        f'<p class="meta" style="opacity:.9">產生時間：{gen}</p>',
        "</div></header>",
        '<div class="wrap">',
        '<nav class="toc">',
        '<a href="#weekly">本週動態摘要</a>',
        '<a href="#database">專案資料庫</a>',
        '<a href="./repos.md">Markdown 資料庫</a>',
        '<a href="./data/repos.json">repos.json</a>',
        '<a href="./latest.json">latest.json</a>',
        '<a href="https://github.com/erichuang777777/awesome-tw-physician-engineer">GitHub 名冊</a>',
        "</nav>",
        f'<p class="note"><strong>隱私：</strong>{html.escape(digest.get("privacy_note") or "")}</p>',
        '<section id="weekly" class="card">',
        "<h2>本週動態摘要</h2>",
        f'<p class="meta">視窗：近 <strong>{digest["window_days"]}</strong> 天'
        f'（自 <code>{html.escape(digest.get("window_start") or "")}</code>）· '
        f'名冊 <strong>{digest["roster_count"]}</strong> · '
        f'有動態 <strong>{digest["active_count"]}</strong></p>',
        f'<p>{html.escape(digest.get("overview_zh") or "")}</p>',
    ]

    entries = digest.get("entries") or []
    if not entries:
        parts.append("<p>本週名冊帳號未偵測到公開新建／推送動態。</p>")
    else:
        for entry in entries:
            login = html.escape(entry["login"])
            profile = html.escape(entry["profile_url"])
            parts.append('<div class="summary">')
            parts.append(f'<h3><a href="{profile}">@{login}</a></h3>')
            parts.append(f'<p>{html.escape(entry.get("summary_zh") or "")}</p>')
            repos = entry.get("repos") or []
            if repos:
                parts.append('<ul class="tight">')
                for repo in repos:
                    flags = []
                    if repo.get("is_new"):
                        flags.append('<span class="chip new">新建</span>')
                    if repo.get("recently_pushed"):
                        flags.append('<span class="chip push">有推送</span>')
                    flag_s = " ".join(flags)
                    day = (repo.get("pushed_at") or repo.get("created_at") or "")[:10]
                    day_s = f" · {html.escape(day)}" if day else ""
                    name = html.escape(repo["name"])
                    url = html.escape(repo["url"])
                    intro = html.escape(repo.get("intro_zh") or "")
                    intro_s = f'<br/><span class="meta">{intro}</span>' if intro else ""
                    parts.append(
                        f'<li><a href="{url}">{name}</a> {flag_s}{day_s}{intro_s}</li>'
                    )
                parts.append("</ul>")
            pushes = entry.get("recent_pushes") or []
            listed = {f"{entry['login']}/{r['name']}" for r in repos}
            extra = [p for p in pushes if p["repo"] not in listed]
            if extra:
                parts.append('<p class="meta">其他公開推送：</p><ul class="tight">')
                for p in extra[:8]:
                    day = (p.get("pushed_at") or "")[:10]
                    parts.append(
                        f'<li><a href="{html.escape(p["url"])}">{html.escape(p["repo"])}</a>'
                        f' · {html.escape(day)}</li>'
                    )
                parts.append("</ul>")
            parts.append("</div>")
    parts.append("</section>")

    # Database section — client-side filter over fetched JSON
    parts.extend([
        '<section id="database" class="card">',
        "<h2>專案資料庫</h2>",
        f'<p class="meta">整合名冊帳號的<strong>每一個</strong>公開非 fork 倉庫，並附一句繁中介紹。'
        f'目前共 <strong>{repo_db["repo_count"]}</strong> 個倉庫、'
        f'<strong>{repo_db["roster_count"]}</strong> 個帳號。</p>',
        '<div class="filters">',
        '<input type="search" id="q" placeholder="搜尋倉庫名稱、說明、主題、帳號…" autocomplete="off"/>',
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
    ])
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
      const wrap = document.createElement('div');
      wrap.className = 'account';
      wrap.dataset.login = acc.login;
      const h = document.createElement('h3');
      const link = document.createElement('a');
      link.href = acc.profile_url;
      link.textContent = '@' + acc.login;
      h.appendChild(link);
      h.appendChild(document.createTextNode(' · ' + repos.length + ' 個倉庫'));
      wrap.appendChild(h);
      for (const r of repos) {
        const card = document.createElement('div');
        card.className = 'repo';
        const name = document.createElement('div');
        name.className = 'name';
        const a = document.createElement('a');
        a.href = r.url; a.target = '_blank'; a.rel = 'noopener';
        a.textContent = r.name;
        name.appendChild(a);
        if (r.theme) {
          const c = document.createElement('span');
          c.className = 'chip'; c.textContent = r.theme; c.style.marginLeft = '.4rem';
          name.appendChild(c);
        }
        if (r.language) {
          const c = document.createElement('span');
          c.className = 'chip'; c.textContent = r.language; c.style.marginLeft = '.25rem';
          name.appendChild(c);
        }
        if (r.archived) {
          const c = document.createElement('span');
          c.className = 'chip push'; c.textContent = '已封存'; c.style.marginLeft = '.25rem';
          name.appendChild(c);
        }
        card.appendChild(name);
        const intro = document.createElement('p');
        intro.className = 'intro';
        intro.textContent = r.intro_zh || '';
        card.appendChild(intro);
        if (r.description) {
          const d = document.createElement('p');
          d.className = 'desc';
          d.textContent = '原始說明：' + r.description;
          card.appendChild(d);
        }
        wrap.appendChild(card);
      }
      frag.appendChild(wrap);
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
    digest, repo_db = build_all(logins, token, now)

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

    print(f"Active accounts: {digest['active_count']}/{digest['roster_count']}", flush=True)
    print(f"Repo DB: {repo_db['repo_count']} repos across {repo_db['roster_count']} accounts", flush=True)
    if digest.get("skipped"):
        print(f"Skipped/partial: {digest['skipped']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
