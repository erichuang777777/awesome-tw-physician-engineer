#!/usr/bin/env python3
"""產生 docs/analysis.html：名冊整體分析頁（繁中）。讀 docs/data/repos.json，週報重建後自動呼叫。"""
from __future__ import annotations
import collections, html, json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

CLUSTERS = [
    ("影像／訊號 AI（DICOM、ECG、病理）", r"影像|image|\bCT\b|MRI|X-?ray|ultrasound|超音波|ECG|心電|EEG|DICOM|segment|病理|眼底|pathology"),
    ("LLM／RAG／MCP／Agent／skill", r"LLM|RAG|MCP|agent|GPT|Claude|Gemini|Ollama|大型語言|prompt|skill"),
    ("研究資料／統計／IRB", r"統計|研究|REDCap|資料庫|database|分析|survival|cohort|MIMIC|IRB"),
    ("考試／教學（題庫、Anki）", r"考試|國考|專科|題庫|quiz|教學|flashcard|anki|OSCE|FSRS"),
    ("臨床計算機／決策／指引", r"計算|calculator|score|評分|劑量|dose|決策|guideline|指引|protocol"),
    ("藥物（劑量、交互作用、藥品查詢）", r"藥|drug|medication|pharm"),
    ("文獻追蹤（PubMed、期刊）", r"文獻|PubMed|paper|期刊|arXiv|journal|digest"),
    ("病歷書寫／語音轉文字／SOAP", r"SOAP|病歷書寫|病歷撰寫|progress note|出院摘要|語音|錄音|transcri|whisper|scribe|交班"),
    ("HIS／瀏覽器擴充自動化", r"HIS|userscript|Tampermonkey|油猴|擴充|extension|AutoHotKey|雲端藥歷"),
    ("健保／ICD／給付", r"健保|NHI|給付|申報|ICD|DRG|核刪"),
    ("護理／復健／透析", r"護理|復健|rehab|nurs|透析|dialysis"),
    ("排班／行政", r"排班|班表|值班|scheduler|請假"),
    ("衛教／病人端", r"衛教|病人教育|patient education|病友|家屬"),
    ("去識別／隱私", r"去識別|deid|de-ident|\bPHI\b|\bPII\b|個資|匿名|scrub|airlock"),
]
EXCL = r"遊戲|game|股票|trading|stock|作業|homework|crypto|個人頁面|portfolio|dotfiles"

DUPES = [
    ("排班／值班", r"排班|值班|duty|scheduler"),
    ("語音轉文字／錄音摘要", r"語音|whisper|transcri|\bASR\b|錄音"),
    ("PubMed／期刊摘要推播", r"pubmed|期刊|journal|paper-?radar|文獻追蹤"),
    ("題庫／考古題", r"題庫|考古|mcq|exam|國考"),
    ("藥物劑量／換算計算機", r"劑量|dose|換算|drip"),
    ("Anki／間隔重複", r"anki|FSRS|間隔重複|flashcard"),
    ("ICD-10 查詢", r"ICD"),
    ("eGFR／腎功能計算", r"eGFR|腎功能|CrCl|ckd-calc"),
    ("健保雲端藥歷讀取", r"雲端藥歷|健保雲端|NHITW"),
    ("OpenEvidence 串接", r"openevidence"),
]

CURATED = [
    [
        "臨床計算機與床邊工具",
        [
            "liangRXdev/opioid-converter-zh",
            "liangRXdev/pill-detective-tw",
            "lantus123/nicu-drip-calc",
            "periop-tools/pump-calc",
            "jeff830621/ckd-calculator",
            "philia81301-commits/osteoporosis-clinic",
            "htlin222/milkCalc",
            "agoodbear/cpr-rate-coach",
            "soanseng/surveymind.tw",
            "keanu77/exercise-prescription-recommendation"
        ]
    ],
    [
        "健保、ICD 與藥品資料",
        [
            "TLAN1012/NHI-Rules",
            "copper0722/nhi-rule-history",
            "copper0722/tw-new-drug-signals",
            "liangRXdev/TFDA-drug-info-search",
            "rickyrickyrickyyu/icd10-tw",
            "odafeng/icd10-finder",
            "zinojeng/diabetes_P4P"
        ]
    ],
    [
        "HIS／健保雲端旁路工具",
        [
            "yanchen0902/NHITW_preop_checker_2",
            "jeff830621/NHITW_clinic_reader",
            "TLAN1012/SOAPIME",
            "Twb06/NTUH-helper",
            "tsaiid/ahk-smartwonder",
            "htlin222/oe-extension"
        ]
    ],
    [
        "影像與報告",
        [
            "ykuo2/dicom2jpg",
            "tsaiid/libera-bmd",
            "u9401066/dicom-overlay-agent",
            "tcs211/AI_EEEG_REPORT"
        ]
    ],
    [
        "文獻追蹤與研究",
        [
            "drpwchen/paper-radar",
            "drpwchen/paper-fetch",
            "agoodbear/em-pulse-tw",
            "odafeng/MedFeedJournalTracker",
            "htlin222/meta-pipe",
            "htlin222/irb-in-hurry",
            "htlin222/breast-cancer-uptodate",
            "Brritany/MLstatkit"
        ]
    ],
    [
        "LLM／MCP／skill",
        [
            "htlin222/openevidence-mcp",
            "u9401066/pubmed-search-mcp",
            "u9401066/medical-calc-mcp",
            "u9401066/pharmacy-mcp",
            "u9401066/zotero-keeper",
            "htlin222/cps-skills",
            "htlin222/nccn-skill",
            "drpwchen/openevidence-tools"
        ]
    ],
    [
        "去識別與隱私",
        [
            "drpwchen/chart-scrub",
            "galencky/local_llm",
            "liangRXdev/phi-guard-tw",
            "u9401066/medical-deidentification"
        ]
    ],
    [
        "排班、教學與語音",
        [
            "tsaiid/random-duty",
            "ww8chw/nurse-scheduler",
            "drpwchen/exam-practice",
            "htlin222/mcq-bank",
            "drpwchen/lecture-to-notes",
            "drpwchen/asr-benchmark",
            "soanseng/voxpen-desktop"
        ]
    ],
    [
        "衛教與病人端",
        [
            "galencky/MedEdBot",
            "voho0000/dm-education-workbench",
            "odafeng/hemorrhoids-postop",
            "Denovortho/open-irehab-brief-schema"
        ]
    ]
]
HOMEPAGES = {
    "liangRXdev/opioid-converter-zh": "https://liangrxdev.github.io/opioid-converter-zh/",
    "liangRXdev/pill-detective-tw": "https://liangrxdev.github.io/pill-detective-tw/",
    "periop-tools/pump-calc": "https://periop-tools.github.io/pump-calc/",
    "soanseng/surveymind.tw": "https://surveymind.tw",
    "keanu77/exercise-prescription-recommendation": "https://exerciseprescription.sportsmedicine.tw/",
    "liangRXdev/TFDA-drug-info-search": "https://liangrxdev.github.io/TFDA-drug-info-search/",
    "u9401066/dicom-overlay-agent": "https://u9401066.github.io/dicom-overlay-agent/",
    "drpwchen/paper-radar": "https://drpwchen.com/posts/paper-radar/",
    "drpwchen/paper-fetch": "https://drpwchen.com/posts/paper-fetch/",
    "htlin222/irb-in-hurry": "https://htlin222.github.io/irb-in-hurry/",
    "u9401066/pubmed-search-mcp": "https://u9401066.github.io/pubmed-search-mcp/",
    "u9401066/zotero-keeper": "https://u9401066.github.io/zotero-keeper/",
    "drpwchen/openevidence-tools": "https://drpwchen.com/posts/my-ai-toolbox/",
    "drpwchen/chart-scrub": "https://drpwchen.github.io/chart-scrub/",
    "drpwchen/exam-practice": "https://drpwchen.com/posts/exam-practice-platform/",
    "htlin222/mcq-bank": "https://htlin222.github.io/mcq-bank/",
    "drpwchen/lecture-to-notes": "https://drpwchen.com/posts/lecture-to-notes/",
    "drpwchen/asr-benchmark": "https://drpwchen.com/posts/my-data-my-benchmark/",
    "soanseng/voxpen-desktop": "https://voxpen.app/",
    "odafeng/hemorrhoids-postop": "https://prototype-zeta-black.vercel.app"
}

FALLBACK_INTRO = {
    "Denovortho/open-irehab-brief-schema": "問題：各院所診前問卷格式不一；做法：定義開放的 JSON 格式，附驗證器與各科範例",
    "galencky/local_llm": "問題：病歷送雲端 LLM 有個資風險；做法：本機模型去識別、換代號、送雲端、回來還原（Airlock）",
}

STANDOUTS = [
    ("htlin222/openevidence-mcp", "沒有 API 就借用自己已登入的瀏覽器分頁當驗證中繼，再包成 MCP，Claude Code、Cursor 都能查 OpenEvidence。"),
    ("drpwchen/paper-radar", "讀論文拆成「找（paper-radar）→抓全文（paper-fetch，先開放取用再出版社 API）→評讀（paper-review-and-digest）」三段；依個人興趣排序，私人頁放在 Cloudflare Access 後。"),
    ("drpwchen/asr-benchmark", "沒有人工逐字稿也能評估語音辨識：用專科文獻建字典，同時量「吐出的字是否真的存在」與「抓到多少真實詞彙」，防幻覺也防漏聽。"),
    ("copper0722/nhi-rule-history", "把健保藥品給付條文當資料工程：年度整編檔視為逐條觀測再推斷新增／改寫／刪除；PostgreSQL 唯一權威、輸出 JSONL／SQLite；先做驗證器再放 LLM 量產。"),
    ("copper0722/tw-new-drug-signals", "拆解 TFDA 許可證上三種「新藥」訊號的定義與失效模式，說明 ATC 為何不能判斷新舊——公開資料欄位語意考據的範本。"),
    ("zinojeng/diabetes_P4P", "P14／P7 收案規則寫成可測試引擎，每條附出處、不確定處寫明保守預設；只在真正缺項時通知醫師，其餘完全靜默。"),
    ("yanchen0902/NHITW_preop_checker_2", "Chrome 擴充在健保雲端藥歷用 ATC5＋學名字典找術前高風險藥，一鍵印去識別評估單；全本機、有 CI。"),
    ("liangRXdev/phi-guard-tw", "掛在 Claude Code hook，模型讀到前強制遮身分證／居留證／病歷號；出錯一律擋下，並防遮罩值被寫回原檔；README 誠實寫「防呆不是保證」。"),
    ("drpwchen/chart-scrub", "規則遮蔽＋再掃殘留、命中就擋；搭配 galencky/local_llm（Airlock）的本機換代號→送雲端→回來還原，就是 PHI 閘道的現成參考。"),
    ("agoodbear/cpr-rate-coach", "單一 HTML：MediaPipe 只框上半身，框內算垂直光流，再用自相關求按壓頻率；信心不足寧可不報，影像不上傳。"),
    ("htlin222/meta-pipe", "Claude Code 驅動九階段統合分析，從研究問題到可投稿手稿；同作者 irb-in-hurry 用 YAML 產 IRB 文件。"),
    ("TLAN1012/SOAPIME", "Windows 常駐熱鍵：在 HIS 選取文字就轉成英文 SOAP＋ICD 建議貼回原處，完全不用跟 HIS 串接。"),
    ("u9401066/dicom-overlay-agent", "監看既有 DICOM 看片軟體，把 AI 結果疊在畫面上，不必改 PACS。"),
]

PATTERNS = [
    "<strong>繞過 HIS、不改 HIS</strong>：瀏覽器擴充、熱鍵常駐、AutoHotKey、畫面疊圖。",
    "<strong>純前端單檔／靜態網站</strong>（GitHub Pages、Cloudflare、Vercel）：資料不離開瀏覽器，順便避開資安審查。",
    "<strong>Claude skill 與 MCP 爆量</strong>：把指引、計算機、文獻庫包成 AI 工具。",
    "<strong>政府開放資料整理成 JSON 快取</strong>：健保、TFDA、ICD。",
    "<strong>隱私設計</strong>：本機模型、遮罩出錯一律擋下（fail-closed）。",
]
GAPS = [
    "<strong>沒有共用的台灣基礎元件</strong>：健保雲端解析、民國日期、身分證規則、ICD 對照各寫一份。",
    "<strong>去識別缺評估資料</strong>：沒有公開的台灣病歷 PHI 標註資料集或 benchmark。",
    "<strong>病房流程空白</strong>：交班、出院準備、會診追蹤、危險值追蹤幾乎沒人做。",
    "<strong>病人端少</strong>：多語衛教、長照銜接、用藥順從。",
    "<strong>品質與授權</strong>：多數 repo 沒有測試、臨床驗證或授權說明。",
    "<strong>其他職類</strong>：護理、藥師工作流程工具偏少。",
]


def esc(s): return html.escape(s or "")


def build() -> str:
    db = json.loads((DOCS / "data" / "repos.json").read_text(encoding="utf-8"))
    repos = {}
    for a in db.get("accounts") or []:
        for r in a.get("repos") or []:
            repos[r["full_name"]] = (a["login"], r)
    cnt = collections.Counter(); auth = collections.defaultdict(set); kept = 0
    dcnt = collections.Counter(); dauth = collections.defaultdict(set)
    for fn, (login, r) in repos.items():
        t = f"{r['name']} {r.get('description') or ''} {r.get('intro_zh') or ''} {' '.join(r.get('topics') or [])}"
        for n, p in DUPES:
            if re.search(p, t, re.I): dcnt[n] += 1; dauth[n].add(login)
        if re.search(EXCL, t, re.I) or (r.get("intro_thin") and not r.get("description")): continue
        hit = [n for n, p in CLUSTERS if re.search(p, t, 0 if n.startswith("HIS") else re.I)]
        if not hit: continue
        kept += 1
        for n in hit: cnt[n] += 1; auth[n].add(login)
    total = len(repos); accounts = len(db.get("accounts") or [])

    def link(fn):
        return f'<a href="https://github.com/{esc(fn)}">{esc(fn)}</a>'

    def intro(fn):
        r = repos.get(fn, (None, {}))[1]
        return esc(r.get("intro_zh") or r.get("description") or FALLBACK_INTRO.get(fn, ""))

    out = []
    rows = "".join(f"<tr><td>{esc(n)}</td><td>{cnt[n]}</td><td>{len(auth[n])}</td></tr>" for n, _ in sorted(CLUSTERS, key=lambda x: -cnt[x[0]]))
    out.append(f'<section class="card" id="direction"><h2>一、大家努力的方向</h2><p class="meta">共 {accounts} 個帳號、{total} 個 repo；排除遊戲、投資、作業、個人站與內容空白者後，約 {kept} 個臨床相關。</p>'
               f'<div class="tbl"><table><thead><tr><th>類別</th><th>repo</th><th>作者</th></tr></thead><tbody>{rows}</tbody></table></div>'
               '<p class="soft-note">數字為關鍵字自動分群的粗估，一個 repo 可同時算入多群；影像類關鍵字較寬，會偏高。</p></section>')
    drows = "".join(f"<tr><td>{esc(n)}</td><td>{dcnt[n]}</td><td>{len(dauth[n])}</td></tr>" for n, _ in sorted(DUPES, key=lambda x: -len(dauth[x[0]])))
    out.append(f'<section class="card" id="dupes"><h2>二、重複造輪子最多的題目</h2><p class="meta">想做之前先看看別人做好的，或一起合作。</p><div class="tbl"><table><thead><tr><th>題目</th><th>repo</th><th>作者</th></tr></thead><tbody>{drows}</tbody></table></div></section>')
    parts = ['<section class="card" id="ready"><h2>三、已經做好、可以直接用的成果</h2><p class="meta">依類別精選；「線上版」可直接打開使用。收錄不代表背書，請自行評估資料安全與院內規範。</p>']
    for grp, fns in CURATED:
        parts.append(f"<h3>{esc(grp)}</h3><ul class=\"items\">")
        for fn in fns:
            hp = HOMEPAGES.get(fn)
            live = f' ・<a href="{esc(hp)}">線上版</a>' if hp else ""
            parts.append(f"<li>{link(fn)}{live}<br><span class=\"intro\">{intro(fn)}</span></li>")
        parts.append("</ul>")
    parts.append("</section>")
    out.append("".join(parts))
    out.append('<section class="card" id="clever"><h2>四、值得細讀的精妙做法</h2><ol class="items">' +
               "".join(f"<li>{link(fn)}<br><span class=\"intro\">{esc(why)}</span></li>" for fn, why in STANDOUTS) + "</ol></section>")
    out.append('<section class="card" id="patterns"><h2>五、共同模式與缺口</h2><h3>大家常用的做法</h3><ul class="items">' +
               "".join(f"<li>{p}</li>" for p in PATTERNS) + '</ul><h3>還沒人在做（或做得少）</h3><ul class="items">' +
               "".join(f"<li>{g}</li>" for g in GAPS) + "</ul></section>")
    gen = esc(db.get("generated_at_taipei") or db.get("generated_at") or "")
    return f'''<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>整體分析 — 台灣臨床醫事工程師</title>
<meta name="description" content="名冊 repo 整體分析：大家在解決什麼問題、重複造輪子、可直接用的成果與精妙做法"/>
<style>
:root{{--bg:#f4f6f8;--card:#fff;--text:#1f2328;--muted:#656d76;--accent:#0969da;--border:#d0d7de}}
*{{box-sizing:border-box}}
body{{margin:0;font-family:"Noto Sans TC",system-ui,-apple-system,"Segoe UI",Roboto,"PingFang TC","Microsoft JhengHei",sans-serif;background:var(--bg);color:var(--text);line-height:1.55;font-size:16px}}
a{{color:var(--accent);text-decoration:none;word-break:break-word}}a:hover{{text-decoration:underline}}
.wrap{{max-width:900px;margin:0 auto;padding:1rem .85rem 2.5rem}}
header.hero{{background:linear-gradient(135deg,#1f6feb 0%,#054da7 100%);color:#fff;padding:1.25rem 0 1.1rem;margin-bottom:1rem}}
header.hero .wrap{{padding-top:0;padding-bottom:0}}
header.hero h1{{margin:0 0 .3rem;font-size:1.45rem}}header.hero p{{margin:.15rem 0;opacity:.95;font-size:.95rem}}
nav.toc{{display:flex;flex-wrap:wrap;gap:.4rem;margin:.75rem 0}}
nav.toc a{{background:var(--card);border:1px solid var(--border);border-radius:999px;padding:.2rem .65rem;font-size:.82rem}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:.85rem .9rem;margin:0 0 .85rem}}
.card h2{{margin:0 0 .45rem;font-size:1.15rem}}.card h3{{font-size:1rem;margin:.9rem 0 .3rem}}
.meta,.soft-note{{color:var(--muted);font-size:.86rem}}
.tbl{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:.92rem}}
th,td{{border-bottom:1px solid var(--border);padding:.35rem .4rem;text-align:left}}td:nth-child(n+2),th:nth-child(n+2){{text-align:right;white-space:nowrap}}
ul.items,ol.items{{margin:.3rem 0 0;padding-left:1.15rem}}.items li{{margin:.45rem 0;font-size:.95rem}}
.intro{{color:var(--muted);font-size:.88rem}}
@media(max-width:600px){{body{{font-size:15px}}header.hero h1{{font-size:1.25rem}}nav.toc a{{font-size:.78rem;padding:.15rem .55rem}}}}
</style></head><body>
<header class="hero"><div class="wrap"><h1>名冊整體分析</h1><p>大家在解決什麼問題、哪些輪子被重複造、哪些成果可以直接用</p><p class="meta" style="color:#fff;opacity:.85">資料時間：{gen}</p></div></header>
<div class="wrap"><nav class="toc"><a href="./index.html">← 回首頁</a><a href="#direction">努力方向</a><a href="#dupes">重複造輪子</a><a href="#ready">可直接用</a><a href="#clever">精妙做法</a><a href="#patterns">模式與缺口</a></nav>
{"".join(out)}
<p class="meta">由 scripts/build_analysis.py 依 docs/data/repos.json 產生；精選清單與評語為人工整理。</p></div></body></html>
'''


def main():
    (DOCS / "analysis.html").write_text(build(), encoding="utf-8")
    print("wrote docs/analysis.html")


if __name__ == "__main__":
    main()
