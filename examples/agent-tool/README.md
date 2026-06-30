# 示例：把 takeout 工具暴露给你的 AI agent

这里是**作者把 `takeout` 工具接进自己私有 agent 框架的胶水示例**，不是本项目核心，
请按你自己的 agent 框架改写。

- `manifest.json` / `tools.js`：作者的框架用文件系统加载自定义工具（CJS）。`tools.js` 定义了
  LLM 看到的 `takeout` 工具 schema（act/target/options），`execute` 把意图交给进程内的
  `globalThis.__foodDispatch`——那是作者框架里"把 job 经反向通道派给家里桥"的入口。

你接入时只需要：
1. 给你的 LLM 注册一个工具 `takeout(act, target, options)`（描述照 `tools.js` 里的写）。
2. 工具被调用时，把 `{tool:"takeout", params:{act,target,options}}` 放进给桥的 job 队列
   （桥通过 `GET /food-bridge/poll` 取走，见根目录 DEPLOYMENT.md 的反向通道协议），等 `/food-bridge/result` 拿回文本。
3. 把返回文本作为 tool_result 交回给 LLM，让它看着表格决定下一步。

> 不想接 agent、只想本地玩：直接用命令行 `python bridge.py takeout ...`，不需要这套。
