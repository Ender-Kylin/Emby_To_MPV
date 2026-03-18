import {
  apiFetch,
  buildWebSocketUrl,
  displayPlaybackState,
  el,
  formatDuration,
  loadSession,
  logout,
  requireUser,
  setStatus,
} from "/static/web/common.js?v=20260318-zh1";

const roomId = window.location.pathname.split("/").pop();
const statusNode = document.getElementById("status");
const roomTitleNode = document.getElementById("room-title");
const roomSubtitleNode = document.getElementById("room-subtitle");
const inviteChipNode = document.getElementById("invite-chip");
const ownerChipNode = document.getElementById("owner-chip");
const currentMediaTitleNode = document.getElementById("current-media-title");
const currentMediaMetaNode = document.getElementById("current-media-meta");
const currentProgressTextNode = document.getElementById("current-progress-text");
const deviceCountNode = document.getElementById("device-count");
const playbackStateChipNode = document.getElementById("playback-state-chip");
const writebackChipNode = document.getElementById("writeback-chip");
const progressSliderNode = document.getElementById("progress-slider");
const seekSecondsNode = document.getElementById("seek-seconds");
const seekButtonNode = document.getElementById("seek-button");
const memberListNode = document.getElementById("member-list");
const mediaListNode = document.getElementById("media-list");
const queueListNode = document.getElementById("queue-list");
const queueMetaNode = document.getElementById("queue-meta");
const bindingSelectNode = document.getElementById("binding-select");
const librarySelectNode = document.getElementById("library-select");
const itemSearchNode = document.getElementById("item-search");
const writebackToggleNode = document.getElementById("writeback-toggle");
const browserPlayerNode = document.getElementById("browser-player");
const browserPlayerChipNode = document.getElementById("browser-player-chip");
const startBrowserPlayerButtonNode = document.getElementById("start-browser-player-button");
const stopBrowserPlayerButtonNode = document.getElementById("stop-browser-player-button");
const launchLocalMpvButtonNode = document.getElementById("launch-local-mpv-button");
const localHelperStatusNode = document.getElementById("local-helper-status");
const localProtocolPanelNode = document.getElementById("local-protocol-panel");
const localProtocolLinkNode = document.getElementById("local-protocol-link");
const refreshBindingsButtonNode = document.getElementById("refresh-bindings-button");
const browseButtonNode = document.getElementById("browse-button");
const clearQueueButtonNode = document.getElementById("clear-queue-button");
const ownerOnlyNodes = document.querySelectorAll(".owner-only");

let room = null;
let members = [];
let bindings = [];
let currentItems = [];
let currentState = null;
let timerId = null;
let lastStateSync = performance.now();
let roomSocket = null;
let browserPlayerSocket = null;
let browserPlayerTimerId = null;
let browserPlayerActive = false;
let browserPendingSeekMs = null;
let sliderScrubbing = false;
const browserDeviceId = getOrCreateBrowserDeviceId();

const COMMAND_LABELS = {
  play: "播放",
  pause: "暂停",
  stop: "停止",
  seek: "跳转",
};

const SOURCE_KIND_LABELS = {
  playlist: "播放列表",
  boxset: "合集",
  queue: "队列",
};

document.getElementById("play-button").addEventListener("click", () => issueCommand("play"));
document.getElementById("pause-button").addEventListener("click", () => issueCommand("pause"));
document.getElementById("stop-button").addEventListener("click", () => issueCommand("stop"));
seekButtonNode.addEventListener("click", () => {
  const seconds = Number(seekSecondsNode.value || 0);
  issueCommand("seek", { position_ms: Math.max(seconds, 0) * 1000 });
});
document.getElementById("search-button").addEventListener("click", () => loadItems({ globalSearch: true }));
document.getElementById("reload-items-button").addEventListener("click", () => loadItems({ globalSearch: false }));
browseButtonNode.addEventListener("click", () => loadItems({ globalSearch: false }));
refreshBindingsButtonNode.addEventListener("click", () => loadBindings());
clearQueueButtonNode.addEventListener("click", () => clearQueue());
startBrowserPlayerButtonNode.addEventListener("click", () => startBrowserPlayer());
stopBrowserPlayerButtonNode.addEventListener("click", () => stopBrowserPlayer({ clearMedia: false }));
launchLocalMpvButtonNode.addEventListener("click", () => launchLocalMpv());
bindingSelectNode.addEventListener("change", () => loadLibraries());
librarySelectNode.addEventListener("change", () => loadItems({ globalSearch: false }));
writebackToggleNode.addEventListener("change", async () => {
  if (!room?.is_owner) {
    return;
  }
  try {
    const response = await apiFetch(`/rooms/${roomId}/writeback-toggle`, {
      method: "POST",
      body: { enabled: writebackToggleNode.checked },
    });
    currentState = response.state;
    updatePlaybackState();
    setStatus(statusNode, "Emby writeback setting updated.", "success");
  } catch (error) {
    writebackToggleNode.checked = !writebackToggleNode.checked;
    setStatus(statusNode, error.message, "error");
  }
});
progressSliderNode.addEventListener("input", () => {
  sliderScrubbing = true;
  const previewMs = Number(progressSliderNode.value || 0);
  seekSecondsNode.value = String(Math.floor(previewMs / 1000));
  renderProgressText(previewMs);
});
progressSliderNode.addEventListener("change", async () => {
  const targetMs = Number(progressSliderNode.value || 0);
  seekSecondsNode.value = String(Math.floor(targetMs / 1000));
  if (room?.is_owner) {
    await issueCommand("seek", { position_ms: targetMs });
  }
  sliderScrubbing = false;
  renderProgressText();
});
progressSliderNode.addEventListener("blur", () => {
  sliderScrubbing = false;
  renderProgressText();
});
itemSearchNode.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    loadItems({ globalSearch: true });
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    logout();
  }
});
window.addEventListener("beforeunload", () => {
  stopBrowserPlayer({ clearMedia: false });
});
browserPlayerNode.addEventListener("loadedmetadata", () => {
  if (browserPendingSeekMs !== null) {
    browserPlayerNode.currentTime = Math.max(browserPendingSeekMs / 1000, 0);
    browserPendingSeekMs = null;
  }
  enforceBrowserPlaybackState();
});
browserPlayerNode.addEventListener("ended", () => {
  if (!browserPlayerActive) {
    return;
  }
  sendBrowserPlaybackUpdate();
});
browserPlayerNode.addEventListener("error", () => {
  if (browserPlayerActive) {
    setBrowserPlayerChip("Browser Error", "danger");
    sendBrowserPlaybackUpdate();
  }
});

async function issueCommand(command, body = undefined) {
  try {
    const response = await apiFetch(`/rooms/${roomId}/playback/${command}`, {
      method: "POST",
      body,
    });
    currentState = response.state;
    lastStateSync = performance.now();
    updatePlaybackState();
    setStatus(statusNode, `Sent ${command} command.`, "success");
  } catch (error) {
    setStatus(statusNode, error.message, "error");
  }
}

async function loadRoom() {
  room = await apiFetch(`/rooms/${roomId}`);
  members = await apiFetch(`/rooms/${roomId}/members`);
  currentState = room.playback;
  lastStateSync = performance.now();
  applyRoomMeta();
  renderMembers();
  updatePlaybackState();
}

function applyRoomMeta() {
  roomTitleNode.textContent = room.name;
  roomSubtitleNode.textContent = room.is_owner
    ? "You are the room owner. You can control playback, run global search, and import playlists or box sets."
    : "You are a room member. You can observe status and the imported queue here, but playback commands stay owner-only.";
  inviteChipNode.textContent = `Invite ${room.invite_code}`;
  ownerChipNode.textContent = room.is_owner ? "Owner View" : "Member View";
  ownerChipNode.className = `chip ${room.is_owner ? "ok" : "warn"}`;
  for (const node of ownerOnlyNodes) {
    node.hidden = !room.is_owner;
  }
  writebackToggleNode.checked = room.writeback_enabled;
  progressSliderNode.disabled = !room.is_owner;
  seekSecondsNode.disabled = !room.is_owner;
  seekButtonNode.disabled = !room.is_owner;
}

function updatePlaybackState() {
  if (!currentState) {
    return;
  }
  const media = currentState.current_media;
  currentMediaTitleNode.textContent = media?.title || "Unselected";
  currentMediaMetaNode.textContent = media?.media_url
    ? `${media.media_url}${media.artwork_url ? " | artwork available" : ""}`
    : "No media loaded.";
  playbackStateChipNode.textContent = currentState.playback_state;
  playbackStateChipNode.className = `chip ${
    currentState.playback_state === "playing"
      ? "ok"
      : currentState.playback_state === "paused"
        ? "warn"
        : ""
  }`.trim();
  writebackChipNode.textContent = currentState.writeback_enabled ? "Writeback On" : "Writeback Off";
  writebackChipNode.className = `chip ${currentState.writeback_enabled ? "ok" : ""}`.trim();
  writebackToggleNode.checked = Boolean(currentState.writeback_enabled);
  progressSliderNode.max = String(media?.duration_ms || currentState.position_ms || 0);
  if (!sliderScrubbing) {
    progressSliderNode.value = String(currentState.position_ms || 0);
    seekSecondsNode.value = String(Math.floor((currentState.position_ms || 0) / 1000));
  }
  renderProgressText(sliderScrubbing ? Number(progressSliderNode.value || 0) : null);
  renderQueue();
  startProgressTicker();
}

function renderProgressText(positionOverrideMs = null) {
  if (!currentState) {
    return;
  }
  const media = currentState.current_media;
  const livePosition = positionOverrideMs ?? getLivePosition();
  currentProgressTextNode.textContent = `${formatDuration(livePosition)} / ${formatDuration(media?.duration_ms || 0)}`;
  if (!sliderScrubbing) {
    progressSliderNode.value = String(Math.min(livePosition, Number(progressSliderNode.max || 0)));
  }
}

function getLivePosition() {
  if (!currentState) {
    return 0;
  }
  if (currentState.playback_state !== "playing") {
    return currentState.position_ms || 0;
  }
  const elapsed = performance.now() - lastStateSync;
  return Math.max((currentState.position_ms || 0) + Math.floor(elapsed), 0);
}

function startProgressTicker() {
  if (timerId) {
    window.clearInterval(timerId);
  }
  timerId = window.setInterval(() => {
    renderProgressText();
  }, 500);
}

function renderMembers() {
  memberListNode.replaceChildren();
  deviceCountNode.textContent = String(
    members.reduce((sum, member) => sum + Number(member.device_count || 0), 0),
  );
  if (!members.length) {
    memberListNode.append(el("div", "empty", "No members in this room."));
    return;
  }

  for (const member of members) {
    const entry = el("div", "member-entry");
    const left = el("div");
    left.append(
      el("div", "entry-title", `${member.username}${member.is_owner ? " | owner" : ""}`),
      el("div", "entry-meta", member.online ? `Online devices: ${member.device_count}` : "No active player online."),
    );
    const badge = el("span", `chip ${member.online ? "ok" : "warn"}`.trim(), member.online ? "Online" : "Offline");
    entry.append(left, badge);
    memberListNode.append(entry);
  }
}

async function loadBindings() {
  if (!room?.is_owner) {
    return;
  }
  setStatus(statusNode, "Refreshing Emby bindings...");
  try {
    const previousBindingId = bindingSelectNode.value;
    bindings = await apiFetch("/emby-bindings");
    bindingSelectNode.replaceChildren();
    if (!bindings.length) {
      bindingSelectNode.append(new Option("No Emby bindings available", ""));
      renderNoBindingsState();
      return;
    }

    for (const binding of bindings) {
      bindingSelectNode.append(new Option(binding.display_name, binding.id));
    }
    bindingSelectNode.value = bindings.some((binding) => binding.id === previousBindingId)
      ? previousBindingId
      : bindings[0].id;
    setStatus(statusNode, `Loaded ${bindings.length} Emby binding(s).`, "success");
    await loadLibraries();
  } catch (error) {
    setStatus(statusNode, error.message, "error");
    mediaListNode.replaceChildren(el("div", "empty", `Failed to load bindings: ${error.message}`));
  }
}

function renderNoBindingsState() {
  const empty = el("div", "empty");
  empty.append(document.createTextNode("No bindings available yet. Add an Emby binding from the dashboard first."));

  const actions = el("div", "button-row");
  actions.style.marginTop = "12px";

  const refreshButton = document.createElement("button");
  refreshButton.type = "button";
  refreshButton.className = "subtle";
  refreshButton.textContent = "Refresh Now";
  refreshButton.addEventListener("click", () => loadBindings());
  actions.append(refreshButton);

  const dashboardLink = document.createElement("a");
  dashboardLink.href = "/app/dashboard";
  dashboardLink.textContent = "Open Dashboard";
  dashboardLink.className = "subtle-link";
  actions.append(dashboardLink);

  empty.append(actions);
  mediaListNode.replaceChildren(empty);
  setStatus(statusNode, "No Emby bindings found for this account. Add one from the dashboard first.");
}

async function loadLibraries() {
  const bindingId = bindingSelectNode.value;
  librarySelectNode.replaceChildren();
  mediaListNode.replaceChildren();
  if (!bindingId) {
    return;
  }
  try {
    const libraries = await apiFetch(`/emby-bindings/${bindingId}/libraries`);
    if (!libraries.length) {
      librarySelectNode.append(new Option("No libraries", ""));
      mediaListNode.replaceChildren(el("div", "empty", "This binding has no accessible libraries."));
      return;
    }
    for (const library of libraries) {
      librarySelectNode.append(new Option(library.name, library.id));
    }
    await loadItems({ globalSearch: false });
  } catch (error) {
    setStatus(statusNode, error.message, "error");
  }
}

async function loadItems({ globalSearch }) {
  const bindingId = bindingSelectNode.value;
  const libraryId = librarySelectNode.value;
  const searchTerm = itemSearchNode.value.trim();
  mediaListNode.replaceChildren(el("div", "empty", globalSearch ? "Searching all Emby media..." : "Loading media list..."));

  if (!bindingId) {
    return;
  }
  if (globalSearch && !searchTerm) {
    mediaListNode.replaceChildren(el("div", "empty", "Enter a keyword to search across all libraries."));
    setStatus(statusNode, "Enter a keyword before running global search.", "error");
    return;
  }
  if (!globalSearch && !libraryId) {
    return;
  }

  try {
    const query = new URLSearchParams({
      limit: "100",
    });
    if (globalSearch) {
      query.set("global_search", "true");
      query.set("recursive", "true");
      query.set("search_term", searchTerm);
    } else {
      query.set("parent_id", libraryId);
      query.set("recursive", "true");
    }
    currentItems = await apiFetch(`/emby-bindings/${bindingId}/items?${query.toString()}`);
    renderItems(globalSearch);
    setStatus(
      statusNode,
      globalSearch ? `Found ${currentItems.length} result(s) across all libraries.` : `Loaded ${currentItems.length} item(s) from the selected library.`,
      "success",
    );
  } catch (error) {
    mediaListNode.replaceChildren(el("div", "empty", `Load failed: ${error.message}`));
    setStatus(statusNode, error.message, "error");
  }
}

function renderItems(globalSearch) {
  mediaListNode.replaceChildren();
  if (!currentItems.length) {
    mediaListNode.append(
      el("div", "empty", globalSearch ? "No matching items were found across Emby." : "No items found in this library."),
    );
    return;
  }

  for (const item of currentItems) {
    const entry = el("article", "media-entry");
    const left = el("div");
    const meta = [];
    if (item.item_type) {
      meta.push(item.item_type);
    }
    if (item.duration_ms) {
      meta.push(formatDuration(item.duration_ms));
    }
    if (item.child_count) {
      meta.push(`${item.child_count} items`);
    }

    left.append(
      el("div", "entry-title", item.name),
      el("div", "entry-meta", meta.join(" | ") || "Unknown"),
      el(
        "div",
        "entry-meta",
        item.overview || (item.can_import ? "Ready to import into the room queue." : "No overview."),
      ),
    );

    const actions = el("div", "button-row");
    if (item.can_import) {
      const importButton = el("button", "subtle", "Import Queue");
      importButton.type = "button";
      importButton.addEventListener("click", () => importQueue(item));
      actions.append(importButton);
    }
    if (item.can_play) {
      const button = el("button", null, "Load Video");
      button.type = "button";
      button.addEventListener("click", () => loadSelectedItem(item.id));
      actions.append(button);
    }
    if (!actions.childNodes.length) {
      actions.append(el("span", "chip warn", "Browsable only"));
    }

    entry.append(left, actions);
    mediaListNode.append(entry);
  }
}

async function loadSelectedItem(itemId) {
  const bindingId = bindingSelectNode.value;
  try {
    const response = await apiFetch(`/rooms/${roomId}/playback/load`, {
      method: "POST",
      body: {
        binding_id: bindingId,
        item_id: itemId,
      },
    });
    currentState = response.state;
    lastStateSync = performance.now();
    updatePlaybackState();
    setStatus(statusNode, "Room media updated.", "success");
  } catch (error) {
    setStatus(statusNode, error.message, "error");
  }
}

async function importQueue(item) {
  const bindingId = bindingSelectNode.value;
  try {
    const response = await apiFetch(`/rooms/${roomId}/queue/import`, {
      method: "POST",
      body: {
        binding_id: bindingId,
        item_id: item.id,
      },
    });
    currentState = response.state;
    lastStateSync = performance.now();
    updatePlaybackState();
    setStatus(statusNode, `${item.name} imported into the room queue.`, "success");
  } catch (error) {
    setStatus(statusNode, error.message, "error");
  }
}

function renderQueue() {
  queueListNode.replaceChildren();
  const queueEntries = currentState?.queue_entries || [];
  if (!queueEntries.length) {
    queueMetaNode.textContent = "No imported playlist or box set yet.";
    queueListNode.append(el("div", "empty", "Import an Emby playlist or box set to build a shared room queue."));
    return;
  }

  const sourceTitle = queueEntries[0].source_title || "Imported Queue";
  const sourceKind = queueEntries[0].source_kind || "queue";
  queueMetaNode.textContent = `${sourceTitle} | ${sourceKind} | ${queueEntries.length} item(s)`;

  for (const [index, entry] of queueEntries.entries()) {
    const node = el("article", "media-entry");
    const left = el("div");
    const meta = [];
    if (entry.item_type) {
      meta.push(entry.item_type);
    }
    if (entry.duration_ms) {
      meta.push(formatDuration(entry.duration_ms));
    }
    meta.push(`Queue #${index + 1}`);

    left.append(
      el("div", "entry-title", entry.title),
      el("div", "entry-meta", meta.join(" | ")),
    );

    const actions = el("div", "button-row");
    if (currentState?.current_queue_index === index) {
      actions.append(el("span", "chip ok", "Current"));
    }
    if (room?.is_owner) {
      const button = el("button", "subtle", "Play Now");
      button.type = "button";
      button.addEventListener("click", () => loadQueueEntry(entry.id));
      actions.append(button);
    }
    node.append(left, actions);
    queueListNode.append(node);
  }
}

async function loadQueueEntry(entryId) {
  try {
    const response = await apiFetch(`/rooms/${roomId}/queue/${entryId}/load`, {
      method: "POST",
    });
    currentState = response.state;
    lastStateSync = performance.now();
    updatePlaybackState();
    setStatus(statusNode, "Queue entry loaded into the room.", "success");
  } catch (error) {
    setStatus(statusNode, error.message, "error");
  }
}

async function clearQueue() {
  try {
    const response = await apiFetch(`/rooms/${roomId}/queue`, {
      method: "DELETE",
    });
    currentState = response.state;
    lastStateSync = performance.now();
    updatePlaybackState();
    setStatus(statusNode, "Room queue cleared.", "success");
  } catch (error) {
    setStatus(statusNode, error.message, "error");
  }
}

async function startBrowserPlayer() {
  const session = loadSession();
  if (!session?.access_token) {
    setStatus(statusNode, "Missing web session. Sign in again.", "error");
    return;
  }
  browserPlayerActive = true;
  setBrowserPlayerChip("Connecting Browser", "warn");
  ensureBrowserPlayerLoop();
  await connectBrowserPlayerSocket(session.access_token);
  applyBrowserTargetState(currentState);
}

function stopBrowserPlayer({ clearMedia }) {
  browserPlayerActive = false;
  if (browserPlayerTimerId) {
    window.clearInterval(browserPlayerTimerId);
    browserPlayerTimerId = null;
  }
  if (browserPlayerSocket) {
    browserPlayerSocket.close();
    browserPlayerSocket = null;
  }
  browserPlayerNode.pause();
  if (clearMedia) {
    browserPlayerNode.removeAttribute("src");
    browserPlayerNode.load();
  }
  setBrowserPlayerChip("Browser Idle", "");
}

async function connectBrowserPlayerSocket(accessToken) {
  if (browserPlayerSocket && browserPlayerSocket.readyState === WebSocket.OPEN) {
    return;
  }
  if (browserPlayerSocket) {
    browserPlayerSocket.close();
  }
  browserPlayerSocket = new WebSocket(buildWebSocketUrl("/ws/client", accessToken));
  browserPlayerSocket.addEventListener("open", () => {
    browserPlayerSocket.send(
      JSON.stringify({
        message_type: "client_hello",
        payload: {
          room_id: roomId,
          device_id: browserDeviceId,
          device_name: "Browser Player",
          client_version: "web-0.1.0",
        },
      }),
    );
    setBrowserPlayerChip("Browser Connected", "ok");
    sendBrowserPlaybackUpdate();
  });
  browserPlayerSocket.addEventListener("message", (event) => {
    handleBrowserPlayerMessage(JSON.parse(event.data));
  });
  browserPlayerSocket.addEventListener("close", () => {
    if (!browserPlayerActive) {
      return;
    }
    setBrowserPlayerChip("Browser Reconnecting", "warn");
    window.setTimeout(() => {
      if (!browserPlayerActive) {
        return;
      }
      const session = loadSession();
      if (session?.access_token) {
        connectBrowserPlayerSocket(session.access_token).catch((error) => {
          setStatus(statusNode, error.message, "error");
        });
      }
    }, 2500);
  });
}

function ensureBrowserPlayerLoop() {
  if (browserPlayerTimerId) {
    return;
  }
  browserPlayerTimerId = window.setInterval(() => {
    if (!browserPlayerActive) {
      return;
    }
    enforceBrowserPlaybackState();
    sendBrowserPlaybackUpdate();
  }, 1000);
}

function handleBrowserPlayerMessage(message) {
  if (message.message_type === "server_notice") {
    setStatus(statusNode, message.payload.message, "success");
    return;
  }
  if (message.message_type === "room_snapshot") {
    currentState = message.payload.state;
    lastStateSync = performance.now();
    applyBrowserTargetState(message.payload.state);
    return;
  }
  if (message.message_type === "playback_command") {
    currentState = message.payload.state;
    lastStateSync = performance.now();
    applyBrowserTargetState(message.payload.state);
    return;
  }
  if (message.message_type === "sync_correction") {
    seekBrowserPlayer(message.payload.expected_position_ms);
  }
}

function applyBrowserTargetState(state) {
  if (!browserPlayerActive || !state) {
    return;
  }
  const media = state.current_media;
  if (!media?.media_url) {
    browserPlayerNode.pause();
    browserPlayerNode.removeAttribute("src");
    browserPlayerNode.load();
    setBrowserPlayerChip("No Browser Media", "warn");
    return;
  }

  if (browserPlayerNode.dataset.currentUrl !== media.media_url) {
    browserPlayerNode.dataset.currentUrl = media.media_url;
    browserPendingSeekMs = state.position_ms || 0;
    browserPlayerNode.src = media.media_url;
    browserPlayerNode.load();
  } else if (Math.abs(browserPlayerNode.currentTime * 1000 - (state.position_ms || 0)) >= 1800) {
    seekBrowserPlayer(state.position_ms || 0);
  }

  enforceBrowserPlaybackState();
}

function enforceBrowserPlaybackState() {
  if (!browserPlayerActive || !currentState) {
    return;
  }
  const media = currentState.current_media;
  if (!media?.media_url) {
    return;
  }
  if (currentState.playback_state === "paused") {
    if (!browserPlayerNode.paused) {
      browserPlayerNode.pause();
    }
    setBrowserPlayerChip("Browser Paused", "warn");
    return;
  }
  if (currentState.playback_state === "stopped") {
    browserPlayerNode.pause();
    if (browserPlayerNode.currentTime !== 0) {
      seekBrowserPlayer(0);
    }
    setBrowserPlayerChip("Browser Stopped", "");
    return;
  }
  if (currentState.playback_state === "playing") {
    const playPromise = browserPlayerNode.play();
    if (playPromise?.catch) {
      playPromise.catch(() => {
        setBrowserPlayerChip("Browser Awaiting Gesture", "warn");
      });
    }
    setBrowserPlayerChip("Browser Playing", "ok");
  }
}

function seekBrowserPlayer(positionMs) {
  const targetSeconds = Math.max(positionMs / 1000, 0);
  if (Number.isFinite(browserPlayerNode.duration) && browserPlayerNode.readyState >= 1) {
    browserPlayerNode.currentTime = targetSeconds;
    return;
  }
  browserPendingSeekMs = positionMs;
}

function sendBrowserPlaybackUpdate() {
  if (!browserPlayerActive || !browserPlayerSocket || browserPlayerSocket.readyState !== WebSocket.OPEN) {
    return;
  }
  const playbackState = buildBrowserPlaybackState();
  browserPlayerSocket.send(
    JSON.stringify({
      message_type: "heartbeat",
      payload: {
        room_id: roomId,
        device_id: browserDeviceId,
        playback_state: playbackState.playback_state,
        position_ms: playbackState.position_ms,
      },
    }),
  );
  browserPlayerSocket.send(
    JSON.stringify({
      message_type: "state_update",
      payload: {
        state: playbackState,
      },
    }),
  );
}

function buildBrowserPlaybackState() {
  let playbackState = "stopped";
  if (browserPlayerNode.error) {
    playbackState = "error";
  } else if (browserPlayerNode.currentSrc) {
    playbackState = browserPlayerNode.paused ? "paused" : "playing";
  }
  return {
    device_id: browserDeviceId,
    device_name: "Browser Player",
    room_id: roomId,
    playback_state: playbackState,
    position_ms: Math.max(Math.floor((browserPlayerNode.currentTime || 0) * 1000), 0),
    duration_ms: Number.isFinite(browserPlayerNode.duration) ? Math.floor(browserPlayerNode.duration * 1000) : null,
    playback_rate: browserPlayerNode.playbackRate || 1.0,
    paused: browserPlayerNode.paused,
    path: browserPlayerNode.currentSrc || null,
    error: browserPlayerNode.error ? `HTML5 video error code ${browserPlayerNode.error.code}` : null,
  };
}

async function launchLocalMpv() {
  const session = loadSession();
  if (!session?.access_token) {
    setStatus(statusNode, "Missing local web session data. Sign in again before launching local mpv.", "error");
    return;
  }
  try {
    const response = await apiFetch(`/rooms/${roomId}/client-handoff`, {
      method: "POST",
    });
    const deepLink = response.deeplink_url;
    localHelperStatusNode.textContent =
      "The browser should ask to open Yuntongbu. If no prompt appears, use the manual launch link below.";
    showLocalProtocolPanel(deepLink);
    setStatus(statusNode, "Local mpv handoff created. Opening the desktop helper...", "success");
    attemptProtocolLaunch(deepLink);
  } catch (error) {
    setStatus(statusNode, error.message, "error");
    localHelperStatusNode.textContent = "Unable to create a local-player handoff. Check your login state and room membership.";
  }
}

function setBrowserPlayerChip(label, kind) {
  browserPlayerChipNode.textContent = label;
  browserPlayerChipNode.className = `chip ${kind || ""}`.trim();
}

function getOrCreateBrowserDeviceId() {
  const key = "yuntongbu.browser.device_id";
  const existing = window.localStorage.getItem(key);
  if (existing) {
    return existing;
  }
  const uuid = window.crypto?.randomUUID ? window.crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const value = `browser-${uuid}`;
  window.localStorage.setItem(key, value);
  return value;
}

function attemptProtocolLaunch(deepLink) {
  showLocalProtocolPanel(deepLink);
  window.location.href = deepLink;
}

function showLocalProtocolPanel(deepLink) {
  localProtocolLinkNode.href = deepLink;
  localProtocolPanelNode.hidden = false;
}

function connectRoomSocket() {
  const session = loadSession();
  if (!session?.access_token) {
    return;
  }
  if (roomSocket) {
    roomSocket.close();
  }
  roomSocket = new WebSocket(buildWebSocketUrl(`/ws/rooms/${roomId}`, session.access_token));
  roomSocket.addEventListener("message", (event) => {
    const payload = JSON.parse(event.data);
    handleSocketMessage(payload);
  });
  roomSocket.addEventListener("close", () => {
    setStatus(statusNode, "Room observer disconnected. Reconnecting...", "error");
    window.setTimeout(() => connectRoomSocket(), 2500);
  });
  roomSocket.addEventListener("open", () => {
    setStatus(statusNode, "Room observer connected.", "success");
  });
}

function handleSocketMessage(message) {
  if (message.message_type === "room_snapshot") {
    currentState = message.payload.state;
    members = message.payload.members || members;
    lastStateSync = performance.now();
    renderMembers();
    updatePlaybackState();
    return;
  }
  if (message.message_type === "playback_command") {
    currentState = message.payload.state;
    lastStateSync = performance.now();
    updatePlaybackState();
    return;
  }
  if (message.message_type === "server_notice") {
    setStatus(statusNode, message.payload.message, "success");
  }
}

async function bootstrap() {
  await requireUser();
  await loadRoom();
  if (room.is_owner) {
    await loadBindings();
  } else {
    renderQueue();
  }
  connectRoomSocket();
}

bootstrap().catch((error) => {
  setStatus(statusNode, error.message, "error");
});
