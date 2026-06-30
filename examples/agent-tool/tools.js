/**
 * 外卖工具（custom-loader 版）—— 部署到原型 data/ones/{笺oneId}/custom-tools/food/tools.js
 *
 * 仅原型有这套文件（不进仓库 → 生产天然没有）。单一工具 takeout,按 act 分发,
 * 笺反复调、每次看返回的页面文本再决定下一步(页面驱动)。
 * 工具只把意图交给进程内的"外卖通道"(globalThis.__foodDispatch),通道派给家里桥跑 AutoGLM+uia+glm-ocr。
 *
 * CJS 模块。导出 { tools: [...] }。
 */

async function call(tool, params) {
  const dispatch = globalThis.__foodDispatch;
  if (typeof dispatch !== "function") {
    return { success: false, output: "", error: "外卖通道未启用（FOOD_BRIDGE_TOKEN 未设？）" };
  }
  try {
    const r = await dispatch(tool, params, "");
    if (!r || !r.ok) {
      return { success: false, output: (r && r.text) || "", error: (r && r.error) || "外卖桥执行失败" };
    }
    return { success: true, output: r.text || "(没读到内容)" };
  } catch (e) {
    return { success: false, output: "", error: (e && e.message) || "外卖桥未响应" };
  }
}

module.exports = {
  tools: [
    {
      name: "takeout",
      description:
        "Operate the user's Meituan takeout app on their real phone to browse and order food. Call it repeatedly, ONE action at a time, reading the returned screen text before deciding the next action (page-driven, like a human tapping through the app). Always read 历史 first if you want to reorder a usual.\n" +
        "`act` (required) is one of:\n" +
        "• 浏览 — find shops. `target` = 美食(meals) | 甜品饮品(drinks) | 超市便利(convenience) | 历史(past orders & frequent shops) | or any keyword to search (a dish/cuisine/shop). Returns ~3 pages of shops.\n" +
        "• 进店 — enter a shop. `target` = shop name. Returns its menu (dishes + prices).\n" +
        "• 加菜 — pick a dish. `target` = dish name. If it needs specs it opens the options page (then call 选规格); if not it's added directly. (Two steps by nature: you must see the dish's options before choosing them.)\n" +
        "• 选规格 — on the options page that 加菜 opened, choose specs and add to cart. `options` = free text like '少冰,少糖,大杯' or '套餐选 鱼香肉丝/宫保鸡丁'.\n" +
        "• 删菜 — remove a dish from the cart. `target` = dish name.\n" +
        "• 购物车 — view the cart contents and total.\n" +
        "• 下单 — check out: auto-applies the largest usable 美团红包 coupon, then pays via the user's no-password limit. REAL money. Within the no-password limit it completes automatically; if it exceeds the limit the phone asks for a password and it will stop and tell you the user must confirm on the phone. Only call when the cart is what the user wants.",
      parameters: [
        { name: "act", type: "string", description: "Action: 浏览 | 进店 | 加菜 | 选规格 | 删菜 | 购物车 | 下单 (required).", required: true },
        { name: "target", type: "string", description: "What the action operates on: category/keyword (浏览), shop name (进店), or dish name (加菜/删菜). Chinese.", required: false },
        { name: "options", type: "string", description: "Spec choices for 选规格, free text e.g. '少冰,少糖,大杯'.", required: false },
      ],
      execute: (p) => call("takeout", {
        act: String((p && p.act) || "").trim(),
        target: String((p && p.target) || "").trim(),
        options: String((p && p.options) || "").trim(),
      }),
    },
  ],
};
