import { apiFetch, loadSession, readNextDestination, saveSession, setStatus } from "/static/web/common.js?v=20260318-zh1";

const form = document.getElementById("login-form");
const statusNode = document.getElementById("status");

if (loadSession()?.access_token) {
  window.location.href = readNextDestination("/app/dashboard");
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  setStatus(statusNode, "正在登录...");
  const payload = {
    username_or_email: document.getElementById("username").value.trim(),
    password: document.getElementById("password").value,
  };

  try {
    const response = await apiFetch("/auth/login", {
      method: "POST",
      auth: false,
      body: payload,
    });
    saveSession({
      access_token: response.access_token,
      refresh_token: response.refresh_token,
      user: response.user,
    });
    setStatus(statusNode, "登录成功，正在跳转...", "success");
    window.location.href = readNextDestination("/app/dashboard");
  } catch (error) {
    setStatus(statusNode, error.message, "error");
  }
});
