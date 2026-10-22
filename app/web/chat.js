import {readSSE} from "/static/sse.js";

const $ = id => document.getElementById(id);
let token = sessionStorage.getItem("campus_token") || "";
let selected = sessionStorage.getItem("campus_conversation") || "";
let controller = null, ready = false, serverBusy = false, authMode = "login", authBusy = false;
let runtime = {mode: "mock", model: "mock-campus-v1", simulated: true};
let conversationPage = 1, historyPage = 1, pollTimer = null, historyVersion = 0;
const mobile = matchMedia("(max-width: 800px)");
const names = {completed: "已完成", generating: "生成中", failed: "失败 · 部分文本仅供查看", cancelled: "已取消 · 部分文本仅供查看"};
const modelLabel = (provider, model) => provider === "local" ? "资料边界提示（未调用模型）" : `${provider === "mock" ? "模拟模型" : provider === "deepseek" ? "DeepSeek" : "模型未记录"}${model ? " · " + model : ""}`;
function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}
function status(text, error = false) {
  for (const id of ["status", ...(!$("auth-view").hidden ? ["auth-status"] : [])]) {
    $(id).textContent = text; $(id).classList.toggle("error", error);
  }
}
function applyRuntime(config) {
  runtime = config; ready = config.ready;
  $("mode").textContent = modelLabel(config.mode, config.model);
  $("scenario-label").hidden = !config.simulated;
  $("mode-description").textContent = config.configuration_error || (config.simulated
    ? "当前为固定模板模拟回复，未调用真实模型。示例指南为虚构演示；用户提供文档另作标记，当前适用性未核验。"
    : "DeepSeek 模式使用真实 API，可进行一般分析；具体校内规定须有依据。示例指南为虚构演示，用户提供文档的当前适用性未核验。");
}
function controls() {
  $("send").disabled = !token || !ready || !!controller || serverBusy;
  $("stop").disabled = !controller;
  $("send-form").setAttribute("aria-busy", String(!!controller));
  for (const id of ["new-conversation", "refresh", "delete-conversation"]) {
    $(id).disabled = !token || !!controller || (id !== "new-conversation" && !selected);
  }
  for (const button of document.querySelectorAll(".conversation, [data-question]")) button.disabled = !!controller;
}
function drawer(open, restoreFocus = true) {
  open = mobile.matches && open;
  $("sidebar").classList.toggle("open", open);
  $("drawer-backdrop").hidden = !open;
  $("sidebar-open").setAttribute("aria-expanded", String(open));
  $("workspace").inert = open;
  if (open) {
    $("sidebar").inert = false; $("sidebar").setAttribute("role", "dialog"); $("sidebar").setAttribute("aria-modal", "true");
    requestAnimationFrame(() => {
      if ($("sidebar").classList.contains("open")) $("sidebar-close").focus();
    });
  } else {
    $("sidebar").removeAttribute("role"); $("sidebar").removeAttribute("aria-modal");
    if (restoreFocus && mobile.matches && !$("app-shell").hidden) $("sidebar-open").focus();
    $("sidebar").inert = mobile.matches;
  }
}
$("sidebar-open").addEventListener("click", () => drawer(true));
for (const id of ["sidebar-close", "drawer-backdrop"]) $(id).addEventListener("click", () => drawer(false));
mobile.addEventListener("change", () => drawer(false, false));
document.addEventListener("keydown", event => {
  if (!mobile.matches || !$("sidebar").classList.contains("open")) return;
  if (event.key === "Escape") { event.preventDefault(); drawer(false); }
  if (event.key === "Tab") {
    const items = [...$("sidebar").querySelectorAll("button:not(:disabled), a[href]")].filter(item => item.getClientRects().length);
    const first = items[0], last = items.at(-1);
    if (!$("sidebar").contains(document.activeElement)) { event.preventDefault(); first.focus(); return; }
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
});
function nearBottom() {
  const box = $("messages"); return box.scrollHeight - box.scrollTop - box.clientHeight < 96;
}
function updateScroll() { $("jump-bottom").hidden = nearBottom() || !$("welcome").hidden; }
function mutateMessages(fn) {
  const follow = !$("welcome").hidden || nearBottom();
  fn(); if (follow) $("messages").scrollTop = $("messages").scrollHeight;
  updateScroll();
}
$("messages").addEventListener("scroll", updateScroll, {passive: true});
$("jump-bottom").addEventListener("click", () => { $("messages").scrollTop = $("messages").scrollHeight; updateScroll(); });
function logout() {
  controller?.abort(); clearTimeout(pollTimer); historyVersion++;
  token = ""; selected = ""; serverBusy = false;
  sessionStorage.removeItem("campus_token"); sessionStorage.removeItem("campus_conversation");
  drawer(false, false); $("guide-dialog").close();
  $("message-list").replaceChildren(); $("conversations").replaceChildren();
  $("account").hidden = true; $("app-shell").hidden = true; $("auth-view").hidden = false;
  $("more-conversations").hidden = true; $("older").hidden = true; $("welcome").hidden = false;
  $("title").textContent = "开始新的咨询"; $("question").value = ""; updateInput(); controls();
}
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers: {
    ...(token ? {Authorization: `Bearer ${token}`} : {}), ...options.headers,
  }});
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    if (response.status === 401 && token) logout();
    const detail = Array.isArray(data.detail) ? data.detail.map(item => item.msg).join("；") : data.detail;
    throw new Error(detail || `请求失败（${response.status}），请稍后重试`);
  }
  return response;
}
async function guideDetails(id) {
  const dialog = $("guide-dialog");
  $("guide-dialog-title").textContent = "指南详情"; $("guide-content").replaceChildren(node("p", "guide-meta", "正在加载指南…"));
  dialog.showModal();
  try {
    const guide = await (await api(`/guides/${id}`)).json();
    if (!dialog.open) return;
    $("guide-dialog-title").textContent = guide.title;
    const body = $("guide-content"); body.replaceChildren();
    body.append(node("p", "guide-meta", `${guide.category} · ${guide.department}`));
    if (guide.is_demo) body.append(node("p", "guide-warning", "演示数据（虚构），仅供功能展示，不代表学校实际办事要求。"));
    for (const [heading, tag, items] of [["所需材料", "ul", guide.materials], ["办理流程", "ol", guide.steps]]) {
      body.append(node("h3", "", heading)); const list = node(tag);
      for (const item of items) list.append(node("li", "", item)); body.append(list);
    }
    body.append(node("h3", "", "来源说明"), node("p", "guide-source", guide.source));
    body.append(node("p", "guide-meta", `更新于 ${new Date(guide.updated_at).toLocaleString("zh-CN")}`));
  } catch (error) {
    if (dialog.open) {
      const retry = node("button", "guide-button", "重新加载指南"); retry.type = "button";
      retry.addEventListener("click", () => { dialog.close(); guideDetails(id); });
      $("guide-content").replaceChildren(node("p", "guide-warning", `指南加载失败：${error.message}`), retry);
    }
  }
}
$("guide-close").addEventListener("click", () => $("guide-dialog").close());
async function openOriginal(reference) {
  const pdf = reference.original_filename.toLowerCase().endsWith(".pdf");
  const preview = pdf ? window.open("about:blank", "_blank") : null;
  if (preview) preview.opener = null;
  try {
    if (!Number.isSafeInteger(reference.document_id) || reference.document_id < 1) throw new Error("原件标识无效");
    // 认证仍走 Authorization 请求头，令牌不放入 URL，不允许用户指定服务器文件路径。
    const response = await api(`/references/documents/${reference.document_id}/original`);
    const url = URL.createObjectURL(await response.blob());
    if (preview) {
      // 无头浏览器/部分手机不支持内置 PDF 预览；先放置安全的下载后备页，避免留白。
      preview.document.title = reference.title;
      const style = node("link"); style.rel = "stylesheet"; style.href = location.origin + "/static/chat.css";
      preview.document.head.append(style);
      const fallback = node("main", "original-preview");
      const link = node("a", "original-fallback-link", "下载原始 PDF，在阅读器中打开");
      link.href = url; link.download = reference.original_filename;
      fallback.append(node("h1", "", reference.title), node("p", "", "正在尝试打开原始 PDF。若浏览器不支持预览，可使用下方原文件链接。"), link,
        node("p", "", "用户提供资料；当前适用性与官方来源尚未核验。"));
      preview.document.body.replaceChildren(fallback);
      if (navigator.pdfViewerEnabled) preview.location.href = url;
    }
    else {
      const download = node("a"); download.href = url; download.download = reference.original_filename;
      document.body.append(download); download.click(); download.remove();
    }
    setTimeout(() => { if (!preview || preview.closed) URL.revokeObjectURL(url); }, 60000);
    status(pdf && preview ? "已在新窗口打开原件入口；不支持 PDF 预览时可下载。" : "原件已下载，可使用本机 Word 或 PDF 阅读器打开。");
  } catch (error) { preview?.close(); status(`原件打开失败：${error.message}`, true); }
}
function documentExcerpt(parent, reference) {
  parent.append(node("blockquote", "reference-excerpt", reference.excerpt));
  if (reference.table_rows?.length) {
    const table = node("table", "reference-table"), head = node("tr");
    for (const title of ["阶段", "等级", "资助比例", "元/年/生"]) head.append(node("th", "", title));
    const thead = node("thead"); thead.append(head); table.append(thead);
    const tbody = node("tbody");
    for (const row of reference.table_rows) {
      const tr = node("tr");
      for (const value of [row.stage, row.level, row.ratio, row.amount_yuan_per_year_per_student]) tr.append(node("td", "", value));
      tbody.append(tr);
    }
    table.append(tbody); parent.append(table);
  }
}
async function documentDetails(reference) {
  const dialog = $("guide-dialog");
  $("guide-dialog-title").textContent = reference.title;
  $("guide-content").replaceChildren(node("p", "guide-meta", "正在加载原文片段…")); dialog.showModal();
  try {
    const detail = await (await api(`/references/chunks/${reference.id}`)).json();
    if (!dialog.open) return;
    const body = $("guide-content"); body.replaceChildren();
    body.append(node("p", "guide-meta", `${reference.citation_id} · ${detail.locator} · ${detail.version}`));
    for (const warning of detail.warnings) body.append(node("p", "guide-warning", warning));
    body.append(node("h3", "", "原文摘录")); documentExcerpt(body, detail);
    body.append(node("p", "guide-meta", `原文件：${detail.original_filename}`), node("p", "guide-meta", `SHA-256：${detail.file_sha256}`));
    const original = node("button", "guide-button", "打开或下载原件"); original.type = "button";
    original.addEventListener("click", () => openOriginal(detail)); body.append(original);
  } catch (error) { if (dialog.open) $("guide-content").replaceChildren(node("p", "guide-warning", `片段加载失败：${error.message}`)); }
}
function references(parent, guides) {
  if (!guides?.length) return;
  const container = node("details", "references");
  const heading = node("summary", "", guides.some(g => g.kind === "document") ? "参考文档" : "参考指南");
  heading.append(node("span", "source-count", `${guides.length} 个片段${guides.some(g => g.is_demo) ? " · 演示数据" : " · 用户提供"}`));
  container.append(heading);
  const items = node("div", "source-items");
  for (const guide of guides) {
    if (guide.kind === "document") {
      const item = node("div", "reference document-reference");
      item.dataset.citationId = guide.citation_id;
      item.append(node("p", "reference-title", `[${guide.citation_id}] ${guide.title}`), node("p", "reference-source", `${guide.locator} · ${guide.version} · 用户提供资料`));
      for (const warning of guide.warnings) item.append(node("p", "reference-warning", warning));
      documentExcerpt(item, guide);
      const detail = node("button", "guide-button", "查看片段定位"); detail.type = "button";
      detail.addEventListener("click", () => documentDetails(guide));
      const original = node("button", "guide-button original-button", guide.original_filename.endsWith(".pdf") ? "打开原件（PDF）" : "下载原件（Word）"); original.type = "button";
      original.addEventListener("click", () => openOriginal(guide));
      item.append(detail, original); items.append(item); continue;
    }
    const item = node("div", "reference"), title = node("p", "reference-title", `[${guide.id}] ${guide.title}`);
    if (guide.is_demo) title.append(node("span", "demo-tag", "演示数据"));
    const detail = node("button", "guide-button", "查看指南详情 →"); detail.type = "button";
    detail.addEventListener("click", () => guideDetails(guide.id));
    item.append(title, node("p", "reference-source", guide.source), detail); items.append(item);
  }
  container.append(items); parent.append(container);
}
function message(data) {
  const element = node("article", `message ${data.role}`); element.dataset.messageId = data.id || ""; element.dataset.status = data.status;
  const label = node("div", "message-label");
  if (data.role === "assistant") {
    const avatar = node("img", "message-avatar"); avatar.src = "/static/assets/assistant.svg"; avatar.alt = "办事助手图标"; avatar.width = 33; avatar.height = 33; element.append(avatar);
    label.append(node("span", "", `上理办事助手 · ${modelLabel(data.model_provider, data.model_name)}`), node("span", "generation-state", names[data.status] || data.status));
  } else label.textContent = "你";
  const body = node("div", "message-body", data.content || (data.status === "generating" ? "正在生成回复…" : "（没有生成文本）"));
  element.append(label, body); references(element, data.references);
  if (data.role === "assistant") {
    const tools = node("div", "message-tools"), copy = node("button", "copy-button", "复制回复"); copy.type = "button";
    copy.disabled = !data.content || data.status === "generating";
    copy.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(data.content); copy.textContent = "已复制 ✓"; status("复制成功，回复已放入剪贴板。"); }
      catch { status("复制失败，请手动选择回复文本后复制。", true); }
    }); tools.append(copy); element.append(tools);
  }
  return {element, body, label};
}
async function loadHistory(older = false, forceBottom = false) {
  if (!token || !selected || controller) return;
  clearTimeout(pollTimer);
  const id = selected, version = ++historyVersion;
  let page;
  if (older) page = historyPage - 1;
  else {
    const first = await (await api(`/conversations/${id}/messages?page_size=100`)).json();
    page = Math.max(1, Math.ceil(first.total / 100));
  }
  const data = await (await api(`/conversations/${id}/messages?page_size=100&page=${page}`)).json();
  if (selected !== id || !token || controller || version !== historyVersion) return;
  // 请求等待期间用户也可能向上滚动；以更新 DOM 的这一刻为准。
  const box = $("messages"), follow = forceBottom || nearBottom(), top = box.scrollTop, height = box.scrollHeight;
  // 保留已经展开的来源卡片，轮询或完成回读不会把它们折叠。
  const expanded = new Set([...$("message-list").querySelectorAll("article:has(details[open])")].map(item => item.dataset.messageId));
  if (!older) $("message-list").replaceChildren();
  const fragment = document.createDocumentFragment();
  for (const row of data.items) {
    const item = message(row).element;
    if (expanded.has(String(row.id))) item.querySelector("details")?.setAttribute("open", "");
    fragment.append(item);
  }
  if (older) $("message-list").prepend(fragment); else $("message-list").append(fragment);
  $("welcome").hidden = !!$("message-list").children.length;
  historyPage = page; $("older").hidden = page <= 1;
  if (!$("welcome").hidden) box.scrollTop = 0;
  else if (older) box.scrollTop = top + box.scrollHeight - height;
  else box.scrollTop = follow ? box.scrollHeight : top;
  updateScroll(); serverBusy = data.items.some(row => row.status === "generating"); controls();
  if (serverBusy) pollTimer = setTimeout(() => loadHistory().catch(e => status(e.message, true)), 1000);
}
async function selectConversation(id, title) {
  if (controller) return;
  selected = id; sessionStorage.setItem("campus_conversation", id);
  $("title").textContent = title; serverBusy = false; controls(); drawer(false);
  for (const button of document.querySelectorAll(".conversation")) {
    const active = button.dataset.id === id; button.classList.toggle("selected", active); button.setAttribute("aria-current", String(active));
  }
  status("正在加载历史会话…");
  await loadHistory(false, true);
  status(serverBusy ? "这段会话仍在生成回复，请稍候。" : ready ? "历史已恢复，可以继续提问。" : runtime.configuration_error || "模型配置暂不可用", !ready);
}
async function loadConversations(append = false) {
  const data = await (await api(`/conversations?page_size=100&page=${append ? conversationPage + 1 : 1}`)).json();
  conversationPage = append ? conversationPage + 1 : 1;
  if (!append) $("conversations").replaceChildren();
  for (const item of data.items) {
    const button = node("button", "conversation"); button.type = "button"; button.dataset.id = item.id; button.title = item.title;
    const icon = node("span", "conversation-icon", "◇"); icon.setAttribute("aria-hidden", "true");
    button.append(icon, node("span", "", item.title));
    button.classList.toggle("selected", item.id === selected); button.setAttribute("aria-current", String(item.id === selected));
    button.addEventListener("click", () => selectConversation(item.id, item.title).catch(e => status(e.message, true)));
    $("conversations").append(button);
  }
  $("conversations-empty").hidden = data.total > 0;
  $("more-conversations").hidden = conversationPage * 100 >= data.total; controls(); return data.items;
}
async function signedIn() {
  const user = await (await api("/users/me")).json();
  $("identity").textContent = user.username; $("account").hidden = false; $("auth-view").hidden = true; $("app-shell").hidden = false; drawer(false, false);
  const items = await loadConversations();
  if (selected) {
    try { const item = await (await api(`/conversations/${selected}`)).json(); await selectConversation(item.id, item.title); return; }
    catch (error) { if (!token) throw error; selected = ""; sessionStorage.removeItem("campus_conversation"); }
  }
  if (items.length) await selectConversation(items[0].id, items[0].title);
  else { $("title").textContent = "开始新的咨询"; $("welcome").hidden = false; controls(); }
}
function setAuth(mode) {
  authMode = mode; const register = mode === "register";
  $("show-login").classList.toggle("active", !register); $("show-register").classList.toggle("active", register);
  $("show-login").setAttribute("aria-pressed", String(!register)); $("show-register").setAttribute("aria-pressed", String(register));
  $("auth-heading").textContent = register ? "从一个问题开始" : "欢迎回来";
  $("auth-description").textContent = register ? "创建本项目账号，保存你的会话与咨询记录。" : "登录本项目账号，继续你的校园事务咨询。";
  $("login-submit").hidden = register; $("register").hidden = !register;
  $("password").autocomplete = register ? "new-password" : "current-password";
  $("password").minLength = register ? 8 : 1;
  $("auth-status").textContent = "";
}
$("show-login").addEventListener("click", () => setAuth("login"));
$("show-register").addEventListener("click", () => setAuth("register"));
$("login-form").addEventListener("submit", async event => {
  event.preventDefault(); if (authBusy) return; authBusy = true;
  const ids = ["login-submit", "register", "show-login", "show-register"];
  for (const id of ids) $(id).disabled = true;
  $("login-form").setAttribute("aria-busy", "true"); status(authMode === "register" ? "正在创建项目账号…" : "正在登录…");
  try {
    const credentials = {username: $("username").value, password: $("password").value};
    if (authMode === "register") {
      await api("/auth/register", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(credentials)});
      setAuth("login"); $("password").value = ""; status("注册成功，请输入密码并登录。"); $("password").focus();
    } else {
      const response = await api("/auth/token", {method: "POST", body: new URLSearchParams(credentials)});
      token = (await response.json()).access_token; sessionStorage.setItem("campus_token", token);
      $("password").value = ""; await signedIn();
      status(ready ? `登录成功。当前使用 ${modelLabel(runtime.mode, runtime.model)}。` : runtime.configuration_error || "模型配置暂不可用", !ready);
    }
  } catch (error) { status(error.message, true); }
  finally { authBusy = false; for (const id of ids) $(id).disabled = false; $("login-form").setAttribute("aria-busy", "false"); }
});
$("logout").addEventListener("click", () => { logout(); status("已退出当前项目账号。"); $("username").focus(); });
async function createConversation(title) {
  const item = await (await api("/conversations", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(title ? {title} : {})})).json();
  selected = item.id; sessionStorage.setItem("campus_conversation", item.id); $("title").textContent = item.title;
  $("message-list").replaceChildren(); $("welcome").hidden = false; $("older").hidden = true; serverBusy = false;
  $("messages").scrollTop = 0; drawer(false); await loadConversations(); controls(); return item;
}
$("new-conversation").addEventListener("click", async () => {
  $("new-conversation").disabled = true;
  try { await createConversation(); status(ready ? "会话已创建，可以发送问题。" : `会话已创建。${runtime.configuration_error || "模型配置暂不可用"}`, !ready); $("question").focus(); }
  catch (error) { status(error.message, true); } finally { controls(); }
});
$("delete-conversation").addEventListener("click", async () => {
  if (!selected || !confirm("删除当前会话及全部消息？")) return;
  try {
    await api(`/conversations/${selected}`, {method: "DELETE"}); selected = ""; clearTimeout(pollTimer);
    sessionStorage.removeItem("campus_conversation"); $("message-list").replaceChildren(); $("welcome").hidden = false;
    await signedIn(); status("会话及关联消息已删除。");
  } catch (error) { status(error.message, true); }
});
$("refresh").addEventListener("click", async () => {
  status("正在加载历史会话…");
  try { await loadHistory(); status("历史已刷新。"); } catch (error) { status(error.message, true); }
});
$("older").addEventListener("click", () => loadHistory(true).catch(e => status(e.message, true)));
$("more-conversations").addEventListener("click", () => loadConversations(true).catch(e => status(e.message, true)));
for (const button of document.querySelectorAll("[data-question]")) button.addEventListener("click", () => {
  $("question").value = button.dataset.question; updateInput(); $("question").focus(); status("问题已填入，点击发送开始咨询。");
});
function updateInput() { $("character-count").textContent = `${$("question").value.length} / 4000`; }
$("question").addEventListener("input", updateInput);
$("question").addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
    event.preventDefault(); if (!$("send").disabled) $("send-form").requestSubmit();
  }
});
$("stop").addEventListener("click", () => { status("正在停止生成…"); controller?.abort(); });
$("send-form").addEventListener("submit", async event => {
  event.preventDefault(); if (controller || !token || !ready || serverBusy) return;
  const content = $("question").value;
  if (!content.trim()) { status("请输入非空问题。", true); return; }
  controller = new AbortController(); controls();
  let current = selected, assistant = null, text = "", terminal = false;
  try {
    applyRuntime(await (await api("/chat/config", {signal: controller.signal})).json());
    if (!ready) throw new Error(runtime.configuration_error || "模型配置暂不可用");
    if (!selected) { await createConversation(content.slice(0, 32)); current = selected; }
    status(`正在生成回复 · ${modelLabel(runtime.mode, runtime.model)}…`);
    const response = await api(`/conversations/${current}/chat`, {method: "POST", signal: controller.signal,
      headers: {"Content-Type": "application/json"}, body: JSON.stringify({content, ...(runtime.simulated ? {mock_scenario: $("scenario").value} : {})})});
    if (!response.headers.get("content-type")?.includes("text/event-stream")) throw new Error("服务器未返回 SSE 流");
    await readSSE(response.body, (name, data) => {
      if (!token || selected !== current) return;
      mutateMessages(() => {
        if (name === "meta") {
          if (data.generation_method !== "evidence_notice") applyRuntime({...runtime, mode: data.mode, model: data.model, simulated: data.simulated});
          $("question").value = ""; updateInput(); $("welcome").hidden = true;
          $("message-list").append(message({id: data.user_message_id, role: "user", content, status: "completed"}).element);
          // meta 中是检索候选；完成后只展示服务端确认已实际引用的来源。
          assistant = message({id: data.assistant_message_id, role: "assistant", content: "", status: "generating", references: [], model_provider: data.mode, model_name: data.model});
          $("message-list").append(assistant.element);
        } else if (name === "delta" && assistant) { text += data.text; assistant.body.textContent = text; }
        else if (name === "done") {
          terminal = true;
          if (assistant) references(assistant.element, data.references || []);
          status("回复已完成并保存。");
        }
        else if (name === "error") { terminal = true; status(data.message || "生成失败，请重试。", true); }
      });
    });
    if (!terminal) throw new Error("流连接提前结束，请刷新历史确认状态。");
  } catch (error) { status(error.name === "AbortError" ? "已停止生成，正在确认保存状态。" : error.message, error.name !== "AbortError"); }
  finally {
    controller = null; controls();
    if (token && selected === current) {
      await loadHistory().catch(e => status(e.message, true)); await loadConversations().catch(e => status(e.message, true));
    }
  }
});

try {
  drawer(false, false);
  const config = await (await api("/chat/config")).json(); applyRuntime(config);
  // 登录前只在表单显示账户操作状态；模式说明保留在工作区。
  $("status").textContent = config.configuration_error || (config.simulated ? "当前为模拟模式，不需要模型 API Key。" : "DeepSeek 模式已配置；连接状态需通过实际请求确认。");
  if (token) await signedIn(); controls();
} catch (error) { status(error.message, true); }
