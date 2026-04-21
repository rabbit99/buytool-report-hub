"""
message_analyzer.py
使用 Gemini API 進行新聞記者式訊息分析
"""

import os
import json
import logging
from typing import List, Dict, Optional
import google.genai as genai
from dotenv import load_dotenv

# ── Logger ───────────────────────────────────────────────────
# 不在此設定 handler，由呼叫端（gen_report.py 或命令列）統一配置
logger = logging.getLogger('message_analyzer')

# 載入 .env
load_dotenv()

# 初始化 Gemini
API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    raise ValueError("GEMINI_API_KEY not found in environment or .env file")

client = genai.Client(api_key=API_KEY)

# 使用 Gemini 2.5 Flash 模型
MODEL = "gemini-2.5-flash"
logger.info(f'message_analyzer 初始化完成，使用模型：{MODEL}')


def analyze_messages_batch(
    group_name: str,
    category: str,
    subcategory: str,
    messages: List[str],
    speaker_count: int = None
) -> Dict[str, str]:
    """
    批量分析一組訊息，返回新聞風格的分析。
    
    Args:
        group_name: 群組名稱（如"特工交流群"）
        category: 主題分類（如"掛機攻略"）
        subcategory: 子主題（如"地點安全性"）
        messages: 訊息列表
        speaker_count: 發言人數（用於量化）
    
    Returns:
        {
            'title': '標題',
            'analysis': '分析段落',
            'supplement': '補充說明（可選）',
            'status': 'success' | 'filtered' | 'error'
        }
    """
    
    if not messages:
        return {
            'title': '',
            'analysis': '',
            'supplement': '',
            'status': 'filtered'
        }
    
    # 組合訊息，過濾掉純提問
    from analyze_guide import _is_noise
    
    filtered_msgs = [m for m in messages if m and len(m.strip()) > 0]
    if not filtered_msgs:
        return {
            'title': '',
            'analysis': '',
            'supplement': '',
            'status': 'filtered'
        }
    
    # 構建 Gemini 提示
    speaker_desc = f"{speaker_count} 人" if speaker_count else "多人"

    prompt = f"""你是天堂經典版遊戲的情報分析師。根據以下 LINE 群組的真實訊息，提取**對玩家有直接幫助的具體情報**。

群組: {group_name}
分類: {category}
子分類: {subcategory}
發言人數: {speaker_desc}

原始訊息（共 {len(filtered_msgs)} 則）:
{chr(10).join(f'- {msg}' for msg in filtered_msgs[:25])}

---

**你的任務是從上述訊息中，提取以下類型的具體情報：**

- 「在哪裡（地點）掛機 → 有什麼收益（天幣/小時或一天）」
- 「需要什麼條件（職業、裝備等級 AC/防武、消耗品）才能在某地點掛」
- 「哪個職業、哪種配置在哪個地點效率最好」
- 「實際玩家測試的數據：X 小時賺 Y 萬、需要 AC Z 以上」
- 任何其他可以幫助玩家決策的具體事實

如果訊息中有這類資訊，請整理成清楚、可操作的條目。
如果訊息中沒有具體條件或數字，請如實反映，不要捏造。

輸出格式（JSON）：
{{
    "title": "一句話概括這批訊息的核心情報",
    "analysis": "具體情報整理，每條一行，格式如：\\n• [地點/條件] + [職業/裝備] → [結果/收益]\\n例：• 墮落地點，體妖精 AC100+，每小時約 5~8 萬天幣（多人回報）\\n若無具體數據則寫出群友的實際說法摘要",
    "supplement": "補充：意見分歧處、尚待驗證的說法、或重要注意事項。無則留空"
}}

僅輸出 JSON，不含其他文字。"""
    
    key = f'{group_name}/{category}/{subcategory}'
    logger.info(f'[AI 呼叫] {key}，訊息數={len(filtered_msgs)}，模型={MODEL}')

    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config={
                "temperature": 0.3,
                "top_k": 40,
                "top_p": 0.95,
            }
        )
        
        if response.text:
            # 嘗試解析 JSON
            try:
                result_text = response.text.strip()
                # 移除可能的 markdown 代碼塊標記
                if result_text.startswith('```'):
                    result_text = result_text.split('```')[1]
                    if result_text.startswith('json'):
                        result_text = result_text[4:]
                
                parsed = json.loads(result_text)
                analysis = parsed.get('analysis', '')
                # AI 有時回傳 list，統一轉為字串
                if isinstance(analysis, list):
                    logger.warning(f'[AI 回傳格式異常] {key}：analysis 為 list，已自動轉換為字串')
                    analysis = '\n'.join(str(item) for item in analysis)
                # 去識別化：移除 LINE ID（@英數）和個人暱稱引用（玩家 @xxx）
                import re as _re
                analysis = _re.sub(r'玩家\s*@[\w\u4e00-\u9fff（(][\w\u4e00-\u9fff）)]+', '有玩家', analysis)
                analysis = _re.sub(r'@[a-zA-Z0-9]{6,}', '', analysis)
                logger.info(f'[AI 成功] {key}，標題：{parsed.get("title", "")[:30]}')
                return {
                    'title': parsed.get('title', ''),
                    'analysis': analysis,
                    'supplement': parsed.get('supplement', ''),
                    'status': 'success'
                }
            except json.JSONDecodeError as je:
                logger.warning(f'[AI JSON 解析失敗] {key}：{je}，降級使用純文字回應')
                # 降級處理：直接使用回應文本
                return {
                    'title': subcategory,
                    'analysis': response.text[:500],
                    'supplement': '',
                    'status': 'success'
                }
        else:
            logger.warning(f'[AI 無回應] {key}：response.text 為空')
            return {
                'title': '',
                'analysis': '（無回應）',
                'supplement': '',
                'status': 'error'
            }
    
    except Exception as e:
        logger.error(f'[AI 呼叫失敗] {key}，模型={MODEL}，錯誤：{e}', exc_info=True)
        return {
            'title': '',
            'analysis': f'（分析錯誤: {str(e)[:50]}）',
            'supplement': '',
            'status': 'error'
        }


def analyze_single_message(message: str) -> Optional[str]:
    """
    單獨分析一條訊息，用於「群友經驗與觀點」中需要深入分析的陳述。
    
    Args:
        message: 單條訊息
    
    Returns:
        分析後的新聞式陳述，或 None 若失敗
    """
    
    if not message or len(message.strip()) < 5:
        return None
    
    prompt = f"""將以下 LINE 群組訊息改寫為新聞記者風格的客觀陳述。

原訊息: {message}

要求:
1. 移除主觀語氣（如「我認為」）
2. 轉換為事實陳述（如「據回報」、「有使用者表示」）
3. 保留具體細節和數據
4. 一句話以內

僅輸出改寫後的文字，不加額外說明。"""
    
    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config={
                "temperature": 0.3,
                "top_k": 40,
                "top_p": 0.95,
            }
        )
        
        if response.text:
            return response.text.strip()
        return None
    except Exception as e:
        return None


if __name__ == "__main__":
    # 測試
    test_msgs = [
        "我掛野外 24小時 半自動 也沒有被鎖啊",
        "野外確實比較安全，我掛了一星期都沒事",
        "野外黑怪多，掛久了容易掉線"
    ]
    
    result = analyze_messages_batch(
        "特工交流群",
        "掛機攻略",
        "地點安全性",
        test_msgs,
        speaker_count=3
    )
    
    print("分析結果:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
