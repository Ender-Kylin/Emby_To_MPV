import { apiFetch, loadSession, saveSession, setStatus } from "/static/web/common.js?v=20260318-zh1";

const form = document.getElementById("register-form");
const statusNode = document.getElementById("status");

if (loadSession()?.access_token) {
  window.location.href = "/app/dashboard";
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const password = document.getElementById("password").value;
  const confirm = document.getElementById("password-confirm").value;
  if (password !== confirm) {
    setStatus(statusNode, "两次输入的密码不一致。", "error");
    return;
  }

  setStatus(statusNode, "正在创建账号...");
  try {
    const response = await apiFetch("/auth/register", {
      method: "POST",
      auth: false,
      body: {
        username: document.getElementById("username").value.trim(),
        email: document.getElementById("email").value.trim() || null,
        password,
      },
    });
    saveSession({
      access_token: response.access_token,
      refresh_token: response.refresh_token,
      user: response.user,
    });
    setStatus(statusNode, "注册成功，正在跳转...", "success");
    window.location.href = "/app/dashboard";
  } catch (error) {
    setStatus(statusNode, error.message, "error");
  }
});
