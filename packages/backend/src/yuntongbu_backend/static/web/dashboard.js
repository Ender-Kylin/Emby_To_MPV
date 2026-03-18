import { apiFetch, displayPlaybackState, el, formatDuration, logout, requireUser, setStatus } from "/static/web/common.js?v=20260318-zh1";

const statusNode = document.getElementById("status");
const bindingStatusNode = document.getElementById("binding-status");
const roomListNode = document.getElementById("room-list");
const bindingListNode = document.getElementById("binding-list");
const currentUserNode = document.getElementById("current-user");
const welcomeCopyNode = document.getElementById("welcome-copy");
const bindingForm = document.getElementById("emby-binding-form");
const bindingIdNode = document.getElementById("binding-id");
const bindingDisplayNameNode = document.getElementById("binding-display-name");
const bindingServerUrlNode = document.getElementById("binding-server-url");
const bindingUsernameNode = document.getElementById("binding-username");
const bindingPasswordNode = document.getElementById("binding-password");
const bindingSubmitButtonNode = document.getElementById("binding-submit-button");
const bindingCancelButtonNode = document.getElementById("binding-cancel-button");
const bindingFormHintNode = document.getElementById("binding-form-hint");
const bindingResultNode = document.getElementById("binding-result");
const bindingResultTitleNode = document.getElementById("binding-result-title");
const bindingResultMessageNode = document.getElementById("binding-result-message");
const bindingResultChipNode = document.getElementById("binding-result-chip");
const bindingResultServerNode = document.getElementById("binding-result-server");
const bindingResultAccountNode = document.getElementById("binding-result-account");
const bindingResultValidatedAtNode = document.getElementById("binding-result-validated-at");

let currentUser = null;
let bindings = [];

document.getElementById("logout-button").addEventListener("click", logout);
document.getElementById("refresh-button").addEventListener("click", async () => {
  await Promise.all([loadRooms(), loadBindings()]);
});

document.getElementById("create-room-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  setStatus(statusNode, "正在创建房间...");
  try {
    const payload = await apiFetch("/rooms", {
      method: "POST",
      body: {
        name: document.getElementById("room-name").value.trim(),
        writeback_enabled: document.getElementById("writeback-enabled").checked,
      },
    });
    window.location.href = `/app/room/${payload.id}`;
  } catch (error) {
    setStatus(statusNode, error.message, "error");
  }
});

document.getElementById("join-room-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  setStatus(statusNode, "正在加入房间...");
  try {
    const payload = await apiFetch("/rooms/join", {
      method: "POST",
      body: {
        invite_code: document.getElementById("invite-code").value.trim().toUpperCase(),
      },
    });
    window.location.href = `/app/room/${payload.id}`;
  } catch (error) {
    setStatus(statusNode, error.message, "error");
  }
});

bindingForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const editingId = bindingIdNode.value.trim();
  const isEditing = Boolean(editingId);

  const body = {
    display_name: bindingDisplayNameNode.value.trim(),
    server_url: bindingServerUrlNode.value.trim(),
    username: bindingUsernameNode.value.trim(),
  };

  if (!isEditing || bindingPasswordNode.value) {
    body.password = bindingPasswordNode.value;
  }

  setStatus(bindingStatusNode, isEditing ? "正在更新 Emby 绑定..." : "正在创建 Emby 绑定...");
  setBindingSubmitting(true, isEditing);
  showBindingAttempt({
    kind: "pending",
    title: isEditing ? "正在更新绑定..." : "正在校验绑定...",
    message: "正在校验 Emby 服务器连接和账号密码，确认无误后才会保存。",
    server: body.server_url || "-",
    account: body.username || "-",
    validatedAt: null,
  });
  try {
    let payload;
    if (isEditing) {
      payload = await apiFetch(`/emby-bindings/${editingId}`, {
        method: "PATCH",
        body,
      });
      setStatus(bindingStatusNode, "绑定已更新。", "success");
    } else {
      payload = await apiFetch("/emby-bindings", {
        method: "POST",
        body,
      });
      setStatus(bindingStatusNode, "绑定已创建。", "success");
    }
    showBindingAttempt({
      kind: "success",
      title: isEditing ? "绑定更新成功" : "绑定创建成功",
      message: `${payload.display_name}${payload.server_name ? ` 已连接到 ${payload.server_name}` : " 已可正常使用"}。`,
      server: payload.server_name ? `${payload.server_name} (${payload.server_url})` : payload.server_url,
      account: payload.username,
      validatedAt: payload.last_validated_at,
    });
    resetBindingForm();
    await loadBindings();
  } catch (error) {
    setStatus(bindingStatusNode, error.message, "error");
    showBindingAttempt({
      kind: "error",
      title: isEditing ? "绑定更新失败" : "绑定校验失败",
      message: error.message,
      server: body.server_url || "-",
      account: body.username || "-",
      validatedAt: null,
    });
  } finally {
    setBindingSubmitting(false, isEditing);
  }
});

bindingCancelButtonNode.addEventListener("click", () => {
  resetBindingForm();
  setStatus(bindingStatusNode, "已取消编辑。");
});

async function loadRooms() {
  setStatus(statusNode, "正在刷新房间列表...");
  try {
    const rooms = await apiFetch("/rooms");
    renderRooms(rooms);
    setStatus(statusNode, `已加载 ${rooms.length} 个房间。`, "success");
  } catch (error) {
    setStatus(statusNode, error.message, "error");
  }
}

function renderRooms(rooms) {
  roomListNode.replaceChildren();
  if (!rooms.length) {
    roomListNode.append(el("div", "empty", "暂时还没有房间，先创建一个，或通过邀请码加入。"));
    return;
  }

  for (const room of rooms) {
    const entry = el("article", "room-entry");
    const left = el("div");
    left.append(
      el("div", "entry-title", room.name),
      el(
        "div",
        "entry-meta",
        `${room.is_owner ? "房主" : "成员"} | 邀请码 ${room.invite_code} | ${displayPlaybackState(room.playback.playback_state)}`,
      ),
      el(
        "div",
        "entry-meta",
        room.playback.current_media
          ? `${room.playback.current_media.title || "未命名媒体"} | ${formatDuration(room.playback.position_ms)}`
          : "尚未加载媒体。",
      ),
    );

    const actions = el("div", "button-row");
    const openButton = el("button", null, "进入房间");
    openButton.type = "button";
    openButton.addEventListener("click", () => {
      window.location.href = `/app/room/${room.id}`;
    });
    actions.append(openButton);

    if (room.is_owner) {
      const deleteButton = el("button", "ghost", "删除房间");
      deleteButton.type = "button";
      deleteButton.addEventListener("click", () => deleteRoom(room));
      actions.append(deleteButton);
    }
    entry.append(left, actions);
    roomListNode.append(entry);
  }
}

async function deleteRoom(room) {
  if (!window.confirm(`确认删除房间“${room.name}”？删除后会断开该房间中的所有客户端。`)) {
    return;
  }
  setStatus(statusNode, "正在删除房间...");
  try {
    await apiFetch(`/rooms/${room.id}`, { method: "DELETE" });
    await loadRooms();
    setStatus(statusNode, "房间已删除。", "success");
  } catch (error) {
    setStatus(statusNode, error.message, "error");
  }
}

async function loadBindings() {
  setStatus(bindingStatusNode, "正在刷新 Emby 绑定...");
  try {
    bindings = await apiFetch("/emby-bindings");
    renderBindings();
    setStatus(bindingStatusNode, `已加载 ${bindings.length} 个绑定。`, "success");
  } catch (error) {
    setStatus(bindingStatusNode, error.message, "error");
  }
}

function renderBindings() {
  bindingListNode.replaceChildren();
  if (!bindings.length) {
    bindingListNode.append(el("div", "empty", "暂时还没有 Emby 绑定，请先使用上方表单添加。"));
    return;
  }

  for (const binding of bindings) {
    const entry = el("article", "room-entry");
    const left = el("div");
    left.append(
      el("div", "entry-title", binding.display_name),
      el("div", "entry-meta", `${binding.username} @ ${binding.server_name || "未知服务器"}`),
    );

    const serverMeta = el("div", "entry-meta");
    const serverCode = document.createElement("code");
    serverCode.textContent = binding.server_url;
    serverMeta.append("服务器：", serverCode);
    left.append(serverMeta);

    left.append(
      el(
        "div",
        "entry-meta",
        binding.last_validated_at
          ? `已校验：${new Date(binding.last_validated_at).toLocaleString()}`
          : "尚未校验",
      ),
    );

    const stateChip = el(
      "span",
      `chip ${binding.last_validated_at ? "ok" : "warn"}`.trim(),
      binding.last_validated_at ? "已验证" : "未验证",
    );

    const actions = el("div", "button-row");
    const editButton = el("button", "subtle", "编辑");
    editButton.type = "button";
    editButton.addEventListener("click", () => beginEdit(binding));

    const deleteButton = el("button", "ghost", "删除");
    deleteButton.type = "button";
    deleteButton.addEventListener("click", () => deleteBinding(binding));

    actions.append(stateChip, editButton, deleteButton);
    entry.append(left, actions);
    bindingListNode.append(entry);
  }
}

function beginEdit(binding) {
  bindingIdNode.value = binding.id;
  bindingDisplayNameNode.value = binding.display_name;
  bindingServerUrlNode.value = binding.server_url;
  bindingUsernameNode.value = binding.username;
  bindingPasswordNode.value = "";
  bindingPasswordNode.required = false;
  bindingSubmitButtonNode.textContent = "保存修改";
  bindingCancelButtonNode.hidden = false;
  bindingFormHintNode.textContent = "如果不想修改密码，可以留空，系统会继续使用原密码。";
  setStatus(bindingStatusNode, `正在编辑绑定“${binding.display_name}”。`);
}

function resetBindingForm() {
  bindingForm.reset();
  bindingIdNode.value = "";
  bindingPasswordNode.required = true;
  bindingSubmitButtonNode.textContent = "添加绑定";
  bindingCancelButtonNode.hidden = true;
  bindingFormHintNode.textContent = "新建绑定会在保存前立即校验连接和账号密码。";
}

function setBindingSubmitting(isSubmitting, isEditing) {
  bindingSubmitButtonNode.disabled = isSubmitting;
  bindingCancelButtonNode.disabled = isSubmitting;
  bindingSubmitButtonNode.textContent = isSubmitting
    ? (isEditing ? "保存中..." : "校验中...")
    : (isEditing ? "保存修改" : "添加绑定");
}

function showBindingAttempt({ kind, title, message, server, account, validatedAt }) {
  bindingResultNode.hidden = false;
  bindingResultTitleNode.textContent = title;
  bindingResultMessageNode.textContent = message;
  bindingResultServerNode.textContent = server || "-";
  bindingResultAccountNode.textContent = account || "-";
  bindingResultValidatedAtNode.textContent = validatedAt
    ? new Date(validatedAt).toLocaleString()
    : (kind === "success" ? "刚刚" : "未校验");
  bindingResultChipNode.textContent =
    kind === "success" ? "已连接" : kind === "error" ? "失败" : "校验中";
  bindingResultChipNode.className = `chip ${
    kind === "success" ? "ok" : kind === "error" ? "danger" : "warn"
  }`.trim();
}

async function deleteBinding(binding) {
  if (!window.confirm(`确认删除 Emby 绑定“${binding.display_name}”？`)) {
    return;
  }
  setStatus(bindingStatusNode, "正在删除 Emby 绑定...");
  try {
    await apiFetch(`/emby-bindings/${binding.id}`, { method: "DELETE" });
    if (bindingIdNode.value === binding.id) {
      resetBindingForm();
    }
    await loadBindings();
    setStatus(bindingStatusNode, "绑定已删除。", "success");
  } catch (error) {
    setStatus(bindingStatusNode, error.message, "error");
  }
}

async function bootstrap() {
  currentUser = await requireUser();
  currentUserNode.textContent = currentUser.username;
  welcomeCopyNode.textContent = `${currentUser.username}，你可以在这里统一管理房间和 Emby 服务器。`;
  await Promise.all([loadRooms(), loadBindings()]);
}

bootstrap().catch((error) => {
  setStatus(statusNode, error.message, "error");
  setStatus(bindingStatusNode, error.message, "error");
});
