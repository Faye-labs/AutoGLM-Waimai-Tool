"""
uiautomator 动作锚点助手 —— 美团动作工具的确定性点击地基。
dump UI 层级 → 按文字/描述定位节点 → 按 bounds 中心精准 input tap。
比 AutoGLM 视觉点击稳:不会把"多肉葡萄"点成"芝芝多肉葡萄"。

前提:系统动画必须关(否则 uiautomator "could not get idle state")。ensure_anim_off() 负责。
注意:本模块直接 subprocess 调 adb.exe(不经 bash),故 /sdcard 路径不会被 MSYS 转换。

只对"有稳定文字锚点"的元素用(选规格/加入购物车/去结算/菜品名/购物车…);
没锚点的兜底仍可回退视觉。
"""

import os
import re
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET

ADB = "adb"


def _run(args, timeout=20):
    return subprocess.run([ADB] + args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def ensure_anim_off():
    """关三档系统动画(idempotent),否则美团页永不 idle、dump 失败。"""
    for k in ("window_animation_scale", "transition_animation_scale", "animator_duration_scale"):
        _run(["shell", "settings", "put", "global", k, "0"])


def dump_xml() -> str:
    """dump 当前屏 UI 层级,返回原始 XML 字符串。"""
    _run(["shell", "uiautomator", "dump", "/sdcard/uia.xml"])
    fd, local = tempfile.mkstemp(suffix=".xml", prefix="uia_")
    os.close(fd)
    _run(["pull", "/sdcard/uia.xml", local])
    try:
        with open(local, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    finally:
        try: os.remove(local)
        except OSError: pass


def dump_nodes() -> list:
    """dump 当前屏 → 解析出 [{text, desc, bounds:(x1,y1,x2,y2), center:(cx,cy), clickable}]。"""
    return _parse(dump_xml())


def _attr(tag: str, name: str) -> str:
    m = re.search(name + r'="([^"]*)"', tag)
    return m.group(1) if m else ""


def _parse(xml: str) -> list:
    nodes = []
    for m in re.finditer(r"<node\b[^>]*>", xml):
        tag = m.group(0)
        b = _attr(tag, "bounds")
        mm = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", b)
        if not mm:
            continue
        x1, y1, x2, y2 = map(int, mm.groups())
        nodes.append({
            "text": _attr(tag, "text"),
            "desc": _attr(tag, "content-desc"),
            "class": _attr(tag, "class"),
            "bounds": (x1, y1, x2, y2),
            "center": ((x1 + x2) // 2, (y1 + y2) // 2),
            "clickable": _attr(tag, "clickable") == "true",
        })
    return nodes


def find(nodes: list, text: str, exact: bool = False, field: str = "text") -> list:
    """按 text(默认)或 desc 模糊/精确匹配,返回匹配节点列表。"""
    out = []
    for n in nodes:
        v = n.get(field, "") or ""
        if (v == text) if exact else (text and text in v):
            out.append(n)
    return out


def tap_center(node: dict):
    cx, cy = node["center"]
    _run(["shell", "input", "tap", str(cx), str(cy)])


def tap_xy(x: int, y: int):
    _run(["shell", "input", "tap", str(x), str(y)])


def swipe_up():
    """列表向下翻(找更下面的元素)。"""
    _run(["shell", "input", "swipe", "540", "1900", "540", "700", "400"])


def scroll_find(text: str, max_scrolls: int = 8, exact: bool = False, field: str = "text"):
    """从当前位置往下滚动找到含 text 的节点;找到返回(node, nodes),没找到返回(None, last_nodes)。"""
    last = []
    for _ in range(max_scrolls):
        nodes = dump_nodes()
        last = nodes
        hits = find(nodes, text, exact=exact, field=field)
        if hits:
            return hits[0], nodes
        swipe_up()
        time.sleep(1.0)
    return None, last


def tap_button_in_row(item_node: dict, nodes: list, button_texts: list, row_tol: int = 180):
    """
    在 item_node 所在卡片里找按钮(button_texts 之一),点它。
    美团卡片:按钮常在卡片右上、菜品名在左下,二者 y 可差 ~130+,故 row_tol 放宽到 180。
    候选里取**离菜品行最近(|Δy| 最小)**的那个=最可能同卡片;平手取靠右的。
    返回点中的按钮 text,没找到返回 None。
    """
    iy = item_node["center"][1]
    cands = []
    for n in nodes:
        t = (n.get("text") or "")
        if any(bt in t for bt in button_texts):
            dy = abs(n["center"][1] - iy)
            if dy <= row_tol:
                cands.append((dy, -n["center"][0], n))
    if not cands:
        return None
    cands.sort(key=lambda c: (c[0], c[1]))  # |Δy| 最小优先,再靠右
    btn = cands[0][2]
    tap_center(btn)
    return btn.get("text")


def find_best(nodes: list, item: str):
    """按匹配质量选最佳节点:精确 > 开头匹配 > 包含。根治"多肉葡萄"误中"芝芝多肉葡萄"。"""
    exact = [n for n in nodes if (n.get("text") or "") == item]
    if exact:
        return exact[0]
    starts = [n for n in nodes if (n.get("text") or "").startswith(item)]
    if starts:
        return starts[0]
    contains = [n for n in nodes if item in (n.get("text") or "")]
    return contains[0] if contains else None


ROW_BUTTONS = ["选规格", "选套餐", "份起购"]  # "+" 多为无文字图标,用几何兜底


# ---------- ElementTree 卡片归属(正解:按 DOM 父子关系定位按钮,不靠几何猜) ----------

def _el_bounds(el):
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", el.get("bounds", ""))
    return tuple(map(int, m.groups())) if m else None


def _el_center(el):
    b = _el_bounds(el)
    return ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2) if b else None


def _best_item_el(root, item: str):
    """在 ET 树里按匹配质量(精确>开头>包含)选菜品文字节点。"""
    texts = [(e, e.get("text") or "") for e in root.iter("node")]
    for pred in (lambda t: t == item, lambda t: t.startswith(item), lambda t: item in t):
        for e, t in texts:
            if t and pred(t):
                return e
    return None


def _find_card_button(root, parent, item_el, button_texts):
    """
    从菜品节点往上找:第一个其子树内含按钮(选规格/选套餐/份起购)的祖先=菜品的卡片;
    在该卡片内若有多个按钮,取离菜品行最近(|Δy|最小)的。返回按钮元素或 None。
    按 DOM 归属,不受菜品在屏幕什么位置影响。
    """
    icy = (_el_center(item_el) or (0, 0))[1]
    cur = item_el
    while cur is not None:
        btns = [e for e in cur.iter("node")
                if any(b in (e.get("text") or "") for b in button_texts)]
        if btns:
            btns.sort(key=lambda e: abs((_el_center(e) or (0, 0))[1] - icy))
            return btns[0]
        cur = parent.get(cur)
    return None


_ADB_IME = "com.android.adbkeyboard/.AdbIME"


def ensure_adb_keyboard():
    """强制把 ADBKeyboard 设为当前输入法。系统/App 会把输入法切回 Gboard,切走则中文广播失效。"""
    _run(["shell", "ime", "set", _ADB_IME])
    time.sleep(0.3)


def type_text(text: str, clear_first: bool = True):
    """用 ADB Keyboard 输入文字(支持中文)。先确保 ADBKeyboard 为当前输入法,再(可选)清空已有内容。"""
    ensure_adb_keyboard()
    if clear_first:
        _run(["shell", "am", "broadcast", "-a", "ADB_CLEAR_TEXT"])
        time.sleep(0.3)
    _run(["shell", "am", "broadcast", "-a", "ADB_INPUT_TEXT", "--es", "msg", text])


def shop_search(item: str) -> bool:
    """
    用店内搜索栏直达菜品(省去一页页滑):点搜索入口→输入菜名→回车搜索。
    返回是否成功发起搜索(找到搜索入口)。失败则调用方回退滚动找。
    """
    nodes = dump_nodes()
    # 优先按 EditText 类定位搜索框——结果页里框显示旧查询词、无提示文字,只有类名稳定。
    entry = next((n for n in nodes if (n.get("class") or "").endswith("EditText")), None)
    if not entry:  # 兜底:菜单页靠提示文字
        for key in ("请输入商品", "搜索商品", "搜本店", "搜索"):
            hits = find(nodes, key) or find(nodes, key, field="desc")
            if hits:
                entry = hits[0]
                break
    if not entry:
        return False
    tap_center(entry)
    time.sleep(1.2)
    type_text(item)            # 内部先 ADB_CLEAR_TEXT 清掉旧词再输入
    time.sleep(0.8)
    _run(["shell", "input", "keyevent", "66"])  # Enter 触发搜索
    time.sleep(1.6)
    return True


def open_item(item: str, max_scrolls: int = 10, use_search: bool = True) -> dict:
    """
    找到菜品 item(按匹配质量选对),用 ElementTree 卡片归属点它的操作按钮:
      选规格/选套餐 → 打开选择页(自绘弹层,后续 select_options 走视觉)
      N份起购/+(无文字) → 几何兜底点该行最右(直接加购)
    返回 {ok, item_matched, button, reason}。
    优先用店内搜索直达菜品(省滑动+位置干净),搜不到再回退滚动找。
    """
    ensure_anim_off()
    if use_search:
        shop_search(item)  # 直达;失败也无妨,下面照常 dump/滚动找
    xml = None
    for _ in range(max_scrolls):
        cur_xml = dump_xml()
        try:
            if _best_item_el(ET.fromstring(cur_xml), item) is not None:
                xml = cur_xml
                break
        except ET.ParseError:
            pass
        swipe_up()
        time.sleep(1.0)
    if xml is None:
        return {"ok": False, "reason": f"菜单里没找到“{item}”"}

    root = ET.fromstring(xml)
    parent = {c: p for p in root.iter() for c in p}
    item_el = _best_item_el(root, item)
    matched = item_el.get("text")
    btn = _find_card_button(root, parent, item_el, ROW_BUTTONS)
    if btn is not None:
        cx, cy = _el_center(btn)
        tap_xy(cx, cy)
        return {"ok": True, "item_matched": matched, "button": btn.get("text")}
    # 卡片内无文字按钮(选规格/选套餐/份起购)→ 多半是 "+" 图标(无文字锚点)。
    # **不盲点坐标**(会点空/谎报),返回 button=None 交给调用方用视觉点 + 确认。
    return {"ok": True, "item_matched": matched, "button": None}


def tap_text_anchor(text: str, max_scrolls: int = 6) -> bool:
    """滚动找到含 text 的按钮并点它中心(用于 去结算/清空 这类固定锚点)。"""
    node, _ = scroll_find(text, max_scrolls=max_scrolls)
    if not node:
        return False
    tap_center(node)
    return True


if __name__ == "__main__":
    import sys, json
    ensure_anim_off()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        for n in dump_nodes():
            if n["text"] or n["desc"]:
                print(f"{n['text'] or '['+n['desc']+']'}  @{n['bounds']}  click={n['clickable']}")
    elif cmd == "find":
        nodes = dump_nodes()
        for n in find(nodes, sys.argv[2]):
            print(json.dumps(n, ensure_ascii=False))
    elif cmd == "tap":
        nodes = dump_nodes()
        hits = find(nodes, sys.argv[2])
        if hits:
            tap_center(hits[0]); print("tapped", hits[0]["text"], "@", hits[0]["center"])
        else:
            print("not found:", sys.argv[2])
