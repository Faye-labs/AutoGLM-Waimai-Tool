"""
外卖桥客户端（家里 PC 常驻）—— 反向连接的"家里那一端"。
主动 long-poll 原型 framework 的 /food-bridge/poll 取活,本地跑 bridge.py 的工具(AutoGLM+glm-ocr
操作 Pixel),把整洁文本 POST 回 /food-bridge/result。智谱 key 只在本地,framework 永远看不到。

环境变量:
  FRAMEWORK_URL      原型 framework 公网基址(桥出站连它),如 https://<原型域名> 或 http://<ip>:<port>
  FOOD_BRIDGE_TOKEN  与原型 ecosystem 里设的同一个共享 secret
  BIGMODEL_API_KEY   智谱 key(本地用,跑 AutoGLM + glm-ocr)
adb 需在 PATH 中(Pixel USB 已授权)。

跑法: python agent_client.py
"""

import os
import subprocess
import sys
import time
import traceback

import requests

import bridge  # 复用 food_read_history / food_search / food_open_shop

FRAMEWORK_URL = os.environ.get("FRAMEWORK_URL", "").rstrip("/")
TOKEN = os.environ.get("FOOD_BRIDGE_TOKEN", "")
BIGMODEL_KEY = os.environ.get("BIGMODEL_API_KEY", "")
# WiFi ADB 目标(如 <手机IP>:5555)。设了就:①把 ANDROID_SERIAL 锁到该设备,
# 所有 adb 命令固定打到它(USB 也插着时不再"more than one device"歧义);
# ②每次干活前幂等 adb connect 一下,WiFi 掉了自动重连。USB 单设备用法留空即可。
ADB_TARGET = os.environ.get("FOOD_BRIDGE_ADB_TARGET", "")
if ADB_TARGET:
    os.environ["ANDROID_SERIAL"] = ADB_TARGET   # 子进程 adb 调用继承,全部锁定此设备

POLL_TIMEOUT = 35   # 略大于服务端 25s 挂起,留余量
RETRY_SLEEP = 4


def _ensure_adb():
    """WiFi 模式:幂等重连(已连=no-op,断了=重连)。USB 模式(未设 TARGET)跳过。"""
    if ADB_TARGET:
        subprocess.run(["adb", "connect", ADB_TARGET], capture_output=True)


def _run_job(job: dict) -> dict:
    """按 tool 分发到 bridge.py,返回 {ok, text} / {ok:False, error}。"""
    tool = job.get("tool")
    params = job.get("params") or {}
    try:
        if tool == "takeout":
            r = bridge.takeout(str(params.get("act", "")).strip(),
                               str(params.get("target", "")).strip(),
                               str(params.get("options", "")).strip(), BIGMODEL_KEY)
        elif tool == "read_history":
            r = bridge.food_read_history(BIGMODEL_KEY)
        elif tool == "find":
            r = bridge.food_find(str(params.get("mode", "搜索")).strip(),
                                 str(params.get("query", "")).strip(), BIGMODEL_KEY)
        elif tool == "search":
            r = bridge.food_search(str(params.get("query", "")).strip(), BIGMODEL_KEY)
        elif tool == "open_shop":
            r = bridge.food_open_shop(str(params.get("shop", "")).strip(), BIGMODEL_KEY)
        elif tool == "add_item":
            r = bridge.food_add_item(str(params.get("item", "")).strip(), BIGMODEL_KEY)
        elif tool == "select_options":
            r = bridge.food_select_options(str(params.get("choices", "")).strip(), BIGMODEL_KEY)
        elif tool == "remove_item":
            r = bridge.food_remove_item(str(params.get("item", "")).strip(), BIGMODEL_KEY)
        elif tool == "view_cart":
            r = bridge.food_view_cart(BIGMODEL_KEY)
        elif tool == "checkout":
            r = bridge.food_checkout(BIGMODEL_KEY)
        else:
            return {"ok": False, "error": f"未知工具: {tool}"}
        if r.get("ok"):
            return {"ok": True, "text": r.get("text", "")}
        return {"ok": False, "error": r.get("error") or "执行失败", "text": r.get("text", "")}
    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "error": f"桥执行异常: {e}"}


def main():
    if not (FRAMEWORK_URL and TOKEN and BIGMODEL_KEY):
        sys.exit("缺环境变量: 需要 FRAMEWORK_URL / FOOD_BRIDGE_TOKEN / BIGMODEL_API_KEY")
    headers = {"Authorization": f"Bearer {TOKEN}"}
    print(f"[外卖桥] 启动,连 {FRAMEWORK_URL},等活…")
    while True:
        try:
            r = requests.get(f"{FRAMEWORK_URL}/food-bridge/poll", headers=headers, timeout=POLL_TIMEOUT)
            if r.status_code == 401:
                print("[外卖桥] 401 鉴权失败,检查 FOOD_BRIDGE_TOKEN")
                time.sleep(RETRY_SLEEP)
                continue
            if r.status_code != 200:
                print(f"[外卖桥] poll 返回 {r.status_code},稍后重试")
                time.sleep(RETRY_SLEEP)
                continue
            job = r.json()
            if not job or not job.get("jobId"):
                continue  # { idle:true } 没活,重新 poll
            print(f"[外卖桥] 收到活: {job.get('tool')} {job.get('params')} (job={job.get('jobId')})")
            _ensure_adb()   # 干活前幂等重连 WiFi ADB(断了自动恢复)
            result = _run_job(job)
            result["jobId"] = job.get("jobId")
            requests.post(f"{FRAMEWORK_URL}/food-bridge/result", headers=headers, json=result, timeout=30)
            print(f"[外卖桥] 已回传 ok={result.get('ok')} 文本{len(result.get('text',''))}字")
        except requests.exceptions.ReadTimeout:
            continue  # long-poll 正常到期
        except requests.exceptions.ConnectionError:
            print("[外卖桥] 连不上 framework,稍后重试")
            time.sleep(RETRY_SLEEP)
        except Exception as e:
            print(f"[外卖桥] 循环异常: {e}")
            time.sleep(RETRY_SLEEP)


if __name__ == "__main__":
    main()
