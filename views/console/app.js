/**
 * 主动回复（ActiveReply）控制台前端。
 *
 * 页面通过 AstrBot 插件视图 bridge 与后端通信：
 *   window.AstrBotPluginView.apiGet(endpoint, params)
 *   window.AstrBotPluginView.apiPost(endpoint, body)
 * 后端统一返回 { success: true, data } 或 { success: false, error }。
 *
 * 交互采用“请求更新 state、render 函数重绘 DOM”的单向数据流。表单输入
 * 先写入 state 中的编辑副本，只有点击保存后才提交后端；重新打开页面或
 * 重新加载配置时，以后端返回值覆盖本地副本，避免把尚未保存的改动误当成
 * 已持久化数据。各视图函数只负责当前页面区域，避免一次操作重建无关页面。
 */

const UI_BUILD = 19;
console.info(`[ActiveReply] UI build ${UI_BUILD}`);

const bridge = window.AstrBotPluginView || window.AstrBotPluginPage || null;

/**
 * 页面唯一的前端状态源：视图数据、编辑中的配置值与记忆列表筛选条件均保存在此处。
 * 读取接口后先更新 state，再由 render 函数将其映射到 DOM。
 */
const state = {
  view: "overview",
  overview: null,
  groups: [],
  groupSearch: "",
  currentGroupId: "",
  currentGroup: null,
  currentTab: "active_reply",
  globalTab: "active_reply",
  providers: [],
  embeddingProviders: [],
  schema: null,
  globalValues: null,
  autoMemory: null,
  blocks: null,
  groupBlocks: [],
  themeMode: "system",
  systemDark: false,
  blockState: {
    global: { search: "", expanded: false },
    group: { search: "", expanded: false },
  },
  rag: {
    page: 1,
    pageSize: 20,
    total: 0,
    hasMore: false,
    keyword: "",
    scope: "",
    role: "",
    scopes: [],
  },
};

const PROVIDER_PATHS = new Set([
  "active_reply.model_choice_provider_id",
  "group_context_enhancement.image_caption_provider_id",
]);

const SECTION_ICONS = {
  active_reply: "message",
  group_context_enhancement: "layers",
  group_reply_enhancement: "sparkles",
  web_search: "search",
  memory_rag: "database",
  runtime: "sliders",
};

/** 按图标名生成引用 SVG sprite 的标记。 */
function icon(name) {
  return `<svg class="icon" aria-hidden="true"><use href="#i-${name}"></use></svg>`;
}

const FIELD_GROUPS = [
  { prefix: "active_reply.", tab: "active_reply", title: "消息主动回复" },
  {
    prefix: "group_context_enhancement.",
    tab: "group_context_enhancement",
    title: "群聊上下文增强",
  },
  {
    prefix: "group_reply_enhancement.",
    tab: "group_reply_enhancement",
    title: "群聊回复增强",
  },
];

/** 通过元素 ID 获取控制台节点，减少重复的 DOM 查询写法。 */
const $ = (id) => document.getElementById(id);

/* ------------------------------------------------------------------ */
/* 基础工具                                                            */
/* ------------------------------------------------------------------ */
/**
 * 对不可信文本执行 HTML 转义，供模板字符串插入标记前使用。
 * @param {*} value 任意待展示值。
 * @returns {string} 转义后的文本。
 */
function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

let toastTimer = null;

/**
 * 页面运行在受限 iframe 中，window.confirm 可能被禁用，
 * 因此用页面内的弹窗代替。
 */
function askConfirm(message) {
  return new Promise((resolve) => {
    $("modal-text").textContent = message;
    const modal = $("modal");
    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");
    const okBtn = $("modal-ok");
    const cancelBtn = $("modal-cancel");
    const finish = (value) => {
      modal.classList.remove("open");
      modal.setAttribute("aria-hidden", "true");
      okBtn.removeEventListener("click", onOk);
      cancelBtn.removeEventListener("click", onCancel);
      resolve(value);
    };
    const onOk = () => finish(true);
    const onCancel = () => finish(false);
    okBtn.addEventListener("click", onOk);
    cancelBtn.addEventListener("click", onCancel);
  });
}

/** 显示短暂的成功或错误提示，并重置之前启动的隐藏计时器。 */
function toast(message, isError = false) {
  const node = $("toast");
  node.textContent = message;
  node.classList.toggle("error", Boolean(isError));
  node.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("show"), 2400);
}

/**
 * 经 AstrBot 插件视图 bridge 调用接口，并统一处理 success/error 包装。
 * @throws {Error} 页面未在 AstrBot 内打开，或接口返回失败时抛出。
 */
async function callApi(method, endpoint, payload) {
  // bridge 不存在通常表示用户绕过 AstrBot 单独打开静态页面；此时明确失败，
  // 不伪造空响应，避免渲染成“请求成功但没有数据”的状态。
  if (!bridge) {
    throw new Error("请在 AstrBot 面板中打开该页面");
  }
  const response =
    method === "GET"
      ? await bridge.apiGet(endpoint, payload || {})
      : await bridge.apiPost(endpoint, payload || {});
  if (response && response.success === false) {
    throw new Error(response.error || "请求失败");
  }
  if (response && response.data !== undefined) {
    return response.data;
  }
  return response;
}

/** 将计数格式化为当前语言环境的千位分隔数字。 */
function formatNumber(value) {
  return Number(value || 0).toLocaleString("zh-CN");
}

/** 清除配置提示末尾多余的中文/英文句号，统一表单文案样式。 */
function cleanHint(text) {
  return String(text || "").trim().replace(/[。.]+$/, "");
}

/** 将长文本压成单行摘要，超长部分用省略号标识。 */
function clip(text, max = 110) {
  const source = String(text || "").replace(/\s+/g, " ").trim();
  return source.length > max ? `${source.slice(0, max)}…` : source;
}

/** 按点分隔路径读取 schema 对应的嵌套配置值。 */
function getPath(obj, path) {
  let current = obj;
  for (const key of String(path).split(".")) {
    if (!current || typeof current !== "object") return undefined;
    current = current[key];
  }
  return current;
}

/** 按点分隔路径创建缺失的对象节点并写入表单字段值。 */
function setPath(obj, path, value) {
  const keys = String(path).split(".");
  let current = obj;
  for (let i = 0; i < keys.length - 1; i += 1) {
    if (!current[keys[i]] || typeof current[keys[i]] !== "object") {
      current[keys[i]] = {};
    }
    current = current[keys[i]];
  }
  current[keys[keys.length - 1]] = value;
}

/** 构造 QQ 群头像地址；调用处负责在加载失败时设置占位图。 */
function avatarUrl(groupId) {
  return `https://p.qlogo.cn/gh/${encodeURIComponent(groupId)}/${encodeURIComponent(groupId)}/100`;
}

/** 生成无需远程请求的 SVG 首字占位头像。 */
function fallbackAvatar(name) {
  const letter = String(name || "#").slice(0, 1) || "#";
  const svg =
    '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">' +
    '<rect width="100" height="100" rx="26" fill="#3b82f6"/>' +
    `<text x="50" y="64" font-size="46" text-anchor="middle" fill="#fff" font-family="sans-serif">${escapeHtml(letter)}</text>` +
    "</svg>";
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

/* ------------------------------------------------------------------ */
/* 主题                                                                */
/* ------------------------------------------------------------------ */
const THEME_KEY = "areply.theme";
const THEME_CYCLE = ["system", "light", "dark"];
const THEME_LABELS = {
  system: "跟随系统",
  light: "日间模式",
  dark: "夜间模式",
};
const THEME_ICONS = {
  system:
    "M4 3h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2ZM8 21h8M12 17v4",
  light:
    "M12 8a4 4 0 1 0 0 8a4 4 0 1 0 0-8ZM12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41",
  dark: "M12 3a6 6 0 0 0 9 9a9 9 0 1 1-9-9Z",
};

/** 将 AstrBot 面板提供的主题状态映射到 body 属性与状态徽标。 */
function applyTheme(isDark) {
  document.body.dataset.theme = isDark ? "dark" : "light";
  $("theme-chip").textContent = isDark ? "深色" : "浅色";
}

/** 读取本地保存的主题偏好；默认跟随系统。 */
function loadThemeMode() {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    if (THEME_CYCLE.includes(saved)) return saved;
  } catch (error) {
    console.warn("读取主题偏好失败", error);
  }
  return "system";
}

/** 把主题图标切换到对应形状（带 Morphicons 形变）。 */
function setThemeIcon(mode) {
  const next = THEME_ICONS[mode] || THEME_ICONS.system;
  if (themeMorph) {
    try {
      themeMorph.morphTo(next);
      return;
    } catch (error) {
      themeMorph = null;
    }
  }
  const path = $("theme-path");
  if (path) path.setAttribute("d", next);
}

/** 应用主题模式：system 跟随系统，light/dark 为手动覆盖。 */
function applyThemeMode(mode, persist = false) {
  const nextMode = THEME_CYCLE.includes(mode) ? mode : "system";
  state.themeMode = nextMode;
  const isDark =
    nextMode === "dark" || (nextMode === "system" && Boolean(state.systemDark));
  applyTheme(isDark);
  setThemeIcon(nextMode);
  const button = $("theme-btn");
  if (button) {
    button.title = `主题：${THEME_LABELS[nextMode]}（点击切换）`;
    button.setAttribute("aria-label", `当前主题：${THEME_LABELS[nextMode]}，点击切换`);
  }
  if (persist) {
    try {
      localStorage.setItem(THEME_KEY, nextMode);
    } catch (error) {
      console.warn("保存主题偏好失败", error);
    }
  }
}

/** 循环切换：跟随系统 → 日间 → 夜间 → 跟随系统。 */
function cycleTheme() {
  const index = THEME_CYCLE.indexOf(state.themeMode);
  const next = THEME_CYCLE[(index + 1) % THEME_CYCLE.length];
  applyThemeMode(next, true);
  toast(`主题：${THEME_LABELS[next]}`);
}

/* ------------------------------------------------------------------ */
/* 抽屉与视图切换                                                      */
/* ------------------------------------------------------------------ */
/** 切换主视图与导航选中态，并按页面类型触发对应数据加载。 */
function switchView(name) {
  state.view = name;
  document.querySelectorAll(".view").forEach((node) => {
    node.classList.toggle("active", node.id === `view-${name}`);
  });
  document.querySelectorAll(".nav-item").forEach((node) => {
    node.classList.toggle("active", node.dataset.view === name);
  });
  closeDrawer();
  if (name === "overview") {
    void loadOverview();
  } else if (name === "groups") {
    void loadGroups();
  } else if (name === "rag") {
    void loadAutoMemory();
    void loadRagStats(true);
    void loadRagList();
  } else if (name === "blocks") {
    void loadBlocks();
  } else if (name === "settings") {
    void loadGlobal();
  }
}

let menuMorph = null;
let themeMorph = null;
const MENU_PATH_D = "M4 5h16M4 12h16M4 19h16";
const CLOSE_PATH_D = "M18 6L6 18M6 6l12 12";

/** 延迟载入 Morphicons 菜单形变动画；依赖失败时保留静态图标能力。 */
async function initMorphIcons() {
  try {
    const { createMorph } = await import("./vendor/morphicons/dom.js");
    const path = document.getElementById("menu-path");
    if (path) {
      menuMorph = createMorph(path, MENU_PATH_D);
      menuMorph.set(MENU_PATH_D);
    }
    const themePath = document.getElementById("theme-path");
    if (themePath) {
      themeMorph = createMorph(themePath, THEME_ICONS[state.themeMode]);
      themeMorph.set(THEME_ICONS[state.themeMode]);
    }
  } catch (error) {
    console.warn("[ActiveReply] morphicons 加载失败，图标回退为静态切换", error);
  }
}

/** 将汉堡菜单图标切换为关闭图标，动画不可用时直接更新 SVG path。 */
function setMenuMorph(open) {
  const next = open ? CLOSE_PATH_D : MENU_PATH_D;
  if (menuMorph) {
    try {
      menuMorph.morphTo(next);
      return;
    } catch (error) {
      menuMorph = null;
    }
  }
  const path = document.getElementById("menu-path");
  if (path) path.setAttribute("d", next);
}

/** 展开侧边栏并同步遮罩、无障碍状态和菜单图标。 */
function openDrawer() {
  $("drawer").classList.add("open");
  $("drawer").setAttribute("aria-hidden", "false");
  $("scrim").classList.add("show");
  $("menu-btn").setAttribute("aria-expanded", "true");
  setMenuMorph(true);
}

/** 关闭侧边栏并恢复遮罩、按钮无障碍状态与菜单图标。 */
function closeDrawer() {
  $("drawer").classList.remove("open");
  $("drawer").setAttribute("aria-hidden", "true");
  $("scrim").classList.remove("show");
  $("menu-btn").setAttribute("aria-expanded", "false");
  setMenuMorph(false);
}

/* ------------------------------------------------------------------ */
/* 状态（概览）                                                        */
/* ------------------------------------------------------------------ */
/** 组合 Embedding 模型名和 provider ID，供状态区及提示文字显示。 */
function embeddingLabel(item) {
  const model = String(item.model || "").trim();
  const id = String(item.id || "").trim();
  if (model && id && model !== id) return `${model}（ID: ${id}）`;
  return model || id || "";
}

/** 将 Embedding 可用状态转换为记忆 RAG 状态卡的简短说明。 */
function ragEmbeddingHint(embedding) {
  if (!embedding || !embedding.available) return "未配置 Embedding 提供商";
  const providers = embedding.providers || [];
  if (providers.length > 1) return `Embedding 就绪（${providers.length} 个）`;
  const first = providers[0];
  return `Embedding：${first ? embeddingLabel(first) : "—"}`;
}

/** 在全局配置的 Embedding 控件下列出已发现的 provider 和模型名。 */
function decorateEmbeddingHint() {
  if (!state.embeddingProviders.length) return;
  const control = document.querySelector(
    '#global-form [data-global-row="memory_rag.embedding_provider_id"] .control'
  );
  if (!control || control.querySelector(".embedding-options")) return;
  const text = state.embeddingProviders
    .map((item) => embeddingLabel(item))
    .filter(Boolean)
    .join("、");
  const hint = document.createElement("span");
  hint.className = "field-hint embedding-options";
  hint.textContent = `当前可用：${text}（填模型名或 ID 均可）`;
  control.appendChild(hint);
}

/** 生成一张统一样式的状态卡 HTML，调用方传入已确定的展示内容。 */
function statusCard(label, value, hint, tone = "") {
  return `
    <div class="status-card">
      <div class="label">${escapeHtml(label)}</div>
      <div class="value ${tone}">${value}</div>
      ${hint ? `<div class="hint">${escapeHtml(hint)}</div>` : ""}
    </div>`;
}

/** 获取插件概览数据并刷新状态页与侧边栏群列表。 */
async function loadOverview(force = false) {
  try {
    const data = await callApi("GET", "overview", force ? { force: 1 } : {});
    state.overview = data;
    state.groups = data.groups || [];
    state.providers = data.providers || [];
    state.embeddingProviders =
      (data.embedding && data.embedding.providers) || [];
    renderOverview(data);
    renderDrawerGroups();
  } catch (error) {
    toast(error.message || "加载状态失败", true);
  }
}

/** 根据概览响应渲染状态卡、全局开关、记忆统计和最近活跃群。 */
function renderOverview(data) {
  const s = data.switches || {};
  const stats = data.stats || {};
  $("tz-chip").textContent = data.timezone || "-";
  $("drawer-version").textContent = `v${data.version || "-"} · UI${UI_BUILD}`;

  const modeLabel = s.mode === "model_choice" ? "模型判定" : "概率";
  $("status-grid").innerHTML = [
    statusCard(
      "主动回复",
      s.active_reply ? "已开启" : "已关闭",
      s.active_reply ? `当前方式：${modeLabel}` : "可在群配置中单独开启",
      s.active_reply ? "" : "muted"
    ),
    statusCard(
      "群聊上下文增强",
      s.context ? "已开启" : "已关闭",
      s.react_pattern ? "React Pattern 已开启" : "React Pattern 未开启"
    ),
    statusCard(
      "智能回复",
      s.smart_reply ? "生效中" : "未生效",
      s.react_pattern ? "@ 与引用解析" : "需要 React Pattern"
    ),
    statusCard(
      "记忆条目",
      formatNumber(stats.total_memories),
      `${(data.groups || []).length} 个已知群`
    ),
    statusCard(
      "记忆 RAG",
      s.memory_rag ? "已开启" : "已关闭",
      ragEmbeddingHint(data.embedding),
      s.memory_rag ? "" : "muted"
    ),
    statusCard(
      "联网搜索",
      s.web_search ? "已开启" : "已关闭",
      s.web_search ? "模型可调用 grok_web_search" : "需要时可在全局设置中启用"
    ),
  ].join("");

  $("overview-stats").innerHTML = [
    ["记忆总数", stats.total_memories],
    ["群范围", stats.group_scope_count],
    ["群数量", stats.group_id_count],
    ["角色数", stats.role_count],
  ]
    .map(
      ([title, value]) => `
      <div class="stat-box">
        <div class="n">${formatNumber(value)}</div>
        <div class="t">${escapeHtml(title)}</div>
      </div>`
    )
    .join("");

  const switches = [
    ["主动回复", s.active_reply],
    ["群聊上下文增强", s.context],
    ["React Pattern", s.react_pattern],
    ["智能回复", s.smart_reply],
    ["联网搜索", s.web_search],
    ["记忆 RAG", s.memory_rag],
  ];
  $("overview-switches").innerHTML = switches
    .map(
      ([label, enabled]) => `
      <li>
        <span>${escapeHtml(label)}</span>
        <span class="tag ${enabled ? "on" : "off"}">${enabled ? "开启" : "关闭"}</span>
      </li>`
    )
    .join("");

  const groups = (data.groups || []).filter((item) => item.messaged);
  $("overview-groups").innerHTML = groups.length
    ? groups
        .map(
          (item) => `
        <button class="group-pill" data-gid="${escapeHtml(item.group_id)}">
          ${escapeHtml(item.name || item.group_id)}
        </button>`
        )
        .join("")
    : '<p class="muted small">还没有收到过任何群消息</p>';

  $("overview-groups")
    .querySelectorAll(".group-pill")
    .forEach((btn) => {
      btn.addEventListener("click", () => {
        switchView("groups");
        void openGroup(btn.dataset.gid);
      });
    });
}

/* ------------------------------------------------------------------ */
/* 群配置                                                              */
/* ------------------------------------------------------------------ */
/** 返回群列表中优先展示的群名，没有可用名称时退回“未命名群”。 */
function groupDisplayName(item) {
  return item && item.name ? item.name : "未命名群";
}

/** 绑定远程 QQ 群头像和一次性错误回退，避免破图图标留在列表中。 */
function bindAvatar(img, groupId, name) {
  img.src = avatarUrl(groupId);
  img.onerror = () => {
    img.onerror = null;
    img.src = fallbackAvatar(name);
  };
}

/** 渲染抽屉内的快捷群列表，并绑定头像回退和打开群配置事件。 */
function renderDrawerGroups() {
  const container = $("drawer-groups");
  if (!container) return;
  const groups = state.groups.slice(0, 40);
  $("drawer-group-count").textContent = String(state.groups.length);
  if (!groups.length) {
    container.innerHTML =
      '<p class="muted small pad">暂无群，收到群消息后会自动出现</p>';
    return;
  }
  container.innerHTML = groups
    .map(
      (item) => `
      <button class="drawer-group ${
        item.group_id === state.currentGroupId ? "active" : ""
      }" data-gid="${escapeHtml(item.group_id)}">
        <img class="mini-avatar" data-avatar="${escapeHtml(item.group_id)}"
             data-name="${escapeHtml(groupDisplayName(item))}" alt="" />
        <span class="drawer-group-name">${escapeHtml(groupDisplayName(item))}</span>
        <span class="dot ${item.active_reply ? "on" : ""}"></span>
      </button>`
    )
    .join("");
  container.querySelectorAll(".drawer-group").forEach((btn) => {
    const img = btn.querySelector("img[data-avatar]");
    if (img) bindAvatar(img, img.dataset.avatar, img.dataset.name);
    btn.addEventListener("click", () => {
      switchView("groups");
      void openGroup(btn.dataset.gid);
    });
  });
}

/** 按搜索词过滤群列表、渲染条目并绑定群配置打开事件。 */
function renderGroupList() {
  const keyword = state.groupSearch.trim().toLowerCase();
  const groups = state.groups.filter((item) => {
    if (!keyword) return true;
    return (
      String(item.group_id).includes(keyword) ||
      String(item.name || "").toLowerCase().includes(keyword)
    );
  });

  const container = $("group-items");
  if (!groups.length) {
    container.innerHTML =
      '<p class="muted small pad">没有匹配的群，可以直接在右侧输入群号打开</p>';
    renderDrawerGroups();
    return;
  }

  container.innerHTML = groups
    .map(
      (item) => `
      <button class="group-item ${
        item.group_id === state.currentGroupId ? "active" : ""
      }" data-gid="${escapeHtml(item.group_id)}">
        <img class="mini-avatar" data-avatar="${escapeHtml(item.group_id)}"
             data-name="${escapeHtml(groupDisplayName(item))}" alt="" />
        <span class="group-item-text">
          <span class="gname">${escapeHtml(groupDisplayName(item))}</span>
          <span class="gid">${escapeHtml(item.group_id)}</span>
        </span>
        <span class="dot ${item.active_reply ? "on" : ""}"></span>
      </button>`
    )
    .join("");

  container.querySelectorAll(".mini-avatar").forEach((img) => {
    bindAvatar(img, img.dataset.avatar, img.dataset.name);
  });
  container.querySelectorAll(".group-item").forEach((btn) => {
    btn.addEventListener("click", () => void openGroup(btn.dataset.gid));
  });
  renderDrawerGroups();
}

/** 获取群目录；已有缓存时直接重绘，force 用于请求后端强制刷新。 */
async function loadGroups(force = false) {
  if (state.groups.length && !force) {
    renderGroupList();
    return;
  }
  try {
    const data = await callApi("GET", "groups", force ? { force: 1 } : {});
    state.groups = data.groups || [];
    renderGroupList();
    renderDrawerGroups();
  } catch (error) {
    toast(error.message || "加载群列表失败", true);
  }
}

/** 按回复模式决定群级表单的条件字段是否可见。 */
function fieldVisible(field, values) {
  if (field.path === "active_reply.possibility") {
    return values["active_reply.mode"] === "probability";
  }
  if (
    field.path === "active_reply.model_history_messages" ||
    field.path === "active_reply.model_choice_provider_id"
  ) {
    return values["active_reply.mode"] === "model_choice";
  }
  return true;
}

/** 生成 provider 下拉选项，同时保留当前配置中已不可用的旧 ID。 */
function providerOptions(selected) {
  const options = [
    `<option value="">跟随会话默认</option>`,
    ...state.providers.map(
      (item) =>
        `<option value="${escapeHtml(item.id)}" ${
          item.id === selected ? "selected" : ""
        }>${escapeHtml(item.label || item.id)}</option>`
    ),
  ];
  if (selected && !state.providers.some((item) => item.id === selected)) {
    options.push(
      `<option value="${escapeHtml(selected)}" selected>${escapeHtml(selected)}</option>`
    );
  }
  return options.join("");
}

/** 按群覆盖字段元数据生成开关、选项、滑块或文本控件。 */
function buildControl(field, value) {
  const path = field.path;
  if (field.kind === "bool") {
    return `
      <label class="switch">
        <input type="checkbox" data-path="${path}" ${value ? "checked" : ""} />
        <span class="slider"></span>
      </label>`;
  }
  if (field.kind === "enum") {
    const options = field.options
      .map(
        (option, index) =>
          `<option value="${escapeHtml(option)}" ${
            option === value ? "selected" : ""
          }>${escapeHtml(field.labels[index] || option)}</option>`
      )
      .join("");
    return `<select data-path="${path}" data-kind="enum">${options}</select>`;
  }
  if (PROVIDER_PATHS.has(path)) {
    return `<select data-path="${path}" data-kind="str">${providerOptions(
      value || ""
    )}</select>`;
  }
  if (field.kind === "int" || field.kind === "float") {
    const step = field.step || (field.kind === "int" ? 1 : 0.1);
    return `
      <input type="range" data-path="${path}" data-kind="${field.kind}"
        min="${field.min}" max="${field.max}" step="${step}" value="${value}" />
      <span class="range-value" data-for="${path}">${value}</span>`;
  }
  if (field.kind === "text") {
    return `<textarea data-path="${path}" data-kind="text">${escapeHtml(value || "")}</textarea>`;
  }
  return `<input type="text" data-path="${path}" data-kind="str" value="${escapeHtml(value || "")}" />`;
}

/** 组合当前群的三组配置表单，并重新绑定控件与 Tab 事件。 */
function renderGroupPanel() {
  const data = state.currentGroup;
  if (!data) return;
  const values = data.values || {};
  const independent = Boolean(data.independent);

  $("group-empty").classList.add("hidden");
  $("group-panel").classList.remove("hidden");
  $("group-name").textContent = data.name || "未命名群";
  $("group-id").textContent = `QQ群 ${data.group_id}`;
  bindAvatar($("group-avatar"), data.group_id, data.name || "");
  $("independent-toggle").checked = independent;
  $("save-hint").textContent = independent
    ? "当前为本群独立配置，会覆盖全局对应项"
    : "当前跟随全局配置；改完后先打开「独立配置」再保存";
  // 按钮保持可点击：未勾选「独立配置」时点击会触发红闪抖动提示

  const panels = FIELD_GROUPS.map((group) => {
    const fields = (data.fields || []).filter((field) =>
      field.path.startsWith(group.prefix)
    );
    const rows = fields
      .map(
        (field) => `
        <div class="form-row" data-row="${field.path}" ${
          fieldVisible(field, values) ? "" : 'style="display:none"'
        }>
          <label>${escapeHtml(field.label)}</label>
          <div class="control">${buildControl(field, values[field.path])}${
            field.hint
              ? `<span class="field-hint">${escapeHtml(cleanHint(field.hint))}</span>`
              : ""
          }</div>
        </div>`
      )
      .join("");
    return `<div class="tab-panel ${
      state.currentTab === group.tab ? "active" : ""
    }" data-panel="${group.tab}"><h4>${escapeHtml(group.title)}</h4>${rows}</div>`;
  }).join("");

  const blockPanel = `
    <div class="tab-panel ${state.currentTab === "user_block" ? "active" : ""}" data-panel="user_block">
      <h4>用户屏蔽</h4>
      <p class="muted small">只作用于本群，被屏蔽用户的消息会被直接忽略</p>
      <div class="block-form compact">
        <label class="block-field"><span>用户 ID</span>
          <input id="group-block-user" class="input mono" placeholder="QQ 号或字母数字" /></label>
        <label class="block-field"><span>屏蔽原因</span>
          <input id="group-block-reason" class="input" placeholder="例如：广告、刷屏" /></label>
        <label class="block-field"><span>屏蔽时间</span>
          <input id="group-block-duration" class="input mono" value="0" placeholder="0=永久，或 30m" /></label>
        <button id="group-block-add" class="primary small-btn" type="button">本群屏蔽</button>
      </div>
      <div class="block-toolbar">
        <input id="group-block-search" class="input" placeholder="搜索用户 ID 或原因" />
        <button id="group-block-toggle" class="ghost small-btn" type="button">展开</button>
      </div>
      <div id="group-block-list" class="block-list collapsed">
        <p class="muted small pad">加载中…</p>
      </div>
    </div>`;

  $("group-form").innerHTML = panels + blockPanel;
  bindFormEvents();
  switchTab(state.currentTab);
  ensureTabVisible("group-tabs");
  const groupAdd = $("group-block-add");
  if (groupAdd) groupAdd.addEventListener("click", () => void onBlockAddGroup());
  const groupSearch = $("group-block-search");
  if (groupSearch) {
    groupSearch.value = state.blockState.group.search;
    groupSearch.addEventListener("input", (event) => {
      state.blockState.group.search = event.target.value;
      if (event.target.value.trim()) state.blockState.group.expanded = true;
      renderGroupBlocks();
    });
  }
  const groupToggle = $("group-block-toggle");
  if (groupToggle) {
    groupToggle.addEventListener("click", () => {
      state.blockState.group.expanded = !state.blockState.group.expanded;
      renderGroupBlocks();
    });
  }
  if (state.currentTab === "user_block") void loadGroupBlocks();
}

/** 在下一帧将当前活动 Tab 滚入其横向滚动容器的可视范围。 */
function ensureTabVisible(containerId) {
  requestAnimationFrame(() => {
    const el = document.querySelector(`#${containerId} .tab.active`);
    if (el && typeof el.scrollIntoView === "function") {
      el.scrollIntoView({ inline: "nearest", block: "nearest" });
    }
  });
}

/** 平滑滚动横向 Tab，使刚切换的分区落在容器中央。 */
function centerTab(tab) {
  if (!tab || typeof tab.scrollIntoView !== "function") return;
  tab.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" });
}

/** 通过移除并重加 CSS 类重新播放分区切换过渡动画。 */
function playTabSwitch(el) {
  if (!el) return;
  el.classList.remove("tab-switch");
  void el.offsetWidth;
  el.classList.add("tab-switch");
}

/** 更新群级配置 Tab 与对应内容面板的活动状态。 */
function switchTab(name) {
  state.currentTab = name;
  document.querySelectorAll("#group-tabs .tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.tab === name);
  });
  document.querySelectorAll("#group-form .tab-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.panel === name);
  });
  if (name === "user_block") void loadGroupBlocks();
}

/** 将群级表单控件的修改同步回 state.currentGroup.values。 */
function bindFormEvents() {
  const form = $("group-form");
  form.querySelectorAll("[data-path]").forEach((input) => {
    const handler = () => {
      const path = input.dataset.path;
      if (!state.currentGroup) return;
      let value;
      if (input.type === "checkbox") {
        value = input.checked;
      } else if (input.type === "range") {
        value = Number(input.value);
        const label = form.querySelector(`.range-value[data-for="${path}"]`);
        if (label) label.textContent = value;
      } else {
        value = input.value;
      }
      state.currentGroup.values[path] = value;
      if (path === "active_reply.mode") {
        refreshConditionalRows();
      }
    };
    input.addEventListener("input", handler);
    input.addEventListener("change", handler);
  });
}

/** 按当前群级配置值重新显示或隐藏与模式相关的字段行。 */
function refreshConditionalRows() {
  const data = state.currentGroup;
  if (!data) return;
  data.fields.forEach((field) => {
    const row = document.querySelector(`[data-row="${field.path}"]`);
    if (row) {
      row.style.display = fieldVisible(field, data.values) ? "" : "none";
    }
  });
}

/** 提示用户先开启群独立配置，不在跟随全局状态下执行写入操作。 */
function shakeIndependent() {
  const wrap = $("independent-wrap");
  wrap.classList.remove("shake");
  void wrap.offsetWidth;
  wrap.classList.add("shake");
  setTimeout(() => wrap.classList.remove("shake"), 800);
  toast("请先打开「独立配置」，再保存或恢复", true);
}

/** 加载指定群配置，更新当前群状态并渲染可编辑的群面板。 */
async function openGroup(groupId) {
  // 先记录当前选择并更新目录选中态，再取后端权威配置；编辑值与全局值
  // 都复制成新对象，后续表单输入只改前端编辑副本，不会污染响应缓存。
  state.currentGroupId = String(groupId);
  renderGroupList();
  renderDrawerGroups();
  try {
    const data = await callApi("GET", `groups/${encodeURIComponent(groupId)}`);
    state.currentGroup = {
      ...data,
      values: { ...(data.values || {}) },
      global: { ...(data.global || {}) },
    };
    if (Array.isArray(data.providers) && data.providers.length) {
      state.providers = data.providers;
    }
    renderGroupPanel();
  } catch (error) {
    toast(error.message || "加载群配置失败", true);
  }
}

/** 校验本群处于独立模式后提交覆盖值，并刷新列表与面板状态。 */
async function saveGroup() {
  const data = state.currentGroup;
  if (!data) return;
  const independent = $("independent-toggle").checked;
  if (!independent) {
    // 跟随全局的群不保存覆盖项；通过视觉提示告诉用户先切换到独立模式。
    shakeIndependent();
    return;
  }
  const button = $("save-group");
  button.disabled = true;
  try {
    // 仅提交群级值与名称备注，成功后重新取目录和该群配置，确保页面展示
    // 的状态来自已持久化的数据，而不是仅沿用本地表单快照。
    await callApi("POST", `groups/${encodeURIComponent(data.group_id)}/save`, {
      independent: true,
      note: data.name || "",
      values: data.values,
    });
    toast("本群配置已保存");
    await loadGroups(true);
    await openGroup(data.group_id);
  } catch (error) {
    toast(error.message || "保存失败", true);
  } finally {
    button.disabled = false;
  }
}

/** 将当前群表单重置为 schema 默认值；更改仅在用户保存后写入。 */
function resetGroup() {
  const data = state.currentGroup;
  if (!data) return;
  if (!$("independent-toggle").checked) {
    shakeIndependent();
    return;
  }
  (data.fields || []).forEach((field) => {
    data.values[field.path] = field.default;
  });
  renderGroupPanel();
  toast("已填入默认值，点击「保存」后生效");
}

/** 二次确认后删除群级覆盖配置，使指定群重新跟随全局值。 */
async function followGlobal(groupId) {
  const confirmed = await askConfirm(
    `取消独立配置会删除该群的自有配置，之后跟随全局配置。继续？`
  );
  if (!confirmed) return;
  try {
    await callApi("POST", `groups/${encodeURIComponent(groupId)}/reset`, {});
    toast("已取消独立配置，改为跟随全局");
    await loadGroups(true);
    await openGroup(groupId);
  } catch (error) {
    toast(error.message || "操作失败", true);
  }
}

/* ------------------------------------------------------------------ */
/* 全局设置（按 _conf_schema.json 渲染）                                */
/* ------------------------------------------------------------------ */
/** 检查 schema 条件对象中的每个键值是否与当前配置区块相符。 */
function conditionOk(meta, scope) {
  if (!meta || !meta.condition) return true;
  return Object.entries(meta.condition).every(
    ([key, expected]) => scope && scope[key] === expected
  );
}

/** 根据全局配置 schema 类型生成对应输入控件及其 data 属性。 */
function buildSchemaControl(meta, value, path) {
  const type = String(meta.type || "string");
  if (type === "bool") {
    return `
      <label class="switch">
        <input type="checkbox" data-global-path="${path}" data-global-type="bool" ${
          value ? "checked" : ""
        } />
        <span class="slider"></span>
      </label>`;
  }
  if (meta._special === "select_provider") {
    return `<select data-global-path="${path}" data-global-type="str">${providerOptions(
      value || ""
    )}</select>`;
  }
  if (Array.isArray(meta.options) && meta.options.length) {
    const labels = Array.isArray(meta.labels) ? meta.labels : [];
    const options = meta.options
      .map(
        (option, index) =>
          `<option value="${escapeHtml(option)}" ${
            option === value ? "selected" : ""
          }>${escapeHtml(labels[index] || option)}</option>`
      )
      .join("");
    return `<select data-global-path="${path}" data-global-type="str">${options}</select>`;
  }
  if (type === "int" || type === "float") {
    const slider = meta.slider || {};
    const min = Number(slider.min ?? 0);
    const max = Number(slider.max ?? 100);
    const step = Number(slider.step ?? (type === "int" ? 1 : 0.1));
    return `
      <input type="range" data-global-path="${path}" data-global-type="${type}"
        min="${min}" max="${max}" step="${step}" value="${value ?? min}" />
      <span class="range-value" data-for-global="${path}">${value ?? min}</span>`;
  }
  if (type === "text") {
    return `<textarea data-global-path="${path}" data-global-type="text">${escapeHtml(
      value || ""
    )}</textarea>`;
  }
  if (type === "list") {
    const items = Array.isArray(value) ? value.join("\n") : "";
    return `<textarea data-global-path="${path}" data-global-type="list" placeholder="每行一个，或用逗号分隔">${escapeHtml(
      items
    )}</textarea>`;
  }
  if (type === "dict") {
    return `<textarea data-global-path="${path}" data-global-type="dict">${escapeHtml(
      JSON.stringify(value || {}, null, 2)
    )}</textarea>`;
  }
  return `<input type="text" data-global-path="${path}" data-global-type="str" value="${escapeHtml(
    value ?? ""
  )}" />`;
}

/** 递归渲染 schema 对象与字段行，并在初始状态隐藏条件字段。 */
function renderSchemaBlock(schema, scopeValues, prefix) {
  let html = "";
  for (const [key, meta] of Object.entries(schema)) {
    if (!meta || typeof meta !== "object") continue;
    const path = prefix ? `${prefix}.${key}` : key;
    if (meta.type === "object" && meta.items) {
      const child =
        scopeValues[key] && typeof scopeValues[key] === "object"
          ? scopeValues[key]
          : {};
      html += `<div class="form-group"><h4>${escapeHtml(
        meta.description || key
      )}</h4>${renderSchemaBlock(meta.items, child, path)}</div>`;
      continue;
    }
    // 条件字段也要渲染出来，只是默认隐藏：这样用户切换下拉时能立刻显示/隐藏
    const condition = meta.condition;
    const hasCondition =
      condition && typeof condition === "object" && Object.keys(condition).length > 0;
    const visible = !hasCondition || conditionOk(meta, scopeValues);
    const condAttr = hasCondition
      ? ` data-cond="${escapeHtml(JSON.stringify(condition))}"`
      : "";
    const style = visible ? "" : ' style="display:none"';
    const hint = meta.hint
      ? `<span class="field-hint">${escapeHtml(cleanHint(meta.hint))}</span>`
      : "";
    html += `
      <div class="form-row" data-global-row="${path}"${condAttr}${style}>
        <label>${escapeHtml(meta.description || key)}</label>
        <div class="control">${buildSchemaControl(meta, scopeValues[key], path)}${hint}</div>
      </div>`;
  }
  return html;
}

/** 收集当前面板值并重绘活动全局配置区块，随后恢复输入事件绑定。 */
function rerenderActiveGlobalPanel() {
  const sections = globalSections();
  const entry = sections.find(([key]) => key === state.globalTab) || sections[0];
  if (!entry) return;
  const [key, meta] = entry;
  const values = collectGlobalValues();
  const scope =
    values[key] && typeof values[key] === "object" ? values[key] : {};
  const panel = document.querySelector(
    `#global-form .tab-panel[data-gpanel="${key}"]`
  );
  if (!panel) return;
  panel.innerHTML =
    `<h4>${escapeHtml(meta.description || key)}</h4>` +
    renderSchemaBlock(meta.items, scope, key);
  bindGlobalInputs();
  refreshGlobalConditions();
}

/** 根据已填写的全局配置值同步 schema 条件字段的显示状态。 */
function refreshGlobalConditions() {
  const values = collectGlobalValues();
  document.querySelectorAll("#global-form [data-cond]").forEach((row) => {
    let condition = {};
    try {
      condition = JSON.parse(row.dataset.cond || "{}");
    } catch (error) {
      condition = {};
    }
    const path = row.dataset.globalRow || "";
    const scopePath = path.split(".").slice(0, -1).join(".");
    const scope = (scopePath ? getPath(values, scopePath) : values) || {};
    const ok = Object.entries(condition).every(
      ([key, expected]) => scope[key] === expected
    );
    row.style.display = ok ? "" : "none";
  });
}

/** 绑定全局表单输入监听，更新滑块标签并及时刷新条件字段。 */
function bindGlobalInputs() {
  document.querySelectorAll("#global-form [data-global-path]").forEach((input) => {
    if (input.type === "range") {
      const label = document.querySelector(
        `.range-value[data-for-global="${input.dataset.globalPath}"]`
      );
      input.addEventListener("input", () => {
        if (label) label.textContent = input.value;
      });
    }
    const rerun = () => refreshGlobalConditions();
    input.addEventListener("change", rerun);
    if (input.tagName === "SELECT" || input.type === "checkbox") {
      input.addEventListener("input", rerun);
    }
  });
}

/** 从加载的 schema 中提取可作为一级 Tab 的对象配置区块。 */
function globalSections() {
  return Object.entries(state.schema || {}).filter(
    ([, meta]) => meta && meta.type === "object" && meta.items
  );
}

/** 切换全局设置的活动 Tab 和相应配置面板。 */
function switchGlobalTab(key) {
  state.globalTab = key;
  document.querySelectorAll("#global-tabs .tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.gtab === key);
  });
  document.querySelectorAll("#global-form .tab-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.gpanel === key);
  });
}

/** 依据 schema 渲染全局配置 Tab 和递归字段表单。 */
function renderGlobalForm() {
  const sections = globalSections();
  if (!sections.length) {
    $("global-tabs").innerHTML = "";
    $("global-form").innerHTML =
      '<p class="muted small pad">没有可用的配置 schema</p>';
    return;
  }

  $("global-tabs").innerHTML = sections
    .map(
      ([key, meta], index) => `
      <button class="tab ${index === 0 ? "active" : ""}" data-gtab="${escapeHtml(
        key
      )}" role="tab">${icon(SECTION_ICONS[key] || "sliders")}${escapeHtml(meta.description || key)}</button>`
    )
    .join("");

  $("global-form").innerHTML = sections
    .map(([key, meta], index) => {
      const scope =
        state.globalValues[key] && typeof state.globalValues[key] === "object"
          ? state.globalValues[key]
          : {};
      return `<div class="tab-panel ${
        index === 0 ? "active" : ""
      }" data-gpanel="${escapeHtml(key)}"><h4>${escapeHtml(
        meta.description || key
      )}</h4>${renderSchemaBlock(meta.items, scope, key)}</div>`;
    })
    .join("");

  bindGlobalInputs();
  const activeKey = sections.some(([key]) => key === state.globalTab)
    ? state.globalTab
    : sections[0][0];
  switchGlobalTab(activeKey);
  ensureTabVisible("global-tabs");
}

/** 递归提取 schema 默认值并复制数组/对象，避免共享引用被表单修改。 */
function schemaDefaults(schema) {
  const defaults = {};
  for (const [key, meta] of Object.entries(schema || {})) {
    if (!meta || typeof meta !== "object") continue;
    if (meta.type === "object" && meta.items) {
      defaults[key] = schemaDefaults(meta.items);
      continue;
    }
    const value = meta.default;
    defaults[key] =
      value && typeof value === "object" ? JSON.parse(JSON.stringify(value)) : value;
  }
  return defaults;
}

/** 从后端载入 schema、全局配置和 provider 选项后刷新设置表单。 */
async function loadGlobal() {
  try {
    const data = await callApi("GET", "config");
    state.schema = data.schema || {};
    state.globalValues = data.values || {};
    state.providers = data.providers || state.providers;
    state.embeddingProviders =
      data.embedding_providers || state.embeddingProviders;
    renderGlobalForm();
    decorateEmbeddingHint();
    $("global-hint").textContent = "";
  } catch (error) {
    $("global-tabs").innerHTML = "";
    $("global-form").innerHTML = `<p class="muted small pad">加载失败：${escapeHtml(
      error.message || "未知错误"
    )}</p>`;
  }
}

/** 将编辑中的全局值重置为 schema 默认值，不在此处立即保存。 */
function resetGlobalDefaults() {
  if (!state.schema || !Object.keys(state.schema).length) {
    toast("配置尚未加载完成，请稍候", true);
    return;
  }
  state.globalValues = schemaDefaults(state.schema);
  renderGlobalForm();
  $("global-hint").textContent = "已填入默认值，点击「保存」后生效";
  toast("已填入默认值，点击「保存」后生效");
}

/** 从 DOM 控件重建完整嵌套配置对象，供服务端 schema 校验。 */
function collectGlobalValues() {
  const values = {};
  document.querySelectorAll("#global-form [data-global-path]").forEach((input) => {
    const path = input.dataset.globalPath;
    const type = input.dataset.globalType;
    let value;
    if (type === "bool") {
      value = input.checked;
    } else if (type === "int" || type === "float") {
      value = Number(input.value);
    } else if (type === "list") {
      value = input.value
        .split(/[\n,，]/)
        .map((item) => item.trim())
        .filter(Boolean);
    } else if (type === "dict") {
      try {
        value = JSON.parse(input.value || "{}");
      } catch (error) {
        value = {};
      }
    } else {
      value = input.value;
    }
    setPath(values, path, value);
  });
  return values;
}

/** 提交收集后的全局设置，展示写盘警告，并刷新配置及概览。 */
async function saveGlobal() {
  const button = $("save-global");
  button.disabled = true;
  $("global-hint").textContent = "保存中…";
  try {
    const result = await callApi("POST", "config/save", {
      values: collectGlobalValues(),
    });
    $("global-hint").textContent = result.warning
      ? `已保存，但写盘失败：${result.warning}`
      : "已保存，立即生效";
    toast(result.warning ? "已保存（写盘失败，仅本次运行生效）" : "全局配置已保存");
    await loadGlobal();
    await loadOverview();
  } catch (error) {
    $("global-hint").textContent = "";
    toast(error.message || "保存失败", true);
  } finally {
    button.disabled = false;
  }
}

/* ------------------------------------------------------------------ */
/* 记忆 RAG                                                            */
/* ------------------------------------------------------------------ */
/** 加载记忆统计；需要筛选器选项时同时更新可用群作用域。 */
async function loadRagStats(updateScopes = false) {
  try {
    const data = await callApi("GET", "rag/stats");
    const stats = data.stats || {};
    if (updateScopes) {
      state.rag.scopes = data.group_scopes || [];
      const select = $("rag-scope");
      const current = state.rag.scope;
      select.innerHTML = [
        `<option value="">全部群范围</option>`,
        ...state.rag.scopes.map(
          (scope) =>
            `<option value="${escapeHtml(scope)}" ${
              scope === current ? "selected" : ""
            }>${escapeHtml(scope)}</option>`
        ),
      ].join("");
    }
    $("rag-stats").innerHTML = [
      statusCard("记忆总数", formatNumber(stats.total_memories), "全部群"),
      statusCard("群范围数", formatNumber(stats.group_scope_count), "不同 scope"),
      statusCard("角色数", formatNumber(stats.role_count), "关联角色 ID"),
      statusCard(
        "时间跨度",
        stats.oldest_memory_time_iso
          ? `${String(stats.oldest_memory_time_iso).slice(0, 10)} 起`
          : "-",
        stats.latest_memory_time_iso
          ? `最近 ${String(stats.latest_memory_time_iso).slice(0, 10)}`
          : "暂无记录"
      ),
    ].join("");
  } catch (error) {
    toast(error.message || "加载记忆统计失败", true);
  }
}

/** 渲染分页记忆记录，并为详情与删除按钮绑定对应操作。 */
function renderRagRows(items) {
  const body = $("rag-body");
  if (!Array.isArray(items) || !items.length) {
    body.innerHTML =
      '<tr><td colspan="7" class="muted center pad">当前筛选条件下没有记忆</td></tr>';
    return;
  }
  body.innerHTML = items
    .map(
      (item) => `
      <tr>
        <td class="mono" data-label="ID">${escapeHtml(item.memory_id)}</td>
        <td data-label="内容" title="${escapeHtml(item.content)}">${escapeHtml(clip(item.content))}</td>
        <td class="mono" data-label="群范围">${escapeHtml(item.group_scope || "-")}</td>
        <td data-label="角色">${
          (item.related_role_ids || [])
            .map((role) => `<span class="pill mono">${escapeHtml(role)}</span>`)
            .join("") || '<span class="muted">-</span>'
        }</td>
        <td data-label="来源"><span class="pill source-pill">${autoSourceLabel(
          item.source
        )}</span></td>
        <td class="mono small" data-label="时间">${escapeHtml(
          (item.memory_time_iso || "-").replace("T", " ").slice(0, 19)
        )}</td>
        <td>
          <div class="actions">
            <button class="link-btn" data-detail="${escapeHtml(item.memory_id)}">${icon("eye")}详情</button>
            <button class="link-btn danger" data-delete="${escapeHtml(
              item.memory_id
            )}">${icon("trash")}删除</button>
          </div>
        </td>
      </tr>`
    )
    .join("");

  body.querySelectorAll("[data-detail]").forEach((btn) => {
    btn.addEventListener("click", () => void showMemory(btn.dataset.detail));
  });
  body.querySelectorAll("[data-delete]").forEach((btn) => {
    btn.addEventListener("click", () => void deleteMemory(btn.dataset.delete));
  });
}

const AUTO_SCOPE_LABELS = {
  group_first: "本群优先 + 全局兜底",
  global: "全部全局",
  group: "仅本群",
};

/** 将数据库中的自动记忆来源标识转换为界面中文标签。 */
function autoSourceLabel(source) {
  const map = { model: "模型抽取", keyword: "关键词", manual: "手动", selftest: "自检" };
  return map[source] || source || "手动";
}

/** 获取自动记忆的当前开关、参数和来源统计。 */
/* ------------------------------------------------------------------ */
/* 用户屏蔽                                                             */
/* ------------------------------------------------------------------ */
function renderBlockList(container, entries, scope, groupId, emptyText) {
  if (!container) return;
  if (!entries || !entries.length) {
    container.innerHTML = `<p class="muted small pad">${escapeHtml(
      emptyText || "暂无屏蔽记录"
    )}</p>`;
    return;
  }
  container.innerHTML = entries
    .map(
      (entry) => `
      <div class="block-row">
        <div class="block-row-main">
          <span class="block-user mono">${escapeHtml(entry.user_id)}</span>
          <span class="pill">${escapeHtml(entry.scope_text || (entry.scope === "all" ? "全部会话" : "仅本群"))}</span>
          <span class="block-until">${escapeHtml(entry.until_text || "永久")}</span>
        </div>
        <div class="block-row-sub">原因：${escapeHtml(entry.reason || "未说明")}</div>
        <button class="link-btn danger" type="button"
          data-block-remove="${escapeHtml(entry.user_id)}"
          data-block-scope="${escapeHtml(scope)}"
          data-block-group="${escapeHtml(groupId || "")}">解除</button>
      </div>`
    )
    .join("");
}

function filterBlocks(entries, keyword) {
  const text = String(keyword || "").trim().toLowerCase();
  if (!text) return entries || [];
  return (entries || []).filter(
    (entry) =>
      String(entry.user_id || "").toLowerCase().includes(text) ||
      String(entry.reason || "").toLowerCase().includes(text)
  );
}

function renderGlobalBlocks() {
  const view = state.blockState.global;
  const entries = filterBlocks((state.blocks || {}).global, view.search);
  renderBlockList(
    $("block-global-list"),
    entries,
    "all",
    "",
    view.search ? "没有匹配的屏蔽记录" : "暂无屏蔽记录"
  );
  const list = $("block-global-list");
  if (list) list.classList.toggle("collapsed", !view.expanded);
  const toggle = $("block-toggle");
  if (toggle) {
    toggle.textContent = view.expanded ? "折叠" : `展开（${entries.length}）`;
  }
}

function renderGroupBlocks() {
  const view = state.blockState.group;
  const entries = filterBlocks(state.groupBlocks, view.search);
  renderBlockList(
    $("group-block-list"),
    entries,
    "group",
    (state.currentGroup || {}).group_id || "",
    view.search ? "没有匹配的屏蔽记录" : "本群暂无单独屏蔽记录"
  );
  const list = $("group-block-list");
  if (list) list.classList.toggle("collapsed", !view.expanded);
  const toggle = $("group-block-toggle");
  if (toggle) {
    toggle.textContent = view.expanded ? "折叠" : `展开（${entries.length}）`;
  }
}

async function loadBlocks() {
  try {
    const data = await callApi("GET", "blocks");
    state.blocks = data;
    const status = $("block-status");
    status.textContent = data.enabled ? "已启用" : "已关闭";
    status.className = `pill ${data.enabled ? "" : "muted-pill"}`;
    renderGlobalBlocks();
  } catch (error) {
    $("block-global-list").innerHTML = `<p class="muted small pad">加载失败：${escapeHtml(
      error.message || "未知错误"
    )}</p>`;
  }
}

async function loadGroupBlocks() {
  const data = state.currentGroup;
  if (!data) return;
  try {
    const result = await callApi("GET", "blocks", { group_id: data.group_id });
    state.groupBlocks = result.group || [];
    renderGroupBlocks();
  } catch (error) {
    const box = $("group-block-list");
    if (box) {
      box.innerHTML = `<p class="muted small pad">加载失败：${escapeHtml(
        error.message || "未知错误"
      )}</p>`;
    }
  }
}

async function submitBlock(payload) {
  return callApi("POST", "blocks/add", payload);
}

async function onBlockAddGlobal() {
  const userId = $("block-user").value.trim();
  const reason = $("block-reason").value.trim();
  const scope = $("block-scope").value;
  const groupId = $("block-group").value.trim();
  const duration = $("block-duration").value.trim() || "0";
  if (!/^[A-Za-z0-9]+$/.test(userId)) {
    toast("用户 ID 只能包含英文字母和数字", true);
    return;
  }
  try {
    await submitBlock({
      user_id: userId,
      reason,
      scope,
      group_id: scope === "group" ? groupId : "",
      duration,
    });
    toast("已添加屏蔽");
    $("block-user").value = "";
    $("block-reason").value = "";
    $("block-duration").value = "0";
    if (scope === "all") {
      void loadBlocks();
    } else {
      toast(`已加入群 ${groupId} 的屏蔽列表`, false);
    }
  } catch (error) {
    toast(error.message || "添加失败", true);
  }
}

async function onBlockAddGroup() {
  const data = state.currentGroup;
  if (!data) return;
  const userId = $("group-block-user").value.trim();
  const reason = $("group-block-reason").value.trim();
  const duration = $("group-block-duration").value.trim() || "0";
  if (!/^[A-Za-z0-9]+$/.test(userId)) {
    toast("用户 ID 只能包含英文字母和数字", true);
    return;
  }
  try {
    await submitBlock({
      user_id: userId,
      reason,
      scope: "group",
      group_id: data.group_id,
      duration,
    });
    toast("已在本群屏蔽该用户");
    $("group-block-user").value = "";
    $("group-block-reason").value = "";
    $("group-block-duration").value = "0";
    void loadGroupBlocks();
  } catch (error) {
    toast(error.message || "添加失败", true);
  }
}

async function removeBlockEntry(button) {
  const userId = button.dataset.blockRemove;
  const scope = button.dataset.blockScope || "all";
  const groupId = button.dataset.blockGroup || "";
  button.disabled = true;
  try {
    await callApi("POST", "blocks/remove", {
      user_id: userId,
      scope,
      group_id: groupId,
    });
    toast("已解除屏蔽");
    if (scope === "group") {
      void loadGroupBlocks();
    } else {
      void loadBlocks();
    }
  } catch (error) {
    toast(error.message || "解除失败", true);
    button.disabled = false;
  }
}

async function loadAutoMemory() {
  try {
    const data = await callApi("GET", "memory/auto");
    state.autoMemory = data;
    renderAutoMemory(data);
  } catch (error) {
    $("auto-memory-body").innerHTML = `<p class="muted small pad">加载失败：${escapeHtml(
      error.message || "未知错误"
    )}</p>`;
  }
}

/** 展示自动记忆参数、开关状态和关键词/模型的记忆数量统计。 */
function renderAutoMemory(data) {
  if (!data) return;
  const enabled = Boolean(data.enabled);
  const status = $("auto-memory-status");
  status.textContent = enabled ? "已开启" : "已关闭";
  status.className = `pill ${enabled ? "" : "muted-pill"}`;
  const toggle = $("auto-memory-toggle");
  toggle.textContent = enabled ? "关闭" : "开启";
  const provider = data.provider_id ? data.provider_id : "跟随当前会话模型";
  const stats = data.stats || {};
  const rows = [
    ["抽取模型", provider],
    ["抽取频率", `${data.every_n} 条 / ${data.interval_minutes} 分钟`],
    ["回看窗口", `最近 ${data.window} 条消息`],
    ["最低置信度", Number(data.min_confidence).toFixed(2)],
    ["记忆作用域", AUTO_SCOPE_LABELS[data.scope] || data.scope || "-"],
  ];
  $("auto-memory-body").innerHTML = `
    <div class="auto-memory-grid">
      ${rows
        .map(
          ([label, value]) => `
        <div class="auto-memory-item">
          <span class="auto-memory-label">${escapeHtml(label)}</span>
          <span class="auto-memory-value">${escapeHtml(String(value))}</span>
        </div>`
        )
        .join("")}
    </div>
    <div class="auto-memory-stats">
      <span>关键词记忆 <b>${formatNumber(stats.keyword || 0)}</b></span>
      <span>模型抽取 <b>${formatNumber(stats.model || 0)}</b></span>
      <span>今日新增 <b>${formatNumber(stats.today || 0)}</b></span>
      <span>记忆总数 <b>${formatNumber(stats.total || 0)}</b></span>
    </div>`;
}

/** 切换自动记忆开关并刷新面板及记忆统计。 */
async function toggleAutoMemory() {
  const current = state.autoMemory;
  if (!current) return;
  const next = !current.enabled;
  const button = $("auto-memory-toggle");
  button.disabled = true;
  try {
    const data = await callApi("POST", "memory/auto/toggle", { enable: next });
    state.autoMemory = data;
    renderAutoMemory(data);
    toast(next ? "已开启自动记忆" : "已关闭自动记忆");
    void loadRagStats(true);
  } catch (error) {
    toast(error.message || "切换失败", true);
  } finally {
    button.disabled = false;
  }
}

/** 以当前页码、关键词、群范围和角色筛选条件加载记忆列表。 */
async function loadRagList() {
  const params = {
    page: state.rag.page,
    page_size: state.rag.pageSize,
  };
  if (state.rag.keyword) params.keyword = state.rag.keyword;
  if (state.rag.scope) params.group_scope = state.rag.scope;
  if (state.rag.role) params.role_id = state.rag.role;
  try {
    const data = await callApi("GET", "rag/list", params);
    state.rag.total = Number(data.total || 0);
    state.rag.hasMore = Boolean(data.has_more);
    renderRagRows(data.items || []);
    const totalPages = Math.max(1, Math.ceil(state.rag.total / state.rag.pageSize));
    $("rag-page-info").textContent = `第 ${state.rag.page} / ${totalPages} 页 · 共 ${formatNumber(
      state.rag.total
    )} 条`;
    $("rag-prev").disabled = state.rag.page <= 1;
    $("rag-next").disabled = !state.rag.hasMore;
  } catch (error) {
    toast(error.message || "加载记忆列表失败", true);
  }
}

/** 查询并在详情抽屉中展示指定记忆的完整 JSON 内容。 */
async function showMemory(memoryId) {
  try {
    const data = await callApi("GET", `rag/${encodeURIComponent(memoryId)}`);
    $("detail-title").textContent = "记忆详情";
    $("detail-body").textContent = JSON.stringify(data, null, 2);
    $("detail").classList.add("open");
    $("detail").setAttribute("aria-hidden", "false");
  } catch (error) {
    toast(error.message || "加载详情失败", true);
  }
}

/** 关闭记忆详情/自检共用的侧边抽屉。 */
function closeDetail() {
  $("detail").classList.remove("open");
  $("detail").setAttribute("aria-hidden", "true");
}

/** 确认删除指定记忆，并在成功后更新统计与当前列表。 */
async function deleteMemory(memoryId) {
  const confirmed = await askConfirm(`确定删除记忆 ${memoryId}？该操作不可恢复`);
  if (!confirmed) return;
  try {
    await callApi("POST", `rag/${encodeURIComponent(memoryId)}/delete`, {});
    toast(`已删除记忆 ${memoryId}`);
    await loadRagStats(false);
    await loadRagList();
  } catch (error) {
    toast(error.message || "删除失败", true);
  }
}

/** 执行记忆 RAG 后端自检并在详情抽屉展示各步骤结果。 */
async function ragSelftest() {
  const button = $("rag-selftest");
  button.disabled = true;
  try {
    const data = await callApi("POST", "rag/selftest", {});
    const steps = data.steps || [];
    const lines = [
      data.passed ? "✅ 自检通过：写入 / 读回 / 语义检索 / 删除 全部正常" : "❌ 自检未通过",
      "",
      ...steps.map(
        (step) => `${step.ok ? "✅" : "❌"} ${step.name}${step.detail ? `：${step.detail}` : ""}`
      ),
    ];
    $("detail-title").textContent = "记忆 RAG 自检";
    $("detail-body").textContent = lines.join("\n");
    $("detail").classList.add("open");
    $("detail").setAttribute("aria-hidden", "false");
    toast(data.passed ? "记忆 RAG 自检通过" : "记忆 RAG 自检未通过", !data.passed);
  } catch (error) {
    toast(error.message || "自检失败", true);
  } finally {
    button.disabled = false;
  }
}

/** 确认执行旧记录规范化，并展示检查/更新数量。 */
async function cleanupMemories() {
  const confirmed = await askConfirm(
    "将把旧记录的毫秒时间戳规范化，并刷新时区元数据。继续？"
  );
  if (!confirmed) return;
  try {
    const data = await callApi("POST", "rag/cleanup", {});
    toast(`清理完成：检查 ${data.scanned ?? 0} 条，更新 ${data.updated ?? 0} 条`);
    await loadRagStats(false);
    await loadRagList();
  } catch (error) {
    toast(error.message || "清理失败", true);
  }
}

/* ------------------------------------------------------------------ */
/* 事件绑定                                                            */
/* ------------------------------------------------------------------ */
/** 一次性绑定页面导航、群配置、全局表单和记忆操作的事件处理器。 */
function lockButton(button) {
  if (button.dataset.locking === "1") return;
  button.dataset.locking = "1";
  button.classList.add("btn-loading");
  button.setAttribute("aria-busy", "true");

  const spinner = document.createElement("span");
  spinner.className = "btn-spinner";
  spinner.setAttribute("aria-hidden", "true");
  const icon = button.querySelector("svg.icon");
  const rect = button.getBoundingClientRect();
  let left = rect.width / 2;
  let top = rect.height / 2;
  if (icon) {
    const iconRect = icon.getBoundingClientRect();
    left = iconRect.left - rect.left + iconRect.width / 2;
    top = iconRect.top - rect.top + iconRect.height / 2;
  }
  spinner.style.left = `${left}px`;
  spinner.style.top = `${top}px`;
  button.appendChild(spinner);

  window.setTimeout(() => {
    button.classList.remove("btn-loading");
    button.removeAttribute("aria-busy");
    delete button.dataset.locking;
    spinner.remove();
  }, 1000);
}

const FEEDBACK_SELECTOR = [
  "#save-group",
  "#save-global",
  "#reset-group",
  "#reset-global",
  "#rag-reset",
  "#overview-refresh",
  "#groups-refresh",
  "#rag-search",
  "#rag-cleanup",
  "#rag-selftest",
  "#rag-prev",
  "#rag-next",
  "#auto-memory-toggle",
  "#block-add",
  "#group-block-add",
  "#modal-ok",
  "[data-delete]",
  "[data-block-remove]",
  "[data-feedback]",
].join(",");

function installButtonFeedback() {
  document.addEventListener(
    "click",
    (event) => {
      const button = event.target.closest("button");
      if (!button) return;
      if (button.classList.contains("no-lock")) return;
      if (!button.matches(FEEDBACK_SELECTOR)) return;
      if (button.classList.contains("btn-loading")) return;
      if (button.disabled) return;
      lockButton(button);
    },
    true
  );
}

function bindEvents() {
  $("menu-btn").addEventListener("click", () => {
    const opened = $("drawer").classList.contains("open");
    opened ? closeDrawer() : openDrawer();
  });
  $("drawer-close").addEventListener("click", closeDrawer);
  $("scrim").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeDrawer();
      closeDetail();
    }
  });

  document.querySelectorAll(".nav-item").forEach((item) => {
    item.addEventListener("click", () => switchView(item.dataset.view));
  });

  $("drawer-manage-groups").addEventListener("click", () => {
    state.currentGroupId = "";
    switchView("groups");
  });
  $("drawer-settings").addEventListener("click", () => switchView("settings"));

  $("overview-refresh").addEventListener("click", () => void loadOverview(true));
  $("groups-refresh").addEventListener("click", () => void loadGroups(true));
  $("group-search").addEventListener("input", (event) => {
    state.groupSearch = event.target.value;
    renderGroupList();
  });

  $("manual-group-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const groupId = $("manual-group-id").value.trim();
    if (!/^\d{4,20}$/.test(groupId)) {
      toast("请输入合法的群号（纯数字）", true);
      return;
    }
    if (!state.groups.some((item) => item.group_id === groupId)) {
      state.groups.push({
        group_id: groupId,
        name: "",
        independent: false,
        active_reply: false,
      });
    }
    renderGroupList();
    void openGroup(groupId);
  });

  // 未开启独立配置时，群面板里的按钮（分区 Tab / 保存 / 恢复默认）点击都会抖动提示
  $("group-panel").addEventListener(
    "click",
    (event) => {
      if (!state.currentGroup) return;
      if ($("independent-toggle").checked) return;
      const button = event.target.closest("button");
      if (!button) return;
      // “用户屏蔽”不是群配置覆盖项，不受「先开独立配置」限制
      if (
        button.closest('[data-panel="user_block"]') ||
        (button.classList.contains("tab") && button.dataset.tab === "user_block")
      ) {
        return;
      }
      shakeIndependent();
      if (!button.classList.contains("tab")) {
        // 保存 / 恢复默认：直接拦下，避免重复提示
        event.preventDefault();
        event.stopPropagation();
      }
    },
    true
  );

  $("group-tabs").addEventListener("click", (event) => {
    const tab = event.target.closest(".tab");
    if (tab) {
      switchTab(tab.dataset.tab);
      playTabSwitch($("group-form"));
      centerTab(tab);
    }
  });

  $("independent-toggle").addEventListener("change", (event) => {
    const data = state.currentGroup;
    if (!data) return;

    if (!event.target.checked) {
      // 关闭独立配置 = 删除该群配置、回到跟随全局；先还原勾选，等用户确认
      event.target.checked = true;
      void followGlobal(data.group_id);
      return;
    }

    data.independent = true;
    $("save-hint").textContent = "当前为本群独立配置，会覆盖全局对应项";
  });

  $("save-group").addEventListener("click", () => void saveGroup());
  $("reset-group").addEventListener("click", () => void resetGroup());

  // 全局设置：兜底用 document 级事件委托，避免个别环境里逐元素绑定失效
  document.addEventListener(
    "change",
    (event) => {
      const target = event.target;
      if (!target || !target.closest) return;
      if (target.closest("#global-form")) {
        if (target.matches("select, input[type=checkbox]")) {
          rerenderActiveGlobalPanel();
        } else {
          refreshGlobalConditions();
        }
        return;
      }
      if (target.closest("#group-form")) {
        refreshConditionalRows();
      }
    },
    true
  );

  $("global-tabs").addEventListener("click", (event) => {
    const tab = event.target.closest(".tab");
    if (tab) {
      switchGlobalTab(tab.dataset.gtab);
      playTabSwitch($("global-form"));
      centerTab(tab);
    }
  });

  $("save-global").addEventListener("click", () => void saveGlobal());
  $("reset-global").addEventListener("click", () => resetGlobalDefaults());

  $("rag-search").addEventListener("click", () => {
    state.rag.keyword = $("rag-keyword").value.trim();
    state.rag.scope = $("rag-scope").value;
    state.rag.role = $("rag-role").value.trim();
    state.rag.page = 1;
    void loadRagList();
  });
  $("rag-reset").addEventListener("click", () => {
    $("rag-keyword").value = "";
    $("rag-scope").value = "";
    $("rag-role").value = "";
    state.rag.keyword = "";
    state.rag.scope = "";
    state.rag.role = "";
    state.rag.page = 1;
    void loadRagList();
  });
  $("theme-btn").addEventListener("click", () => cycleTheme());
  $("drawer-blocks").addEventListener("click", () => switchView("blocks"));
  $("block-add").addEventListener("click", () => void onBlockAddGlobal());
  $("block-search").addEventListener("input", (event) => {
    state.blockState.global.search = event.target.value;
    if (event.target.value.trim()) state.blockState.global.expanded = true;
    renderGlobalBlocks();
  });
  $("block-toggle").addEventListener("click", () => {
    state.blockState.global.expanded = !state.blockState.global.expanded;
    renderGlobalBlocks();
  });
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-block-remove]");
    if (button) void removeBlockEntry(button);
  });
  $("auto-memory-toggle").addEventListener("click", () => void toggleAutoMemory());
  $("auto-memory-settings").addEventListener("click", () => {
    state.globalTab = "memory_rag";
    switchView("settings");
  });
  $("rag-cleanup").addEventListener("click", () => void cleanupMemories());
  $("rag-selftest").addEventListener("click", () => void ragSelftest());
  $("rag-prev").addEventListener("click", () => {
    if (state.rag.page <= 1) return;
    state.rag.page -= 1;
    void loadRagList();
  });
  $("rag-next").addEventListener("click", () => {
    if (!state.rag.hasMore) return;
    state.rag.page += 1;
    void loadRagList();
  });
  $("rag-page-size").addEventListener("change", (event) => {
    state.rag.pageSize = Number(event.target.value) || 20;
    state.rag.page = 1;
    void loadRagList();
  });
  $("rag-keyword").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      $("rag-search").click();
    }
  });
  $("detail-close").addEventListener("click", closeDetail);
  installButtonFeedback();
}

/* ------------------------------------------------------------------ */
/* 启动                                                                */
/* ------------------------------------------------------------------ */
/** 等待 AstrBot bridge、注册主题变化监听，再进入状态概览页。 */
async function init() {
  bindEvents();
  state.themeMode = loadThemeMode();
  state.systemDark =
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-color-scheme: dark)").matches;
  void initMorphIcons();
  applyThemeMode(state.themeMode);
  $("drawer-version").textContent = `UI${UI_BUILD}`;
  if (!bridge) {
    document.body.insertAdjacentHTML(
      "afterbegin",
      '<div style="padding:14px 20px;background:#fdeceb;color:#b42318;font-size:13px;">' +
        "未检测到 AstrBot 插件视图环境，请从 AstrBot 面板的插件详情页打开本页面</div>"
    );
    return;
  }
  try {
    const context = await bridge.ready();
    if (context) {
      state.systemDark = Boolean(context.isDark);
      applyThemeMode(state.themeMode);
      document.title = context.pageTitle || "主动回复 · 控制台";
    }
    if (typeof bridge.onContext === "function") {
      bridge.onContext((next) => {
        if (next) {
          state.systemDark = Boolean(next.isDark);
          applyThemeMode(state.themeMode);
        }
      });
    }
  } catch (error) {
    console.warn("bridge.ready failed", error);
  }
  switchView("overview");
}

void init();
