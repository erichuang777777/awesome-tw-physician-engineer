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
    _EMPTY_PROFILE_LINE,
    _EMPTY_SITE_LINE,
    _banned_intro,
    _is_personal_site,
    _is_profile_repo,
    synthesize_zh_intro,
)

REPOS_PATH = ROOT / "docs" / "data" / "repos.json"
CACHE_DIR = ROOT / "research" / "readme_cache"
PROGRESS_PATH = ROOT / "research" / "intro_batches" / "readme_intros.json"
BATCH_LOG = ROOT / "research" / "intro_batches" / "llm_batches.jsonl"

SYSTEM_PROMPT = """你是台灣臨床工程師社群的繁體中文編輯。
任務：根據倉庫名稱、description、README 摘錄，寫「一句」繁體中文簡介，讓同溫層能判斷是否已有輪子可重用。
必須同時說清楚：
1) 功能（做什麼）
2) 解決什麼問題／痛點（README 有才寫；沒有就只寫功能，勿臆造）
規則：
- 只輸出一句繁體中文（可用「；」連兩短句），約 24–64 字，以「。」結尾。
- 直接寫功能，不要用「這是一個／本專案／該工具」開頭。
- 禁止：真實姓名、醫院／院所全名或縮寫（即使 README 有也請改寫成「機構／院內」等泛稱）、個人檔案行銷、「公開專案／倉庫」、「主要語言」、語言或 LLM 標籤堆砌、簡體字。
- 禁止空泛形容：易用、強大、現代化、賦能、無縫、旨在。
- 個人網站／profile／dotfiles 請用固定句，勿自行發揮。
- 不要加引號、列點或「簡介：」前綴。
- README 幾乎沒資訊時：短句說明並標「（README 不足）」。
"""

USER_TMPL = """倉庫：{full_name}
名稱：{name}
description：{description}
主題推測：{theme}
README 摘錄：
---
{excerpt}
---
請輸出一句繁中簡介。"""


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
    t = (text or "").strip().strip('"\'「」')
    t = re.sub(r"\s+", " ", t)
    if not t:
        return None
    # take first line only
    t = t.splitlines()[0].strip()
    t = re.sub(r"^(簡介|介绍|Intro|Summary)\s*[:：]\s*", "", t, flags=re.I)
    if len(t) > 110:
        t = t[:109].rstrip() + "…"
    if not t.endswith(("。", "！", "？", "…")):
        t += "。"
    if _banned_intro(t):
        return None
    if looks_simplified(t):
        return None
    cjk = sum(1 for c in t if "\u4e00" <= c <= "\u9fff")
    if cjk < 6:
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
        if ok:
            return ok, model
        # one retry with stricter reminder
        messages2 = messages + [
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": "改寫成一句「繁體中文」、含功能與問題、勿簡體、勿行銷腔、勿超過 70 字。只回那一句。",
            },
        ]
        raw2 = openrouter_chat(messages2, model=model)
        ok2 = validate_intro(raw2 or "")
        if ok2:
            return ok2, model
    return None, None


def heuristic_intro(item: dict[str, Any], cache: dict[str, Any] | None) -> tuple[str, bool]:
    """Return (intro, thin)."""
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
        return "個人開發環境與 shell／編輯器設定檔集合。", True

    # Prefer CJK first sentence from README if present
    if cache and cache.get("status") == "ok" and (cache.get("text") or "").strip():
        excerpt = readme_excerpt(cache["text"], 900)
        # find a CJK-heavy sentence
        for sent in re.split(r"(?<=[。！？])\s*|\n+", excerpt):
            s = sent.strip()
            cjk = sum(1 for c in s if "\u4e00" <= c <= "\u9fff")
            if cjk >= 10 and not _banned_intro(s):
                s = validate_intro(s) or s
                if s and not _banned_intro(s):
                    if not s.endswith(("。", "！", "？", "…")):
                        s += "。"
                    return s[:95], False
        # English README: fall through to synthesize + mark thin-ish if no desc
        base = synthesize_zh_intro(name, desc, topics, item.get("language"), owner=owner)
        # If we have English excerpt, append honest marker only when synthesize is hollow
        thin = any(
            x in base
            for x in ("推斷", "說明不足", "說明文字不足", "無可讀說明", "無可單獨")
        )
        return base, thin

    base = synthesize_zh_intro(name, desc, topics, item.get("language"), owner=owner)
    thin = True
    if desc and sum(1 for c in desc if "\u4e00" <= c <= "\u9fff") >= 8:
        thin = _banned_intro(base) or any(x in base for x in ("推斷", "說明不足"))
    return base, thin


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
        # still LLM if English description is rich?
        d = (desc or "").strip()
        if d and len(d) >= 40 and not sum(1 for c in d if "\u4e00" <= c <= "\u9fff"):
            return True
        return False
    excerpt = readme_excerpt(cache.get("text") or "", 400)
    if len(excerpt) < 40:
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
            and prev.get("intro_zh")
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
            "intro_thin": False,
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
