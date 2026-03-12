"""analyze.py 單元測試"""

import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import pandas as pd

# 讓 import 能找到同目錄的 analyze
import sys
sys.path.insert(0, os.path.dirname(__file__))

from analyze import (
    parse_chat_file,
    detect_action,
    detect_items,
    detect_prices,
    analyze,
    build_summary,
    build_exchange_rate_sheet,
    export_excel,
    _sanitize_text,
    ALIAS_TO_ITEM,
)


# ── Helper ──────────────────────────────────────────────────────
def _write_tmp(content: str) -> str:
    """寫入暫存檔並回傳路徑。"""
    fd, path = tempfile.mkstemp(suffix='.txt')
    with os.fdopen(fd, 'w', encoding='utf-8-sig') as f:
        f.write(content)
    return path


TODAY = datetime.now().strftime('%Y.%m.%d')
YESTERDAY = (datetime.now() - timedelta(days=1)).strftime('%Y.%m.%d')


# ══════════════════════════════════════════════════════════════════
#  parse_chat_file
# ══════════════════════════════════════════════════════════════════
class TestParseChatFile:
    def test_basic_parsing(self):
        txt = f"""{TODAY} 星期四
20:00 小明 收+7大馬 串串
20:01 小華 賣+5鋼手 3000T
"""
        path = _write_tmp(txt)
        msgs = parse_chat_file(path)
        os.unlink(path)

        assert len(msgs) == 2
        assert msgs[0]['time'] == '20:00'
        assert msgs[0]['nickname'] == '小明'
        assert '大馬' in msgs[0]['content']
        assert msgs[1]['nickname'] == '小華'

    def test_multiline_message(self):
        txt = f"""{TODAY} 星期四
20:00 小明 收+7大馬
也收+5鋼手
串串報價
20:01 小華 測試
"""
        path = _write_tmp(txt)
        msgs = parse_chat_file(path)
        os.unlink(path)

        assert len(msgs) == 2
        assert '鋼手' in msgs[0]['content']
        assert '串串報價' in msgs[0]['content']

    def test_date_change(self):
        txt = f"""{YESTERDAY} 星期三
23:59 小明 測試訊息一
{TODAY} 星期四
00:00 小華 測試訊息二
"""
        path = _write_tmp(txt)
        msgs = parse_chat_file(path)
        os.unlink(path)

        assert len(msgs) == 2
        assert msgs[0]['date'].strftime('%Y.%m.%d') == YESTERDAY
        assert msgs[1]['date'].strftime('%Y.%m.%d') == TODAY

    def test_skip_empty_lines(self):
        txt = f"""{TODAY} 星期四
20:00 小明 收鋼手

20:01 小華 賣鋼靴
"""
        path = _write_tmp(txt)
        msgs = parse_chat_file(path)
        os.unlink(path)

        assert len(msgs) == 2

    def test_nickname_only_message(self):
        """暱稱後沒有訊息內容。"""
        txt = f"""{TODAY} 星期四
20:00 售+5剛靴4700t
"""
        path = _write_tmp(txt)
        msgs = parse_chat_file(path)
        os.unlink(path)

        assert len(msgs) == 1
        assert msgs[0]['nickname'] == '售+5剛靴4700t'
        assert msgs[0]['content'] == ''


# ══════════════════════════════════════════════════════════════════
#  detect_action
# ══════════════════════════════════════════════════════════════════
class TestDetectAction:
    def test_buy(self):
        assert detect_action('收+7大馬 串串') == '收購'
        assert detect_action('買+5鋼手') == '收購'

    def test_sell(self):
        assert detect_action('賣+5鋼靴 3000T') == '出售'
        assert detect_action('售+7十字') == '出售'
        assert detect_action('出天幣 1:170') == '出售'

    def test_both(self):
        assert detect_action('收+7大馬 賣+5鋼手') == '買賣皆有'

    def test_none(self):
        assert detect_action('今天天氣好啊') is None
        assert detect_action('大家晚安') is None

    def test_skip_system_messages(self):
        assert detect_action('小明加入聊天') is None
        assert detect_action('小明已收回訊息') is None
        assert detect_action('Auto-reply 歡迎您') is None

    def test_false_positive_excluded(self):
        """收到、收回 等不該被判為收購。"""
        assert detect_action('有人收到信了嗎') is None
        assert detect_action('我買到了一個好東西') is None
        assert detect_action('出去玩了') is None
        assert detect_action('出現了一隻怪') is None

    def test_sell_with_出_digit(self):
        assert detect_action('出170 3張') == '出售'


# ══════════════════════════════════════════════════════════════════
#  detect_items
# ══════════════════════════════════════════════════════════════════
class TestDetectItems:
    def test_standard_name(self):
        items = detect_items('收+7大馬士革')
        assert ('大馬士革', '+7') in items

    def test_alias(self):
        items = detect_items('收+6艾盔')
        assert ('艾爾穆', '+6') in items

    def test_alias_2(self):
        items = detect_items('收+5保抖')
        assert ('保斗', '+5') in items

    def test_alias_剛靴(self):
        items = detect_items('收+5剛靴')
        assert ('鋼靴', '+5') in items

    def test_multiple_items(self):
        items = detect_items('收 +7精盾 +7精甲 +7紅頭 +5鋼手 +5保斗 +5內衣')
        names = [n for n, _ in items]
        assert '精盾' in names
        assert '精甲' in names
        assert '紅頭巾' in names
        assert '鋼手' in names
        assert '保斗' in names

    def test_no_plus_sign(self):
        """「7艾盔」沒有 + 號也應該抓到。"""
        items = detect_items('收 7艾盔')
        assert ('艾爾穆', '+7') in items

    def test_no_enhancement(self):
        items = detect_items('收多羅皮帶')
        assert ('多羅皮帶', '') in items

    def test_empty(self):
        items = detect_items('今天天氣好')
        assert items == []

    def test_夏納(self):
        items = detect_items('賣夏納變捲一組100張')
        assert ('夏納變身卷', '') in items


# ══════════════════════════════════════════════════════════════════
#  detect_prices
# ══════════════════════════════════════════════════════════════════
class TestDetectPrices:
    def test_T_price(self):
        prices = detect_prices('收+7大馬 3000T')
        assert ('3000', 'T') in prices

    def test_萬_price(self):
        prices = detect_prices('賣天幣 30萬')
        assert ('30', '萬') in prices

    def test_W_price(self):
        prices = detect_prices('天幣6W收品鑽')
        assert ('6', '萬') in prices

    def test_exchange_rate(self):
        prices = detect_prices('收幣1:195 LP或街口')
        assert ('1:195', '匯率') in prices

    def test_exchange_rate_fullwidth(self):
        prices = detect_prices('收幣：1：200')
        assert ('1:200', '匯率') in prices

    def test_decimal_T(self):
        prices = detect_prices('+7艾盔 1.7T')
        assert ('1.7', 'T') in prices

    def test_no_price(self):
        prices = detect_prices('收+7大馬 串串')
        assert prices == []

    def test_multiple_prices(self):
        prices = detect_prices('+7艾盔 1.7T 或 300萬天幣')
        units = [u for _, u in prices]
        assert 'T' in units
        assert '萬' in units


# ══════════════════════════════════════════════════════════════════
#  analyze (整合測試)
# ══════════════════════════════════════════════════════════════════
class TestAnalyze:
    def _make_messages(self):
        return [
            {'date': datetime.now(), 'time': '20:00', 'nickname': '小明',
             'content': '收+7大馬 3000T', 'raw': '小明 收+7大馬 3000T'},
            {'date': datetime.now(), 'time': '20:01', 'nickname': '小華',
             'content': '賣+5鋼手 2000T', 'raw': '小華 賣+5鋼手 2000T'},
            {'date': datetime.now(), 'time': '20:02', 'nickname': '小美',
             'content': '今天天氣好', 'raw': '小美 今天天氣好'},
            {'date': datetime.now() - timedelta(days=60), 'time': '10:00',
             'nickname': '老王', 'content': '收+8鎖破', 'raw': '老王 收+8鎖破'},
        ]

    def test_basic_analyze(self):
        df = analyze(self._make_messages(), days_limit=30)
        assert len(df) == 2  # 非交易訊息 + 過期都被排除

    def test_days_filter(self):
        df = analyze(self._make_messages(), days_limit=90)
        assert len(df) == 3  # 60天前的也包含

    def test_target_items_filter(self):
        df = analyze(self._make_messages(), days_limit=30, target_items={'大馬士革'})
        assert len(df) == 1
        assert df.iloc[0]['物品分類'] == '大馬士革'

    def test_skip_other(self):
        msgs = [
            {'date': datetime.now(), 'time': '20:00', 'nickname': '小明',
             'content': '收某神祕物品', 'raw': '小明 收某神祕物品'},
        ]
        df_skip = analyze(msgs, days_limit=30, skip_other=True)
        df_all = analyze(msgs, days_limit=30, skip_other=False)
        assert len(df_skip) == 0
        assert len(df_all) == 1

    def test_output_columns(self):
        df = analyze(self._make_messages(), days_limit=30)
        expected_cols = {'日期', '時間', '物品', '物品分類', '強化等級', '動作', '價格', '完整訊息'}
        assert expected_cols.issubset(set(df.columns))


# ══════════════════════════════════════════════════════════════════
#  build_summary
# ══════════════════════════════════════════════════════════════════
class TestBuildSummary:
    def test_summary(self):
        df = pd.DataFrame([
            {'物品': '+7 大馬士革', '物品分類': '大馬士革', '動作': '收購', '價格': '3000 T', '日期': '2026-03-12', '完整訊息': 'a'},
            {'物品': '+7 大馬士革', '物品分類': '大馬士革', '動作': '出售', '價格': '3500 T', '日期': '2026-03-12', '完整訊息': 'b'},
            {'物品': '+5 鋼手', '物品分類': '鋼手', '動作': '收購', '價格': '2000 T', '日期': '2026-03-12', '完整訊息': 'c'},
        ])
        summary = build_summary(df)
        assert len(summary) == 2
        assert '總筆數' in summary.columns
        assert '收購價(T)' in summary.columns
        assert '出售價(T)' in summary.columns

    def test_empty(self):
        summary = build_summary(pd.DataFrame())
        assert summary.empty


# ══════════════════════════════════════════════════════════════════
#  build_exchange_rate_sheet
# ══════════════════════════════════════════════════════════════════
class TestExchangeRate:
    def test_rate_extraction(self):
        df = pd.DataFrame([
            {'價格': '1:195 匯率', '日期': '2026-03-12', '時間': '20:00', '動作': '收購', '完整訊息': '收幣1:195'},
            {'價格': '3000 T', '日期': '2026-03-12', '時間': '20:01', '動作': '出售', '完整訊息': '賣鋼手3000T'},
        ])
        rate_df = build_exchange_rate_sheet(df)
        assert len(rate_df) == 1
        assert rate_df.iloc[0]['匯率'] == 195


# ══════════════════════════════════════════════════════════════════
#  export_excel
# ══════════════════════════════════════════════════════════════════
class TestExportExcel:
    def test_export_creates_file(self, tmp_path):
        df = pd.DataFrame([{
            '日期': '2026-03-12', '時間': '20:00', '物品': '+7 大馬士革',
            '物品分類': '大馬士革', '強化等級': '+7', '動作': '收購',
            '價格': '3000 T', '完整訊息': '收+7大馬 3000T',
        }])
        summary = pd.DataFrame([{
            '物品': '+7 大馬士革', '收購筆數': 1, '出售筆數': 0,
            '去重收購': 1, '去重出售': 0,
            '收購價(T)': '3000', '收購價(萬)': '-',
            '出售價(T)': '-', '出售價(萬)': '-',
            '日期範圍': '2026-03-12 ~ 2026-03-12', '總筆數': 1,
        }])
        rate_df = pd.DataFrame()

        out = tmp_path / 'test.xlsx'
        export_excel(df, summary, rate_df, out)
        assert out.exists()
        assert out.stat().st_size > 0

    def test_export_with_special_chars(self, tmp_path):
        """含 emoji 與特殊字元不該報錯。"""
        df = pd.DataFrame([{
            '日期': '2026-03-12', '時間': '20:00', '物品': '天幣',
            '物品分類': '天幣', '強化等級': '', '動作': '出售',
            '價格': '1:170 匯率', '完整訊息': '😂 出天幣 1:170 🎈\x01\x02',
        }])
        summary = build_summary(df)
        rate_df = pd.DataFrame()

        out = tmp_path / 'test_special.xlsx'
        export_excel(df, summary, rate_df, out)
        assert out.exists()


# ══════════════════════════════════════════════════════════════════
#  _sanitize_text
# ══════════════════════════════════════════════════════════════════
class TestSanitize:
    def test_remove_control_chars(self):
        assert _sanitize_text('hello\x00world') == 'helloworld'
        assert _sanitize_text('abc\x01\x0edef') == 'abcdef'

    def test_keep_normal_text(self):
        assert _sanitize_text('正常文字 abc 123') == '正常文字 abc 123'

    def test_non_string(self):
        assert _sanitize_text(123) == 123
        assert _sanitize_text(None) is None


# ══════════════════════════════════════════════════════════════════
#  端對端測試
# ══════════════════════════════════════════════════════════════════
class TestEndToEnd:
    def test_full_pipeline(self, tmp_path):
        """從 txt 到 Excel 的完整流程。"""
        txt = f"""{TODAY} 星期四
20:00 買東西 收 +7精盾 +7精甲 +7紅頭 +5鋼手 +5保斗 +5內衣 串
20:00 Unknown 收幣：1：200（1-10張）
20:04 Unknown 買+5鋼手、+5內衣、+5剛靴，賣的串串唷
20:12 Unknown 收騎士裝、 +6 大馬 2500t +6紅頭巾 1400t +6 精煉 1200t
20:14 Unknown 賣30等包季敏妖 +6十字 多羅皮帶 退坑價26000T
20:22 大香蕉 賣夏納100張=800T 防卷=210T
20:35 天下人間 收預約序號300
"""
        chat_file = tmp_path / 'chat.txt'
        chat_file.write_text(txt, encoding='utf-8-sig')

        msgs = parse_chat_file(str(chat_file))
        assert len(msgs) >= 6

        df = analyze(msgs, days_limit=7, skip_other=True)
        assert len(df) > 0
        assert '物品' in df.columns

        summary = build_summary(df)
        assert len(summary) > 0

        rate_df = build_exchange_rate_sheet(df)

        out = tmp_path / 'result.xlsx'
        export_excel(df, summary, rate_df, out)
        assert out.exists()

        # 驗證 Excel 可讀取
        loaded = pd.read_excel(out, sheet_name='交易明細')
        assert len(loaded) == len(df)

    def test_real_chat_file(self):
        """測試真實的聊天記錄檔案（如果存在）。"""
        real_file = Path(__file__).parent / 'txt' / '[LINE]天堂經典版：冥王黑帝斯交易討論群.txt'
        if not real_file.exists():
            pytest.skip('真實聊天檔案不存在')

        msgs = parse_chat_file(str(real_file))
        assert len(msgs) > 1000

        df = analyze(msgs, days_limit=14, skip_other=True)
        assert len(df) > 100
        assert df['物品分類'].nunique() > 5
