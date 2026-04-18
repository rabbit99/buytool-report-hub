"""
message_analyzer.py
使用 Gemini API 進行新聞記者式訊息分析
"""

import os
import json
from typing import List, Dict, Optional
import google.genai as genai
from dotenv import load_dotenv

# 載入 .env
load_dotenv()

# 初始化 Gemini
API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    raise ValueError("GEMINI_API_KEY not found in environment or .env file")

client = genai.Client(api_key=API_KEY)

# 使用 Gemini 2.0 Flash 模型（快速且便宜）
MODEL = "gemini-2.0-flash"


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
    
    prompt = f"""根據以下 LINE 群組訊息，進行新聞記者式的客觀分析。

群組: {group_name}
分類: {category}
子分類: {subcategory}
提及人數: {speaker_desc}

訊息列表:
{chr(10).join(f'- {msg}' for msg in filtered_msgs[:20])}

請按照以下格式輸出（JSON 格式）：
{{
    "title": "一句話概括議題",
    "analysis": "客觀新聞體段落（1-3句，包含人數、具體事實、相關條件）",
    "supplement": "補充說明（可選，用於對立意見或待驗證部分。如無則留空）"
}}

要求:
1. 避免使用「我認為」、「應該」等主觀詞彙
2. 人數量化：2-3人→「少數」，4-10人→「部分」，10+人→「多數」
3. 優先突出具體數字、時間、條件
4. 平衡呈現不同聲音
5. 合理標註不確定性

僅輸出 JSON，不包含其他文字。"""
    
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
                return {
                    'title': parsed.get('title', ''),
                    'analysis': parsed.get('analysis', ''),
                    'supplement': parsed.get('supplement', ''),
                    'status': 'success'
                }
            except json.JSONDecodeError:
                # 降級處理：直接使用回應文本
                return {
                    'title': subcategory,
                    'analysis': response.text[:500],
                    'supplement': '',
                    'status': 'success'
                }
        else:
            return {
                'title': '',
                'analysis': '（無回應）',
                'supplement': '',
                'status': 'error'
            }
    
    except Exception as e:
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
