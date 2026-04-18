"""外掛廠商設定檔

每個廠商有各自的：
- 群組名稱、聊天檔路徑
- 廣告暱稱過濾清單
- Auto-reply 關鍵字（過濾系統自動回覆）
- 額外分類關鍵字（補充 analyze_guide 的基礎關鍵字）

新增廠商時，只需在 VENDORS dict 中加一筆即可。
"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# ── 廠商設定 ────────────────────────────────────────────────────

VENDORS = {
    '特工': {
        'name': '特工',
        'full_name': '天堂經典版特工交流群',
        'txt_dir': BASE_DIR / 'txt' / '特工',
        'spec_dir': BASE_DIR / 'spec' / '特工',
        'report_dir': BASE_DIR / 'reports' / '特工',
        'out_dir': BASE_DIR / 'out' / '特工',
        'ad_nicknames': [
            '機加酒', '絲瓜蛤蜊', '懶人救星', 'Dong徐',
            '泰雅-腳本設置', '山姆雲科',
        ],
        'auto_reply_keywords': ['特工'],
        'extra_skip_keywords': [],
        'extra_keywords': {},
        # 群組來源優先級（數字越大越可靠，報告優先引用）
        'sources': {
            '[LINE]天堂經典版特工付費用戶群.txt': {'priority': 10, 'label': '付費群'},
            '[LINE]天堂經典版特工交流群.txt': {'priority': 5, 'label': '免費群'},
        },
    },
    '機器熊': {
        'name': '機器熊',
        'full_name': '天堂經典版TW機器熊仿真瓜瓜群',
        'txt_dir': BASE_DIR / 'txt' / '機器熊',
        'spec_dir': BASE_DIR / 'spec' / '機器熊',
        'report_dir': BASE_DIR / 'reports' / '機器熊',
        'out_dir': BASE_DIR / 'out' / '機器熊',
        'ad_nicknames': [
            'ROBOBEAR群管-湯姆熊', 'ROBOBEAR群管-苦命熊',
            'ROBOBEAR群管',
        ],
        'auto_reply_keywords': ['ROBOBEAR', 'robobear', '機器熊'],
        'extra_skip_keywords': [],
        'extra_keywords': {
            '帳號安全': {
                '驗證機制': [
                    '自動解驗證', '手機驗證', '驗證網址',
                    '點圖', '營運者請求', '暫停遊戲',
                ],
            },
            '設定教學': {
                '自動解驗證': ['自動解驗證', '解驗證'],
                '驅動問題': ['驅動', 'dos', 'DOS'],
            },
        },
    },
    '交易群': {
        'name': '交易群',
        'full_name': '天堂經典版：冥王黑帝斯交易討論群',
        'txt_dir': BASE_DIR / 'txt' / '交易群',
        'spec_dir': BASE_DIR / 'spec' / '交易群',
        'report_dir': BASE_DIR / 'reports' / '交易群',
        'out_dir': BASE_DIR / 'out' / '交易群',
        'ad_nicknames': [],
        'auto_reply_keywords': [],
        'extra_skip_keywords': [],
        'extra_keywords': {},
    },
}


def get_vendor(name):
    """取得廠商設定。支援部分匹配（如 '熊' 匹配 '機器熊'）。"""
    if name in VENDORS:
        return VENDORS[name]
    # 部分匹配
    for key, cfg in VENDORS.items():
        if name in key or key in name:
            return cfg
    raise ValueError(
        f"找不到廠商「{name}」。可用的廠商：{', '.join(VENDORS.keys())}"
    )


def list_vendors():
    """列出所有可用廠商名稱。"""
    return list(VENDORS.keys())
