const SESSION_KEY = "yuntongbu.web.session";

const PLAYBACK_STATE_LABELS = {
  stopped: "已停止",
  playing: "播放中",
  paused: "已暂停",
  buffering: "缓冲中",
  error: "异常",
};

export function loadSession() {
  const raw = window.localStorage.getItem(SESSION_KEY);
  if (!raw) {
    return null;
  }
  try {
    return JSON.parse(raw);
  } catch {
    window.localStorage.removeItem(SESSION_KEY);
    return null;
  }
}

export function saveSession(session) {
  window.localStorage.setItem(SESSION_KEY, JSON.stringify(session));
}

export function clearSession() {
  window.localStorage.removeItem(SESSION_KEY);
}

export function redirectToLogin(nextPath = window.location.pathname) {
  const next = encodeURIComponent(nextPath);
  window.location.href = `/app/login?next=${next}`;
}

export function logout() {
  clearSession();
  redirectToLogin("/app/login");
}

export function readNextDestination(fallback = "/app/dashboard") {
  const next = new URLSearchParams(window.location.search).get("next");
  return next || fallback;
}

export async function requireUser() {
  const session = loadSession();
  if (!session?.access_token) {
    redirectToLogin();
    throw new Error("未找到登录会话。");
  }
  try {
    return await apiFetch("/auth/me");
  } catch (error) {
    if (error.status === 401) {
      redirectToLogin();
    }
    throw error;
  }
}

async function refreshSession(session) {
  if (!session?.refresh_token) {
    return null;
  }
  const response = await fetch("/auth/refresh", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify({ refresh_token: session.refresh_token }),
  });
  if (!response.ok) {
    clearSession();
    return null;
  }
  const payload = await response.json();
  const updated = {
    access_token: payload.access_token,
    refresh_token: payload.refresh_token,
    user: payload.user,
  };
  saveSession(updated);
  return updated;
}

export async function apiFetch(path, options = {}) {
  const { method = "GET", body, auth = true } = options;
  let session = loadSession();
  const headers = {
    Accept: "application/json",
    ...(options.headers || {}),
  };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (auth && session?.access_token) {
    headers.Authorization = `Bearer ${session.access_token}`;
  }

  let response = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (response.status === 401 && auth && session?.refresh_token) {
    session = await refreshSession(session);
    if (!session?.access_token) {
      redirectToLogin();
      throw new Error("401 未授权");
    }
    headers.Authorization = `Bearer ${session.access_token}`;
    response = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  }

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      if (payload?.detail) {
        detail = formatApiError(payload.detail);
      }
    } catch {
      const text = await response.text();
      if (text) {
        detail = text;
      }
    }
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }

  if (response.status === 204) {
    return null;
  }

  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return await response.json();
  }
  return await response.text();
}

function formatApiError(detail) {
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail)) {
    return detail.map((item) => formatValidationItem(item)).join("; ");
  }
  return JSON.stringify(detail);
}

function formatValidationItem(item) {
  if (!item || typeof item !== "object") {
    return String(item);
  }
  const loc = Array.isArray(item.loc) ? item.loc.filter((part) => part !== "body").join(".") : "field";
  if (item.type === "missing") {
    return `${loc || "字段"} 为必填项`;
  }
  if (item.type === "string_too_short" && item.ctx?.min_length) {
    return `${loc || "字段"} 至少需要 ${item.ctx.min_length} 个字符`;
  }
  return item.msg || JSON.stringify(item);
}

export function buildWebSocketUrl(path, token) {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}${path}?token=${encodeURIComponent(token)}`;
}

export function formatDuration(totalMs) {
  const safe = Math.max(Number(totalMs || 0), 0);
  const totalSeconds = Math.floor(safe / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

export function displayPlaybackState(state) {
  return PLAYBACK_STATE_LABELS[state] || state || "未知";
}

export function setStatus(element, message, kind = "info") {
  element.textContent = message || "";
  element.className = `status-banner ${kind === "error" ? "error" : kind === "success" ? "success" : ""}`.trim();
}

export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== undefined) {
    node.textContent = text;
  }
  return node;
}
