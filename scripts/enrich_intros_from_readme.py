#!/usr/bin/env python3
"""Fetch-aware 繁中 intro enrichment from README caches.

Writes intro_zh (+ readme_hash, intro_thin, intro_source) into docs/data/repos.json
and research/intro_batches/progress.json for resumability.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from weekly_digest import (  # noqa: E402
    _DOTFILES_LINE,
    _EMPTY_PROFILE_LINE,
    _EMPTY_SITE_LINE,
    _banned_intro,
    _is_personal_site,
    _is_profile_repo,
    is_two_part_intro,
    normalize_two_part,
    synthesize_zh_intro,
)

REPOS_PATH = ROOT / "docs" / "data" / "repos.json"
CACHE_DIR = ROOT / "research" / "readme_cache"
PROGRESS_PATH = ROOT / "research" / "intro_batches" / "readme_intros.json"
BATCH_LOG = ROOT / "research" / "intro_batches" / "llm_batches.jsonl"

SYSTEM_PROMPT = """你是台灣臨床工程師社群的繁體中文編輯。
把每個倉庫收成剛好兩個概念，且只輸出一行：
問題：<作者要解的具體痛點>；做法：<用來解的做法或框架>

範例（只學格式）：
問題：現場不知道 CPR 按壓有沒有到 100–120 次/分；做法：用手機鏡頭即時估速率

規則：
- 問題寫現場卡點（誰、什麼情況、缺什麼），不要寫成「如何…？」問句。
- 做法寫機制，不要只重複產品名，不要「開發一個平台」。
- 兩半各約 12–28 字。不要句號結尾，不要第二行。
- 只用繁體中文。禁止：真實姓名、醫院／院所全名或縮寫（README 有也改成「院內」）、個人檔案、「公開專案／倉庫」、「主要語言」、語言標籤、簡體字。
- 禁止空泛形容：易用、強大、現代化、賦能、無縫、旨在。
- 痛點是使用者卡住的事（找不到、散落、做不到）。README 或 description 看得出來就寫出來，不要躲去「說明未寫」。
- 「說明未寫具體痛點」只用在兩邊都看不出在幫誰解決什麼時；做法就只依名稱如實短寫，不要發明框架。
- 做法寫機制，少堆模型或產品名。
- 個人網站／profile：問題：個人頁面，沒有單一待解問題；做法：作品集站，沒有可單獨說明的做法
- dotfiles：問題：沒有產品問題要解；做法：個人 shell／編輯器設定檔
- 不要加引號或「簡介：」。
"""

USER_TMPL = """倉庫：{full_name}
名稱：{name}
description：{description}
主題推測：{theme}
README 摘錄：
---
{excerpt}
---
只輸出一行「問題：…；做法：…」。"""


def _strip_md(text: str) -> str:
    t = text or ""
    t = re.sub(r"(?is)<script.*?</script>", " ", t)
    t = re.sub(r"(?is)<style.*?</style>", " ", t)
    t = re.sub(r"(?is)<!--.*?-->", " ", t)
    t = re.sub(r"(?is)<[^>]+>", " ", t)
    t = re.sub(r"(?im)^\s*\|.*\|\s*$", " ", t)  # tables
    t = re.sub(r"(?im)^\s{0,3}#{1,6}\s*", "", t)
    t = re.sub(r"(?is)```.*?```", " ", t)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    t = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"(?im)^\s*>\s?", "", t)
    t = re.sub(r"(?im)^\s*[-*+]\s+", "", t)
    t = re.sub(r"(?im)^\s*\d+\.\s+", "", t)
    t = re.sub(r"(?i)https?://\S+", " ", t)
    t = re.sub(r"(?i)badge|shields\.io|travis-ci|codecov", " ", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def readme_excerpt(text: str, limit: int = 1400) -> str:
    clean = _strip_md(text)
    if not clean:
        return ""
    # Prefer early paragraphs with substance
    parts = [p.strip() for p in re.split(r"\n\s*\n", clean) if p.strip()]
    buf: list[str] = []
    n = 0
    for p in parts:
        if len(p) < 8:
            continue
        # skip pure install / license noise early if we already have substance
        low = p.lower()
        if n > 80 and any(
            k in low
            for k in (
                "pip install",
                "npm install",
                "license",
                "contributing",
                "changelog",
                "table of contents",
            )
        ):
            continue
        buf.append(p)
        n += len(p)
        if n >= limit:
            break
    out = "\n".join(buf)
    return out[:limit]


def load_cache(full_name: str) -> dict[str, Any] | None:
    p = CACHE_DIR / f"{full_name.replace('/', '__')}.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def looks_simplified(text: str) -> bool:
    # crude markers
    markers = ("软件", "默认", "质量", "网络", "数据", "用户", "识别", "设计", "应用", "图像", "视频", "打开", "关闭", "帮助")
    return any(m in text for m in markers)


def validate_intro(text: str) -> str | None:
    t = normalize_two_part(text or "")
    if not t:
        return None
    if looks_simplified(t):
        return None
    if not is_two_part_intro(t):
        return None
    return t


def openrouter_chat(messages: list[dict], model: str, max_tokens: int = 120) -> str | None:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return None
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=data,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/erichuang777777/awesome-tw-physician-engineer",
            "X-Title": "awesome-tw-physician-engineer-intros",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            obj = json.loads(resp.read().decode())
            return (obj["choices"][0]["message"]["content"] or "").strip()
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")[:300]
        print(f"LLM HTTP {e.code}: {err}", file=sys.stderr, flush=True)
        return None
    except Exception as e:
        print(f"LLM error: {e}", file=sys.stderr, flush=True)
        return None


MODELS = [
    "openai/gpt-4o-mini",
    "mistralai/mistral-small-3.1-24b-instruct",
    "openai/gpt-4.1-mini",
    "qwen/qwen-2.5-7b-instruct",
]


def llm_intro(item: dict[str, Any], excerpt: str) -> tuple[str | None, str | None]:
    user = USER_TMPL.format(
        full_name=item["full_name"],
        name=item["name"],
        description=(item.get("description") or "（無）")[:240],
        theme=item.get("theme") or "（無）",
        excerpt=excerpt or "（無 README）",
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
    for model in MODELS:
        raw = openrouter_chat(messages, model=model)
        if not raw:
            continue
        ok = validate_intro(raw)
        if ok and not ("說明未寫" in ok and len(excerpt) > 180):
            return ok, model
        # one retry with stricter reminder
        messages2 = messages + [
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": "上一句不合格。痛點要是使用者卡住的事，README 看得懂就不要寫「說明未寫」。做法寫機制、不要問句、不要簡體、不要院所名。只回一行「問題：…；做法：…」。",
            },
        ]
        raw2 = openrouter_chat(messages2, model=model)
        ok2 = validate_intro(raw2 or "")
        if ok2:
            return ok2, model
    return None, None


def _thin_line(text: str) -> bool:
    return any(x in (text or "") for x in ("說明未寫", "說明不足", "看不出", "沒有可寫", "沒有單一待解", "沒有產品問題"))


def heuristic_intro(item: dict[str, Any], cache: dict[str, Any] | None) -> tuple[str, bool]:
    """Return (intro, thin). Always 問題／做法. Does not invent a framework."""
    del cache  # README substance is handled by the LLM path
    name = item["name"]
    owner = item["login"]
    desc = item.get("description")
    topics = list(item.get("topics") or [])
    if _is_personal_site(name, desc):
        return _EMPTY_SITE_LINE, True
    if _is_profile_repo(name, desc, owner):
        return _EMPTY_PROFILE_LINE, True
    low = (name or "").lower()
    if low in {"dotfiles", "dot-files"} or "dotfile" in low:
        return _DOTFILES_LINE, True
    base = synthesize_zh_intro(name, desc, topics, item.get("language"), owner=owner)
    if not is_two_part_intro(base):
        base = synthesize_zh_intro(name, None, topics, None, owner=owner)
    return base, _thin_line(base)


MED_THEME = {
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
MED_NAME_RE = re.compile(
    r"(?i)medic|clinic|hospital|fhir|dicom|ehr|emr|nhi|icd|pharm|drug|oncolog|cancer|"
    r"emergen|triage|radiol|pathol|nurs|opthal|ophthal|dental|patient|ward|icu|ecg|eeg|"
    r"dialysis|anesthes|surgery|guideline|pubmed|醫師|醫療|臨床|病歷|急診|藥|腫瘤|護理|健保"
)


def priority_score(item: dict[str, Any], cache: dict[str, Any] | None) -> int:
    score = 0
    theme = item.get("theme")
    if theme in MED_THEME:
        score += 100
    blob = " ".join(
        [
            item.get("name") or "",
            item.get("description") or "",
            " ".join(item.get("topics") or []),
            theme or "",
        ]
    )
    if MED_NAME_RE.search(blob):
        score += 50
    score += min(int(item.get("stars") or 0), 40)
    if cache and cache.get("status") == "ok" and (cache.get("size") or 0) > 200:
        score += 10
    # recently pushed
    pushed = item.get("pushed_at") or ""
    if pushed.startswith("2026-"):
        score += 5
    return score


def needs_llm(item: dict[str, Any], cache: dict[str, Any] | None) -> bool:
    name = item["name"]
    desc = item.get("description")
    owner = item["login"]
    if _is_personal_site(name, desc) or _is_profile_repo(name, desc, owner):
        return False
    low = (name or "").lower()
    if "dotfile" in low:
        return False
    if not cache or cache.get("status") != "ok":
        # Name + description only: stay honest. Do not invent a framework.
        return False
    excerpt = readme_excerpt(cache.get("text") or "", 400)
    # Thin READMEs produce made-up 痛點. Heuristic is the honest path.
    if len(excerpt) < 120:
        return False
    return True


def main() -> int:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)

    repo_db = json.loads(REPOS_PATH.read_text(encoding="utf-8"))
    progress: dict[str, Any] = {}
    if PROGRESS_PATH.is_file():
        try:
            progress = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
        except Exception:
            progress = {}

    items: list[dict[str, Any]] = []
    for acc in repo_db.get("accounts") or []:
        login = acc.get("login") or ""
        for r in acc.get("repos") or []:
            it = dict(r)
            it["login"] = login
            it["full_name"] = it.get("full_name") or f"{login}/{it.get('name')}"
            items.append(it)

    # Force rewrite if --force
    force = "--force" in sys.argv
    limit = None
    for a in sys.argv:
        if a.startswith("--limit="):
            limit = int(a.split("=", 1)[1])

    # Build worklist
    work: list[tuple[int, dict, dict | None]] = []
    skip_llm = 0
    for it in items:
        cache = load_cache(it["full_name"])
        key = it["full_name"]
        prev = progress.get(key)
        rh = (cache or {}).get("readme_hash")
        if (
            not force
            and prev
            and is_two_part_intro(prev.get("intro_zh") or "")
            and prev.get("readme_hash") == rh
            and not prev.get("failed")
        ):
            skip_llm += 1
            continue
        if needs_llm(it, cache):
            work.append((priority_score(it, cache), it, cache))
        else:
            intro, thin = heuristic_intro(it, cache)
            progress[key] = {
                "intro_zh": intro,
                "intro_thin": thin,
                "intro_source": "heuristic",
                "readme_hash": rh,
                "model": None,
            }

    work.sort(key=lambda x: -x[0])
    if limit is not None:
        work = work[:limit]

    print(
        f"Items {len(items)}; progress-skip {skip_llm}; heuristic-filled {len(progress)}; LLM queue {len(work)}",
        flush=True,
    )

    ok_n = fail_n = 0
    t0 = time.time()

    # Sequential with light parallelism (2) to respect rate limits / budget
    def do_one(triple):
        _score, it, cache = triple
        excerpt = readme_excerpt((cache or {}).get("text") or "", 1400) if cache else ""
        if not excerpt and it.get("description"):
            excerpt = f"description: {it.get('description')}"
        intro, model = llm_intro(it, excerpt)
        if not intro:
            intro, thin = heuristic_intro(it, cache)
            return it["full_name"], {
                "intro_zh": intro,
                "intro_thin": True,
                "intro_source": "heuristic_fallback",
                "readme_hash": (cache or {}).get("readme_hash"),
                "model": None,
                "failed": True,
            }, False
        return it["full_name"], {
            "intro_zh": intro,
            "intro_thin": _thin_line(intro),
            "intro_source": "readme_llm",
            "readme_hash": (cache or {}).get("readme_hash"),
            "model": model,
        }, True

    # Use 3 workers — OpenRouter handles modest concurrency
    workers = int(os.environ.get("INTRO_WORKERS", "3"))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(do_one, w): w[1]["full_name"] for w in work}
        done = 0
        for fut in as_completed(futs):
            key, rec, success = fut.result()
            progress[key] = rec
            done += 1
            if success:
                ok_n += 1
            else:
                fail_n += 1
            if done % 25 == 0 or done == len(work):
                PROGRESS_PATH.write_text(
                    json.dumps(progress, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                print(
                    f"LLM {done}/{len(work)} ok={ok_n} fail={fail_n} "
                    f"elapsed={time.time()-t0:.0f}s last={key}: {rec['intro_zh'][:60]}",
                    flush=True,
                )
            # gentle pacing
            time.sleep(0.05)

    PROGRESS_PATH.write_text(
        json.dumps(progress, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # Apply to repo_db
    applied = thin_n = readme_n = 0
    for acc in repo_db.get("accounts") or []:
        login = acc.get("login") or ""
        for r in acc.get("repos") or []:
            key = r.get("full_name") or f"{login}/{r.get('name')}"
            rec = progress.get(key)
            cache = load_cache(key)
            if not rec:
                intro, thin = heuristic_intro(
                    {
                        "login": login,
                        "name": r.get("name"),
                        "full_name": key,
                        "description": r.get("description"),
                        "topics": r.get("topics") or [],
                        "language": r.get("language"),
                        "theme": r.get("theme"),
                    },
                    cache,
                )
                rec = {
                    "intro_zh": intro,
                    "intro_thin": thin,
                    "intro_source": "heuristic",
                    "readme_hash": (cache or {}).get("readme_hash"),
                }
                progress[key] = rec
            r["intro_zh"] = rec["intro_zh"]
            if rec.get("readme_hash"):
                r["readme_hash"] = rec["readme_hash"]
            else:
                r.pop("readme_hash", None)
            r["intro_thin"] = bool(rec.get("intro_thin"))
            r["intro_source"] = rec.get("intro_source")
            applied += 1
            if r["intro_thin"]:
                thin_n += 1
            if rec.get("intro_source") == "readme_llm":
                readme_n += 1

    PROGRESS_PATH.write_text(
        json.dumps(progress, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    REPOS_PATH.write_text(
        json.dumps(repo_db, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"Applied {applied}; readme_llm={readme_n}; thin={thin_n}; "
        f"llm_ok={ok_n}; llm_fail={fail_n}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
