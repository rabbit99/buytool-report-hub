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

from vendor_config import get_vendor, list_vendors

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


def _pick_informative(records, max_items=5):
    """挑選資訊性內容（非問句、有實質資訊、優先高可靠來源）。"""
    candidates = []
    for r in records:
        content = r.get('content', '').strip()
        if not content or len(content) < 8:
            continue
        if _is_question(content):
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


# ── 報告產生器 ──────────────────────────────────────────────────

def _generate_category_report(vendor_name, cat_file, cat_title, records, date_range, analyze=False):
    """產生單一分類的 Markdown 報告（分析總結式）。
    
    Args:
        analyze: 若 True，使用 Gemini AI 分析群友經驗與觀點（消耗 token）
    """
    grouped = _group_by_sub(records)
    total = len(records)
    all_speakers = set(r.get('nickname', '') for r in records if r.get('nickname'))

    lines = []
    lines.append(f'# {vendor_name} — {cat_title}整理\n')
    lines.append(f'> 資料來源：LINE 群組（{date_range}）｜自動產生於 {datetime.now().strftime("%Y/%m/%d %H:%M")}')
    lines.append(f'> 分析基礎：{total} 則訊息、{len(all_speakers)} 位發言者\n')
    lines.append('---\n')

    # 摘要表格
    lines.append('## 討論熱度\n')
    lines.append('| 子分類 | 討論量 | 發言者 | 佔比 |')
    lines.append('|--------|--------|--------|------|')
    for sub, recs in grouped.items():
        pct = len(recs) / total * 100 if total else 0
        spk = len(set(r.get('nickname', '') for r in recs if r.get('nickname')))
        lines.append(f'| {sub} | {len(recs)} 則 | {spk} 人 | {pct:.0f}% |')
    lines.append('')

    # 各子分類分析
    section_num = 1
    chinese_nums = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十']
    sub_summaries = []  # 收集每個子分類的摘要供總結用

    for sub, recs in grouped.items():
        cn = chinese_nums[section_num - 1] if section_num <= len(chinese_nums) else str(section_num)
        lines.append(f'---\n')
        lines.append(f'## {cn}、{sub}\n')

        analysis = _analyze_subcategory(sub, recs)

        # 1. 熱門實體表格（若有明確實體）
        top_entities = analysis['entity_counts'].most_common(10)
        # 過濾掉太泛化的關鍵字（只保留具體實體）
        meaningful_entities = [
            (e, c) for e, c in top_entities
            if len(e) >= 2 and c >= 2
        ]
        if meaningful_entities:
            lines.append('### 熱門關鍵字\n')
            lines.append('| 關鍵字 | 提及次數 | 討論人數 |')
            lines.append('|--------|----------|----------|')
            for entity, count in meaningful_entities[:8]:
                spk_count = len(analysis['entity_speakers'].get(entity, set()))
                lines.append(f'| {entity} | {count} | {spk_count} |')
            lines.append('')

        # 2. 數值資料（若有）
        if analysis['data_points']:
            lines.append('### 數據信號摘要\n')
            signal_lines = _build_numeric_signal_lines(analysis['data_points'])
            for line in signal_lines:
                lines.append(line)
            lines.append('')

        # 3. 重點資訊（經分析的關鍵判斷）
        lines.append('### 群友經驗與觀點\n')
        for line in _build_observation_summary(sub, analysis, meaningful_entities):
            lines.append(line)

        if analyze and _ANALYZER_AVAILABLE:
            ai_prompt_lines = [
                f'子分類：{sub}',
                f'不重複訊息：{analysis["unique"]}',
                f'參與者：{analysis["speakers"]}',
                f'提問訊號：{analysis["question_count"]}',
                f'高頻關鍵字：{"、".join(e for e, _ in meaningful_entities[:5]) or "無"}',
            ]
            result = analyze_messages_batch(
                vendor_name,
                cat_title,
                sub,
                ai_prompt_lines,
                speaker_count=analysis['speakers'],
            )
            if result.get('status') == 'success' and result.get('analysis'):
                lines.append(f'- AI 補充判讀：{result["analysis"]}')
            lines.append('')

        # 收集子分類摘要
        sub_summary = f'**{sub}**：{analysis["unique"]} 則不重複討論、{analysis["speakers"]} 人參與'
        if meaningful_entities:
            top3 = '、'.join(e for e, _ in meaningful_entities[:3])
            sub_summary += f'，熱門：{top3}'
        sub_summaries.append(sub_summary)

        section_num += 1

    # ── 總體總結 ──
    lines.append('---\n')
    lines.append(f'## 總體總結\n')

    # 各子分類概況
    lines.append('### 各面向概況\n')
    for s in sub_summaries:
        lines.append(f'- {s}')
    lines.append('')

    # 整體判斷
    lines.append('### 整體判斷\n')
    top_sub = list(grouped.keys())[0] if grouped else ''
    top_count = len(list(grouped.values())[0]) if grouped else 0
    top_pct = top_count / total * 100 if total else 0
    lines.append(
        f'- 本分類共 **{total} 則**訊息、**{len(all_speakers)}** 位參與者，'
        f'最熱門子題為「{top_sub}」（佔 {top_pct:.0f}%）'
    )

    # 日期分佈
    date_counts = Counter(r.get('date', '') for r in records if r.get('date'))
    if len(date_counts) > 1:
        peak_date, peak_count = date_counts.most_common(1)[0]
        lines.append(f'- 討論高峰：{peak_date}（{peak_count} 則）')

    # 來源分佈
    src_counts = Counter(r.get('src', '') for r in records)
    if len(src_counts) > 1:
        src_parts = '、'.join(f'{s}({c}則)' for s, c in src_counts.most_common())
        lines.append(f'- 來源分佈：{src_parts}')

    lines.append('')

    return '\n'.join(lines)


def generate_reports(vendor_name, html=False, analyze=False):
    """產生一個廠商的所有報告。
    
    Args:
        analyze: 若 True，使用 Gemini AI 分析群友經驗與觀點（消耗 token）
    """
    vendor_cfg = get_vendor(vendor_name)
    spec_dir = vendor_cfg['spec_dir']
    report_dir = vendor_cfg['report_dir']
    report_dir.mkdir(parents=True, exist_ok=True)

    if not spec_dir.exists():
        print(f'  錯誤：找不到 {spec_dir}，請先執行 ingest.py')
        return

    meta = _load_meta(spec_dir)
    generated = 0

    for cat_file, cat_title in CATEGORIES:
        jsonl_path = spec_dir / f'{cat_file}.jsonl'
        records = _load_jsonl(jsonl_path)
        if not records:
            print(f'  {cat_file}：無資料，跳過')
            continue

        date_range = _date_range_str(records)
        md_content = _generate_category_report(
            vendor_cfg['name'], cat_file, cat_title, records, date_range, analyze=analyze,
        )

        # 寫入 Markdown
        md_path = report_dir / f'{cat_file}.md'
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(md_content)
        print(f'  ✓ {md_path.name}（{len(records)} 則）')
        generated += 1

        # 寫入 HTML
        if html:
            try:
                import markdown
                html_content = _wrap_html(
                    markdown.markdown(md_content, extensions=['tables']),
                    f'{vendor_cfg["name"]} — {cat_title}',
                )
                html_path = report_dir / f'{cat_file}.html'
                with open(html_path, 'w', encoding='utf-8') as f:
                    f.write(html_content)
                print(f'    + {html_path.name}')
            except ImportError:
                print('    ⚠ 需要 markdown 套件才能產生 HTML：pip install markdown')

    # 產生總覽摘要
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
    """產生儀表板風格的總覽 HTML。"""
    generated_at = datetime.now().strftime("%Y/%m/%d %H:%M")
    total_categories = len([c for c in category_stats if c['count'] > 0])
    total_messages = sum(c['count'] for c in category_stats)
    total_speakers = sum(c['speakers'] for c in category_stats)

    top_category = max(category_stats, key=lambda c: c['count'], default=None)
    top_label = top_category['title'] if top_category and top_category['count'] > 0 else '—'

    cards_html = (
        f"<div class='kpi-card'><span class='kpi-label'>累計訊息</span><strong>{total_messages:,}</strong></div>"
        f"<div class='kpi-card'><span class='kpi-label'>分類覆蓋</span><strong>{total_categories} / {len(CATEGORIES)}</strong></div>"
        f"<div class='kpi-card'><span class='kpi-label'>參與者估計</span><strong>{total_speakers:,}</strong></div>"
        f"<div class='kpi-card'><span class='kpi-label'>最熱分類</span><strong>{html_lib.escape(top_label)}</strong></div>"
    )

    switch_buttons = []
    for vname in list_vendors():
        vcfg = get_vendor(vname)
        active = " is-active" if vname == vendor_cfg['name'] else ""
        href = f"../{vname}/00_總覽.html"
        switch_buttons.append(
            "<a class='vendor-btn{active}' href='{href}'>{name}</a>".format(
                active=active,
                href=href,
                name=html_lib.escape(vname),
            ),
        )

    nav_buttons = []
    for cat in category_stats:
        disabled = " is-disabled" if cat['count'] == 0 else ""
        href = f"{cat['file']}.html" if cat['count'] > 0 else "#"
        nav_buttons.append(
            "<a class='nav-btn{disabled}' href='{href}'>{title}<span>{count} 則</span></a>".format(
                disabled=disabled,
                href=href,
                title=html_lib.escape(cat['title']),
                count=cat['count'],
            ),
        )

    unique_ranges = {}
    for r in meta.get('ingested_ranges', []) if meta else []:
        file_name = str(r.get('file', '')).strip()
        min_date = str(r.get('min_date', '')).strip()
        max_date = str(r.get('max_date', '')).strip()
        msg_count = str(r.get('msg_count', '')).strip()
        key = (
            file_name,
            min_date,
            max_date,
            msg_count,
        )
        current = unique_ranges.get(key)
        if not current or r.get('ingested_at', '') > current.get('ingested_at', ''):
            unique_ranges[key] = r
    deduped_ranges = list(unique_ranges.values())

    source_rows = []
    for r in deduped_ranges:
        fname = html_lib.escape(r['file'])
        src = sources_cfg.get(r['file'], {})
        label = html_lib.escape(src.get('label', '—'))
        pri = src.get('priority', 5)
        date_range = f"{r['min_date']} ~ {r['max_date']}"
        source_rows.append(
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                fname,
                label,
                pri,
                html_lib.escape(date_range),
                r['msg_count'],
            ),
        )
    if not source_rows:
        source_rows.append("<tr><td colspan='5'>尚無來源資料</td></tr>")

    timeline_items = []
    ranges_sorted = sorted(
        deduped_ranges,
        key=lambda x: x.get('ingested_at', ''),
        reverse=True,
    )
    for item in ranges_sorted[:8]:
        src = sources_cfg.get(item.get('file', ''), {})
        label = src.get('label', '一般來源')
        when = item.get('ingested_at', '')[:16].replace('T', ' ')
        summary = f"{item.get('min_date', '')} ~ {item.get('max_date', '')}｜{item.get('msg_count', 0)} 則"
        timeline_items.append(
            "<li><time>{}</time><p><strong>{}</strong> {}</p><span>{}</span></li>".format(
                html_lib.escape(when or '未知時間'),
                html_lib.escape(label),
                html_lib.escape(item.get('file', '')),
                html_lib.escape(summary),
            ),
        )
    if not timeline_items:
        timeline_items.append("<li><time>—</time><p><strong>尚無更新記錄</strong></p><span>請先執行 ingest</span></li>")

    category_rows = []
    for cat in category_stats:
        link = f"<a href='{cat['file']}.html'>開啟報告</a>" if cat['count'] > 0 else "—"
        category_rows.append(
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                html_lib.escape(cat['title']),
                cat['count'],
                cat['speakers'],
                link,
            ),
        )

    html_content = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html_lib.escape(vendor_cfg['name'])} — 儀表板總覽</title>
<style>
:root {{
    --bg: #f4f8fa;
    --panel: #ffffff;
    --brand: #1f7a6c;
    --brand-2: #1e5f88;
    --line: #d7e3e8;
    --text: #1a2b34;
    --muted: #58717c;
    --shadow: 0 14px 28px rgba(19, 48, 58, 0.1);
}}
* {{ box-sizing: border-box; }}
body {{
    margin: 0;
    font-family: "Noto Sans TC", "Microsoft JhengHei", sans-serif;
    color: var(--text);
    background:
        radial-gradient(circle at 8% 8%, #d9ede8 0%, transparent 40%),
        radial-gradient(circle at 92% 5%, #d7e6f3 0%, transparent 38%),
        var(--bg);
    padding: 22px 14px 30px;
}}
.shell {{
    max-width: 1180px;
    margin: 0 auto;
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 18px;
    overflow: hidden;
    box-shadow: var(--shadow);
}}
.hero {{
    padding: 20px 24px;
    background: linear-gradient(120deg, var(--brand), var(--brand-2));
    color: #fff;
}}
.hero h1 {{ margin: 0 0 4px; font-size: 1.75rem; }}
.hero p {{ margin: 0; opacity: 0.92; }}
.content {{ padding: 18px 20px 26px; }}
.vendor-switch {{
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 12px;
}}
.vendor-btn {{
    display: inline-block;
    text-decoration: none;
    color: #1a3c48;
    background: #eff6f9;
    border: 1px solid #d3e1e8;
    border-radius: 999px;
    padding: 6px 12px;
    font-size: 0.92rem;
    font-weight: 700;
}}
.vendor-btn.is-active {{
    color: #fff;
    border-color: rgba(255,255,255,0.45);
    background: rgba(17, 47, 58, 0.45);
}}
.kpi-grid {{
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 10px;
    margin-bottom: 14px;
}}
.kpi-card {{
    border: 1px solid var(--line);
    border-radius: 12px;
    background: #f8fcfd;
    padding: 10px 12px;
}}
.kpi-label {{ display: block; font-size: 0.82rem; color: var(--muted); margin-bottom: 2px; }}
.kpi-card strong {{ font-size: 1.2rem; }}
.panel {{
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 12px;
    margin-bottom: 12px;
    background: #fff;
}}
.panel h2 {{ margin: 0 0 10px; font-size: 1.08rem; color: #1b4b5a; }}
.nav-grid {{
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 8px;
}}
.nav-btn {{
    display: block;
    text-decoration: none;
    color: #173b43;
    background: #eef5f8;
    border: 1px solid #d2e0e7;
    border-radius: 10px;
    padding: 10px;
    font-weight: 700;
}}
.nav-btn span {{
    display: block;
    margin-top: 4px;
    font-size: 0.82rem;
    color: var(--muted);
    font-weight: 500;
}}
.nav-btn:hover {{ background: #e4f0f5; }}
.nav-btn.is-disabled {{ pointer-events: none; opacity: 0.56; }}
.timeline {{
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 8px;
}}
.timeline li {{
    border: 1px solid #dfe9ee;
    background: #f9fcfe;
    border-radius: 10px;
    padding: 8px 10px;
    margin: 0;
}}
.timeline time {{
    display: block;
    color: #40616f;
    font-size: 0.82rem;
    margin-bottom: 2px;
}}
.timeline p {{
    margin: 0;
    font-size: 0.95rem;
}}
.timeline span {{
    color: #5f7680;
    font-size: 0.83rem;
}}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ border-bottom: 1px solid #e8eef1; padding: 8px 7px; text-align: left; font-size: 0.94rem; }}
th {{ color: #244854; font-weight: 700; background: #f2f8fb; }}
tr:last-child td {{ border-bottom: 0; }}
@media (max-width: 900px) {{
    .kpi-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    .nav-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
}}
@media (max-width: 560px) {{
    .hero h1 {{ font-size: 1.34rem; }}
    .kpi-grid {{ grid-template-columns: 1fr; }}
    .nav-grid {{ grid-template-columns: 1fr; }}
}}
</style>
</head>
<body>
<main class="shell">
    <header class="hero">
        <h1>{html_lib.escape(vendor_cfg['name'])} 報告儀表板</h1>
        <p>{html_lib.escape(vendor_cfg['full_name'])} ｜ 更新時間 {generated_at}</p>
    </header>
    <section class="content">
        <nav class="vendor-switch">{''.join(switch_buttons)}</nav>
        <div class="kpi-grid">{cards_html}</div>

        <section class="panel">
            <h2>分類導覽</h2>
            <div class="nav-grid">{''.join(nav_buttons)}</div>
        </section>

        <section class="panel">
            <h2>來源覆蓋</h2>
            <table>
                <thead>
                    <tr><th>資料檔案</th><th>標籤</th><th>優先級</th><th>日期範圍</th><th>訊息數</th></tr>
                </thead>
                <tbody>{''.join(source_rows)}</tbody>
            </table>
        </section>

        <section class="panel">
            <h2>最近更新時間軸</h2>
            <ul class="timeline">{''.join(timeline_items)}</ul>
        </section>

        <section class="panel">
            <h2>分類統計</h2>
            <table>
                <thead>
                    <tr><th>分類</th><th>訊息數</th><th>參與者</th><th>連結</th></tr>
                </thead>
                <tbody>{''.join(category_rows)}</tbody>
            </table>
        </section>
    </section>
</main>
</body>
</html>"""

    html_path = report_dir / '00_總覽.html'
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    print(f'  ✓ {html_path.name}（儀表板）')


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
