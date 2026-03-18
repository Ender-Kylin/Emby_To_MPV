import { apiFetch, el, formatDuration, logout, requireUser, setStatus } from "/static/web/common.js";

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
  setStatus(statusNode, "Creating room...");
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
  setStatus(statusNode, "Joining room...");
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

  setStatus(bindingStatusNode, isEditing ? "Updating Emby binding..." : "Creating Emby binding...");
  setBindingSubmitting(true, isEditing);
  showBindingAttempt({
    kind: "pending",
    title: isEditing ? "Updating binding..." : "Validating binding...",
    message: "Testing the Emby server connection and credentials before saving.",
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
      setStatus(bindingStatusNode, "Binding updated.", "success");
    } else {
      payload = await apiFetch("/emby-bindings", {
        method: "POST",
        body,
      });
      setStatus(bindingStatusNode, "Binding created.", "success");
    }
    showBindingAttempt({
      kind: "success",
      title: isEditing ? "Binding updated successfully" : "Binding created successfully",
      message: `${payload.display_name} is ready to use${payload.server_name ? ` on ${payload.server_name}` : ""}.`,
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
      title: isEditing ? "Binding update failed" : "Binding validation failed",
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
  setStatus(bindingStatusNode, "Edit cancelled.");
});

async function loadRooms() {
  setStatus(statusNode, "Refreshing room list...");
  try {
    const rooms = await apiFetch("/rooms");
    renderRooms(rooms);
    setStatus(statusNode, `Loaded ${rooms.length} room(s).`, "success");
  } catch (error) {
    setStatus(statusNode, error.message, "error");
  }
}

function renderRooms(rooms) {
  roomListNode.replaceChildren();
  if (!rooms.length) {
    roomListNode.append(el("div", "empty", "No rooms yet. Create one or join with an invite code."));
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
        `${room.is_owner ? "Owner" : "Member"} | Invite ${room.invite_code} | ${room.playback.playback_state}`,
      ),
      el(
        "div",
        "entry-meta",
        room.playback.current_media
          ? `${room.playback.current_media.title || "Unnamed media"} | ${formatDuration(room.playback.position_ms)}`
          : "No media loaded yet.",
      ),
    );

    const actions = el("div", "button-row");
    const openButton = el("button", null, "Open Room");
    openButton.type = "button";
    openButton.addEventListener("click", () => {
      window.location.href = `/app/room/${room.id}`;
    });
    actions.append(openButton);

    if (room.is_owner) {
      const deleteButton = el("button", "ghost", "Delete Room");
      deleteButton.type = "button";
      deleteButton.addEventListener("click", () => deleteRoom(room));
      actions.append(deleteButton);
    }
    entry.append(left, actions);
    roomListNode.append(entry);
  }
}

async function deleteRoom(room) {
  if (!window.confirm(`Delete room "${room.name}"? This will disconnect all clients in the room.`)) {
    return;
  }
  setStatus(statusNode, "Deleting room...");
  try {
    await apiFetch(`/rooms/${room.id}`, { method: "DELETE" });
    await loadRooms();
    setStatus(statusNode, "Room deleted.", "success");
  } catch (error) {
    setStatus(statusNode, error.message, "error");
  }
}

async function loadBindings() {
  setStatus(bindingStatusNode, "Refreshing Emby bindings...");
  try {
    bindings = await apiFetch("/emby-bindings");
    renderBindings();
    setStatus(bindingStatusNode, `Loaded ${bindings.length} binding(s).`, "success");
  } catch (error) {
    setStatus(bindingStatusNode, error.message, "error");
  }
}

function renderBindings() {
  bindingListNode.replaceChildren();
  if (!bindings.length) {
    bindingListNode.append(el("div", "empty", "No Emby bindings yet. Add one using the form above."));
    return;
  }

  for (const binding of bindings) {
    const entry = el("article", "room-entry");
    const left = el("div");
    left.append(
      el("div", "entry-title", binding.display_name),
      el("div", "entry-meta", `${binding.username} @ ${binding.server_name || "Unknown Server"}`),
    );

    const serverMeta = el("div", "entry-meta");
    const serverCode = document.createElement("code");
    serverCode.textContent = binding.server_url;
    serverMeta.append("Server: ", serverCode);
    left.append(serverMeta);

    left.append(
      el(
        "div",
        "entry-meta",
        binding.last_validated_at
          ? `Validated: ${new Date(binding.last_validated_at).toLocaleString()}`
          : "Validated: never",
      ),
    );

    const stateChip = el(
      "span",
      `chip ${binding.last_validated_at ? "ok" : "warn"}`.trim(),
      binding.last_validated_at ? "Validated" : "Unverified",
    );

    const actions = el("div", "button-row");
    const editButton = el("button", "subtle", "Edit");
    editButton.type = "button";
    editButton.addEventListener("click", () => beginEdit(binding));

    const deleteButton = el("button", "ghost", "Delete");
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
  bindingSubmitButtonNode.textContent = "Save Changes";
  bindingCancelButtonNode.hidden = false;
  bindingFormHintNode.textContent = "Leave password blank to keep the existing Emby password.";
  setStatus(bindingStatusNode, `Editing binding "${binding.display_name}".`);
}

function resetBindingForm() {
  bindingForm.reset();
  bindingIdNode.value = "";
  bindingPasswordNode.required = true;
  bindingSubmitButtonNode.textContent = "Add Binding";
  bindingCancelButtonNode.hidden = true;
  bindingFormHintNode.textContent = "A new binding validates credentials immediately before being saved.";
}

function setBindingSubmitting(isSubmitting, isEditing) {
  bindingSubmitButtonNode.disabled = isSubmitting;
  bindingCancelButtonNode.disabled = isSubmitting;
  bindingSubmitButtonNode.textContent = isSubmitting
    ? (isEditing ? "Saving..." : "Validating...")
    : (isEditing ? "Save Changes" : "Add Binding");
}

function showBindingAttempt({ kind, title, message, server, account, validatedAt }) {
  bindingResultNode.hidden = false;
  bindingResultTitleNode.textContent = title;
  bindingResultMessageNode.textContent = message;
  bindingResultServerNode.textContent = server || "-";
  bindingResultAccountNode.textContent = account || "-";
  bindingResultValidatedAtNode.textContent = validatedAt
    ? new Date(validatedAt).toLocaleString()
    : (kind === "success" ? "Just now" : "Not validated");
  bindingResultChipNode.textContent =
    kind === "success" ? "Connected" : kind === "error" ? "Failed" : "Checking";
  bindingResultChipNode.className = `chip ${
    kind === "success" ? "ok" : kind === "error" ? "danger" : "warn"
  }`.trim();
}

async function deleteBinding(binding) {
  if (!window.confirm(`Delete Emby binding "${binding.display_name}"?`)) {
    return;
  }
  setStatus(bindingStatusNode, "Deleting Emby binding...");
  try {
    await apiFetch(`/emby-bindings/${binding.id}`, { method: "DELETE" });
    if (bindingIdNode.value === binding.id) {
      resetBindingForm();
    }
    await loadBindings();
    setStatus(bindingStatusNode, "Binding deleted.", "success");
  } catch (error) {
    setStatus(bindingStatusNode, error.message, "error");
  }
}

async function bootstrap() {
  currentUser = await requireUser();
  currentUserNode.textContent = currentUser.username;
  welcomeCopyNode.textContent = `${currentUser.username}, manage your rooms and Emby servers from this dashboard.`;
  await Promise.all([loadRooms(), loadBindings()]);
}

bootstrap().catch((error) => {
  setStatus(statusNode, error.message, "error");
  setStatus(bindingStatusNode, error.message, "error");
});
