"""
AutoGLM-Waimai-Tool 工具层 —— 美团点外卖。
只用两个模型:autoglm-phone(导航/动作) + glm-4v-flash(识别屏幕→markdown 表格)。
对外是单一工具 takeout(act, target, options),按 act 分发;读取类动作内置安全闸
(确认回调返回 False 挡支付),只有"下单"放行支付(免密额度兜底)。
多屏(搜店/进店)滚动采集:每屏出表格→按首列去重合并;两屏画面几乎一致=滚到底。

命令行(本地直接用):
  python bridge.py takeout 浏览 甜品饮品
  python bridge.py takeout 进店 喜茶
  python bridge.py takeout 加菜 多肉葡萄
  python bridge.py takeout 选规格 "" "少冰,少糖,大杯"
依赖环境变量 BIGMODEL_API_KEY(或 --apikey)。adb 需在 PATH。运行时依赖同级 ../Open-AutoGLM。
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time

from PIL import Image, ImageChops

# 复用 Open-AutoGLM
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Open-AutoGLM"))
from phone_agent import PhoneAgent
from phone_agent.agent import AgentConfig
from phone_agent.model import ModelConfig

from ocr import ocr_table, ocr_screen  # ocr_table:glm-4v-flash 出 markdown 表格(主用);ocr_screen 兜底
import uia  # uiautomator 动作锚点:菜单/去结算等可确定性点(规格弹层自绘除外,仍视觉)

BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
NAV_MODEL = "autoglm-phone"      # 导航(动作)
ADB = "adb"                       # PATH 里有


def _adb_screenshot() -> str:
    """截屏到临时 png,返回路径。"""
    fd, path = tempfile.mkstemp(suffix=".png", prefix="foodbridge_")
    os.close(fd)
    with open(path, "wb") as f:
        subprocess.run([ADB, "exec-out", "screencap", "-p"], stdout=f, check=True)
    return path


def _shot_ocr(api_key: str, context: str = "generic") -> str:
    """截当前屏 → glm-4v-flash 出表格 → 返回文本(清理临时文件)。"""
    shot = _adb_screenshot()
    try:
        return (ocr_table(shot, context, api_key) or {}).get("text", "")
    finally:
        try:
            os.remove(shot)
        except OSError:
            pass


def _swipe_down():
    """向上滑 = 列表向下翻近一整屏(步长加大,少重叠)。屏 1080x2424。"""
    subprocess.run([ADB, "shell", "input", "swipe", "540", "2150", "540", "480", "500"],
                   capture_output=True)


# 吸顶不滚的表头行(每屏重复),拼接时在新屏直接丢弃
_STICKY = {"切换地址", "销量优先", "速度优先", "美食餐饮", "超市便利",
           "神券商家", "堂食店", "综合排序"}


def _stitch(prev_lines: list, new_lines: list,
            max_overlap: int = 150, header_zone: int = 50) -> list:
    """
    重叠拼接:在新屏靠上区域(避开吸顶表头)找到"上一屏尾部内容块"再次出现的位置,
    把它及之前的全部裁掉,只接续真正新增的内容。块长>=3 才算,避免误匹配通用短行。
    找不到重叠就整屏接上(大步长下重叠小,多数能匹配)。
    """
    if not prev_lines:
        return new_lines
    p = [x.strip() for x in prev_lines]
    n_strip = [x.strip() for x in new_lines]
    cap = min(max_overlap, len(p), len(n_strip))
    for k in range(cap, 2, -1):
        tail = p[-k:]
        for j in range(0, min(header_zone, len(n_strip) - k) + 1):
            if n_strip[j:j + k] == tail:
                return new_lines[j + k:]
    return new_lines


def _img_mean_diff(p1: str, p2: str) -> float:
    """两张截图的平均灰度差(0~255)。很小 = 画面几乎一致 = 滚不动了。"""
    a = Image.open(p1).convert("L").resize((64, 64))
    b = Image.open(p2).convert("L").resize((64, 64))
    h = ImageChops.difference(a, b).histogram()
    return sum(i * h[i] for i in range(256)) / max(sum(h), 1)


def _refuse_sensitive(message: str) -> bool:
    """读取类工具:任何敏感操作一律拒绝,绝不下单/支付。"""
    print(f"[安全闸] 拒绝敏感操作: {message}")
    return False


def _takeover(message: str) -> None:
    """需人工接管(登录/验证码):原型阶段只标记,不阻塞自动流程。"""
    print(f"[NEED_HUMAN] {message}")


def _allow_sensitive(message: str) -> bool:
    """仅下单(checkout)用:放行敏感操作(支付)。安全靠免密额度上限兜底,超额手机会弹密码=人工。"""
    print(f"[允许敏感操作] {message}")
    return True


def _navigate(task: str, api_key: str, max_steps: int = 25, allow_sensitive: bool = False) -> str:
    """用 AutoGLM 把手机导航到目标屏,返回 agent 的总结文本。
    allow_sensitive=True 仅 checkout 用(放行支付);其余一律 _refuse_sensitive 挡支付。"""
    agent = PhoneAgent(
        model_config=ModelConfig(base_url=BASE_URL, api_key=api_key, model_name=NAV_MODEL),
        agent_config=AgentConfig(max_steps=max_steps, lang="cn", verbose=True),
        confirmation_callback=_allow_sensitive if allow_sensitive else _refuse_sensitive,
        takeover_callback=_takeover,
    )
    return agent.run(task)


def _wake():
    subprocess.run([ADB, "shell", "input", "keyevent", "KEYCODE_WAKEUP"],
                   capture_output=True)


def _is_sep_row(cells: list) -> bool:
    return bool(cells) and all(set(c) <= set("-: ") for c in cells) and any("-" in c for c in cells)


def _merge_table_rows(texts: list) -> str:
    """多屏的 markdown 表格合并:保留第一张表头+分隔行,数据行按首列去重,非表格行(起送/合计等状态)保留唯一。"""
    header = sep = None
    seen, rows, extra = set(), [], []
    for t in texts:
        for ln in t.split("\n"):
            s = ln.strip()
            if not s:
                continue
            if s.startswith("|"):
                cells = [c.strip() for c in s.strip("|").split("|")]
                if _is_sep_row(cells):
                    if sep is None:
                        sep = ln
                    continue
                if header is None:
                    header = ln
                    continue
                if s == header.strip():   # 后续屏重复的表头行,跳过
                    continue
                key = cells[0] if cells else s
                if key and key not in seen:
                    seen.add(key)
                    rows.append(ln)
            elif s not in extra:
                extra.append(s)
    parts = []
    if header:
        parts.append(header)
        parts.append(sep or "| " + " | ".join(["---"] * len(header.strip("|").split("|"))) + " |")
        parts.extend(rows)
    if extra:
        parts += ([""] if parts else []) + extra
    return "\n".join(parts).strip()


def _collect_scrolling(api_key: str, max_screens: int, context: str = "generic") -> dict:
    """从当前屏开始,边截图(glm-4v-flash 出表格)边下滚,多屏表格按首列去重合并回给笺。
    两屏画面几乎一致 = 滚到底,停。"""
    texts, prev_shot, screens = [], None, 0
    for i in range(max_screens):
        shot = _adb_screenshot()
        if prev_shot is not None and _img_mean_diff(prev_shot, shot) < 2.5:
            os.remove(shot)
            break
        r = ocr_table(shot, context, api_key)
        if r.get("ok"):
            texts.append(r["text"])
            screens += 1
        if prev_shot is not None:
            os.remove(prev_shot)
        prev_shot = shot
        _swipe_down()
        time.sleep(1.3)
    if prev_shot is not None and os.path.exists(prev_shot):
        os.remove(prev_shot)
    return {"ok": bool(texts), "text": _merge_table_rows(texts), "screens": screens}


# ---------------- 工具 ----------------

def food_read_history(api_key: str) -> dict:
    """优先级1:读历史外卖订单 + 买过的店(单屏即够)。"""
    _wake()
    _navigate(
        "打开美团外卖,进入底部的“订单”页面,显示我的历史外卖订单列表。"
        "只查看,绝对不要再来一单、不要加购、不要下单、不要支付。",
        api_key,
    )
    shot = _adb_screenshot()
    result = ocr_table(shot, "history", api_key)
    os.remove(shot)
    return result


def food_find(mode: str, query: str, api_key: str) -> dict:
    """
    统一入口(Faye 定):固定走 美团→外卖,再四选一:
      美食 / 甜品饮品 / 超市便利 → 点该分类
      搜索 → 在搜索框输入 query
    然后下滑 3 页,把结果一起返回。提示词指挥 AutoGLM 做这条确定性导航。
    """
    _wake()
    if mode == "搜索":
        nav = (f"打开美团,点击首页顶部的“外卖”,在搜索框输入“{query}”并搜索,"
               "停在店铺结果列表顶部。只查看,绝对不要加购、不要下单、不要支付。")
    else:
        nav = (f"打开美团,点击首页顶部的“外卖”,再点击分类入口“{mode}”,"
               "停在该分类的店铺列表顶部。只查看,绝对不要加购、不要下单、不要支付。")
    _navigate(nav, api_key)
    # 下滑 3 页,三页一起返回(滚动采集自带去重)
    return _collect_scrolling(api_key, max_screens=4, context="shops")


# 兼容旧名:food_search = 搜索模式的 food_find
def food_search(query: str, api_key: str) -> dict:
    return food_find("搜索", query, api_key)


def food_open_shop(shop: str, api_key: str) -> dict:
    """优先级2:进店,滚到底收完店内所有菜品。前提:已在某个搜索/列表页。"""
    _wake()
    _navigate(
        f"在当前美团外卖页面,点进店铺“{shop}”,进入它的店内菜单页。"
        "只查看,绝对不要加购、不要下单、不要支付。",
        api_key,
    )
    # 收屏上限 6:把 open_shop 压进 framework 的 180s 工具超时内(超长菜单如海底捞牺牲完整性换速度)
    return _collect_scrolling(api_key, max_screens=6, context="menu")


def _act_single(task: str, api_key: str, settle: float = 1.6, context: str = "generic") -> dict:
    """动作类工具通用:导航执行(敏感操作被 _refuse_sensitive 硬挡) → 等界面切换稳 → 单屏出表格返回。"""
    _wake()
    _navigate(task, api_key)
    time.sleep(settle)  # 让加购/确认后的界面切换落定,避免 OCR 截到过渡态
    shot = _adb_screenshot()
    result = ocr_table(shot, context, api_key)
    os.remove(shot)
    return result


def _minorder_note(text: str) -> str:
    """检测底部购物车栏"还差¥X起送"→提醒笺还没到起送价、需再加菜。达标/可结算则不提醒。"""
    m = re.search(r"(?:还)?差\s*[¥￥]?\s*([\d.]+)\s*元?\s*(?:起送|可起送|才起送)", text)
    if m:
        return f"[⚠️ 还没到起送价,还差 ¥{m.group(1)} 才能下单,需要再加菜。]\n"
    if re.search(r"差.{0,8}起送|起送.{0,8}差|未达起送", text):
        return "[⚠️ 还没到起送价,需要再加菜才能下单。]\n"
    return ""


def food_add_item(item: str, api_key: str) -> dict:
    """
    P3:加购某菜品 —— 用 uiautomator 锚点确定性选对菜(根治视觉认错相似菜名),点它那行的按钮:
      选规格/选套餐 → 打开选择页(自绘弹层,后续 food_select_options 走视觉)
      N份起购/+     → 直接加购
    页面驱动:点完 glm-ocr 当前屏原样返回(并附"点了哪个菜/哪个按钮")。
    """
    _wake()
    r = uia.open_item(item)
    if not r.get("ok"):
        return {"ok": False, "text": "", "error": r.get("reason", "菜单里没找到这个菜")}
    item_m = r.get("item_matched")
    btn = r.get("button")

    if btn in ("选规格", "选套餐"):
        # 有规格/套餐 → uia 已点开选择页(可靠),让笺接着调“选规格”
        time.sleep(1.8)
        body = _shot_ocr(api_key, "generic")
        note = (f"[「{item_m}」有规格/套餐需要选择,已打开选择页。"
                f"请接着用 takeout(act=选规格, options=...) 选好规格完成加购。下面是规格选项:]\n")
        return {"ok": True, "text": _minorder_note(body) + note + body}

    if btn:
        # 有文字的直接加购按钮(如 N份起购),uia 已点,文字锚点可靠
        time.sleep(1.5)
        body = _shot_ocr(api_key, "generic")
        note = (f"[已点「{item_m}」的「{btn}」加入购物车。请从下面页面/购物车确认数量已 +1。]\n")
        return {"ok": True, "text": _minorder_note(body) + note + body}

    # btn is None → 纯 "+" 图标,无文字锚点。用视觉点它的 + 并让笺确认(不假报已加入)。
    _navigate(
        f"在当前美团外卖店内菜单页,找到菜品“{item_m or item}”,点它右侧的“+”加号把它加入购物车"
        "(只点加号,不要打开商品详情、不要选规格)。绝对不要去结算、不要支付。",
        api_key,
    )
    time.sleep(1.5)
    body = _shot_ocr(api_key, "generic")
    note = (f"[已尝试点「{item_m or item}」的+号加购。**请从下面页面/购物车确认数量是否真的 +1**"
            "(若没加上可再调一次加菜)。]\n")
    return {"ok": True, "text": _minorder_note(body) + note + body}


def food_select_options(choices: str, api_key: str) -> dict:
    """P3:在规格弹层(自绘,只能视觉)按要求选择并确认加购。提示词逐项约束 + 必点加入购物车。"""
    res = _act_single(
        "你现在在一个饮品/菜品的规格选择弹层。请按下面的要求逐项点选规格,执行要准:\n"
        f"要求:{choices}\n"
        "规则:\n"
        "① 弹层里分若干类(如 控糖/甜度、冰量、杯型、温度/状态、分装等)。对要求里的每一项,"
        "在对应类别下点中与之最接近的那个选项,使它变成高亮选中态(没提到的类别保持默认不动)。\n"
        "② 全部点完后,**必须点击弹层最底部的那个主按钮完成加购**——通常是黄色的“加入购物车”。"
        "如果那个位置显示的是数量“− 1 +”加减(说明此规格已在购物车),那就点右侧的“+”加一份。\n"
        "③ 加购成功后弹层会收起、回到菜单页(你看到菜单列表=成功了)。\n"
        "④ **如果当前根本没有规格选择弹层(比如已经是菜单页/购物车页,说明这菜没规格、已经加好了),"
        "不要乱点、不要找,直接停下,说明“此处没有规格可选,可能已加购”。**\n"
        "全程绝对不要去结算、不要提交订单、不要支付。",
        api_key,
        context="cart",
    )
    if res.get("ok"):
        res["text"] = _minorder_note(res.get("text", "")) + res.get("text", "")
    return res


def food_remove_item(item: str, api_key: str) -> dict:
    """P3:从购物车移除某商品。"""
    return _act_single(
        f"展开美团外卖底部的购物车,找到商品“{item}”,"
        "一直点它的减号“-”把数量减到 0(或点删除/垃圾桶图标),把它从购物车移除。"
        "完成后显示购物车现状。绝对不要去结算、不要提交订单、不要支付。",
        api_key,
        context="cart",
    )


def food_view_cart(api_key: str) -> dict:
    """P3:展开购物车,看商品明细 + 合计。"""
    res = _act_single(
        "点击美团外卖页面**最底部的购物车图标**(不是菜单),展开购物车浮层,"
        "显示里面的商品、数量和合计金额。只查看,绝对不要去结算、不要提交订单、不要支付。",
        api_key,
        context="cart",
    )
    if res.get("ok"):
        res["text"] = _minorder_note(res.get("text", "")) + res.get("text", "")
    return res


def food_checkout(api_key: str) -> dict:
    """
    下单:uiautomator 确定性点“去结算”→确认订单页→自动选最大可用红包→免密支付完成下单。
    真花钱!安全靠免密额度上限:额度内自动完成;超额手机会弹密码/指纹→AI 停手报警让 Faye 人工。
    """
    _wake()
    # 先看结算栏状态:有菜没选必选品时,结算按钮会变成“未点必选品/未选必选品”,不能直接去结算。
    nodes = uia.dump_nodes()
    must = next((n for n in nodes
                 if any(k in (n.get("text") or "") for k in ("必选品", "未点必选", "未选必选", "选好必选"))), None)
    if must:
        uia.tap_center(must)   # 点它跳到需要选必选品的地方
        time.sleep(1.8)
        body = _shot_ocr(api_key, "generic")
        return {"ok": True, "text":
                "[下单被拦:购物车里有菜还没选“必选品”(必选口味/规格/加料等)。已跳到需要选择的地方。"
                "请用 takeout(act=选规格, options=...) 选好必选项,再重新 takeout(act=下单)。以下是需要选的内容:]\n" + body}
    # 状态自适应:可能在购物车(需点去结算),也可能已在确认订单页(直接选红包+支付)。
    def _present(ns, *keys):
        return any(any(k in (n.get("text") or "") for k in keys) for n in ns)

    on_confirm = _present(nodes, "极速支付", "提交订单", "去支付", "美团红包", "确认订单")
    if not on_confirm:
        if not uia.tap_text_anchor("去结算"):
            body = _shot_ocr(api_key, "cart")
            return {"ok": False, "text": body,
                    "error": "没点成“去结算”(可能购物车为空、未达起送价、或有其它拦截)。以上是当前页面,请据此判断下一步。"}
        time.sleep(2.2)
        nodes = uia.dump_nodes()

    # —— 到这里应在“确认订单”页 ——
    # ① 红包:uia 确定性点开“美团红包”行,再让视觉选最大可用红包并返回(选择子页可能自绘)
    hb = next((n for n in nodes if "美团红包" in (n.get("text") or "") and n["clickable"]), None) \
        or next((n for n in nodes if "红包可用" in (n.get("text") or "") and n["clickable"]), None) \
        or next((n for n in nodes if "美团红包" in (n.get("text") or "")), None)
    if hb:
        uia.tap_center(hb)
        time.sleep(1.5)
        _navigate("这是红包/优惠券选择页。选中**面额最大的、可用的**那个红包(点它打上勾),"
                  "然后点“确定/完成/使用”返回上一页。**不要支付、不要提交订单**,只选红包。", api_key)
        time.sleep(1.5)

    # ② 餐具/其它必选(若确认页上还有没选的):视觉补一刀,不支付
    #    (多数情况餐具已默认“需要”,此步通常 no-op)
    # ③ 支付:uia 确定性点“极速支付/提交订单/去支付”(免密额度内自动完成;超额弹密码=停手)
    time.sleep(0.5)
    paid_btn = None
    for label in ("极速支付", "提交订单", "去支付"):
        if uia.tap_text_anchor(label, max_scrolls=2):
            paid_btn = label
            break
    time.sleep(2.5)  # 等支付/结果页
    body = _shot_ocr(api_key, "cart")
    if not paid_btn:
        return {"ok": False, "text": body,
                "error": "没找到支付按钮(极速支付/提交订单)。以上是当前页面,请据此判断(可能红包页没返回,或需人工)。"}
    if any(k in body for k in ("密码", "指纹", "输入支付")):
        return {"ok": False, "text": body, "error": "支付需要本人验证(可能超过免密额度),请在手机上确认。"}
    return {"ok": True, "text": "[已点“" + paid_btn + "”完成下单(免密额度内)。以下是结果页:]\n" + body}


_CATS = {"美食", "甜品饮品", "超市便利"}


def takeout(act: str, target: str, options: str, api_key: str) -> dict:
    """单一外卖工具:按 act 分发到内部动作。笺反复调,每次看返回再决定下一步(页面驱动)。"""
    a = (act or "").strip().lower()
    t = (target or "").strip()
    o = (options or "").strip()
    if a in ("浏览", "找店", "browse", "find", "搜索", "search", "历史", "history"):
        if t == "历史" or a in ("历史", "history"):
            return food_read_history(api_key)
        if t in _CATS:
            return food_find(t, "", api_key)
        return food_find("搜索", t, api_key)          # 否则当搜索词
    if a in ("进店", "open", "open_shop"):
        return food_open_shop(t, api_key)
    if a in ("加菜", "加购", "add", "add_item"):
        return food_add_item(t, api_key)
    if a in ("选规格", "规格", "spec", "options", "select"):
        return food_select_options(o or t, api_key)
    if a in ("删菜", "删除", "remove", "remove_item"):
        return food_remove_item(t, api_key)
    if a in ("购物车", "看购物车", "cart", "view_cart"):
        return food_view_cart(api_key)
    if a in ("下单", "结算", "支付", "付款", "checkout", "pay"):
        return food_checkout(api_key)
    return {"ok": False, "error": f"未知动作“{act}”。可用:浏览/进店/加菜/选规格/删菜/购物车/下单。"}


TOOLS = {
    "takeout": lambda key, args: takeout(args.query, args.arg2, args.arg3, key),
    "read_history": lambda key, args: food_read_history(key),
    "find": lambda key, args: food_find(args.query, args.arg2, key),
    "search": lambda key, args: food_search(args.query, key),
    "open_shop": lambda key, args: food_open_shop(args.query, key),
    "add_item": lambda key, args: food_add_item(args.query, key),
    "select_options": lambda key, args: food_select_options(args.query, key),
    "remove_item": lambda key, args: food_remove_item(args.query, key),
    "view_cart": lambda key, args: food_view_cart(key),
    "checkout": lambda key, args: food_checkout(key),
}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("tool", choices=list(TOOLS))
    p.add_argument("query", nargs="?", default="")   # takeout: act / find: mode
    p.add_argument("arg2", nargs="?", default="")     # takeout: target / find: query
    p.add_argument("arg3", nargs="?", default="")     # takeout: options
    p.add_argument("--apikey", default=os.environ.get("BIGMODEL_API_KEY", ""))
    args = p.parse_args()
    if not args.apikey:
        raise SystemExit("缺 API key:设 BIGMODEL_API_KEY 或 --apikey")
    out = TOOLS[args.tool](args.apikey, args)
    n = out.get("screens")
    print(f"\n=== 工具返回 (整洁文本,原样给笺{f';{n}屏' if n else ''}) ===")
    print(out.get("text") if out["ok"] else "[OCR失败] " + out.get("error", ""))
