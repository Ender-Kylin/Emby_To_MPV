import { apiFetch, loadSession, saveSession, setStatus } from "/static/web/common.js";

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
    setStatus(statusNode, "Passwords do not match.", "error");
    return;
  }

  setStatus(statusNode, "Creating account...");
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
    setStatus(statusNode, "Registration successful. Redirecting...", "success");
    window.location.href = "/app/dashboard";
  } catch (error) {
    setStatus(statusNode, error.message, "error");
  }
});
