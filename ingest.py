"""將 LINE 聊天 txt 消化到 spec/ JSONL 中繼資料。

流程：
1. 讀取 txt → parse_chat_file() 解析
2. classify_message() 分類每則訊息
3. 去重後 append 到 spec/<vendor>/XX_分類.jsonl
4. 更新 _meta.json

用法：
    python ingest.py --vendor 機器熊
    python ingest.py --vendor 機器熊 --file path/to/chat.txt
    python ingest.py --vendor 機器熊 --force   # 清空 spec 重建
    python ingest.py --all                      # 消化所有廠商
"""

import json
import argparse
import sys
from datetime import datetime
from pathlib import Path
from collections import defaultdict

from analyze import parse_chat_file
from analyze_guide import (
    set_vendor, classify_message, _is_noise, _is_ad,
    ALL_CATEGORIES,
)
from vendor_config import get_vendor, list_vendors

# ── 分類檔名對照 ───────────────────────────────────────────────
CATEGORY_FILES = {
    '掛機攻略': '01_掛機攻略.jsonl',
    '設定教學': '02_設定教學.jsonl',
    '帳號安全': '03_帳號安全.jsonl',
    'Bug排除': '04_Bug排除.jsonl',
    '環境設定': '05_環境設定.jsonl',
    '買賣交易': '06_買賣交易.jsonl',
}


def _dedup_key(msg_dict):
    """產生去重 key：(date, time, nickname, content前50字)"""
    return (
        msg_dict.get('date', ''),
        msg_dict.get('time', ''),
        msg_dict.get('nickname', ''),
        msg_dict.get('content', '')[:50],
    )


def _load_existing_keys(spec_dir):
    """讀取 spec 目錄中所有 JSONL，回傳已有的去重 key set。"""
    keys = set()
    if not spec_dir.exists():
        return keys
    for jf in spec_dir.glob('*.jsonl'):
        with open(jf, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    keys.add(_dedup_key(rec))
                except json.JSONDecodeError:
                    continue
    return keys


def _load_meta(spec_dir):
    """讀取 _meta.json，若不存在則回傳空結構。"""
    meta_path = spec_dir / '_meta.json'
    if meta_path.exists():
        with open(meta_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {
        'vendor': '',
        'ingested_ranges': [],
        'total_messages': 0,
        'last_updated': '',
    }


def _save_meta(spec_dir, meta):
    """寫入 _meta.json。"""
    meta_path = spec_dir / '_meta.json'
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def ingest_vendor(vendor_name, file_path=None, force=False):
    """消化一個廠商的聊天記錄到 spec。

    Args:
        vendor_name: 廠商名稱
        file_path: 指定 txt 路徑（None = 自動找 txt_dir）
        force: True = 清空 spec 重建

    Returns:
        dict: {'new': int, 'skipped': int, 'total': int}
    """
    vendor_cfg = set_vendor(vendor_name)
    spec_dir = vendor_cfg['spec_dir']
    spec_dir.mkdir(parents=True, exist_ok=True)

    # ── 決定輸入檔案 ──
    if file_path:
        files = [Path(file_path)]
    else:
        txt_dir = vendor_cfg['txt_dir']
        if not txt_dir.exists():
            print(f'錯誤：找不到 {txt_dir}')
            return None
        files = sorted(txt_dir.glob('*.txt'))
        if not files:
            print(f'錯誤：{txt_dir} 中沒有 .txt 檔案')
            return None

    # ── force 模式：清空 spec ──
    if force:
        for jf in spec_dir.glob('*.jsonl'):
            jf.unlink()
        meta_path = spec_dir / '_meta.json'
        if meta_path.exists():
            meta_path.unlink()
        print(f'  [force] 已清空 {spec_dir}')

    # ── 載入已有的去重 key ──
    existing_keys = _load_existing_keys(spec_dir)
    meta = _load_meta(spec_dir)
    meta['vendor'] = vendor_cfg['name']

    total_new = 0
    total_skipped = 0

    for fp in files:
        print(f'  解析：{fp.name} ...')
        messages = parse_chat_file(str(fp))
        print(f'    共 {len(messages)} 則訊息')

        # 偵測日期範圍
        dates = [m['date'] for m in messages if m.get('date')]
        if not dates:
            print(f'    ⚠ 無法偵測日期範圍，跳過')
            continue
        min_date = min(dates).strftime('%Y-%m-%d')
        max_date = max(dates).strftime('%Y-%m-%d')
        print(f'    日期範圍：{min_date} ~ {max_date}')

        # ── 分類 & 去重 & 寫入 ──
        # 用 dict 收集本次要寫入的資料（按分類）
        buffers = defaultdict(list)
        file_new = 0
        file_skipped = 0

        # 查詢此檔案的來源優先級
        sources_cfg = vendor_cfg.get('sources', {})
        src_info = sources_cfg.get(fp.name, {})
        file_priority = src_info.get('priority', 5)  # 預設 5
        file_label = src_info.get('label', '')
        if file_label:
            print(f'    來源標籤：{file_label}（優先級 {file_priority}）')

        for msg in messages:
            if _is_noise(msg):
                continue
            if _is_ad(msg):
                continue

            classifications = classify_message(msg)
            if not classifications:
                continue

            date_str = msg['date'].strftime('%Y-%m-%d') if msg.get('date') else ''
            time_str = msg.get('time', '')
            nickname = msg.get('nickname', '')
            content = msg.get('content', '')

            for main_cat, sub_cat, matched_kw in classifications:
                rec = {
                    'date': date_str,
                    'time': time_str,
                    'nickname': nickname,
                    'content': content,
                    'sub': sub_cat,
                    'keywords': ', '.join(matched_kw),
                    'src': fp.name,
                    'priority': file_priority,
                }
                key = _dedup_key(rec)
                if key in existing_keys:
                    file_skipped += 1
                    continue
                existing_keys.add(key)
                buffers[main_cat].append(rec)
                file_new += 1

        # ── 寫入 JSONL ──
        for main_cat, records in buffers.items():
            jsonl_name = CATEGORY_FILES.get(main_cat)
            if not jsonl_name:
                continue
            jsonl_path = spec_dir / jsonl_name
            with open(jsonl_path, 'a', encoding='utf-8') as f:
                for rec in records:
                    f.write(json.dumps(rec, ensure_ascii=False) + '\n')

        # ── 更新 meta ──
        meta['ingested_ranges'].append({
            'file': fp.name,
            'min_date': min_date,
            'max_date': max_date,
            'msg_count': len(messages),
            'new_records': file_new,
            'ingested_at': datetime.now().isoformat(timespec='seconds'),
        })

        total_new += file_new
        total_skipped += file_skipped
        print(f'    ✓ 新增 {file_new} 則，跳過 {file_skipped} 則重複')

    # ── 統計總數 ──
    total = 0
    for jf in spec_dir.glob('*.jsonl'):
        with open(jf, 'r', encoding='utf-8') as f:
            total += sum(1 for line in f if line.strip())
    meta['total_messages'] = total
    meta['last_updated'] = datetime.now().isoformat(timespec='seconds')
    _save_meta(spec_dir, meta)

    print(f'  ── 完成：新增 {total_new}，跳過 {total_skipped}，spec 累計 {total} 則')
    return {'new': total_new, 'skipped': total_skipped, 'total': total}


# ── CLI ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='將 LINE 聊天 txt 消化到 spec/ JSONL 中繼資料',
    )
    parser.add_argument(
        '--vendor', '-v',
        type=str,
        default=None,
        help=f'指定廠商（可用：{", ".join(list_vendors())}）',
    )
    parser.add_argument(
        '--file', '-f',
        type=str,
        default=None,
        help='指定 txt 檔案路徑（預設：自動找 txt/<vendor>/）',
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='清空 spec 後重新消化（用於修正分類規則後重建）',
    )
    parser.add_argument(
        '--all',
        action='store_true',
        help='消化所有已設定的廠商',
    )

    args = parser.parse_args()

    if args.all:
        print('=== 消化所有廠商 ===\n')
        for vname in list_vendors():
            print(f'【{vname}】')
            ingest_vendor(vname, force=args.force)
            print()
        return

    if not args.vendor:
        print('錯誤：請指定 --vendor 或 --all')
        parser.print_help()
        sys.exit(1)

    print(f'【{args.vendor}】')
    ingest_vendor(args.vendor, file_path=args.file, force=args.force)


if __name__ == '__main__':
    main()
