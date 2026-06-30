# AutoGLM-Waimai-Tool 🍱

> 把"在**美团**点外卖"封装成一个**给 AI agent 调用的工具**：你的 AI 调 `takeout(act, target, options)`，
> 拿回**干净的文本(markdown 表格)**——它**不用自己看屏幕**。可做成 **MCP** 或当**原生 tool** 直接挂给模型。

**本项目是 [Open-AutoGLM](https://github.com/zai-org/Open-AutoGLM) 生态下的衍生工具。**
"像人一样看屏幕、点手机"这件事由 Open-AutoGLM 干（单独安装，见 [DEPLOYMENT.md](DEPLOYMENT.md)）；
本项目在它之上做的是**另一件事**——把整套外卖操作收敛成**文本进、文本出**的一个工具。
来源与致谢见 [NOTICE](NOTICE)。

## 这是给谁用的（重要）

- **给"不方便在本地看图"的 AI** —— 比如跑在云端、或文本型/没把手机屏摆在面前的**伙伴 / agent**。
  它像调 `web_search` 一样调 `takeout`，收到的是表格文本，不需要多模态、不需要逐帧看截图。
- **不是**又一个"手机操作 agent"。如果你的 agent 就在本地、乐意自己做视觉循环，**直接用 Open-AutoGLM 即可**，
  用不上这套。本项目的价值在于把"手机操作"变成**对非视觉/远程 LLM 友好的工具调用**。

## 为此做的三件事

1. **工具化 / 页面驱动**：对外只有**一个工具** `takeout(act, …)`，AI 反复调、每次看返回再决定下一步——
   像人一步步点，但每一步都是普通的 tool_call。易于 **MCP 封装**或作**原生 tool**接入。
2. **输出对非视觉模型友好**：用 `glm-4v-flash` 把屏幕识别成 **markdown 表格**（菜名｜价格｜月售｜操作），
   行列对应清晰；多屏滚动按首列去重合并成一张表。远端 LLM 读文本即可,无需看图。
3. **动作可靠,不用调用方盯像素**：能用 `uiautomator` 元素树定位的(菜品名/选规格/搜索框/去结算)走
   **确定性点击**(按文字找节点→DOM 卡片归属点对按钮),根治"把多肉葡萄点成芝芝多肉葡萄"; 美团**自绘弹层**才回退视觉。
   配套领域处理:起送价提醒、餐具/必选自动选、红包选最大、登录/验证码人工接管。
   **安全**:默认只读不下单;只有 `下单` 会真支付,靠**免密支付额度上限**做物理兜底,超额则停手交人工。

只用**两个模型**：`autoglm-phone`（Open-AutoGLM 的导航/动作）+ `glm-4v-flash`（识别出表格），都走智谱 BigModel。

## 单一工具 `takeout(act, target, options)`

AI 反复调它、每次看返回的表格再决定下一步（页面驱动，像人一步步点）：

| act | target | options | 作用 |
|-----|--------|---------|------|
| 浏览 | 美食/甜品饮品/超市便利/历史/或搜索词 | — | 找店（含读历史订单），返回店铺表格 |
| 进店 | 店名 | — | 进店看菜单（菜名+价格表格） |
| 加菜 | 菜名 | — | 选中菜品（需规格则打开规格页） |
| 选规格 | — | `少冰,少糖,大杯` | 在规格页选好并加入购物车 |
| 删菜 | 菜名 | — | 从购物车移除 |
| 购物车 | — | — | 看购物车明细+合计 |
| 下单 | — | — | 去结算→选最大红包→**免密支付（真花钱）** |

## 怎么接给你的 AI

工具本体是 `bridge.takeout(act, target, options) -> 文本`,纯函数式、文本进文本出,接法随意:

- **当原生 tool**：直接把 `takeout` 注册成你 agent 的一个工具（schema 见 `examples/agent-tool/`），
  本地能直连手机时最省事。
- **封成 MCP**：在 `takeout` 外面套一层 MCP server，任何支持 MCP 的客户端就能用。
- **反向通道（agent 在云端、手机在你家，跨 NAT）**：`agent_client.py` 常驻在连手机的机器上、
  出站长轮询你的服务端取活回传（服务端实现 `/food-bridge/poll` + `/food-bridge/result`，协议见 [DEPLOYMENT.md](DEPLOYMENT.md)）。
- **命令行（调试/试玩）**：
  ```bash
  python bridge.py takeout 浏览 甜品饮品
  python bridge.py takeout 进店 喜茶
  python bridge.py takeout 加菜 多肉葡萄
  ```

## 快速开始

见 **[DEPLOYMENT.md](DEPLOYMENT.md)**：装 Open-AutoGLM、装 ADB Keyboard、连手机（USB 或 WiFi）、配 `.env`、跑起来。

## 已知边界（诚实说）

- **动作可靠性有天花板**：能锚点的稳；美团自绘弹层（规格选择）只能视觉，偶有不稳——靠"页面驱动 + agent 看到结果重试"兜。
- **要关系统动画**：否则 `uiautomator dump` 会卡在 "could not get idle state"（部署脚本会帮你关）。
- 目前只做**美团**、中文界面。其它平台/语言需另调提示词与锚点。
- 真支付前请务必设好**免密支付额度上限**作为物理花费上限。

## License

[Apache-2.0](LICENSE)。来源与第三方依赖见 [NOTICE](NOTICE)。
本项目与 Open-AutoGLM、智谱无官方关系，仅为社区衍生工具。
