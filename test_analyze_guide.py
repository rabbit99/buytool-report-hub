"""analyze_guide.py 單元測試"""

import os
import tempfile
from datetime import datetime, timedelta

import pytest
import pandas as pd

import sys
sys.path.insert(0, os.path.dirname(__file__))

from analyze_guide import (
    classify_message,
    analyze_guide,
    build_summary,
    extract_farming_tips,
    extract_setting_tips,
    export_excel,
    _is_noise,
    _is_ad,
    _match_keywords,
    _deduplicate_tips,
)
from analyze import parse_chat_file


# ── Helper ──────────────────────────────────────────────────────
def _write_tmp(content: str) -> str:
    fd, path = tempfile.mkstemp(suffix='.txt')
    with os.fdopen(fd, 'w', encoding='utf-8-sig') as f:
        f.write(content)
    return path


TODAY = datetime.now().strftime('%Y.%m.%d')


def _make_msg(content, nickname='測試者', time='20:00'):
    return {
        'date': datetime.now(),
        'time': time,
        'nickname': nickname,
        'content': content,
        'raw': f'{nickname} {content}',
    }


# ══════════════════════════════════════════════════════════════════
#  _is_noise
# ══════════════════════════════════════════════════════════════════
class TestIsNoise:
    def test_system_message(self):
        msg = _make_msg('', nickname='小明加入聊天')
        msg['content'] = '加入聊天'
        assert _is_noise(msg) is True

    def test_recalled(self):
        msg = _make_msg('已收回訊息')
        assert _is_noise(msg) is True

    def test_sticker(self):
        msg = _make_msg('貼圖')
        assert _is_noise(msg) is True

    def test_image(self):
        msg = _make_msg('圖片')
        assert _is_noise(msg) is True

    def test_short(self):
        msg = _make_msg('哦')
        assert _is_noise(msg) is True

    def test_valid(self):
        msg = _make_msg('古丁三樓一小時大概3萬')
        assert _is_noise(msg) is False


# ══════════════════════════════════════════════════════════════════
#  _is_ad
# ══════════════════════════════════════════════════════════════════
class TestIsAd:
    def test_ad_nickname(self):
        msg = _make_msg('代客收費設定', nickname='機加酒')
        assert _is_ad(msg) is True

    def test_ad_content(self):
        msg = _make_msg('代客收費設定破解全圖ATS 自動練功 洗魔補血 串聊')
        assert _is_ad(msg) is True

    def test_normal(self):
        msg = _make_msg('古三掛一天大概30萬', nickname='路人')
        assert _is_ad(msg) is False


# ══════════════════════════════════════════════════════════════════
#  classify_message
# ══════════════════════════════════════════════════════════════════
class TestClassify:
    def test_farming_location(self):
        msg = _make_msg('古丁4樓掛一天大概30萬')
        results = classify_message(msg)
        cats = [r[0] for r in results]
        assert '掛機攻略' in cats

    def test_farming_income(self):
        msg = _make_msg('一小時大概1萬多')
        results = classify_message(msg)
        sub_cats = [r[1] for r in results]
        assert '收益' in sub_cats

    def test_farming_gear(self):
        msg = _make_msg('防武滿才適合掛古丁')
        results = classify_message(msg)
        sub_cats = [r[1] for r in results]
        assert '裝備門檻' in sub_cats

    def test_setting_half_auto(self):
        msg = _make_msg('半自動你只要按開始 開始的位置就是中心')
        results = classify_message(msg)
        cats = [r[0] for r in results]
        assert '設定教學' in cats

    def test_setting_teleport(self):
        msg = _make_msg('無經驗40秒順移 清完怪之後就順移飛走')
        results = classify_message(msg)
        sub_cats = [r[1] for r in results]
        assert '順移瞬移' in sub_cats

    def test_setting_transform(self):
        msg = _make_msg('要一般變身 身上別放 金色傳說')
        results = classify_message(msg)
        sub_cats = [r[1] for r in results]
        assert '變身設定' in sub_cats

    def test_security_ban(self):
        msg = _make_msg('掛好掛滿 鎖一開二')
        results = classify_message(msg)
        cats = [r[0] for r in results]
        assert '帳號安全' in cats

    def test_security_prison(self):
        msg = _make_msg('進過兩次監獄沒事 還是秒登號')
        results = classify_message(msg)
        sub_cats = [r[1] for r in results]
        assert '監獄機制' in sub_cats

    def test_bug_crash(self):
        msg = _make_msg('開了立馬閃退 防毒要關')
        results = classify_message(msg)
        cats = [r[0] for r in results]
        assert 'Bug排除' in cats

    def test_env_antivirus(self):
        msg = _make_msg('防毒防火牆要關掉')
        results = classify_message(msg)
        sub_cats = [r[1] for r in results]
        assert '防毒防火牆' in sub_cats

    def test_trade_residual_card(self):
        msg = _make_msg('收一張殘卡 LP')
        results = classify_message(msg)
        cats = [r[0] for r in results]
        assert '買賣交易' in cats

    def test_multiple_categories(self):
        msg = _make_msg('防武滿掛古三 半自動就好')
        results = classify_message(msg)
        cats = set(r[0] for r in results)
        assert '掛機攻略' in cats
        assert '設定教學' in cats


# ══════════════════════════════════════════════════════════════════
#  analyze_guide
# ══════════════════════════════════════════════════════════════════
class TestAnalyzeGuide:
    def test_full_pipeline(self):
        txt = f"""{TODAY} 星期一
20:00 路人甲 古丁4樓掛一天大概30萬
20:01 路人乙 半自動設定補血40%比較安全
20:02 路人丙加入聊天
20:03 路人丁 進過監獄沒事 出來繼續掛
20:04 路人戊 貼圖
20:05 機加酒 代客收費設定破解全圖ATS 串聊
"""
        path = _write_tmp(txt)
        messages = parse_chat_file(path)
        os.unlink(path)

        result = analyze_guide(messages)
        # 系統訊息和廣告不應出現
        all_contents = []
        for cat_data in result.values():
            for msgs in cat_data.values():
                all_contents.extend(m['content'] for m in msgs)
        assert not any('加入聊天' in c for c in all_contents)
        assert not any('代客收費' in c for c in all_contents)
        # 有效內容應該被分類
        assert len(result['掛機攻略']) > 0
        assert len(result['設定教學']) > 0

    def test_days_limit(self):
        old_date = (datetime.now() - timedelta(days=10)).strftime('%Y.%m.%d')
        txt = f"""{old_date} 星期一
20:00 路人甲 古丁掛一天30萬
{TODAY} 星期一
20:00 路人乙 蟻洞掛一天10萬
"""
        path = _write_tmp(txt)
        messages = parse_chat_file(path)
        os.unlink(path)

        result = analyze_guide(messages, days_limit=3)
        all_contents = []
        for cat_data in result.values():
            for msgs in cat_data.values():
                all_contents.extend(m['content'] for m in msgs)
        assert any('蟻洞' in c for c in all_contents)
        assert not any('古丁' in c for c in all_contents)


# ══════════════════════════════════════════════════════════════════
#  extract tips
# ══════════════════════════════════════════════════════════════════
class TestExtractTips:
    def _get_categorized(self):
        txt = f"""{TODAY} 星期一
20:00 老手A 古丁三樓一小時大概3萬 防武滿才不會噴水
20:01 老手B 半自動設定 巡邏範圍設100格 回城用順移
20:02 新手C 掛哪好？
"""
        path = _write_tmp(txt)
        messages = parse_chat_file(path)
        os.unlink(path)
        return analyze_guide(messages)

    def test_farming_tips(self):
        cat = self._get_categorized()
        df = extract_farming_tips(cat)
        assert not df.empty
        # 純短問句應被過濾
        contents = df['內容'].tolist()
        assert not any('掛哪好？' == c.strip() for c in contents)

    def test_setting_tips(self):
        cat = self._get_categorized()
        df = extract_setting_tips(cat)
        assert not df.empty


# ══════════════════════════════════════════════════════════════════
#  deduplicate
# ══════════════════════════════════════════════════════════════════
class TestDeduplicate:
    def test_removes_dupes(self):
        # 前30字完全相同，後面不同 → 應去重
        prefix = 'A' * 30
        msgs = [
            {'content': prefix + ' 版本一', 'date': '', 'time': '', 'nickname': 'A', 'keywords': ''},
            {'content': prefix + ' 版本二', 'date': '', 'time': '', 'nickname': 'B', 'keywords': ''},
            {'content': '掛機攻略很重要大家一起來研究', 'date': '', 'time': '', 'nickname': 'C', 'keywords': ''},
        ]
        result = _deduplicate_tips(msgs)
        assert len(result) == 2  # 前兩則前30字相同去重

    def test_keeps_different(self):
        msgs = [
            {'content': '收殘卡', 'date': '', 'time': '', 'nickname': 'A', 'keywords': ''},
            {'content': '古丁掛一天30萬', 'date': '', 'time': '', 'nickname': 'B', 'keywords': ''},
        ]
        result = _deduplicate_tips(msgs)
        assert len(result) == 2


# ══════════════════════════════════════════════════════════════════
#  Excel export
# ══════════════════════════════════════════════════════════════════
class TestExportExcel:
    def test_export(self, tmp_path):
        txt = f"""{TODAY} 星期一
20:00 老手 古三掛一天大概30萬 防武滿
20:01 老手 半自動設定要勾撿物模式
20:02 老手 進監獄不用怕 鎖一開二
20:03 老手 閃退要關防毒
20:04 老手 紫P可以遠端看
20:05 老手 收殘卡 LP
"""
        path = _write_tmp(txt)
        messages = parse_chat_file(path)
        os.unlink(path)

        categorized = analyze_guide(messages)
        out = str(tmp_path / 'test_output.xlsx')
        export_excel(categorized, out)

        assert os.path.exists(out)
        # 檢查工作表
        xls = pd.ExcelFile(out)
        assert '摘要總覽' in xls.sheet_names
        assert '掛機攻略' in xls.sheet_names
        assert '設定教學' in xls.sheet_names


# ══════════════════════════════════════════════════════════════════
#  build_summary
# ══════════════════════════════════════════════════════════════════
class TestBuildSummary:
    def test_summary(self):
        categorized = {
            '掛機攻略': {
                '地點': [{'content': 'a', 'date': '', 'time': '', 'nickname': '', 'keywords': ''}],
                '收益': [{'content': 'b', 'date': '', 'time': '', 'nickname': '', 'keywords': ''}],
            },
            '設定教學': {},
        }
        df = build_summary(categorized)
        assert len(df) == 2
        assert df.iloc[0]['訊息總數'] == 2


# ══════════════════════════════════════════════════════════════════
#  Integration with real file
# ══════════════════════════════════════════════════════════════════
class TestIntegrationReal:
    """整合測試：使用真實聊天記錄檔（如果存在）。"""

    REAL_FILE = os.path.join(
        os.path.dirname(__file__),
        'txt',
        '[LINE]天堂經典版特工交流群.txt',
    )

    @pytest.mark.skipif(
        not os.path.exists(REAL_FILE),
        reason='真實聊天記錄檔不存在',
    )
    def test_real_file(self, tmp_path):
        messages = parse_chat_file(self.REAL_FILE)
        assert len(messages) > 0

        categorized = analyze_guide(messages)
        summary = build_summary(categorized)
        assert not summary.empty

        # 確認掛機攻略和設定教學有資料
        assert sum(len(v) for v in categorized.get('掛機攻略', {}).values()) > 0
        assert sum(len(v) for v in categorized.get('設定教學', {}).values()) > 0

        # 匯出 Excel
        out = str(tmp_path / 'real_output.xlsx')
        export_excel(categorized, out)
        assert os.path.exists(out)
