"""
外卖桥 - 屏幕识别
ocr_table: 用 glm-4v-flash 看截图,按页面类型输出 markdown 表格(行列对应清晰,笺好读)。
  实测 glm-4v-flash 出表格 ~6.5s(和 glm-ocr 同量级)且结构化,比 glm-ocr 的线性文本好读得多。
ocr_screen: 老的 glm-ocr 线性文本(保留作兜底/对照,当前不用)。
"""

import base64

import requests
from openai import OpenAI

BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
TABLE_MODEL = "glm-4v-flash"          # 出表格:快 + 结构化
LAYOUT_PARSING_URL = "https://open.bigmodel.cn/api/paas/v4/layout_parsing"
OCR_MODEL = "glm-ocr"


def _img_data_uri(image_path: str) -> str:
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"data:image/png;base64,{b64}"


# 各页面的表格指令(列由 Faye 定)。都要求"只输出表格 + 底部状态行",省得夹带废话。
_TABLE_PROMPTS = {
    "shops": (
        "这是美团外卖的店铺列表截图。把每家店整理成 markdown 表格,列:"
        "店名 | 评分 | 配送时间 | 配送费 | 起送价。看不到的填 -。只输出表格。"
    ),
    "menu": (
        "这是美团外卖店内菜单截图。把每个菜品整理成 markdown 表格,列:"
        "菜名 | 价格 | 月售 | 操作按钮(选规格/选套餐/+/N份起购)。看不到的填 -。只输出表格。"
    ),
    "cart": (
        "这是美团外卖的购物车/确认订单截图。先用 markdown 表格列出商品,列:"
        "商品 | 规格 | 数量 | 价格。表格下方再用‘字段：值’各列一行:合计、配送费、起送(若显示‘还差¥X起送’要原样写出)、"
        "红包/优惠、收货地址(若有)。只输出这些,不要别的。"
    ),
    "history": (
        "这是美团外卖的订单页截图。用 markdown 表格列出历史订单,列:店名 | 菜品 | 金额 | 状态。"
        "表格下方再列一行‘买过的店：…’(若有‘买过N次’也带上)。只输出这些。"
    ),
    "generic": (
        "看这张美团外卖截图,把其中的关键信息整理成清晰的结构(能用 markdown 表格就用表格,"
        "选项类的用分类清单),保留每一项的对应关系。若底部有‘还差¥X起送/合计/必选项(餐具/口味等)’也带上。只输出整理结果。"
    ),
}


def ocr_table(image_path: str, context: str, api_key: str, model: str = TABLE_MODEL) -> dict:
    """截图 → glm-4v-flash 按 context 出 markdown 表格 → {ok, text}。"""
    prompt = _TABLE_PROMPTS.get(context, _TABLE_PROMPTS["generic"])
    try:
        client = OpenAI(base_url=BASE_URL, api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": _img_data_uri(image_path)}},
            ]}],
            temperature=0.0,
            max_tokens=1024,   # glm-4v-flash 上限
        )
        text = (resp.choices[0].message.content or "").strip()
        return {"ok": bool(text), "text": text}
    except Exception as e:
        return {"ok": False, "text": "", "error": str(e)[:200]}


def _clean(md: str) -> str:
    import re
    md = re.sub(r"!\[\]\([^)]*\)", "", md)
    md = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", md)
    return md.strip()


def ocr_screen(image_path: str, api_key: str, timeout: int = 120) -> dict:
    """老 glm-ocr 线性文本(兜底/对照用)。"""
    try:
        resp = requests.post(
            LAYOUT_PARSING_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": OCR_MODEL, "file": _img_data_uri(image_path), "need_layout_visualization": False},
            timeout=timeout,
        )
        resp.raise_for_status()
        text = _clean(resp.json().get("md_results") or "")
        return {"ok": bool(text), "text": text}
    except Exception as e:
        return {"ok": False, "text": "", "error": str(e)[:200]}


if __name__ == "__main__":
    import sys, json
    img, ctx, key = sys.argv[1], sys.argv[2], sys.argv[3]
    print(json.dumps(ocr_table(img, ctx, key), ensure_ascii=False, indent=2))
