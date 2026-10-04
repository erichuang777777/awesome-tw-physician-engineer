#!/usr/bin/env python3
"""繁中 GitHub Pages：本週創作者動態摘要 + 全公開非 fork 專案資料庫。

Privacy:
  - Only GitHub @handles and public repo names/URLs/descriptions/topics
  - Never invent real names, hospitals, or private bios

Intros:
  - Every intro is exactly two ideas, one line:
    問題：<痛點>；做法：<做法或框架>
  - Prefer README-aware lines from scripts/enrich_intros_from_readme.py
    (cached under research/readme_cache/). If the README states no real
    problem, say so and describe only what name+description support.
  - Each repo may store readme_hash / intro_source / intro_thin. Weekly runs
    keep a line only when it is already 問題／做法 and metadata (or README
    hash) is unchanged.
  - --from-cache --rewrite-intros keeps two-part intros unless --force-intros.
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
README_CACHE_DIR = CACHE_DIR / "readme_cache"
INTRO_PROGRESS_PATH = CACHE_DIR / "intro_batches" / "readme_intros.json"
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
    (['ehr', 'emr', '電子病歷', 'openemr', 'cpoe', 'electronic medical record', 'medical record'], '電子病歷／臨床資訊系統'),
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
        hit = False
        for k in keys:
            if not k:
                continue
            # CJK or longer tokens: substring OK; short ASCII: word-ish boundary
            if re.search(r"[一-鿿]", k) or len(k) >= 5:
                if k in blob:
                    hit = True
                    break
            else:
                if re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", blob):
                    hit = True
                    break
        if hit:
            return label
    return None


def _banned_intro(text: str) -> bool:
    """True when intro is empty or reads like profile/marketing filler."""
    if not text:
        return True
    banned = (
        "公開專案", "公開倉庫", "主要語言", "相關公開", "尚無說明文字",
        "個人檔案", "GitHub 個人", "相關倉庫", "個人網站說明",
        "易用／可擴充", "可擴充／可靠", "可靠／安全", "易用／",
        "強化／", "更智慧", "旨在／",
    )
    if any(b in text for b in banned):
        return True
    # slash-stacked buzzword piles with little substance
    if text.count("／") >= 4 and any(w in text for w in ("易用", "可擴充", "可靠", "安全", "團隊", "決策")):
        return True
    return False


_PROFILE_DESC_RE = re.compile(
    r"(?i)\b("
    r"my profile|about me|profile readme|github profile|"
    r"config files for my github profile|personal (profile|readme)|"
    r"files for my github profile"
    r")\b"
)

_EMPTY_SITE_LINE = "問題：個人頁面，沒有單一待解問題；做法：作品集站，沒有可單獨說明的做法"
_EMPTY_PROFILE_LINE = "問題：個人頁面，沒有單一待解問題；做法：作品集站，沒有可單獨說明的做法"
_DOTFILES_LINE = "問題：沒有產品問題要解；做法：個人 shell／編輯器設定檔"

_TWO_PART_RE = re.compile(
    r"^問題：([^；\n]{2,56})；做法：([^\n]{2,56})$"
)


def is_two_part_intro(text: str) -> bool:
    return bool(_TWO_PART_RE.match((text or "").strip()))


def _clip_clause(text: str, limit: int = 42) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip())
    t = t.strip(" 。；;，,、\"'「」『』")
    t = re.sub(r"^(這是一個|這是|本專案|本倉庫|該工具|該專案|一個)", "", t).strip()
    t = t.strip("。； ")
    if len(t) > limit:
        cut = t[:limit]
        for sep in ("，", "、", " ", "—", "-", "／", "/"):
            i = cut.rfind(sep)
            if i >= 12:
                cut = cut[:i]
                break
        t = cut.rstrip(" ，,；;、—-/／")
    return t


_HOSP_TOKENS = {
    "ntuh", "cgmh", "tsgh", "vgh", "vghtpe", "cch", "tmuh", "tmwh", "linkou",
}
_ORG_RE = re.compile(
    r"(三軍總醫院|臺大醫院|台大醫院|榮民總醫院|榮總|長庚醫院|長庚|馬偕醫院|馬偕|"
    r"奇美醫院|慈濟醫院|慈濟|松山分院|彰化基督教醫院|彰基|衛生福利部|草屯療養院|"
    r"林口長庚|國立臺灣大學醫學院附設醫院)"
)


def _redact_orgs(text: str) -> str:
    s = _ORG_RE.sub("", text or "")
    s = re.sub(r"\b(NTUH|TSGH|CGMH|VGHTPE|TMUH|TMWH)\b", "", s, flags=re.I)
    s = re.sub(r"W\d+病房", "病房", s)
    s = re.sub(r"[／/]{2,}", "／", s)
    s = re.sub(r"^[／/\s、，]+|[／/\s、，]+$", "", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip(" ／/")


def to_two_part(problem: str, approach: str) -> str:
    p = _clip_clause(_redact_orgs(problem)) or "說明未寫具體痛點"
    a = _clip_clause(_redact_orgs(approach)) or "依名稱整理，沒有可寫的做法"
    return f"問題：{p}；做法：{a}"


def normalize_two_part(text: str) -> str | None:
    """Accept only a compact 問題／做法 line. None if it should be retried."""
    t = (text or "").strip().strip("\"'「」『』")
    if not t:
        return None
    t = t.splitlines()[0].strip()
    t = re.sub(r"^(簡介|介紹|Intro|Summary)\s*[:：]\s*", "", t, flags=re.I)
    t = re.sub(r"^問題\s*[:：]\s*", "問題：", t)
    t = re.sub(r"[;；]\s*做法\s*[:：]\s*", "；做法：", t)
    if not t.startswith("問題：") or "；做法：" not in t:
        return None
    problem, approach = t[len("問題："):].split("；做法：", 1)
    problem = _clip_clause(problem, 42)
    approach = _clip_clause(approach, 42)
    if not problem or not approach:
        return None
    if problem.startswith(("如何", "怎麼", "怎樣", "為什麼", "為何")):
        return None
    blob = problem + approach
    if any(x in blob for x in ("本專案", "該工具", "這是一個", "公開專案", "公開倉庫", "主要語言", "旨在", "賦能", "無縫")):
        return None
    if re.search(r"(醫院|醫學中心|療養院|衛生福利部|診所)", blob):
        return None
    out = f"問題：{problem}；做法：{approach}"
    if _banned_intro(out):
        return None
    cjk = sum(1 for c in out if "\u4e00" <= c <= "\u9fff")
    if cjk < 8:
        return None
    if not is_two_part_intro(out):
        return None
    return out


def _blurb_to_two_part(blurb: str) -> str:
    """Turn a functional sentence into 問題／做法 without inventing a framework."""
    if is_two_part_intro(blurb):
        return blurb.strip()
    b = re.sub(r"\s+", " ", (blurb or "").strip()).rstrip("。")
    b = re.sub(r"（依倉庫名稱推斷；上游說明不足）", "依名稱，說明不足", b)
    b = re.sub(r"（依倉庫名稱推斷）", "依名稱推斷", b)
    b = re.sub(r"（說明文字不足）", "說明不足", b)
    b = re.sub(r"（說明不足）", "說明不足", b)
    b = re.sub(r"（無可讀說明）", "沒有可讀說明", b)
    thin = any(x in b for x in ("說明不足", "推斷", "無可讀", "沒有可讀", "無可單獨"))
    # description already states a pain after the function
    m = re.match(r"^(.{4,42}?)[，,]\s*(?:用來|以便|以)?(解決|減少|避免|省去|不用再)(.{2,40})$", b)
    if m and not thin:
        return to_two_part(m.group(2) + m.group(3), m.group(1))
    m = re.match(r"^(?:為了|用來)(?:解決)?(.{2,36})[，,]\s*(.{4,42})$", b)
    if m and not thin:
        return to_two_part(m.group(1), m.group(2))
    problem = "說明不足，看不出具體痛點" if thin else "說明未寫具體痛點"
    approach = b or "依名稱整理，沒有可寫的做法"
    return to_two_part(problem, approach)

# Marketing / bio adjectives — never emit as product substance
_MARKETING_WORDS = {
    "accessible", "extensible", "reliable", "safer", "smarter", "empower",
    "seamless", "seamlessly", "powerful", "modern", "lightweight", "easy",
    "simple", "comprehensive", "aims", "aiming", "designed", "established",
    "various", "several", "personal", "awesome", "best", "great", "smart",
    "safer", "smarter", "decisions", "decision", "teams", "team",
    "open", "source", "opensource", "free", "cool", "nice", "useful", "pure", "client", "client-side", "clientside",
}


def _name_tokens(name: str) -> list[str]:
    parts = re.split(r"[-_\s\.]+", (name or "").lower())
    return [p for p in parts if p and not p.isdigit()]


# Compact glossary: token/phrase → 繁中（用於名稱與英文說明改寫）
_TERM_ZH: dict[str, str] = {
    "ecg": "心電圖", "ekg": "心電圖", "eeg": "腦波", "emr": "電子病歷", "ehr": "電子病歷",
    "fhir": "FHIR", "dicom": "DICOM", "pacs": "PACS", "hl7": "HL7", "icd": "ICD",
    "nhi": "健保", "tfda": "食藥署", "pubmed": "PubMed", "llm": "語言模型",
    "rag": "RAG", "mcp": "MCP", "nlp": "自然語言處理", "ocr": "OCR", "tts": "語音合成",
    "stt": "語音辨識", "pdf": "PDF", "anki": "Anki", "dashboard": "儀表板",
    "annotator": "標註工具", "annotation": "標註", "calculator": "計算器", "calc": "計算器",
    "scheduler": "排班工具", "schedule": "排班", "quiz": "測驗", "exam": "考試",
    "tutorial": "教學", "course": "課程", "notes": "筆記", "note": "筆記",
    "bot": "聊天機器人", "chatbot": "聊天機器人", "extension": "擴充功能",
    "plugin": "外掛", "pipeline": "管線", "workflow": "工作流", "toolkit": "工具組",
    "library": "函式庫", "sdk": "SDK", "api": "API", "cli": "命令列工具",
    "web": "網頁", "app": "應用", "mobile": "行動", "android": "Android",
    "ios": "iOS", "docker": "Docker", "dataset": "資料集", "crawler": "爬蟲",
    "scraper": "爬蟲", "visuali": "視覺化", "visualization": "視覺化",
    "analysis": "分析", "analyzer": "分析工具", "monitor": "監測", "radar": "雷達",
    "digest": "摘要", "summary": "摘要", "search": "搜尋", "fetcher": "擷取器",
    "converter": "轉換器", "parser": "解析器", "helper": "小幫手", "assistant": "助手",
    "manager": "管理工具", "organizer": "整理工具", "tracker": "追蹤器",
    "portfolio": "作品集", "homepage": "個人網站", "blog": "部落格", "dotfiles": "開發環境設定",
    "medical": "醫療", "clinical": "臨床", "medicine": "醫學", "hospital": "醫院",
    "pharmacy": "藥學", "drug": "藥品", "medication": "藥物", "dose": "劑量",
    "cancer": "腫瘤", "oncology": "腫瘤", "breast": "乳癌", "cardiac": "心臟",
    "heart": "心臟", "renal": "腎臟", "kidney": "腎臟", "neuro": "神經",
    "ophthalm": "眼科", "dental": "牙科", "nursing": "護理", "emergency": "急診",
    "er": "急診", "icu": "加護病房", "triage": "檢傷", "radiology": "放射",
    "pathology": "病理", "surgery": "外科", "anesthesia": "麻醉", "anes": "麻醉",
    "rehab": "復健", "dialysis": "透析", "diabetes": "糖尿病", "cgm": "連續血糖",
    "guideline": "指引", "protocol": "流程", "reference": "速查參考",
    "lecture": "講義", "textbook": "教科書", "paper": "論文", "journal": "期刊",
    "literature": "文獻", "citation": "引用", "meta": "統合分析",
    "prediction": "預測", "classifier": "分類器", "segmentation": "分割",
    "detection": "偵測", "risk": "風險", "score": "評分", "assessment": "評估",
    "image": "影像", "imaging": "影像", "waveform": "波形", "signal": "訊號",
    "lab": "檢驗", "lis": "檢驗系統", "billing": "申報", "coding": "編碼",
    "taiwan": "台灣", "tw": "台灣", "zh": "繁中", "prompt": "提示詞",
    "agent": "代理人", "skill": "技能包", "claude": "Claude", "gemini": "Gemini",
    "openai": "OpenAI", "whisper": "Whisper", "line": "LINE",
    "discord": "Discord", "telegram": "Telegram", "obsidian": "Obsidian",
    "logseq": "Logseq", "heptabase": "Heptabase", "roam": "Roam",
    "shiny": "Shiny", "streamlit": "Streamlit", "flask": "Flask",
    "react": "React", "vue": "Vue", "nextjs": "Next.js",
    "transcribe": "轉錄", "dictation": "聽寫輸入", "voice": "語音",
    "podcast": "Podcast", "slide": "簡報", "presentation": "簡報",
    "learning": "學習", "education": "教育", "practice": "練習",
    "automation": "自動化", "auto": "自動", "sync": "同步",
    "highlight": "標記", "bookmark": "書籤", "vault": "知識庫",
    "knowledge": "知識", "research": "研究", "trial": "試驗",
    "patient": "病人", "clinic": "診所", "ward": "病房",
    "opd": "門診", "bedside": "床邊", "portable": "可攜式",
    "capillaroscopy": "微循環鏡", "capillary": "微循環",
    "ptosis": "眼瞼下垂", "burn": "燒燙傷", "ortho": "骨科",
    "rheum": "風濕", "hematology": "血液", "mds": "MDS",
    "ipssm": "IPSS-M", "gwas": "GWAS", "parkinson": "帕金森",
    "depression": "憂鬱", "adhd": "ADHD", "dementia": "失智",
    "lung": "肺", "ldct": "低劑量胸部電腦斷層", "ct": "電腦斷層",
    "mri": "磁振造影", "xray": "X光", "oct": "OCT",
    "pelvimetry": "骨盆測量", "figo": "FIGO", "pirads": "PI-RADS",
    "vanco": "Vancomycin", "auc": "AUC", "abg": "動脈血氣",
    "bmi": "BMI", "rom": "關節活動度", "emg": "肌電圖",
    "template": "範本",
    "demo": "示範", "test": "測試", "homework": "作業",
    "fork": "分支實驗", "mirror": "鏡像", "archive": "封存資料",
    "pharmacokinetics": "藥物動力學",
    "capillaroscopes": "微循環鏡",
    "intensive care": "重症加護",
    "classification": "分類",
    "visualisation": "視覺化",
    "ophthalmology": "眼科",
    "documentation": "文件",
    "reimbursement": "給付",
    "irresponsible": "隨興",
    "hemodialysis": "血液透析",
    "bibliography": "書目",
    "prescription": "處方",
    "notification": "通知",
    "telemedicine": "遠距醫療",
    "consultation": "會診",
    "multilingual": "多語",
    "openevidence": "OpenEvidence",
    "experimental": "實驗性",
        "statistical": "統計",
    "established": "既有",
    "bookmarklet": "書籤小工具",
    "annotations": "註記",
    "boilerplate": "樣板",
    "computation": "計算",
    "integration": "整合",
    "interaction": "交互作用",
    "traditional": "繁體",
    "statistics": "統計",
    "seamlessly": "無縫",
    "organizing": "整理",
    "annotating": "標註",
        "laboratory": "實驗室",
    "conversion": "轉換",
    "highlights": "畫線",
    "assignment": "作業",
    "orthopedic": "骨科",
    "pharmacist": "藥師",
    "vancomycin": "Vancomycin",
    "ultrasound": "超音波",
    "nephrology": "腎臟科",
    "guidelines": "指引",
    "flashcards": "閃卡",
    "embeddings": "嵌入",
    "typescript": "TypeScript",
    "javascript": "JavaScript",
    "systematic": "系統性",
    "references": "參考資料",
    "cheatsheet": "速查表",
    "simulation": "模擬",
    "evaluation": "評估",
    "extraction": "擷取",
    "criteria": "條件",
    "criterion": "條件",
    "rule": "規則",
    "rules": "規則",
    "ebook": "電子書",
    "e-book": "電子書",
    "reading": "閱讀",
    "syntax": "語法",
    "mlm": "MLM",
    "mlms": "MLM",
    "arden": "Arden",
    "information": "資訊",
    "visualization": "視覺化",
    "visualisation": "視覺化",
    "intensivecare": "重症加護",
    "playground": "實驗場",
    "chess": "棋類",
    "serial": "序列",
    "bridge": "橋接",
    "tokenbench": "權杖基準",
    "token": "權杖",
    "bench": "基準測試",
    "lazyswitch": "懶人切換",
    "webmcp": "Web MCP",
    "mcp": "MCP",
    "cql": "CQL",
    "edf": "EDF",
    "csv": "CSV",
    "matlab": "MATLAB",
    "perceptron": "感知機",
    "inaction": "實作",
    "mrd": "MRD",
    "measurement": "量測",
    "plot": "繪圖",
    "plot2d": "二維繪圖",
    "assets": "靜態資源",
    "shooter": "射擊遊戲",
    "bankart": "Bankart",
    "nervus": "神經",
    "barazou": "醫學專案合集",
    "icdmap": "ICD 對照",
    "iciv": "加護資訊視覺化",
    "downloader": "下載器",
    "deployment": "部署",
    "kubernetes": "Kubernetes",
    "deidentify": "去識別",
    "compliance": "合規",
    "newsletter": "電子報",
    "screenshot": "截圖",
    "telehealth": "遠距健康",
    "simplified": "簡體",
    "deprecated": "已棄用",
    "physician": "醫師",
    "intensive": "重症",
    "integrate": "整合",
    "workflows": "工作流",
    "expanding": "展開",
    "publisher": "出版商",
    "automatic": "自動",
    "analytics": "分析",
    "generator": "產生器",
    "templates": "範本",
    "fullstack": "全端",
    "real-time": "即時",
    "checklist": "檢核表",
    "flashcard": "閃卡",
    "messenger": "Messenger",
    "langchain": "LangChain",
    "embedding": "嵌入",
    "retrieval": "檢索",
    "utilities": "工具函式",
    "inference": "推論",
    "benchmark": "基準測試",
    "tokenizer": "斷詞器",
    "extractor": "擷取器",
    "migration": "遷移",
    "terraform": "Terraform",
    "anonymize": "去識別",
    "formulary": "處方集",
    "prescribe": "開立",
    "injection": "注射",
    "clipboard": "剪貼簿",
    "discharge": "出院",
    "admission": "入院",
    "computing": "運算",
    "raspberry": "Raspberry",
    "kaohsiung": "高雄",
    "bilingual": "雙語",
    "prototype": "原型",
    "autoreply": "自動回覆",
    "one-click": "一鍵",
    "one click": "一鍵",
    "floating": "浮動",
    "firmware": "韌體",
    "seamless": "無縫",
    "organise": "整理",
    "organize": "整理",
    "annotate": "標註",
    "acronyms": "縮寫",
    "markdown": "Markdown",
    "platform": "平台",
        "teaching": "教學",
    "analytic": "分析",
    "frontend": "前端",
    "realtime": "即時",
    "calendar": "日曆",
    "surgical": "外科",
    "glaucoma": "青光眼",
    "genetics": "遺傳學",
    "facebook": "Facebook",
    "semantic": "語意",
    "examples": "範例",
    "workshop": "工作坊",
    "bootcamp": "密集訓練",
    "simulate": "模擬",
    "training": "訓練",
    "evaluate": "評估",
    "uploader": "上傳工具",
    "security": "資安",
    "password": "密碼",
    "infusion": "輸注",
    "wearable": "穿戴式",
    "reminder": "提醒",
    "referral": "轉診",
    "transfer": "轉床",
    "hardware": "硬體",
    "japanese": "日文",
    "revising": "改寫",
    "progress": "病程",
    "grounded": "有依據",
    "upstream": "上游",
    "network": "網路",
    "display": "顯示",
    "methods": "方法",
        "records": "紀錄",
    "systems": "系統",
    "convert": "轉換",
    "raycast": "Raycast",
    "windows": "Windows",
    "powered": "驅動",
    "reports": "報告",
    "builder": "建置工具",
    "backend": "後端",
    "offline": "離線",
    "browser": "瀏覽器",
    "on-call": "值班",
    "failure": "衰竭",
    "glucose": "血糖",
    "medline": "MEDLINE",
    "pathway": "路徑",
    "keynote": "Keynote",
    "prompts": "提示詞",
    "fastapi": "FastAPI",
    "website": "網站",
    "example": "範例",
    "starter": "起始專案",
    "scripts": "腳本",
    "compute": "計算",
    "metrics": "指標",
    "parsing": "解析",
    "extract": "擷取",
    "migrate": "遷移",
    "testing": "測試",
    "privacy": "隱私",
    "consent": "同意",
    "allergy": "過敏",
    "capture": "擷取",
    "meeting": "會議",
    "consult": "會診",
    "signals": "訊號",
    "sensors": "感測器",
    "cluster": "叢集",
    "arduino": "Arduino",
    "english": "英文",
    "nihongo": "日文",
    "chinese": "中文",
    "reviser": "改寫器",
    "rewrite": "改寫",
    "configs": "設定",
    "concept": "概念",
    "totally": "完全",
    "t-embed": "T-Embed",
    "doctor": "醫師",
    "fields": "欄位",
    "images": "影像",
    "player": "播放器",
    "sketch": "程式草稿",
    "export": "匯出",
    "google": "Google",
    "chrome": "Chrome",
    "record": "病歷",
    "system": "系統",
    "graphs": "圖表",
    "reader": "閱讀器",
    "simple": "簡易",
        "health": "健康",
    "papers": "論文",
    "weekly": "每週",
    "report": "報告",
    "skills": "技能",
    "agents": "代理人",
    "server": "伺服器",
    "client": "客戶端",
    "online": "線上",
    "widget": "小工具",
    "roster": "名冊",
    "oncall": "值班",
    "trauma": "外傷",
    "retina": "視網膜",
    "dosage": "劑量",
    "genome": "基因體",
    "slides": "簡報",
    "speech": "語音",
    "notion": "Notion",
    "vector": "向量",
    "spider": "爬蟲",
    "django": "Django",
    "python": "Python",
    "golang": "Go",
    "kotlin": "Kotlin",
    "review": "回顧",
    "trials": "試驗",
    "models": "模型",
    "upload": "上傳",
    "backup": "備用",
    "deploy": "部署",
    "claims": "申報",
    "trough": "谷濃度",
    "kanban": "看板",
    "sticky": "便利貼",
    "webcam": "網路攝影機",
    "camera": "相機",
    "stream": "串流",
    "vitals": "生命徵象",
    "sensor": "感測器",
    "taipei": "台北",
    "charts": "病歷",
    "verify": "查核",
    "viewer": "檢視器",
    "editor": "編輯器",
    "vscode": "VS Code",
    "config": "設定",
    "themes": "主題",
    "puzzle": "解謎",
    "legacy": "舊版",
    "lilygo": "LilyGO",
    "cc1101": "CC1101",
    "local": "本地",
    "localfirst": "本地優先",
    "local-first": "本地優先",
    "oneclick": "一鍵",
    "local-first": "本地優先",
    "local first": "本地優先",
        "music": "音樂",
            "decks": "牌組",
    "daily": "每日",
    "batch": "批次",
    "addon": "外掛",
    "shift": "班表",
    "wound": "傷口",
    "nurse": "護理",
    "drugs": "藥品",
    "x-ray": "X光",
    "icd10": "ICD-10",
    "swift": "Swift",
    "about": "關於",
    "utils": "工具函式",
    "cheat": "速查",

    "sheet": "表",
    "model": "模型",
    "stats": "統計",
    "fetch": "擷取",
    "oauth": "OAuth",
    "login": "登入",
    "claim": "申報",
    "alert": "警示",
    "alarm": "警報",
    "audio": "音訊",
    "video": "視訊",
    "vital": "生命徵象",
    "esp32": "ESP32",
    "chart": "病歷",
    "shell": "shell",
    "theme": "主題",
    "fonts": "字型",
    "icons": "圖示",
    "games": "遊戲",
    "unity": "Unity",
    "godot": "Godot",
    "proof": "概念驗證",
    "bruce": "Bruce",
    "embed": "嵌入式",
        "data": "資料",
    "text": "文字",
    "drag": "拖曳",
    "docs": "文件",
        "html": "HTML",
    "deck": "牌組",
    "kobo": "Kobo",
        "maps": "地圖",
    "nccn": "NCCN",
    "ipss": "IPSS",
    "her2": "HER2",
    "next": "Next",
    "rust": "Rust",
    "stat": "統計",
    "unit": "單元",
    "auth": "驗證",
    "peak": "峰濃度",
    "oral": "口服",
    "feed": "訂閱源",
    "todo": "待辦",
    "snip": "截取",
    "grid": "網格",
    "soap": "SOAP",
    "nvim": "Neovim",
    "bash": "bash",
    "font": "字型",
    "icon": "圖示",
    "game": "遊戲",
    "cpu": "CPU",
    "doi": "DOI",
        "usb": "USB",
    "gui": "圖形介面",
    "map": "地圖",
    "idh": "透析中低血壓",
    "asr": "語音辨識",
    "gpt": "GPT",
    "etl": "ETL",
    "ner": "命名實體辨識",
    "k8s": "Kubernetes",
    "e2e": "端對端",
    "adr": "不良反應",
    "pwa": "PWA",
    "spa": "單頁應用",
    "ssr": "伺服器渲染",
    "rss": "RSS",
    "mic": "麥克風",
    "iot": "物聯網",
    "pcb": "電路板",
    "ide": "IDE",
    "vim": "Vim",
    "zsh": "zsh",
    "poc": "概念驗證",
    "wav": "WAV",
    "mp3": "MP3",
    "ml": "機器學習",
    "ai": "AI",
    "dl": "深度學習",
    "cv": "電腦視覺",
    "ci": "CI",
    "cd": "CD",
    "pk": "藥物動力學",
    "pd": "藥效學",
    "iv": "靜脈",
}


def _token_zh(tok: str) -> str | None:
    t = tok.lower()
    if t in _TERM_ZH:
        return _TERM_ZH[t]
    for k, v in _TERM_ZH.items():
        if len(k) >= 4 and k in t:
            return v
    return None


def _is_profile_repo(name: str, description: str | None, owner: str | None = None) -> bool:
    low = (name or "").lower()
    if owner and low == owner.lower():
        return True
    if low in {"profile", "readme", "about-me", "about_me"}:
        return True
    desc = (description or "").strip()
    if desc and _PROFILE_DESC_RE.search(desc):
        return True
    return False


def _is_personal_site(name: str, description: str | None = None) -> bool:
    low = (name or "").lower()
    if (
        low.endswith(".github.io")
        or low.endswith(".github.com")
        or low in {"homepage", "blog", "site", "website"}
    ):
        return True
    if "portfolio" in low:
        return True
    desc = (description or "").lower()
    if desc and re.search(r"\b(personal (website|site|blog|homepage)|my (blog|website|portfolio)|cv of)\b", desc):
        return True
    return False


def _format2_from_name(name: str) -> str | None:
    """edf2csv / pdf2anki style converters."""
    m = re.fullmatch(r"([A-Za-z][A-Za-z0-9+]{1,12})2([A-Za-z][A-Za-z0-9+]{1,12})", name or "")
    if not m:
        return None
    a = _token_zh(m.group(1)) or m.group(1).upper()
    b = _token_zh(m.group(2)) or m.group(2).upper()
    return f"將 {a} 轉成 {b} 的轉換工具。"


def _phrase_from_name(name: str, theme: str | None, owner: str | None = None) -> str:
    low = (name or "").lower()
    if _is_personal_site(name):
        return _EMPTY_SITE_LINE
    if low in {"dotfiles", "dot-files"} or "dotfile" in low:
        return _DOTFILES_LINE
    if _is_profile_repo(name, None, owner):
        return _EMPTY_PROFILE_LINE

    conv = _format2_from_name(name)
    if conv:
        return conv

    tokens = _name_tokens(name)
    noise = {
        "github", "io", "com", "org", "tw", "zh", "en", "v1", "v2", "v3", "v4",
        "main", "src", "app", "project", "repo", "test", "demo", "tmp", "new",
        "my", "the", "and", "for", "with", "from", "into", "of", "to", "in", "on",
        "by", "plus", "lite", "pro", "free", "open", "source", "dev", "lab",
        "practice", "homework", "assignment", "playground",
    }
    useful = [t for t in tokens if t not in noise and len(t) > 1]
    mapped: list[str] = []
    for t in useful[:8]:
        if t in _HOSP_TOKENS:
            continue
        zh = _token_zh(t)
        if zh and zh not in mapped and zh not in {"個人檔案", "說明檔", "作品集", "個人網站"}:
            mapped.append(zh)
        elif re.fullmatch(r"[a-z]{2,6}", t) and t in {"ai", "ml", "cv", "ui", "ux", "db", "qa", "mcp", "cql", "edf", "csv", "pdf", "nlp", "ocr", "rag"}:
            up = t.upper()
            if up not in mapped:
                mapped.append(up)
        elif re.fullmatch(r"[A-Za-z]{2,8}", t) and t.isupper():
            if t not in mapped:
                mapped.append(t)

    # suffix / kind hints from name
    kind = None
    kind_map = [
        (("dashboard",), "儀表板"),
        (("calculator", "calc"), "計算器"),
        (("chatbot", "bot"), "聊天機器人"),
        (("mcp",), "MCP 工具"),
        (("pipeline",), "資料管線"),
        (("annotator",), "標註工具"),
        (("downloader", "fetcher"), "下載／擷取工具"),
        (("radar",), "文獻／動態雷達"),
        (("digest",), "摘要工具"),
        (("extension", "plugin", "addon"), "擴充／外掛"),
        (("cli",), "命令列工具"),
        (("sdk", "api"), "API／SDK"),
        (("dataset", "data"), "資料集"),
        (("scraper", "crawler", "spider"), "爬蟲"),
        (("viewer",), "檢視器"),
        (("scheduler", "schedule", "shift"), "排班工具"),
        (("quiz", "exam", "flashcard", "anki"), "測驗／題庫工具"),
        (("notes", "note"), "筆記"),
        (("guideline", "protocol", "cheatsheet", "ref", "reference"), "速查／指引"),
    ]
    for keys, label in kind_map:
        if any(k in low for k in keys):
            kind = label
            break

    if mapped and kind:
        core = "／".join(mapped[:4])
        # Avoid「Web MCP相關的MCP 工具」style redundancy
        core_compact = re.sub(r"\s+", "", core)
        kind_compact = re.sub(r"\s+", "", kind)
        shared_markers = ("MCP", "API", "SDK", "CLI", "RAG", "LLM", "Bot", "機器人", "儀表板", "管線", "擴充", "外掛")
        overlap = any(
            tok and tok in kind_compact
            for tok in re.split(r"[／/]", core_compact)
            if len(tok) >= 3
        ) or kind_compact in core_compact or core_compact in kind_compact
        overlap = overlap or any(m in core_compact and m in kind_compact for m in shared_markers)
        if overlap:
            # Prefer the more specific mapped label when it already names the kind
            if len(core) >= len(kind):
                return f"{core}工具（依倉庫名稱推斷）。"
            return f"{kind}（依倉庫名稱推斷）。"
        return f"{core}相關的{kind}。"
    if kind and theme:
        return f"{theme}向的{kind}（依倉庫名稱推斷）。"
    if kind:
        return f"{kind}（依倉庫名稱推斷；上游說明不足）。"
    if mapped:
        core = "／".join(mapped[:4])
        if theme and theme not in {"個人網站／部落格", "開發環境／dotfiles", "遊戲／互動"}:
            return f"{core}相關工具；偏{theme}。"
        return f"{core}相關工具或實驗（說明不足）。"
    if theme and theme not in {"個人網站／部落格", "開發環境／dotfiles"}:
        return f"{theme}方向工具或實驗（說明文字不足）。"
    nice = re.sub(r"[-_]+", " ", name).strip()
    return f"{nice}：依名稱推斷的工具／實驗（無可讀說明）。"


def _english_to_zh_blurb(
    name: str, desc: str, theme: str | None, topics: list[str], owner: str | None = None
) -> str:
    """Heuristic English→繁中；輸出以中文為主的功能句，拒絕行銷堆砌。"""
    soft_themes = {
        "個人網站／部落格", "開發環境／dotfiles", "遊戲／互動",
        "生成式 AI／LLM", "機器學習", "自然語言處理", "函式庫／API",
        "DevOps／基礎建設", "行動應用", "資料視覺化／儀表板",
        "教育／學習資源", "簡報／教材",
    }

    def with_theme(body: str) -> str:
        body = body.rstrip("。")
        if theme and theme not in body and theme not in soft_themes:
            body = f"{body}；偏{theme}"
        return body + "。"

    raw = re.sub(r"\s+", " ", desc.strip())
    # Keep parenthetical expansion before stripping acronym lead-in
    paren = None
    m_paren = re.match(r"^([A-Z][A-Za-z0-9+-]{1,15})\s*\(([^)]{3,100})\)\s*", raw)
    if m_paren:
        paren = m_paren.group(2).strip()
        raw = raw[m_paren.end():].strip() or paren

    d = raw
    d = re.sub(r"^(this (project|repo|repository|tool|app|library|package)?\s*(is|provides)?\s*)", "", d, flags=re.I)
    d = re.sub(r"^(an?\s+)?open[- ]source\s+", "", d, flags=re.I)
    d = re.sub(r"^(a|an|the)\s+", "", d, flags=re.I)
    d = d.strip(" .")

    # Drop hollow first clause "... designed." / "... built."
    parts = re.split(r"(?<=[.!?])\s+", d, maxsplit=1)
    if len(parts) == 2 and re.search(
        r"\b(designed|built|created|developed|made)\.?$", parts[0], flags=re.I
    ):
        d = parts[1].strip()

    low = d.lower()
    if _is_profile_repo(name, desc, owner) or _PROFILE_DESC_RE.search(desc or ""):
        return _EMPTY_PROFILE_LINE
    if _is_personal_site(name, desc):
        return _EMPTY_SITE_LINE

    def zh_np(phrase: str) -> str:
        phrase = phrase.strip(" .,;:")
        if not phrase:
            return ""
        if _has_cjk(phrase):
            return _first_sentence(phrase, 80).rstrip("。")
        phrase_norm = re.sub(r"\b(local)\s*-\s*(first)\b", r"localfirst", phrase, flags=re.I)
        phrase_norm = re.sub(r"\b(one)\s*-\s*(click)\b", r"oneclick", phrase_norm, flags=re.I)
        phrase_norm = re.sub(r"\b(open)\s*-\s*(source)\b", " ", phrase_norm, flags=re.I)
        phrase_norm = re.sub(r"\b(intensive)\s*-?\s*(care)\b", r"intensivecare", phrase_norm, flags=re.I)
        phrase_norm = re.sub(r"\b(electronic)\s+(medical)\s+(record)s?\b", r"emr", phrase_norm, flags=re.I)
        phrase_norm = re.sub(r"\b(full)\s*-?\s*(text)\b", r"fulltext", phrase_norm, flags=re.I)
        phrase_norm = re.sub(r"\b(e)\s*-\s*(book)s?\b", r"ebook", phrase_norm, flags=re.I)
        words = re.split(r"[\s,/|+:;—–&]+", phrase_norm)
        skip = {
            "a", "an", "the", "and", "or", "of", "to", "for", "with", "on", "in",
            "from", "into", "via", "using", "based", "my", "your", "our", "its",
            "their", "this", "that", "all", "some", "any", "very", "is", "are",
            "be", "been", "was", "were", "as", "by", "at", "over", "under",
            "between", "than", "then", "also", "just", "only", "such", "like",
            "onto", "across", "within", "without", "per", "each", "other",
            "more", "most", "less", "least", "many", "much", "few",
            "designed", "provides", "provide", "allows", "allow", "helps", "help",
            "enables", "enable", "made", "make", "built", "create", "creates",
            "created", "develop", "developed", "implements", "implement",
            "including", "include", "includes", "etc", "eg", "ie", "vs",
            "python", "javascript", "typescript", "html", "css", "java", "ruby",
            "golang", "rust", "kotlin", "swift", "php", "scala", "r",
            "working", "work", "under", "over", "related", "towards", "toward",
            "about", "around", "across", "along", "among", "through",
            "aims", "aiming", "empower", "empowers", "empowering",
            "im", "ive", "ll", "re", "ve", "dont", "doesnt", "isnt", "wasnt",
            "so", "very", "really", "just", "too",
        } | _MARKETING_WORDS
        toks = [re.sub(r"[^A-Za-z0-9.+-]", "", w) for w in words]
        toks = [t for t in toks if t]
        mapped: list[str] = []
        i = 0
        while i < len(toks):
            if i + 1 < len(toks):
                big = (toks[i] + " " + toks[i + 1]).lower()
                if big in {"open source", "open-source", "full text", "real time"}:
                    i += 2
                    continue
                if big in _TERM_ZH:
                    z = _TERM_ZH[big]
                    if z not in mapped:
                        mapped.append(z)
                    i += 2
                    continue
            wl = toks[i].lower()
            if wl in skip or wl in _MARKETING_WORDS or wl in _HOSP_TOKENS:
                i += 1
                continue
            z = _token_zh(wl)
            if z:
                if z in {
                    "Python", "JavaScript", "TypeScript", "HTML", "CSS", "Java",
                    "Ruby", "Go", "Rust", "Kotlin", "Swift", "個人檔案", "說明檔",
                }:
                    i += 1
                    continue
                if z not in mapped:
                    mapped.append(z)
            elif re.fullmatch(r"[A-Z0-9][A-Z0-9.+-]{1,11}", toks[i]):
                ac = toks[i].upper() if toks[i].isalpha() else toks[i]
                if ac not in mapped:
                    mapped.append(ac)
            elif re.fullmatch(r"[A-Za-z][A-Za-z0-9.+-]{2,16}", toks[i]) and toks[i][0].isupper():
                # Proper nouns / product names
                if toks[i] not in mapped and len(mapped) < 5:
                    mapped.append(toks[i])
            i += 1
        dedup: list[str] = []
        for m in mapped:
            if not dedup or dedup[-1] != m:
                dedup.append(m)
        if len(dedup) >= 3 and dedup[0] == dedup[-1]:
            dedup = dedup[:-1]
        return "／".join(dedup[:5])

    kind_map = {
        "web app": "網頁應用", "web application": "網頁應用", "application": "應用程式",
        "platform": "平台", "library": "函式庫", "toolkit": "工具組", "package": "套件",
        "extension": "擴充功能", "plugin": "外掛", "cli": "命令列工具",
        "chatbot": "聊天機器人", "bot": "機器人", "dashboard": "儀表板",
        "calculator": "計算器", "pipeline": "管線", "framework": "框架",
        "tool": "工具", "system": "系統", "manager": "管理工具",
        "annotator": "標註工具", "fetcher": "擷取工具", "converter": "轉換器",
        "helper": "小幫手", "assistant": "助手", "tracker": "追蹤工具",
        "organizer": "整理工具", "scheduler": "排班工具", "radar": "雷達",
        "digest": "摘要", "note": "筆記", "notes": "筆記",
        "reference": "速查參考", "cheatsheet": "速查表", "player": "播放器",
    }

    # Prefer acronym expansion when that is the substance
    if paren and len(paren.split()) >= 2:
        paren_zh = zh_np(paren)
        if paren_zh and paren_zh.count("／") <= 4:
            # If remainder is only marketing (aims to empower...), ignore it
            rest_low = low
            if (
                not rest_low
                or re.match(r"^(is\s+)?(an?\s+)?(open[- ]source\s+)?platform\b", rest_low)
                or re.match(r"^aims?\s+to\b", rest_low)
                or re.match(r"^designed\b", rest_low)
                or len(zh_np(d)) <= 2
            ):
                kind = "平台" if "platform" in (desc or "").lower() else "工具"
                return _first_sentence(f"{paren_zh}{kind}。", 90)

    # "Kind: substance" (Chrome extension: drag-to-highlight ...)
    m_colon = re.match(
        r"^(.{3,60}?)\s*:\s*(.{8,120})$",
        d,
        flags=re.I,
    )
    if m_colon:
        left, right = m_colon.group(1).strip(), m_colon.group(2).strip()
        mk = re.search(
            r"(chrome extension|browser extension|extension|plugin|addon|dashboard|calculator|bot|cli|tool|app|library|toolkit)",
            left,
            flags=re.I,
        )
        if mk:
            kind = {
                "chrome extension": "Chrome 擴充功能",
                "browser extension": "瀏覽器擴充功能",
                "extension": "擴充功能",
                "plugin": "外掛",
                "addon": "外掛",
            }.get(mk.group(1).lower(), kind_map.get(mk.group(1).lower(), "工具"))
            # Keep a short concrete English action clause if glossary is thin
            right_zh = zh_np(right)
            short = _first_sentence(right, 55).rstrip(".…")
            # Prefer English action clause when glossary stack is noisy / bilingual junk
            noisy = (
                not right_zh
                or right_zh.count("／") >= 3
                or re.search(r"[A-Za-z]{3,}", right_zh)  # leftover English crumbs in zh stack
                or any(x in right_zh for x in ("Pure", "客戶端", "無縫", "易用"))
            )
            if short and not re.search(r"\b(accessible|extensible|reliable|empower)\b", short, flags=re.I):
                if noisy or (right_zh and len(short) > len(right_zh) + 10):
                    return _first_sentence(f"{kind}：{short}。", 90)
            if right_zh and right_zh.count("／") <= 3 and len(right_zh) >= 2 and not noisy:
                return _first_sentence(f"{kind}：{right_zh}。", 90)

    # "X for Y"
    m_for = re.match(r"^(.{3,55}?)\s+for\s+(?:working with\s+)?(.{3,80})$", d, flags=re.I)
    if m_for and not re.search(r"\b(designed|built|created|made|intended)\b", m_for.group(1), flags=re.I):
        left, right = m_for.group(1), m_for.group(2)
        if "(" not in left and len(left.split()) <= 8:
            left_zh = zh_np(left)
            right_zh = zh_np(right)
            mk = re.search(
                r"(web app(?:lication)?|extension|plugin|library|toolkit|platform|calculator|dashboard|bot|cli|tool|app|player|package)",
                left,
                flags=re.I,
            )
            kind_zh = kind_map.get(mk.group(1).lower(), "工具") if mk else None
            if kind_zh and right_zh:
                core = f"{kind_zh}，用於{right_zh}"
            elif left_zh and right_zh:
                core = f"{left_zh}，用於{right_zh}"
            elif right_zh:
                core = f"用於{right_zh}的工具"
            else:
                core = None
            if core:
                return _first_sentence(with_theme(core), 90)

    m = re.match(r"^collection of\s+(.+)$", d, flags=re.I)
    if m:
        return _first_sentence(f"彙整{zh_np(m.group(1)) or '相關'}專案的合集。", 90)

    if re.search(r"\breference\b", low):
        domain = theme or zh_np(name) or "臨床"
        return f"{domain}速查參考。"

    if re.match(r"^using\s+", low):
        raw_obj = re.sub(r"(?i)^using\s+", "", d).strip(" .")
        obj = zh_np(raw_obj)
        # Keep tech tokens like p5.js / three.js when glossary misses them
        if not obj or obj in {"實驗場", "練習"}:
            obj = raw_obj or name
        return _first_sentence(f"以 {obj} 做的互動／練習實驗。", 90)

    # verb patterns
    m_kind = re.search(
        r"\b(web app(?:lication)?|application|platform|library|toolkit|package|extension|plugin|cli|chatbot|bot|dashboard|calculator|pipeline|framework|tool|system|manager|annotator|fetcher|converter|helper|assistant|tracker|organizer|scheduler|radar|digest|notes?|reference|cheatsheet)\b",
        d,
        flags=re.I,
    )
    verb_patterns = [
        (r"\b(organiz(?:e|ing)|organise|organising)\b.{0,40}?(.+)$", "整理"),
        (r"\b(annotat(?:e|ing|ion)?)\b.{0,40}?(.+)$", "標註"),
        (r"\b(convert(?:s|ing)?|conversion)\b.{0,20}?(.+?)\s+to\s+(.+)$", "轉換"),
        (r"\b(fetch(?:es|ing)?|download(?:s|ing)?)\b.{0,40}?(.+)$", "擷取"),
        (r"\b(track(?:s|ing)?|monitor(?:s|ing)?)\b.{0,40}?(.+)$", "追蹤"),
        (r"\b(summar(?:y|ize|ise|izing)|digest)\b.{0,40}?(.+)$", "摘要"),
        (r"\b(search(?:es|ing)?)\b.{0,40}?(.+)$", "搜尋"),
        (r"\b(predict(?:s|ing|ion)?|risk score)\b.{0,40}?(.+)$", "預測"),
        (r"\b(schedul(?:e|er|ing))\b.{0,40}?(.+)$", "排班"),
        (r"\b(visuali[sz]e|visualization|visualisation)\b.{0,40}?(.+)$", "視覺化"),
        (r"\b(automat(?:e|ion|ing))\b.{0,40}?(.+)$", "自動化"),
        (r"\b(transcri(?:be|ption)|dictation)\b.{0,40}?(.+)$", "語音轉錄"),
        (r"\b(extract(?:s|ing|ion)?)\b.{0,40}?(.+)$", "擷取"),
        (r"\b(read(?:s|ing)?)\b.{0,40}?(.+)$", "閱讀"),
    ]
    for pat, verb_zh in verb_patterns:
        m = re.search(pat, d, flags=re.I)
        if not m:
            continue
        if verb_zh == "轉換" and m.lastindex and m.lastindex >= 3:
            a, b = zh_np(m.group(2)), zh_np(m.group(3))
            return _first_sentence(f"將{a}轉成{b}的工具。", 90)
        obj = zh_np(m.group(m.lastindex or 1)) if m.lastindex else ""
        kind = "工具"
        if m_kind:
            k = m_kind.group(1).lower()
            kind = kind_map.get(k) or kind_map.get(k.replace("application", "app")) or "工具"
        if obj:
            # Avoid「擷取條件／擷取的工具」duplication
            if verb_zh in obj:
                return _first_sentence(f"{obj}的{kind}。", 90)
            return _first_sentence(f"{verb_zh}{obj}的{kind}。", 90)
        return _first_sentence(with_theme(f"{verb_zh}{kind}"), 90)

    if m_kind:
        k = m_kind.group(1).lower()
        kind = kind_map.get(k) or kind_map.get("web app" if "web app" in k else k) or "工具"
        rest = re.sub(
            rf"^.{{0,60}}?\b{re.escape(m_kind.group(1))}\b(?:\s+designed)?[.!]?\s*",
            "",
            d,
            count=1,
            flags=re.I,
        ).strip(" .,")
        rest = re.sub(
            r"^(?:designed[.!]\s*)?(?:aims? to|aiming to|helps? to|helps?|to|for|that|which|designed for)\s+",
            "",
            rest,
            flags=re.I,
        )
        rest = re.sub(r"^empower\s+", "", rest, flags=re.I)
        # Drop trailing marketing clause tails
        rest = re.split(r"\b(?:with accessible|and reliable|for safer|and smarter)\b", rest, maxsplit=1, flags=re.I)[0]
        obj = zh_np(rest) if rest else ""
        if obj and not _banned_intro(obj + "的" + kind):
            if kind in obj or obj.endswith(kind):
                body = obj
            elif any(obj.endswith(s) for s in ("工具", "平台", "系統", "套件", "函式庫", "儀表板")):
                body = obj
            else:
                body = f"{obj}的{kind}"
            # Rescue buzzword stacks
            if _banned_intro(body + "。") or body.count("／") >= 4:
                rescue = zh_np(paren) if paren else zh_np(name)
                if rescue:
                    return _first_sentence(f"{rescue}{kind}。", 90)
                return _phrase_from_name(name, theme, owner)
            return _first_sentence(with_theme(body), 90)
        if paren:
            pz = zh_np(paren)
            if pz:
                return _first_sentence(f"{pz}{kind}。", 90)
        return _first_sentence(with_theme(kind), 90)

    if re.search(r"\b(llm|gpt|claude|gemini|ai)[-\s]?powered\b", low) or re.search(r"\b(langchain|rag)\b", low):
        obj = zh_np(re.sub(r"(?i).{0,30}(llm|gpt|claude|gemini|ai)[-\s]?powered\s*", "", d))
        if not obj:
            obj = zh_np(name) or "內容"
        return _first_sentence(f"以語言模型輔助{obj}的工具。", 90)

    # Noun-stack descriptions like "Rule based criteria extraction"
    desc_bits = zh_np(d)
    name_bits = zh_np(name.replace("-", " ").replace("_", " "))
    if desc_bits:
        if desc_bits.count("／") >= 1 and not desc_bits.endswith(("工具", "平台", "系統", "套件")):
            # Turn「規則／條件／擷取」into a verb phrase when last token is action-like
            parts = desc_bits.split("／")
            if parts[-1] in {"擷取", "轉換", "分析", "視覺化", "追蹤", "摘要", "搜尋", "預測", "標註", "整理"}:
                action = parts[-1]
                obj = "／".join(parts[:-1]) or name_bits or "資料"
                return _first_sentence(f"{obj}{action}工具。", 90)
            return _first_sentence(with_theme(f"{desc_bits}相關工具"), 90)
        return _first_sentence(with_theme(desc_bits if desc_bits.endswith(("工具", "平台", "系統")) else f"{desc_bits}相關工具"), 90)

    if name_bits:
        return _first_sentence(with_theme(f"{name_bits}相關工具"), 90)
    return _phrase_from_name(name, theme, owner)



def _polish_intro(text: str, name: str, theme: str | None, owner: str | None) -> str:
    """Collapse duplicated kind labels and rescue hollow lines."""
    t = (text or "").strip()
    if not t:
        return _phrase_from_name(name, theme, owner)
    # 「擴充功能擴充功能。」 / 「工具工具。」
    t = re.sub(
        r"(擴充功能|外掛|平台|工具組|工具|系統|套件|儀表板|聊天機器人|速查參考){2,}",
        r"\1",
        t,
    )
    t = re.sub(r"(的){2,}", "的", t)
    t = re.sub(
        r"^(擴充功能|外掛)相關的(擴充／外掛|擴充功能|外掛)。?$",
        r"\1（依倉庫名稱推斷；上游說明不足）。",
        t,
    )
    if _banned_intro(t) or re.fullmatch(r"(擴充功能|外掛|平台|工具|系統|套件)。?", t):
        return _phrase_from_name(name, theme, owner)
    if not t.endswith(("。", "！", "？", "…")):
        t += "。"
    return t[:95]


def synthesize_zh_intro(
    name: str,
    description: str | None,
    topics: list[str],
    language: str | None = None,  # kept for API compat; never emitted
    owner: str | None = None,
) -> str:
    """一行繁中：問題：…；做法：…。沒有痛點就不編框架。"""
    del language  # unused on purpose
    desc = (description or "").strip()
    theme = detect_theme(name, desc, topics)
    low = (name or "").lower()

    if _is_personal_site(name, desc):
        return _EMPTY_SITE_LINE
    if low in {"dotfiles", "dot-files"} or "dotfile" in low:
        return _DOTFILES_LINE
    if _is_profile_repo(name, desc, owner):
        return _EMPTY_PROFILE_LINE

    if desc and _has_cjk(desc):
        # Prefer concrete CJK; reject fluff / mostly-English marketing paste
        cand = _first_sentence(desc, 90)
        cjk_n = sum(1 for c in cand if "\u4e00" <= c <= "\u9fff")
        if not _banned_intro(cand) and cjk_n >= 4:
            return _blurb_to_two_part(_polish_intro(cand, name, theme, owner))

    if desc:
        intro = _english_to_zh_blurb(name, desc, theme, topics, owner=owner)
        return _blurb_to_two_part(_polish_intro(intro, name, theme, owner))

    return _blurb_to_two_part(_polish_intro(_phrase_from_name(name, theme, owner), name, theme, owner))


def _load_readme_cache(full_name: str) -> dict[str, Any] | None:
    """Load cached README record written by enrich_intros_from_readme.py."""
    if not full_name or "/" not in full_name:
        return None
    path = README_CACHE_DIR / f"{full_name.replace('/', '__')}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def choose_intro_zh(
    name: str,
    description: str | None,
    topics: list[str],
    language: str | None,
    previous: dict[str, Any] | None = None,
    owner: str | None = None,
    readme_hash: str | None = None,
) -> str:
    """Reuse prior good intro when metadata (+ optional README hash) unchanged.

    When a previous intro was README-derived (`intro_source` starts with
    `readme`) and `readme_hash` still matches, keep it even if description
    text drifted slightly. If the hash changed, fall back to synthesize so a
    later enrich pass can refresh from the new README.
    """
    if previous:
        old = (previous.get("intro_zh") or "").strip()
        prev_hash = previous.get("readme_hash")
        src = (previous.get("intro_source") or "").strip()
        meta_same = (
            previous.get("name") == name
            and (previous.get("description") or None) == (description or None)
            and list(previous.get("topics") or []) == list(topics or [])
        )
        keepable = is_two_part_intro(old) and not _banned_intro(old)
        readme_same = (
            readme_hash
            and prev_hash
            and readme_hash == prev_hash
            and keepable
            and (src.startswith("readme") or not previous.get("intro_thin"))
        )
        if readme_same:
            return old
        if meta_same and keepable:
            # Keep prior two-part intro unless hash explicitly changed
            if prev_hash and readme_hash and prev_hash != readme_hash:
                pass  # fall through to synthesize; enrich will rewrite
            else:
                return old
    return synthesize_zh_intro(name, description, topics, language, owner=owner)





def fetch_all_non_fork_repos(
    login: str,
    token: str | None,
    previous_by_name: dict[str, dict[str, Any]] | None = None,
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
            prev = (previous_by_name or {}).get(name)
            full_name = repo.get("full_name") or f"{login}/{name}"
            cached_readme = _load_readme_cache(full_name)
            readme_hash = (cached_readme or {}).get("readme_hash") or (prev or {}).get("readme_hash")
            intro_zh = choose_intro_zh(
                name,
                description,
                topics,
                language,
                prev,
                owner=login,
                readme_hash=readme_hash,
            )
            row = {
                "name": name,
                "full_name": full_name,
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
            if readme_hash:
                row["readme_hash"] = readme_hash
            kept_prev = bool(prev) and intro_zh == (prev.get("intro_zh") or "").strip()
            if kept_prev:
                if prev.get("intro_source"):
                    row["intro_source"] = prev.get("intro_source")
                if "intro_thin" in prev:
                    row["intro_thin"] = bool(prev.get("intro_thin"))
            else:
                row["intro_source"] = "synthesize"
                row["intro_thin"] = True
            repos.append(row)
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

SOFT_BLURB_THEMES = SOFT_NOISE_THEMES | {
    "生成式 AI／LLM",
    "機器學習",
    "自然語言處理",
    "函式庫／API",
    "DevOps／基礎建設",
    "行動應用",
    "資料視覺化／儀表板",
    "教育／學習資源",
    "簡報／教材",
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

    # Preserve curated intros across weekly runs when metadata unchanged
    prev_accounts: dict[str, dict[str, dict[str, Any]]] = {}
    for path in (DATA_DIR / "repos.json", CACHE_DIR / "repos.json"):
        if not path.is_file():
            continue
        try:
            prev_db = json.loads(path.read_text(encoding="utf-8"))
            for acc in prev_db.get("accounts") or []:
                login_key = (acc.get("login") or "").lower()
                prev_accounts[login_key] = {
                    (r.get("name") or ""): r for r in (acc.get("repos") or [])
                }
            break
        except (OSError, json.JSONDecodeError):
            continue

    for i, login in enumerate(logins):
        print(f"[{i + 1}/{len(logins)}] @{login}", flush=True)
        err = None
        all_repos: list[dict[str, Any]] = []
        try:
            prev_map = prev_accounts.get(login.lower())
            all_repos, err = fetch_all_non_fork_repos(login, token, prev_map)
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
                        **({"intro_source": r.get("intro_source")} if r.get("intro_source") else {}),
                        **({"intro_thin": bool(r.get("intro_thin"))} if "intro_thin" in r else {}),
                        **({"readme_hash": r.get("readme_hash")} if r.get("readme_hash") else {}),
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



_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def load_nongithub() -> dict[str, Any] | None:
    """Static public products for clinicians without a personal forge login.

    Not produced by the GitHub activity scan. Missing file is fine.
    """
    path = DATA_DIR / "nongithub.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not data.get("people"):
        return None
    return data


def _md_inline_to_html(src: str) -> str:
    """Escape text, then restore only markdown links."""
    parts: list[str] = []
    last = 0
    for m in _MD_LINK.finditer(src):
        parts.append(html.escape(src[last:m.start()]))
        label = html.escape(m.group(1))
        url = html.escape(m.group(2), quote=True)
        parts.append(f'<a href="{url}">{label}</a>')
        last = m.end()
    parts.append(html.escape(src[last:]))
    return "".join(parts)


def render_nongithub_md(data: dict[str, Any]) -> str:
    lines = [
        f"## {data.get('title') or '無公開 GitHub，但有公開作品'}",
        "",
        data.get("note") or "",
        "",
    ]
    for person in data.get("people") or []:
        lines.append(f"### {person.get('name') or ''}")
        lines.append("")
        if person.get("identity"):
            lines.append(person["identity"])
            lines.append("")
        for w in person.get("works") or []:
            lines.append(f"- [{w['name']}]({w['url']}) — {w['blurb']}")
        if person.get("pending"):
            lines.append(f"- 待補：{person['pending']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_nongithub_html(data: dict[str, Any]) -> str:
    title = html.escape(data.get("title") or "無公開 GitHub，但有公開作品")
    note = _md_inline_to_html(data.get("note") or "")
    chunks = [
        '<section id="nongithub" class="card">',
        f"<h2>{title}</h2>",
        f'<p class="meta">{note}</p>',
    ]
    for person in data.get("people") or []:
        chunks.append(f"<h3>{html.escape(person.get('name') or '')}</h3>")
        if person.get("identity"):
            chunks.append(f"<p>{_md_inline_to_html(person['identity'])}</p>")
        chunks.append('<ul class="highlights">')
        for w in person.get("works") or []:
            name = html.escape(w.get("name") or "")
            url = html.escape(w.get("url") or "", quote=True)
            blurb = html.escape(w.get("blurb") or "")
            chunks.append(f'<li><a href="{url}">{name}</a> — {blurb}</li>')
        if person.get("pending"):
            chunks.append(f"<li>待補：{html.escape(person['pending'])}</li>")
        chunks.append("</ul>")
    chunks.append("</section>")
    return "\n".join(chunks)


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

    nongithub = load_nongithub()
    if nongithub:
        lines.append("---")
        lines.append("")
        lines.append(render_nongithub_md(nongithub).rstrip())
        lines.append("")
    lines.extend(
        [
            "---",
            "",
            "## 專案資料庫（公開非 fork）",
            "",
            "依作者分組；每位作者帳號只出現一次，其下為緊湊「倉庫名 — 問題；做法」。",
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
        "# 專案資料庫",
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
        "<p>本週值得追蹤 · 依作者分組的專案資料庫</p>",
        f'<p class="meta" style="opacity:.9">產生時間：{gen}</p>',
        "</div></header>",
        '<div class="wrap">',
        '<nav class="toc">',
        '<a href="#weekly">本週值得追蹤</a>',
        '<a href="#nongithub">無公開 GitHub 的公開作品</a>',
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

    nongithub = load_nongithub()
    if nongithub:
        parts.append(render_nongithub_html(nongithub))

    parts.extend(
        [
            '<section id="database" class="card">',
            "<h2>專案資料庫</h2>",
            f'<p class="meta">依<strong>作者分組一次</strong>：帳號標題下為緊湊「倉庫名 — 問題；做法」。'
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
        rewrite_intros = (
            "--rewrite-intros" in sys.argv
            or os.environ.get("REWRITE_INTROS", "").lower() in {"1", "true", "yes"}
        )
        if rewrite_intros:
            force_intros = (
                "--force-intros" in sys.argv
                or os.environ.get("FORCE_INTROS", "").lower() in {"1", "true", "yes"}
            )
            n = 0
            kept = 0
            for acc in repo_db.get("accounts") or []:
                for r in acc.get("repos") or []:
                    r["theme"] = detect_theme(
                        r.get("name") or "",
                        r.get("description") or "",
                        list(r.get("topics") or []),
                    )
                    old = (r.get("intro_zh") or "").strip()
                    # Keep an already two-part intro unless explicitly forced
                    if (
                        not force_intros
                        and is_two_part_intro(old)
                        and not _banned_intro(old)
                    ):
                        kept += 1
                        n += 1
                        continue
                    r["intro_zh"] = synthesize_zh_intro(
                        r.get("name") or "",
                        r.get("description"),
                        list(r.get("topics") or []),
                        r.get("language"),
                        owner=acc.get("login"),
                    )
                    r["intro_source"] = "synthesize"
                    r["intro_thin"] = True
                    n += 1
            # keep digest window intros in sync when present
            by_full = {
                f"{acc['login']}/{r['name']}": r.get("intro_zh")
                for acc in repo_db.get("accounts") or []
                for r in acc.get("repos") or []
            }
            for entry in digest.get("entries") or []:
                login = entry.get("login") or ""
                for r in entry.get("repos") or []:
                    key = f"{login}/{r.get('name')}"
                    if key in by_full:
                        r["intro_zh"] = by_full[key]
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
