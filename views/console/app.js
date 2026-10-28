/**
 * 主动回复（ActiveReply）控制台前端。
 *
 * 通过 AstrBot 插件视图 bridge 与后端通信：
 *   window.AstrBotPluginView.apiGet(endpoint, params)
 *   window.AstrBotPluginView.apiPost(endpoint, body)
 * 后端统一返回 { success: true, data } 或 { success: false, error }。
 */

const UI_BUILD = 10;
console.info(`[ActiveReply] UI build ${UI_BUILD}`);

const bridge = window.AstrBotPluginView || window.AstrBotPluginPage || null;

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

const $ = (id) => document.getElementById(id);

/* ------------------------------------------------------------------ */
/* 基础工具                                                            */
/* ------------------------------------------------------------------ */
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

function toast(message, isError = false) {
  const node = $("toast");
  node.textContent = message;
  node.classList.toggle("error", Boolean(isError));
  node.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("show"), 2400);
}

async function callApi(method, endpoint, payload) {
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

function formatNumber(value) {
  return Number(value || 0).toLocaleString("zh-CN");
}

function cleanHint(text) {
  return String(text || "").trim().replace(/[。.]+$/, "");
}

function clip(text, max = 110) {
  const source = String(text || "").replace(/\s+/g, " ").trim();
  return source.length > max ? `${source.slice(0, max)}…` : source;
}

function getPath(obj, path) {
  let current = obj;
  for (const key of String(path).split(".")) {
    if (!current || typeof current !== "object") return undefined;
    current = current[key];
  }
  return current;
}

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

function avatarUrl(groupId) {
  return `https://p.qlogo.cn/gh/${encodeURIComponent(groupId)}/${encodeURIComponent(groupId)}/100`;
}

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
function applyTheme(isDark) {
  document.body.dataset.theme = isDark ? "dark" : "light";
  $("theme-chip").textContent = isDark ? "深色" : "浅色";
}

/* ------------------------------------------------------------------ */
/* 抽屉与视图切换                                                      */
/* ------------------------------------------------------------------ */
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
    void loadRagStats(true);
    void loadRagList();
  } else if (name === "settings") {
    void loadGlobal();
  }
}

function openDrawer() {
  $("drawer").classList.add("open");
  $("drawer").setAttribute("aria-hidden", "false");
  $("scrim").classList.add("show");
  $("menu-btn").setAttribute("aria-expanded", "true");
}

function closeDrawer() {
  $("drawer").classList.remove("open");
  $("drawer").setAttribute("aria-hidden", "true");
  $("scrim").classList.remove("show");
  $("menu-btn").setAttribute("aria-expanded", "false");
}

/* ------------------------------------------------------------------ */
/* 状态（概览）                                                        */
/* ------------------------------------------------------------------ */
function embeddingLabel(item) {
  const model = String(item.model || "").trim();
  const id = String(item.id || "").trim();
  if (model && id && model !== id) return `${model}（ID: ${id}）`;
  return model || id || "";
}

function ragEmbeddingHint(embedding) {
  if (!embedding || !embedding.available) return "未配置 Embedding 提供商";
  const providers = embedding.providers || [];
  if (providers.length > 1) return `Embedding 就绪（${providers.length} 个）`;
  const first = providers[0];
  return `Embedding：${first ? embeddingLabel(first) : "—"}`;
}

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

function statusCard(label, value, hint, tone = "") {
  return `
    <div class="status-card">
      <div class="label">${escapeHtml(label)}</div>
      <div class="value ${tone}">${value}</div>
      ${hint ? `<div class="hint">${escapeHtml(hint)}</div>` : ""}
    </div>`;
}

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
function groupDisplayName(item) {
  return item && item.name ? item.name : "未命名群";
}

function bindAvatar(img, groupId, name) {
  img.src = avatarUrl(groupId);
  img.onerror = () => {
    img.onerror = null;
    img.src = fallbackAvatar(name);
  };
}

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

  $("group-form").innerHTML = panels;
  bindFormEvents();
  switchTab(state.currentTab);
}

function switchTab(name) {
  state.currentTab = name;
  document.querySelectorAll("#group-tabs .tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.tab === name);
  });
  document.querySelectorAll("#group-form .tab-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.panel === name);
  });
}

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

function shakeIndependent() {
  const wrap = $("independent-wrap");
  wrap.classList.remove("shake");
  void wrap.offsetWidth;
  wrap.classList.add("shake");
  setTimeout(() => wrap.classList.remove("shake"), 800);
  toast("请先打开「独立配置」，再保存或恢复", true);
}

async function openGroup(groupId) {
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

async function saveGroup() {
  const data = state.currentGroup;
  if (!data) return;
  const independent = $("independent-toggle").checked;
  if (!independent) {
    shakeIndependent();
    return;
  }
  const button = $("save-group");
  button.disabled = true;
  try {
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
function conditionOk(meta, scope) {
  if (!meta || !meta.condition) return true;
  return Object.entries(meta.condition).every(
    ([key, expected]) => scope && scope[key] === expected
  );
}

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

function globalSections() {
  return Object.entries(state.schema || {}).filter(
    ([, meta]) => meta && meta.type === "object" && meta.items
  );
}

function switchGlobalTab(key) {
  state.globalTab = key;
  document.querySelectorAll("#global-tabs .tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.gtab === key);
  });
  document.querySelectorAll("#global-form .tab-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.gpanel === key);
  });
}

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
}

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

function renderRagRows(items) {
  const body = $("rag-body");
  if (!Array.isArray(items) || !items.length) {
    body.innerHTML =
      '<tr><td colspan="6" class="muted center pad">当前筛选条件下没有记忆</td></tr>';
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

function closeDetail() {
  $("detail").classList.remove("open");
  $("detail").setAttribute("aria-hidden", "true");
}

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
    if (tab) switchTab(tab.dataset.tab);
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
    if (tab) switchGlobalTab(tab.dataset.gtab);
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
}

/* ------------------------------------------------------------------ */
/* 启动                                                                */
/* ------------------------------------------------------------------ */
async function init() {
  bindEvents();
  applyTheme(false);
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
      applyTheme(Boolean(context.isDark));
      document.title = context.pageTitle || "主动回复 · 控制台";
    }
    if (typeof bridge.onContext === "function") {
      bridge.onContext((next) => {
        if (next) applyTheme(Boolean(next.isDark));
      });
    }
  } catch (error) {
    console.warn("bridge.ready failed", error);
  }
  switchView("overview");
}

void init();
