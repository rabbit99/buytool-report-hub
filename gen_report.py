"""從 spec/ JSONL 自動產生 Markdown 報告。

讀取 spec/<vendor>/ 下的 JSONL 分類資料，
分析、總結後產出結構化的 Markdown 報告到 reports/<vendor>/。

用法：
    python gen_report.py --vendor 機器熊
    python gen_report.py --vendor 機器熊 --html
    python gen_report.py --all --analyze    # 啟用 AI 分析（消耗 Gemini token）
"""

import re
import json
import html as html_lib
import argparse
import sys
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime

import logging

from vendor_config import get_vendor, list_vendors, list_publish_vendors

logger = logging.getLogger('gen_report')

# 嘗試導入 message_analyzer（若 API key 未設定會失敗，但不中斷）
try:
    from message_analyzer import analyze_messages_batch
    _ANALYZER_AVAILABLE = True
except Exception as e:
    _ANALYZER_AVAILABLE = False
    _ANALYZER_ERROR = str(e)

# ── 分類檔名 & 標題 ────────────────────────────────────────────
CATEGORIES = [
    ('01_掛機攻略', '掛機攻略'),
    ('02_設定教學', '設定教學'),
    ('03_帳號安全', '帳號安全與封鎖風險'),
    ('04_Bug排除', 'Bug 排除'),
    ('05_環境設定', '環境設定'),
    ('06_買賣交易', '買賣交易資訊'),
]

# ── 雜訊過濾模式 ─────────────────────────────────────────────
_URL_ONLY = re.compile(r'^https?://\S+$')
_INVITATION_KWS = ['邀請加入', 'utm_source=invitati', '點選以下連結', 'join.line.me', '邀請你加入']
_SALE_PREFIXES = re.compile(r'^(賣|售|出售|徵|WTB|WTS|收購|求購|換|交換)\s', re.IGNORECASE)
_ENCHANT_LIST = re.compile(r'\+\d+\S+')
_ARTICLE_CITE = re.compile(r'[（(][A-Za-z][A-Za-z\-]+[）)]')


def _load_jsonl(path):
    """讀取 JSONL 檔案，回傳 list[dict]。"""
    records = []
    if not path.exists():
        return records
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _load_meta(spec_dir):
    """讀取 _meta.json。"""
    meta_path = spec_dir / '_meta.json'
    if meta_path.exists():
        with open(meta_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None


def _date_range_str(records):
    """從記錄中取得日期範圍字串，如 2026/04/17-04/20。"""
    dates = sorted(set(r['date'] for r in records if r.get('date')))
    if not dates:
        return '未知日期'
    min_d = dates[0].replace('-', '/')
    max_d = dates[-1].replace('-', '/')
    # 簡化顯示：同年則省略年份
    if min_d[:4] == max_d[:4]:
        return f"{min_d}-{max_d[5:]}"
    return f"{min_d}-{max_d}"


def _group_by_sub(records):
    """按 sub（子分類）分組，回傳 {sub: [records]}，按數量降序。"""
    groups = defaultdict(list)
    for r in records:
        groups[r.get('sub', '其他')].append(r)
    return dict(sorted(groups.items(), key=lambda x: -len(x[1])))


def _deduplicate(records):
    """按 content 前 50 字去重。"""
    seen = set()
    unique = []
    for r in records:
        key = r.get('content', '')[:50].strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


# ── 內容分析工具 ────────────────────────────────────────────────

def _is_question(content):
    """判斷是否為純提問（非資訊性內容）。"""
    s = content.strip()
    if s.endswith('?') or s.endswith('？') or s.endswith('嗎') or s.endswith('呢'):
        return True
    q_patterns = [
        r'^請問', r'^想問', r'^問一下', r'^有人.+嗎',
        r'^怎麼', r'^如何', r'^要怎麼', r'^可以問',
        r'^為什麼', r'^是不是', r'^有沒有',
        r'^誰', r'^哪裡', r'^什麼時',
        r'怎麼辦$', r'怎辦$',
    ]
    for p in q_patterns:
        if re.search(p, s):
            return True
    return False


# 數值擷取模式
_NUM_PATTERNS = [
    (r'(\d[\d.]*)\s*[萬w]', '萬'),           # X萬
    (r'(\d[\d.]*)\s*[元塊]', '元'),           # X元
    (r'(\d[\d.]*)\s*%', '%'),                  # X%
    (r'(\d[\d.]*)\s*小時', '小時'),            # X小時
    (r'(\d[\d.]*)\s*[天日]', '天'),            # X天
    (r'(\d[\d.]*)\s*開', '開'),                # X開
    (r'(\d[\d.]*)\s*等', '等'),                # X等
    (r'(\d[\d.]*)\s*次', '次'),                # X次
    (r'(\d[\d.]*)\s*罐', '罐'),               # X罐
    (r'(\d[\d.]*)\s*隻', '隻'),               # X隻
    (r'AC\s*(\d+)', 'AC'),                     # ACxx
]


def _extract_numbers(content):
    """從內容擷取數值資料點。回傳 [(數值, 單位, 上下文片段)]"""
    results = []
    for pat, unit in _NUM_PATTERNS:
        for m in re.finditer(pat, content, re.IGNORECASE):
            val = m.group(1)
            # 擷取數值前後的上下文
            start = max(0, m.start() - 10)
            end = min(len(content), m.end() + 10)
            ctx = content[start:end].strip()
            results.append((val, unit, ctx))
    return results


def _extract_keyword_entities(records):
    """從記錄的 keywords 欄位統計實體出現頻率。"""
    entity_counts = Counter()
    entity_speakers = defaultdict(set)
    entity_records = defaultdict(list)
    for r in records:
        kws = [k.strip() for k in r.get('keywords', '').split(',') if k.strip()]
        nickname = r.get('nickname', '')
        for kw in kws:
            # 跳過正則模式的 keyword
            if any(c in kw for c in r'[]+*?{}|()\\'):
                continue
            entity_counts[kw] += 1
            if nickname:
                entity_speakers[kw].add(nickname)
            entity_records[kw].append(r)
    return entity_counts, entity_speakers, entity_records


def _is_display_noise(content: str) -> bool:
    """判斷訊息是否為顯示雜訊（不適合作為引言展示）。"""
    s = content.strip()
    if _URL_ONLY.match(s):
        return True
    if any(kw in s for kw in _INVITATION_KWS):
        return True
    if _SALE_PREFIXES.match(s):
        return True
    if len(_ENCHANT_LIST.findall(s)) >= 3:
        return True
    if _ARTICLE_CITE.search(s) and len(s) > 150:
        return True
    return False


def _pick_informative(records, max_items=5):
    """挑選資訊性內容（非問句、有實質資訊、優先高可靠來源）。"""
    candidates = []
    for r in records:
        content = r.get('content', '').strip()
        if not content or len(content) < 8:
            continue
        if _is_question(content):
            continue
        if _is_display_noise(content):
            continue
        candidates.append(r)

    candidates.sort(
        key=lambda r: (-r.get('priority', 5), -len(r.get('content', ''))),
    )
    # 去重
    seen = set()
    unique = []
    for r in candidates:
        key = r['content'][:40]
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique[:max_items]


def _format_quote(content, max_len=150):
    """截斷過長內容。"""
    content = content.strip().replace('\n', ' ')
    if len(content) > max_len:
        return content[:max_len] + '…'
    return content


def _build_numeric_signal_lines(data_points, max_items=5):
    """將數值提及整理為匿名化信號摘要，不輸出原句。"""
    unit_values = defaultdict(list)
    for dp in data_points:
        for val, unit, _ctx in dp.get('numbers', []):
            cleaned = re.sub(r'[^0-9.]', '', str(val))
            if not cleaned:
                continue
            try:
                num = float(cleaned)
            except ValueError:
                continue
            unit_values[unit].append(num)

    lines = []
    for unit, values in unit_values.items():
        if not values:
            continue
        min_v = min(values)
        max_v = max(values)
        if min_v.is_integer() and max_v.is_integer():
            min_text = str(int(min_v))
            max_text = str(int(max_v))
        else:
            min_text = f'{min_v:.2f}'.rstrip('0').rstrip('.')
            max_text = f'{max_v:.2f}'.rstrip('0').rstrip('.')

        if min_text == max_text:
            lines.append(f'- {unit}：提及 {len(values)} 次，代表值約 {min_text}{unit}')
        else:
            lines.append(f'- {unit}：提及 {len(values)} 次，區間約 {min_text}~{max_text}{unit}')

    lines.sort()
    return lines[:max_items]


def _build_observation_summary(sub_name, analysis, meaningful_entities):
    """生成去識別的觀點摘要，避免輸出可追溯原句。"""
    unique = analysis['unique']
    speakers = analysis['speakers']
    question_count = analysis['question_count']
    declarative_count = max(0, unique - question_count)

    if speakers >= 10:
        speaker_label = '多數參與者'
    elif speakers >= 4:
        speaker_label = '部分參與者'
    else:
        speaker_label = '少數參與者'

    lines = [
        (
            f'- {speaker_label}在「{sub_name}」議題提供 {declarative_count} 則敘述型訊號，'
            f'提問型訊號 {question_count} 則，顯示社群以經驗回報為主。'
        ),
    ]

    if meaningful_entities:
        top_entities = '、'.join(entity for entity, _ in meaningful_entities[:3])
        lines.append(f'- 討論焦點集中在 {top_entities}，屬於此主題下的高頻關鍵面向。')

    numeric_lines = _build_numeric_signal_lines(analysis['data_points'], max_items=2)
    if numeric_lines:
        lines.append('- 數值訊號顯示此議題具有可量化討論，可作為後續策略與風險評估依據。')

    return lines


# ── 子分類分析 ──────────────────────────────────────────────────

def _analyze_subcategory(sub_name, records):
    """分析一個子分類的所有記錄，產生結構化分析結果。"""
    deduped = _deduplicate(records)
    speakers = set(r.get('nickname', '') for r in deduped if r.get('nickname'))

    # 實體頻率
    entity_counts, entity_speakers, entity_records = _extract_keyword_entities(deduped)

    # 數值資料點
    data_points = []
    for r in deduped:
        nums = _extract_numbers(r.get('content', ''))
        if nums:
            data_points.append({
                'content': r['content'],
                'numbers': nums,
                'nickname': r.get('nickname', ''),
                'priority': r.get('priority', 5),
            })
    # 按優先級排序
    data_points.sort(key=lambda x: -x['priority'])

    # 關鍵資訊（非問句的實質內容）
    insights = _pick_informative(deduped, max_items=8)
    question_count = sum(1 for r in deduped if _is_question(r.get('content', '')))

    return {
        'total': len(records),
        'unique': len(deduped),
        'speakers': len(speakers),
        'question_count': question_count,
        'entity_counts': entity_counts,
        'entity_speakers': entity_speakers,
        'data_points': data_points,
        'insights': insights,
    }


# ── AI 快取 ────────────────────────────────────────────────────

def _ai_cache_load(spec_dir: Path) -> dict:
    """讀取 AI 分析快取。"""
    cache_path = spec_dir / '_ai_cache.json'
    if cache_path.exists():
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            logger.info(f'[快取讀取] {cache_path}，共 {len(data)} 筆')
            return data
        except Exception as e:
            logger.warning(f'[快取讀取失敗] {cache_path}：{e}')
    return {}


def _ai_cache_save(spec_dir: Path, cache: dict):
    """寫入 AI 分析快取。"""
    cache_path = spec_dir / '_ai_cache.json'
    try:
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        logger.info(f'[快取寫入] {cache_path}，共 {len(cache)} 筆')
    except Exception as e:
        logger.warning(f'[快取寫入失敗] {cache_path}：{e}')


# ── AI 快取 ────────────────────────────────────────────────────

def _ai_cache_load(spec_dir: Path) -> dict:
    """讀取 AI 分析快取。"""
    cache_path = spec_dir / '_ai_cache.json'
    if cache_path.exists():
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            logger.info(f'[快取讀取] {cache_path}，共 {len(data)} 筆')
            return data
        except Exception as e:
            logger.warning(f'[快取讀取失敗] {cache_path}：{e}')
    return {}


def _ai_cache_save(spec_dir: Path, cache: dict):
    """寫入 AI 分析快取。"""
    cache_path = spec_dir / '_ai_cache.json'
    try:
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        logger.info(f'[快取寫入] {cache_path}，共 {len(cache)} 筆')
    except Exception as e:
        logger.warning(f'[快取寫入失敗] {cache_path}：{e}')


# ── 報告產生器 ──────────────────────────────────────────────────

def _compute_sub_analyses(
    vendor_name, cat_file, cat_title, grouped, analyze, jsonl_path, ai_cache,
):
    """計算各子分類的結構化分析資料（Markdown 與 HTML 共用）。"""
    sub_analyses = {}
    for sub, recs in grouped.items():
        analysis = _analyze_subcategory(sub, recs)
        top_entities = analysis['entity_counts'].most_common(10)
        meaningful_entities = [
            (e, c) for e, c in top_entities if len(e) >= 2 and c >= 2
        ]
        quotes = _pick_informative(_deduplicate(recs), max_items=5)

        ai_result = None
        if _ANALYZER_AVAILABLE and ai_cache is not None:
            cache_key = f'{cat_file}:{sub}'
            jsonl_mtime = jsonl_path.stat().st_mtime if jsonl_path and jsonl_path.exists() else 0
            cached = ai_cache.get(cache_key)
            if cached and cached.get('jsonl_mtime') == jsonl_mtime:
                # 快取命中：無論是否 --analyze 都直接使用
                ai_result = cached['result']
                logger.info(f'[快取命中] {cache_key}')
                print(f'      ↩ AI 快取命中：{cache_key}')
            elif analyze:
                # 僅在 --analyze 時才發出新的 API 請求
                messages = [r.get('content', '') for r in quotes if r.get('content')]
                if messages:
                    logger.info(f'[AI 請求] {cache_key}（首次分析）')
                    ai_result = analyze_messages_batch(
                        vendor_name, cat_title, sub, messages,
                        speaker_count=analysis['speakers'],
                    )
                    if ai_result and ai_result.get('status') == 'success':
                        ai_cache[cache_key] = {'jsonl_mtime': jsonl_mtime, 'result': ai_result}
                    else:
                        status = ai_result.get('status') if ai_result else 'None'
                        logger.warning(f'[快取未寫入] {cache_key}，AI status={status}，訊息：')

        sub_analyses[sub] = {
            'records': recs,
            'analysis': analysis,
            'entities': meaningful_entities,
            'quotes': quotes,
            'ai_result': ai_result,
        }
    return sub_analyses


def _generate_category_report(
    vendor_name, cat_file, cat_title, records, date_range,
    analyze=False, jsonl_path=None, ai_cache=None,
):
    """產生單一分類的 Markdown 報告。回傳 (markdown_str, sub_analyses)。"""
    grouped = _group_by_sub(records)
    total = len(records)
    all_speakers = set(r.get('nickname', '') for r in records if r.get('nickname'))

    sub_analyses = _compute_sub_analyses(
        vendor_name, cat_file, cat_title, grouped, analyze, jsonl_path, ai_cache,
    )

    lines = []
    lines.append(f'# {vendor_name} — {cat_title}整理')
    lines.append('')
    lines.append(f'> 資料來源：LINE 群組（{date_range}）｜自動產生於 {datetime.now().strftime("%Y/%m/%d %H:%M")}')
    lines.append(f'> 分析基礎：{total} 則訊息、{len(all_speakers)} 位發言者')
    lines.append('')
    lines.append('---')
    lines.append('')

    lines.append('## 討論熱度')
    lines.append('')
    lines.append('| 子分類 | 討論量 | 發言者 | 佔比 |')
    lines.append('|--------|--------|--------|------|')
    for sub, recs in grouped.items():
        pct = len(recs) / total * 100 if total else 0
        spk = len(set(r.get('nickname', '') for r in recs if r.get('nickname')))
        lines.append(f'| {sub} | {len(recs)} 則 | {spk} 人 | {pct:.0f}% |')
    lines.append('')

    section_num = 1
    chinese_nums = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十']
    sub_summaries = []

    for sub, data in sub_analyses.items():
        analysis = data['analysis']
        meaningful_entities = data['entities']
        ai_result = data.get('ai_result')
        quotes = data['quotes']
        cn = chinese_nums[section_num - 1] if section_num <= len(chinese_nums) else str(section_num)
        lines.append('---')
        lines.append('')
        lines.append(f'## {cn}、{sub}')
        lines.append('')

        if meaningful_entities:
            lines.append('### 熱門關鍵字')
            lines.append('')
            lines.append('| 關鍵字 | 提及次數 | 討論人數 |')
            lines.append('|--------|----------|----------|')
            for entity, count in meaningful_entities[:8]:
                spk_count = len(analysis['entity_speakers'].get(entity, set()))
                lines.append(f'| {entity} | {count} | {spk_count} |')
            lines.append('')

        lines.append('### 群友經驗與觀點')
        lines.append('')
        for line in _build_observation_summary(sub, analysis, meaningful_entities):
            lines.append(line)
        lines.append('')

        if ai_result and ai_result.get('status') == 'success':
            lines.append('### AI 情報整理')
            lines.append('')
            if ai_result.get('title'):
                lines.append(f'**{ai_result["title"]}**')
                lines.append('')
            for part in ai_result.get('analysis', '').split('\n'):
                part = part.strip()
                if part:
                    lines.append(part)
            if ai_result.get('supplement'):
                lines.append(f'\n> **補充**：{ai_result["supplement"]}')
            lines.append('')

        if quotes:
            lines.append('### 群友原話（節錄）')
            lines.append('')
            for q in quotes[:4]:
                lines.append(f'- 「{_format_quote(q.get("content", ""), max_len=120)}」')
            lines.append('')

        sub_summary = f'**{sub}**：{analysis["unique"]} 則不重複討論、{analysis["speakers"]} 人參與'
        if meaningful_entities:
            top3 = '、'.join(e for e, _ in meaningful_entities[:3])
            sub_summary += f'，熱門：{top3}'
        sub_summaries.append(sub_summary)
        section_num += 1

    lines.append('---')
    lines.append('')
    lines.append('## 總體總結')
    lines.append('')
    lines.append('### 各面向概況')
    lines.append('')
    for s in sub_summaries:
        lines.append(f'- {s}')
    lines.append('')
    lines.append('### 整體判斷')
    lines.append('')
    top_sub = list(grouped.keys())[0] if grouped else ''
    top_count = len(list(grouped.values())[0]) if grouped else 0
    top_pct = top_count / total * 100 if total else 0
    lines.append(
        f'- 本分類共 **{total} 則**訊息、**{len(all_speakers)}** 位參與者，'
        f'最熱門子題為「{top_sub}」（佔 {top_pct:.0f}%）'
    )
    date_counts = Counter(r.get('date', '') for r in records if r.get('date'))
    if len(date_counts) > 1:
        peak_date, peak_count = date_counts.most_common(1)[0]
        lines.append(f'- 討論高峰：{peak_date}（{peak_count} 則）')
    src_counts = Counter(r.get('src', '') for r in records)
    if len(src_counts) > 1:
        src_parts = '、'.join(f'{s}({c}則)' for s, c in src_counts.most_common())
        lines.append(f'- 來源分佈：{src_parts}')
    lines.append('')

    return '\n'.join(lines), sub_analyses


def generate_reports(vendor_name, html=False, analyze=False):
    """產生一個廠商的所有報告。"""
    vendor_cfg = get_vendor(vendor_name)
    spec_dir = vendor_cfg['spec_dir']
    report_dir = vendor_cfg['report_dir']
    report_dir.mkdir(parents=True, exist_ok=True)

    if not spec_dir.exists():
        print(f'  錯誤：找不到 {spec_dir}，請先執行 ingest.py')
        return

    _load_meta(spec_dir)
    generated = 0

    # 載入 AI 快取（整個廠商共用）
    # 即使不重新分析，也讀取快取來顯示已有的 AI 結果
    ai_cache = _ai_cache_load(spec_dir) if _ANALYZER_AVAILABLE else {}

    for cat_file, cat_title in CATEGORIES:
        jsonl_path = spec_dir / f'{cat_file}.jsonl'
        records = _load_jsonl(jsonl_path)
        if not records:
            print(f'  {cat_file}：無資料，跳過')
            continue

        date_range = _date_range_str(records)
        md_content, sub_analyses = _generate_category_report(
            vendor_cfg['name'], cat_file, cat_title, records, date_range,
            analyze=analyze, jsonl_path=jsonl_path, ai_cache=ai_cache,
        )

        md_path = report_dir / f'{cat_file}.md'
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(md_content)
        print(f'  \u2713 {md_path.name}（{len(records)} 則）')
        generated += 1

        if html:
            try:
                html_content = _build_category_html(
                    vendor_cfg, cat_file, cat_title, records, date_range, sub_analyses,
                )
                html_path = report_dir / f'{cat_file}.html'
                with open(html_path, 'w', encoding='utf-8') as f:
                    f.write(html_content)
                print(f'    + {html_path.name}')
            except Exception as e:
                logger.error(f'HTML 產生失敗 {cat_file}: {e}', exc_info=True)
                print(f'    ⚠ HTML 產生失敗：{e}')

    # 儲存 AI 快取
    if analyze and _ANALYZER_AVAILABLE:
        _ai_cache_save(spec_dir, ai_cache)

    _generate_overview(vendor_cfg, spec_dir, report_dir, html=html)

    print(f'  ── 共產生 {generated} 份報告')


def _generate_overview(vendor_cfg, spec_dir, report_dir, html=False):
    """產生總覽摘要 00_總覽.md。"""
    meta = _load_meta(spec_dir)
    sources_cfg = vendor_cfg.get('sources', {})
    category_stats = []
    lines = []
    lines.append(f'# {vendor_cfg["name"]} — 分析總覽\n')
    lines.append(f'> 群組：{vendor_cfg["full_name"]}')
    lines.append(f'> 自動產生於 {datetime.now().strftime("%Y/%m/%d %H:%M")}\n')

    if meta:
        lines.append('## 資料涵蓋範圍\n')
        lines.append('| 消化記錄 | 標籤 | 優先級 | 日期範圍 | 訊息數 | 消化時間 |')
        lines.append('|----------|------|--------|----------|--------|----------|')
        for r in meta.get('ingested_ranges', []):
            fname = r['file']
            src = sources_cfg.get(fname, {})
            label = src.get('label', '—')
            pri = src.get('priority', 5)
            lines.append(
                f'| {fname} | {label} | {pri} '
                f'| {r["min_date"]} ~ {r["max_date"]} '
                f'| {r["msg_count"]} | {r["ingested_at"][:16]} |'
            )
        lines.append(f'\n**累計 spec 記錄：{meta.get("total_messages", 0)} 則**\n')

    # 各分類統計
    lines.append('## 各分類統計\n')
    lines.append('| 分類 | 訊息數 | 報告連結 |')
    lines.append('|------|--------|----------|')
    for cat_file, cat_title in CATEGORIES:
        jsonl_path = spec_dir / f'{cat_file}.jsonl'
        records = _load_jsonl(jsonl_path)
        count = len(records)
        speakers = len(set(r.get('nickname', '') for r in records if r.get('nickname')))
        link = f'[{cat_file}.md]({cat_file}.md)' if count > 0 else '—'
        lines.append(f'| {cat_title} | {count} | {link} |')
        category_stats.append({
            'file': cat_file,
            'title': cat_title,
            'count': count,
            'speakers': speakers,
        })
    lines.append('')

    md_path = report_dir / '00_總覽.md'
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f'  ✓ {md_path.name}（總覽）')
    if html:
        _generate_overview_dashboard_html(vendor_cfg, report_dir, meta, category_stats, sources_cfg)


def _generate_overview_dashboard_html(vendor_cfg, report_dir, meta, category_stats, sources_cfg):
    """產生 Stitch 風格儀表板總覽 HTML（與分類報告共用設計系統）。"""
    generated_at = datetime.now().strftime('%Y/%m/%d %H:%M')
    vendor_name = vendor_cfg['name']
    total_categories = len([c for c in category_stats if c['count'] > 0])
    total_messages = sum(c['count'] for c in category_stats)
    total_speakers = sum(c['speakers'] for c in category_stats)
    top_category = max(category_stats, key=lambda c: c['count'], default=None)
    top_label = top_category['title'] if top_category and top_category['count'] > 0 else '—'

    # 資料日期範圍
    all_ranges = meta.get('ingested_ranges', []) if meta else []
    all_dates = [r['min_date'] for r in all_ranges] + [r['max_date'] for r in all_ranges]
    data_date_range = f"{min(all_dates)} ~ {max(all_dates)}" if all_dates else '—'

    # 廠商切換側欄連結
    vendor_links = ''.join(
        f'<a class="vendor-link{" is-active" if vn == vendor_name else ""}" '
        f'href="../{vn}/00_總覽.html">{html_lib.escape(vn)}</a>'
        for vn in list_publish_vendors()
    )

    # 分類側欄連結（帶訊息數）
    cat_sidebar_links = ''.join(
        f'<a class="cat-link" href="{cat["file"]}.html">'
        f'{html_lib.escape(cat["title"])}'
        f'<span>{cat["count"]:,}</span></a>'
        for cat in category_stats
    )

    # 分類卡片（2欄 grid）
    cat_cards = []
    for cat in category_stats:
        empty_cls = ' is-empty' if cat['count'] == 0 else ''
        href = f'{cat["file"]}.html' if cat['count'] > 0 else '#'
        pct = f'{cat["count"] / total_messages * 100:.0f}%' if total_messages > 0 else '0%'
        cat_cards.append(
            f'<a class="cat-card{empty_cls}" href="{href}">'
            f'<div class="cat-card-header">'
            f'<div class="cat-card-title">{html_lib.escape(cat["title"])}</div>'
            f'<div class="cat-card-meta">{cat["count"]:,} 則｜{cat["speakers"]:,} 位發言者</div>'
            f'</div>'
            f'<div class="cat-card-body">'
            f'<div class="cat-bar"><div class="cat-bar-fill" style="width:{pct}"></div></div>'
            f'<div class="cat-bar-label">{pct} 討論量占比</div>'
            f'</div></a>'
        )

    # 來源表格（去重）
    unique_ranges: dict = {}
    for r in all_ranges:
        key = (r.get('file', ''), r.get('min_date', ''), r.get('max_date', ''), r.get('msg_count', ''))
        current = unique_ranges.get(key)
        if not current or r.get('ingested_at', '') > current.get('ingested_at', ''):
            unique_ranges[key] = r
    deduped = list(unique_ranges.values())

    source_rows = []
    for r in deduped:
        fname = html_lib.escape(str(r.get('file', '')))
        src_cfg = sources_cfg.get(r.get('file', ''), {})
        label = html_lib.escape(src_cfg.get('label', '—'))
        pri = src_cfg.get('priority', 5)
        date_r = html_lib.escape(f"{r.get('min_date', '')} ~ {r.get('max_date', '')}")
        source_rows.append(
            f'<tr><td>{fname}</td><td>{label}</td><td>{pri}</td>'
            f'<td>{date_r}</td><td>{r.get("msg_count", "")}</td></tr>'
        )
    if not source_rows:
        source_rows.append('<tr><td colspan="5">尚無來源資料</td></tr>')

    # 時間軸
    timeline_items = []
    for item in sorted(deduped, key=lambda x: x.get('ingested_at', ''), reverse=True)[:6]:
        src_cfg = sources_cfg.get(item.get('file', ''), {})
        label = html_lib.escape(src_cfg.get('label', '一般來源'))
        when = html_lib.escape(item.get('ingested_at', '')[:16].replace('T', ' '))
        summary = html_lib.escape(
            f"{item.get('min_date', '')} ~ {item.get('max_date', '')}｜{item.get('msg_count', 0)} 則"
        )
        timeline_items.append(
            f'<li class="tl-item"><time class="tl-time">{when}</time>'
            f'<div class="tl-body"><strong>{label}</strong></div>'
            f'<div class="tl-sub">{summary}</div></li>'
        )
    if not timeline_items:
        timeline_items.append(
            '<li class="tl-item"><time class="tl-time">—</time>'
            '<div class="tl-body"><strong>尚無更新記錄</strong></div></li>'
        )

    html_content = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html_lib.escape(vendor_name)} — 總覽</title>
<style>
:root{{
  --sidebar-bg:#13303a;--sidebar-hover:#1e4655;--sidebar-active:#2a7f6c;
  --brand:#2a7f6c;--brand-2:#1e5f88;
  --brand-grad:linear-gradient(120deg,var(--brand) 0%,var(--brand-2) 100%);
  --page-bg:#f1f5f8;--card-bg:#fff;--card-border:#d7e3e8;
  --card-shadow:0 2px 12px rgba(15,35,45,.07);
  --card-shadow-hover:0 6px 24px rgba(15,35,45,.13);
  --text:#1a2b34;--text-sub:#4d6168;--radius:16px;--radius-sm:10px;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:"Noto Sans TC","Microsoft JhengHei",sans-serif;color:var(--text);background:var(--page-bg);line-height:1.7}}
.layout{{display:grid;grid-template-columns:240px 1fr;min-height:100vh}}
.sidebar{{background:var(--sidebar-bg);color:#c8dde5;position:sticky;top:0;height:100vh;overflow-y:auto;display:flex;flex-direction:column}}
.sidebar-top{{padding:18px 16px 12px;border-bottom:1px solid rgba(255,255,255,.08)}}
.sidebar-vendor{{font-size:1.1rem;font-weight:700;color:#e8f4f8;margin-bottom:2px}}
.sidebar-sub{{font-size:.82rem;color:#80b0bd}}
.sidebar-section-label{{font-size:.72rem;letter-spacing:.08em;text-transform:uppercase;color:#6090a0;padding:12px 16px 4px}}
.vendor-link{{display:block;padding:7px 16px;color:#a8c8d5;text-decoration:none;font-size:.9rem;border-left:3px solid transparent;transition:all .15s ease}}
.vendor-link:hover{{background:var(--sidebar-hover);color:#e8f4f8;border-left-color:var(--brand)}}
.vendor-link.is-active{{background:rgba(42,127,108,.25);color:#6fdbb8;border-left-color:var(--brand)}}
.cat-link{{display:flex;justify-content:space-between;padding:6px 16px;color:#7aa0b0;text-decoration:none;font-size:.85rem;transition:color .12s}}
.cat-link:hover{{color:#c8dde5}}
.cat-link span{{color:#5a8090;font-size:.8rem}}
.sidebar-footer{{margin-top:auto;padding:12px 16px;border-top:1px solid rgba(255,255,255,.08);font-size:.78rem;color:#5a8090}}
.main{{padding:20px 22px 32px;max-width:900px}}
.topbar{{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:14px}}
.topbar-title{{font-size:1.35rem;font-weight:700;color:var(--text)}}
.summary-bar{{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap}}
.summary-kpi{{background:#fff;border:1px solid var(--card-border);border-radius:var(--radius-sm);padding:7px 13px;font-size:.88rem;color:var(--text-sub)}}
.summary-kpi strong{{color:var(--text);font-size:1.02rem}}
.cat-grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin-bottom:14px}}
.cat-card{{background:var(--card-bg);border:1px solid var(--card-border);border-radius:var(--radius);box-shadow:var(--card-shadow);overflow:hidden;transition:box-shadow .2s ease;text-decoration:none;color:var(--text);display:block}}
.cat-card:hover{{box-shadow:var(--card-shadow-hover)}}
.cat-card-header{{padding:11px 15px;background:var(--brand-grad);color:#fff}}
.cat-card-title{{font-size:1rem;font-weight:700}}
.cat-card-meta{{font-size:.82rem;opacity:.85;margin-top:2px}}
.cat-card-body{{padding:9px 15px 11px}}
.cat-bar{{height:5px;background:#e4eff3;border-radius:3px;margin-bottom:5px}}
.cat-bar-fill{{height:100%;background:var(--brand-grad);border-radius:3px}}
.cat-bar-label{{font-size:.8rem;color:var(--text-sub)}}
.cat-card.is-empty .cat-card-header{{background:#8899a6}}
.cat-card.is-empty{{opacity:.55;pointer-events:none}}
.panel{{background:var(--card-bg);border:1px solid var(--card-border);border-radius:var(--radius);box-shadow:var(--card-shadow);padding:14px 16px;margin-bottom:12px}}
.panel-title{{font-size:.95rem;font-weight:700;color:#1b4b5a;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid var(--card-border)}}
table{{width:100%;border-collapse:collapse}}
th,td{{border-bottom:1px solid #eef3f5;padding:7px 6px;text-align:left;font-size:.87rem}}
th{{color:#244854;font-weight:700;background:#f5fafc}}
tr:last-child td{{border-bottom:0}}
.timeline{{list-style:none;display:grid;gap:7px}}
.tl-item{{border:1px solid #dfe9ee;background:#f9fcfe;border-radius:var(--radius-sm);padding:8px 11px}}
.tl-time{{display:block;color:#40616f;font-size:.78rem;margin-bottom:2px}}
.tl-body{{font-size:.88rem}}
.tl-sub{{color:#5f7680;font-size:.82rem}}
.notice-bar{{background:#fff3cd;border:1px solid #ffc107;border-radius:var(--radius-sm);padding:7px 14px;font-size:.82rem;color:#664d03;margin-bottom:12px}}
.notice-bar a{{color:#5a3e02;font-weight:700}}
.report-footer{{margin-top:10px;font-size:.82rem;color:#7a909a;text-align:center;padding:12px 0 0;border-top:1px solid var(--card-border)}}
@media(max-width:780px){{
  .layout{{grid-template-columns:1fr}}
  .sidebar{{position:static;height:auto}}
  .main{{padding:14px 12px 24px}}
  .cat-grid{{grid-template-columns:1fr}}
}}
</style>
</head>
<body>
<div class="layout">
  <aside class="sidebar">
    <div class="sidebar-top">
      <div class="sidebar-vendor">{html_lib.escape(vendor_name)}</div>
      <div class="sidebar-sub">報告總覽</div>
    </div>
    <div class="sidebar-section-label">切換廠商</div>
    {vendor_links}
    <div class="sidebar-section-label">所有分類</div>
    {cat_sidebar_links}
    <div class="sidebar-footer">
      資料：{html_lib.escape(data_date_range)}<br>
      產生：{generated_at}
    </div>
  </aside>
  <main class="main">
    <div class="notice-bar">本站內容為玩家社群討論之自動化彙整，僅供資訊參考。本站不鼓勵、不協助任何違反遊戲服務條款或相關法規之行為。<a href="../legal/disclaimer.html">閱讀完整免責聲明</a></div>
    <div class="topbar">
      <div class="topbar-title">{html_lib.escape(vendor_name)} 報告總覽</div>
    </div>
    <div class="summary-bar">
      <div class="summary-kpi"><strong>{total_messages:,}</strong> 則訊息</div>
      <div class="summary-kpi"><strong>{total_speakers:,}</strong> 位發言者</div>
      <div class="summary-kpi">資料期間 <strong>{html_lib.escape(data_date_range)}</strong></div>
      <div class="summary-kpi">最熱分類 <strong>{html_lib.escape(top_label)}</strong></div>
    </div>
    <div class="cat-grid">{''.join(cat_cards)}</div>

    <div class="panel">
      <div class="panel-title">資料來源</div>
      <table>
        <thead><tr><th>檔案</th><th>標籤</th><th>優先級</th><th>日期範圍</th><th>訊息數</th></tr></thead>
        <tbody>{''.join(source_rows)}</tbody>
      </table>
    </div>

    <div class="panel">
      <div class="panel-title">最近更新</div>
      <ul class="timeline">{''.join(timeline_items)}</ul>
    </div>

    <footer class="report-footer">
      {html_lib.escape(vendor_cfg['full_name'])} ｜
      自動產生於 {generated_at}
    </footer>
  </main>
</div>
</body>
</html>"""

    html_path = report_dir / '00_總覽.html'
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    print(f'  \u2713 {html_path.name}\uff08\u5100\u8868\u677f\uff09')

# ── 分類報告 HTML（Stitch 風格） ─────────────────────────────────

def _build_category_html(vendor_cfg, cat_file, cat_title, records, date_range, sub_analyses):
    """產生 Stitch-inspired 卡片式分類報告 HTML。"""
    from datetime import date as date_type

    vendor_name = vendor_cfg['name']
    generated_at = datetime.now().strftime('%Y/%m/%d %H:%M')
    total = len(records)
    all_speakers = len(set(r.get('nickname', '') for r in records if r.get('nickname')))

    # ── 側欄子分類導覽 ──
    nav_items = ''.join(
        f'<a href="#sub-{i}" class="sub-link">{html_lib.escape(sub)}</a>'
        for i, sub in enumerate(sub_analyses.keys())
    )

    # ── 分類導覽（左側） ──
    cat_links = ''.join(
        f'<a class="cat-link{" is-active" if cf == cat_file else ""}" href="{cf}.html">'
        f'{html_lib.escape(ct)}</a>'
        for cf, ct in CATEGORIES
    )

    # ── 廠商切換 ──
    vendor_btns = ''.join(
        f'<a class="vendor-btn{" is-active" if vn == vendor_name else ""}" '
        f'href="../{vn}/00_總覽.html">{html_lib.escape(vn)}</a>'
        for vn in list_publish_vendors()
    )

    # ── 子分類卡片 ──
    cards_html = []
    for i, (sub, data) in enumerate(sub_analyses.items()):
        analysis    = data['analysis']
        entities    = data['entities']
        quotes      = data['quotes']
        ai_result   = data.get('ai_result')
        recs        = data['records']
        speakers    = analysis['speakers']
        unique      = analysis['unique']

        # 可信度
        if speakers >= 10:
            conf_text, conf_cls = f'高可信（{speakers} 人）', 'conf-high'
        elif speakers >= 4:
            conf_text, conf_cls = f'中可信（{speakers} 人）', 'conf-mid'
        else:
            conf_text, conf_cls = f'小樣本（{speakers} 人）', 'conf-low'

        # 資料新鮮度
        dates_sub = sorted(set(r['date'] for r in recs if r.get('date')))
        freshness_html = ''
        if dates_sub:
            try:
                latest = datetime.strptime(dates_sub[-1], '%Y-%m-%d').date()
                days_ago = (date_type.today() - latest).days
                if days_ago <= 3:
                    freshness_html = '<span class="fresh-chip fresh-recent">近 3 日</span>'
                elif days_ago <= 7:
                    freshness_html = '<span class="fresh-chip fresh-week">近 1 週</span>'
                elif days_ago <= 14:
                    freshness_html = '<span class="fresh-chip fresh-fortnight">近 2 週</span>'
                else:
                    freshness_html = f'<span class="fresh-chip fresh-old">{days_ago} 天前</span>'
            except ValueError:
                pass

        # 關鍵字 chips
        chips_html = ''
        if entities:
            chips = ''.join(
                f'<span class="chip">{html_lib.escape(e)} <em>×{c}</em></span>'
                for e, c in entities[:8]
            )
            chips_html = f'<div class="keyword-chips">{chips}</div>'

        # AI 分析區塊
        def _md_to_html(text: str) -> str:
            """Escape HTML then convert **bold** and newlines."""
            escaped = html_lib.escape(text)
            escaped = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', escaped)
            return escaped

        ai_html = ''
        if ai_result and ai_result.get('status') == 'success':
            title_esc = html_lib.escape(ai_result.get('title', ''))
            items = [
                l.strip().lstrip('•-').strip()
                for l in ai_result.get('analysis', '').split('\n') if l.strip()
            ]
            items_html = ''.join(f'<li>{_md_to_html(it)}</li>' for it in items if it)
            sup_html = ''
            if ai_result.get('supplement'):
                sup_text = _md_to_html(ai_result['supplement']).replace('\n', '<br>')
                sup_html = (
                    f'<div class="ai-supplement">'
                    f'⚠ {sup_text}</div>'
                )
            ai_html = (
                f'<div class="ai-block">'
                f'<div class="ai-block-header"><span class="ai-badge">AI 情報整理</span></div>'
                f'<div class="ai-title">{title_esc}</div>'
                f'<ul class="ai-list">{items_html}</ul>'
                f'{sup_html}</div>'
            )

        cards_html.append(f'''<article class="sub-card" id="sub-{i}">
  <header class="card-header">
    <div class="card-title">{html_lib.escape(sub)}</div>
    <div class="card-badges">
      <span class="badge-count">{len(recs)} 則</span>
      <span class="badge-speakers">{unique} 不重複</span>
      <span class="badge {conf_cls}">{html_lib.escape(conf_text)}</span>
      {freshness_html}
    </div>
  </header>
  {chips_html}
  {ai_html}
</article>''')

    return f'''<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html_lib.escape(vendor_name)} — {html_lib.escape(cat_title)}</title>
<style>
:root{{
  --sidebar-bg:#13303a;--sidebar-hover:#1e4655;--sidebar-active:#2a7f6c;
  --brand:#2a7f6c;--brand-2:#1e5f88;
  --brand-grad:linear-gradient(120deg,var(--brand) 0%,var(--brand-2) 100%);
  --page-bg:#f1f5f8;--card-bg:#fff;--card-border:#d7e3e8;
  --card-shadow:0 2px 12px rgba(15,35,45,.07);
  --card-shadow-hover:0 6px 24px rgba(15,35,45,.13);
  --text:#1a2b34;--text-sub:#4d6168;--radius:16px;--radius-sm:10px;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:"Noto Sans TC","Microsoft JhengHei",sans-serif;color:var(--text);background:var(--page-bg);line-height:1.7}}
.layout{{display:grid;grid-template-columns:240px 1fr;min-height:100vh}}
.sidebar{{background:var(--sidebar-bg);color:#c8dde5;position:sticky;top:0;height:100vh;overflow-y:auto;display:flex;flex-direction:column}}
.sidebar-top{{padding:18px 16px 12px;border-bottom:1px solid rgba(255,255,255,.08)}}
.back-link{{display:inline-block;color:#80b0bd;text-decoration:none;font-size:.85rem;margin-bottom:8px}}
.back-link:hover{{color:#c8dde5}}
.sidebar-vendor{{font-size:.78rem;color:#80b0bd;margin-bottom:2px}}
.sidebar-cat{{font-size:1.1rem;font-weight:700;color:#e8f4f8}}
.sidebar-section-label{{font-size:.72rem;letter-spacing:.08em;text-transform:uppercase;color:#6090a0;padding:12px 16px 4px}}
.sub-link{{display:block;padding:7px 16px;color:#a8c8d5;text-decoration:none;font-size:.9rem;border-left:3px solid transparent;transition:all .15s ease}}
.sub-link:hover{{background:var(--sidebar-hover);color:#e8f4f8;border-left-color:var(--brand)}}
.sub-link.is-active{{background:rgba(42,127,108,.25);color:#6fdbb8;border-left-color:var(--brand)}}
.cat-nav{{border-top:1px solid rgba(255,255,255,.08);padding:8px 0}}
.cat-link{{display:block;padding:6px 16px;color:#7aa0b0;text-decoration:none;font-size:.85rem;transition:color .12s}}
.cat-link:hover{{color:#c8dde5}}
.cat-link.is-active{{color:#6fdbb8;font-weight:700}}
.sidebar-footer{{margin-top:auto;padding:12px 16px;border-top:1px solid rgba(255,255,255,.08);font-size:.78rem;color:#5a8090}}
.main{{padding:20px 22px 32px;max-width:900px}}
.topbar{{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:14px}}
.topbar-title{{font-size:1.35rem;font-weight:700;color:var(--text)}}
.vendor-switch{{display:flex;flex-wrap:wrap;gap:6px}}
.vendor-btn{{display:inline-block;text-decoration:none;color:#1a3c48;background:#eff6f9;border:1px solid #d3e1e8;border-radius:999px;padding:4px 12px;font-size:.85rem;font-weight:700}}
.vendor-btn.is-active{{color:#fff;background:var(--brand);border-color:var(--brand)}}
.summary-bar{{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap}}
.summary-kpi{{background:#fff;border:1px solid var(--card-border);border-radius:var(--radius-sm);padding:7px 13px;font-size:.88rem;color:var(--text-sub)}}
.summary-kpi strong{{color:var(--text);font-size:1.02rem}}
.sub-card{{background:var(--card-bg);border:1px solid var(--card-border);border-radius:var(--radius);box-shadow:var(--card-shadow);margin-bottom:16px;overflow:hidden;transition:box-shadow .2s ease}}
.sub-card:hover{{box-shadow:var(--card-shadow-hover)}}
.card-header{{display:flex;align-items:center;justify-content:space-between;padding:11px 16px;background:var(--brand-grad);color:#fff;gap:8px;flex-wrap:wrap}}
.card-title{{font-size:1.04rem;font-weight:700;letter-spacing:.02em}}
.card-badges{{display:flex;gap:5px;flex-wrap:wrap;align-items:center}}
.badge-count,.badge-speakers{{background:rgba(255,255,255,.2);border-radius:999px;padding:2px 9px;font-size:.8rem;font-weight:600}}
.badge{{border-radius:999px;padding:2px 9px;font-size:.78rem;font-weight:700}}
.conf-high{{background:#16a34a}}
.conf-mid{{background:#ca8a04}}
.conf-low{{background:#6b7280}}
.fresh-chip{{border-radius:999px;padding:2px 9px;font-size:.78rem;font-weight:600}}
.fresh-recent{{background:#10b981;color:#fff}}
.fresh-week{{background:#3b82f6;color:#fff}}
.fresh-fortnight{{background:#f59e0b;color:#fff}}
.fresh-old{{background:#9ca3af;color:#fff}}
.keyword-chips{{display:flex;flex-wrap:wrap;gap:5px;padding:9px 14px;background:#f5fbf9;border-bottom:1px solid #e8f0ee}}
.chip{{background:#daeee9;color:#0f4c40;border:1px solid #b8ddd7;border-radius:999px;padding:3px 10px;font-size:.83rem}}
.chip em{{font-style:normal;color:#2a7f6c;font-weight:700}}
.ai-block{{margin:12px 14px;background:linear-gradient(135deg,#eef6ff 0%,#edfcf5 100%);border:1px solid #93c5fd;border-left:4px solid #2563eb;border-radius:12px;padding:12px 14px}}
.ai-block-header{{display:flex;align-items:center;gap:8px;margin-bottom:6px}}
.ai-badge{{background:#2563eb;color:#fff;border-radius:999px;padding:2px 10px;font-size:.78rem;font-weight:700;letter-spacing:.05em}}
.ai-title{{font-weight:700;color:#1e3a5f;margin-bottom:6px;font-size:.97rem;line-height:1.45}}
.ai-list{{padding-left:16px;color:var(--text)}}
.ai-list li{{margin-bottom:5px;font-size:.92rem;line-height:1.65}}
.ai-supplement{{margin-top:8px;padding:7px 11px;background:rgba(245,158,11,.1);border:1px solid rgba(245,158,11,.35);border-radius:8px;font-size:.88rem;color:#7c4e0a}}
.quotes-section{{padding:10px 14px 14px}}
.quotes-title{{font-size:.82rem;font-weight:700;color:var(--text-sub);letter-spacing:.03em;margin-bottom:7px}}
.quotes-section blockquote{{margin:0 0 7px;padding:7px 11px;background:#f8fafb;border-left:3px solid #93c5fd;border-radius:0 8px 8px 0;color:#2a3a40;font-size:.9rem;line-height:1.6}}
.notice-bar{{background:#fff3cd;border:1px solid #ffc107;border-radius:var(--radius-sm);padding:7px 14px;font-size:.82rem;color:#664d03;margin-bottom:12px}}
.notice-bar a{{color:#5a3e02;font-weight:700}}
.report-footer{{margin-top:10px;font-size:.82rem;color:#7a909a;text-align:center;padding:12px 0 0;border-top:1px solid var(--card-border)}}
@media(max-width:780px){{
  .layout{{grid-template-columns:1fr}}
  .sidebar{{position:static;height:auto}}
  .main{{padding:14px 12px 24px}}
  .card-header{{flex-direction:column;align-items:flex-start}}
}}
</style>
</head>
<body>
<div class="layout">
  <aside class="sidebar">
    <div class="sidebar-top">
      <a href="00_總覽.html" class="back-link">← 總覽</a>
      <div class="sidebar-vendor">{html_lib.escape(vendor_name)}</div>
      <div class="sidebar-cat">{html_lib.escape(cat_title)}</div>
    </div>
    <div class="sidebar-section-label">子分類</div>
    {nav_items}
    <div class="cat-nav">
      <div class="sidebar-section-label">所有分類</div>
      {cat_links}
    </div>
    <div class="sidebar-footer">
      資料：{date_range}<br>
      產生：{generated_at}
    </div>
  </aside>
  <main class="main">
    <div class="notice-bar">本站內容為玩家社群討論之自動化彙整，僅供資訊參考。本站不鼓勵、不協助任何違反遊戲服務條款或相關法規之行為。<a href="../legal/disclaimer.html">閱讀完整免責聲明</a></div>
    <div class="topbar">
      <div class="topbar-title">{html_lib.escape(cat_title)}整理</div>
      <nav class="vendor-switch">{vendor_btns}</nav>
    </div>
    <div class="summary-bar">
      <div class="summary-kpi"><strong>{total:,}</strong> 則訊息</div>
      <div class="summary-kpi"><strong>{all_speakers}</strong> 位發言者</div>
      <div class="summary-kpi">資料期間 <strong>{date_range}</strong></div>
      <div class="summary-kpi">共 <strong>{len(sub_analyses)}</strong> 個子主題</div>
    </div>
    {''.join(cards_html)}
    <footer class="report-footer">
      {html_lib.escape(vendor_cfg.get('full_name', vendor_name))} ｜
      自動產生於 {generated_at}
    </footer>
  </main>
</div>
<script>
(function(){{
  const links=document.querySelectorAll('.sub-link');
  const cards=[...links].map(l=>document.querySelector(l.getAttribute('href')));
  const obs=new IntersectionObserver(entries=>{{
    entries.forEach(e=>{{
      if(e.isIntersecting){{
        links.forEach(l=>l.classList.remove('is-active'));
        const idx=cards.indexOf(e.target);
        if(idx>=0)links[idx].classList.add('is-active');
      }}
    }});
  }},{{threshold:0.3}});
  cards.forEach(c=>c&&obs.observe(c));
}})();
</script>
</body>
</html>'''


def _wrap_html(body_html, title):
    """包裝 HTML 內容。"""
    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{
    --bg-main: #f3f6f8;
    --bg-panel: #ffffff;
    --bg-soft: #eef3f1;
    --line: #d7e0dd;
    --text-main: #1f2a2e;
    --text-sub: #4d6168;
    --brand: #2a7f6f;
    --brand-2: #1f5e86;
    --brand-soft: #d8ece8;
    --warn-soft: #fff4dd;
    --radius-lg: 16px;
    --radius-md: 10px;
    --shadow: 0 12px 30px rgba(20, 45, 51, 0.08);
}}

* {{ box-sizing: border-box; }}

body {{
    margin: 0;
    color: var(--text-main);
    font-family: "Noto Sans TC", "Microsoft JhengHei", sans-serif;
    line-height: 1.75;
    background:
        radial-gradient(circle at 10% 10%, #dbeee8 0%, transparent 45%),
        radial-gradient(circle at 90% 8%, #dceaf4 0%, transparent 42%),
        linear-gradient(180deg, #f7fafb 0%, var(--bg-main) 100%);
    padding: 28px 16px 40px;
}}

.page {{
    max-width: 1080px;
    margin: 0 auto;
    background: var(--bg-panel);
    border: 1px solid var(--line);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow);
    overflow: hidden;
}}

.report-body {{
    padding: 20px 26px 30px;
}}

h1 {{
    margin: -20px -26px 20px;
    padding: 22px 26px 20px;
    font-size: 1.9rem;
    letter-spacing: 0.02em;
    color: #ffffff;
    background: linear-gradient(120deg, var(--brand) 0%, var(--brand-2) 90%);
}}

h2 {{
    margin-top: 1.4em;
    margin-bottom: 0.55em;
    padding: 8px 12px;
    font-size: 1.24rem;
    color: #173a41;
    border-left: 5px solid var(--brand);
    background: var(--bg-soft);
    border-radius: 0 var(--radius-md) var(--radius-md) 0;
}}

h3 {{
    margin-top: 1.1em;
    margin-bottom: 0.4em;
    color: #21515a;
    font-size: 1.06rem;
}}

hr {{
    border: 0;
    border-top: 1px solid var(--line);
    margin: 20px 0;
}}

table {{
    width: 100%;
    border-collapse: separate;
    border-spacing: 0;
    margin: 0.7em 0 1.05em;
    border: 1px solid var(--line);
    border-radius: 12px;
    overflow: hidden;
}}

th, td {{
    border-bottom: 1px solid #e7eeeb;
    padding: 9px 12px;
    text-align: left;
    vertical-align: top;
}}

th {{
    font-weight: 700;
    color: #f7fffd;
    background: linear-gradient(120deg, #267b6e 0%, #246286 90%);
}}

tr:last-child td {{
    border-bottom: 0;
}}

tr:nth-child(even) {{
    background: #fafdfc;
}}

blockquote {{
    margin: 0.95em 0;
    padding: 0.75em 1em;
    border-left: 4px solid #d4aa3a;
    border-radius: 8px;
    color: #5f4e22;
    background: var(--warn-soft);
}}

ul {{
    margin-top: 0.35em;
    padding-left: 1.15em;
}}

li {{
    margin-bottom: 0.34em;
}}

code {{
    font-family: Consolas, "Courier New", monospace;
    background: #eef3f8;
    border: 1px solid #dde5ee;
    border-radius: 6px;
    padding: 0.1em 0.42em;
}}

p {{
    margin-top: 0.45em;
    margin-bottom: 0.7em;
}}

@media (max-width: 820px) {{
    .report-body {{
        padding: 16px 14px 22px;
    }}

    h1 {{
        margin: -16px -14px 16px;
        padding: 16px 14px;
        font-size: 1.45rem;
    }}

    h2 {{
        font-size: 1.08rem;
    }}

    th, td {{
        padding: 8px 8px;
        font-size: 0.92rem;
    }}
}}
</style>
</head>
<body>
<main class="page">
    <article class="report-body">
{body_html}
    </article>
</main>
</body>
</html>"""


# ── CLI ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='從 spec/ JSONL 自動產生 Markdown 報告',
    )
    parser.add_argument(
        '--vendor', '-v',
        type=str,
        default=None,
        help=f'指定廠商（可用：{", ".join(list_vendors())}）',
    )
    parser.add_argument(
        '--html',
        action='store_true',
        help='同時產生 HTML 報告',
    )
    parser.add_argument(
        '--all',
        action='store_true',
        help='產生所有廠商的報告',
    )
    parser.add_argument(
        '--analyze',
        action='store_true',
        help='使用 Gemini AI 分析群友經驗與觀點（需要 GEMINI_API_KEY，消耗 token）',
    )

    args = parser.parse_args()

    # ── Logging 設定 ──
    log_level = logging.INFO
    handlers = [logging.StreamHandler(sys.stdout)]
    log_file = Path('analyzer.log')
    handlers.append(logging.FileHandler(log_file, encoding='utf-8'))
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=handlers,
    )

    if args.analyze and not _ANALYZER_AVAILABLE:
        print('⚠ 警告：AI 分析不可用')
        if _ANALYZER_ERROR:
            print(f'  錯誤：{_ANALYZER_ERROR}')
        print('  跳過 --analyze 選項')
        print()

    if args.all:
        print('=== 產生所有廠商報告 ===\n')
        for vname in list_vendors():
            print(f'【{vname}】')
            generate_reports(vname, html=args.html, analyze=args.analyze and _ANALYZER_AVAILABLE)
            print()
        return

    if not args.vendor:
        print('錯誤：請指定 --vendor 或 --all')
        parser.print_help()
        sys.exit(1)

    print(f'【{args.vendor}】')
    generate_reports(args.vendor, html=args.html, analyze=args.analyze and _ANALYZER_AVAILABLE)


if __name__ == '__main__':
    main()
