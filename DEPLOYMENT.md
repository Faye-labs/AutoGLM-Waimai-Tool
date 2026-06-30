# 部署 AutoGLM-Waimai-Tool

## 0. 你需要准备

- 一台**安卓手机**（Android 7.0+），装好**美团** App 并**已登录**；开**开发者选项 + USB 调试**。
- 一台电脑（Win/macOS/Linux），Python 3.11+，已装 **adb**（Android platform-tools，在 PATH 里）。
- **智谱 BigModel API Key**：https://open.bigmodel.cn → 控制台 API Keys（要能调 `autoglm-phone` 和 `glm-4v-flash`）。

## 1. 目录结构：把本项目和 Open-AutoGLM 放成**同级**

本项目运行时 `import` Open-AutoGLM 的 `phone_agent`，约定它在**上一级目录**：

```
some-parent/
├── Open-AutoGLM/        # git clone https://github.com/zai-org/Open-AutoGLM
└── AutoGLM-Waimai-Tool/     # 本项目
```

```bash
git clone https://github.com/zai-org/Open-AutoGLM.git
git clone <本项目地址> AutoGLM-Waimai-Tool

# 用 Open-AutoGLM 的 venv(它已装好 phone_agent 依赖),再补本项目依赖
cd Open-AutoGLM && python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt     # Win;mac/linux 用 .venv/bin/python
cd ../AutoGLM-Waimai-Tool
../Open-AutoGLM/.venv/Scripts/python -m pip install -r requirements.txt
```
（也可以各自独立 venv,只要跑本项目的那个 python 能 import 到 Open-AutoGLM 的 phone_agent。）

## 2. 装 ADB Keyboard（中文输入必需）

原生 `adb input text` 打不了中文,店内搜索要用它:
```bash
curl -L -o ADBKeyboard.apk https://github.com/senzhk/ADBKeyBoard/raw/master/ADBKeyboard.apk
adb install -r ADBKeyboard.apk
adb shell ime enable com.android.adbkeyboard/.AdbIME
adb shell ime set com.android.adbkeyboard/.AdbIME
```
（本项目会在输入前自动把它设为当前输入法,所以被系统切回 Gboard 也不怕。）

## 3. 关掉系统动画（否则 uiautomator dump 失败）

美团页面有持续动画,`uiautomator dump` 会报 "could not get idle state"。关掉:
```bash
adb shell settings put global window_animation_scale 0
adb shell settings put global transition_animation_scale 0
adb shell settings put global animator_duration_scale 0
```

## 4. 连手机：USB 或 WiFi（二选一,同一份代码）

- **USB**:插线、勾"一律允许 USB 调试"即可。`.env` 里 `FOOD_BRIDGE_ADB_TARGET` 留空。
- **WiFi**(免拔插掉线,推荐常驻):
  ```bash
  adb tcpip 5555                         # 需 USB 在
  adb shell ip -f inet addr show wlan0   # 查手机 IP
  adb connect <手机IP>:5555              # 之后可拔 USB
  adb devices                            # 确认只剩 <手机IP>:5555 一个设备
  ```
  并在 `.env` 设 `FOOD_BRIDGE_ADB_TARGET=<手机IP>:5555`,`agent_client` 会自动重连。
  建议同时开"充电时保持唤醒":`adb shell settings put global stay_on_while_plugged_in 3`。

  ⚠️ USB 和 WiFi **同时连**会让 adb "more than one device" 报错——只保留一个。

## 5. 美团 App 前置（手机上做一次）

- **已登录**美团。
- **定位**:系统定位开 + 美团 App 定位权限给(否则刷不出附近商家)。
- 网络正常(别开飞行模式)。

## 6. 配置 `.env`

```bash
cp .env.example .env
# 填 BIGMODEL_API_KEY;用反向通道再填 FRAMEWORK_URL / FOOD_BRIDGE_TOKEN;WiFi 填 FOOD_BRIDGE_ADB_TARGET
```

## 7. 跑起来

### 用法 A：命令行直接用
```bash
export BIGMODEL_API_KEY=你的key        # 或放 .env 后自行加载
PY=../Open-AutoGLM/.venv/Scripts/python
$PY bridge.py takeout 浏览 甜品饮品
$PY bridge.py takeout 进店 喜茶
$PY bridge.py takeout 加菜 多肉葡萄
$PY bridge.py takeout 选规格 "" "少冰,少糖,大杯"
$PY bridge.py takeout 购物车
# $PY bridge.py takeout 下单     # ⚠️ 真支付!确认购物车无误再调
```

### 用法 B：接你自己的 AI agent（反向通道）
`agent_client.py` 出站长轮询你的服务端取活、执行、回传。你的服务端实现两个端点:

- `GET /food-bridge/poll` — 桥来取活。鉴权 `Authorization: Bearer <FOOD_BRIDGE_TOKEN>`。
  有活返回 `{"jobId","tool":"takeout","params":{"act","target","options"}}`;无活返回 `{"idle":true}`(可挂起~25s 再返)。
- `POST /food-bridge/result` — 桥回结果 `{"jobId","ok":bool,"text":str,"error":str}`。同样 Bearer 鉴权。

你的 agent 决定调 `takeout` 时,把一个 job 放进队列让 `/poll` 取走,然后等 `/result`。
启动桥:
```bash
export FRAMEWORK_URL=https://your-agent-server  FOOD_BRIDGE_TOKEN=...  BIGMODEL_API_KEY=...  FOOD_BRIDGE_ADB_TARGET=<手机IP>:5555
../Open-AutoGLM/.venv/Scripts/python -u agent_client.py
```
> 把工具暴露给 LLM 的写法参考 `examples/agent-tool/`(那是作者接入私有 agent 框架的示例,按你的框架改)。

## 安全须知 🔒

- 只有 `下单` 动作会**真支付**。请先在手机支付设置里设好**免密支付额度上限**——这是物理花费上限,
  AI 失控最多也就花到额度;超额会弹密码,本项目检测到就**停手交人工**。
- 想"只看不买":别调 `下单` 即可;其余动作都不花钱(加购到购物车不扣款)。
- 这是操作**你自己账号**的自动化工具,请自行评估与各 App 用户协议的关系,自负其责。
