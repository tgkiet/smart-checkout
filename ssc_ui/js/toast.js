/**
 * toast.js — Lightweight toast notification system
 */

const container = document.getElementById("toast-container");

const ICONS = {
    success: "✓",
    error:   "✕",
    info:    "ℹ",
    warning: "⚠",
};

/**
 * Show a toast notification.
 * @param {string} message
 * @param {"success"|"error"|"info"|"warning"} type
 * @param {number} duration ms
 */
export function toast(message, type = "info", duration = 4000) {
    const el = document.createElement("div");
    el.className = `toast ${type}`;
    el.innerHTML = `
        <span class="toast-icon">${ICONS[type] || "ℹ"}</span>
        <span class="toast-msg">${message}</span>
    `;
    container.appendChild(el);
    setTimeout(() => {
        el.style.opacity = "0";
        el.style.transform = "translateX(20px)";
        el.style.transition = "all 0.3s ease";
        setTimeout(() => el.remove(), 300);
    }, duration);
}

export const toastSuccess = (msg) => toast(msg, "success");
export const toastError   = (msg) => toast(msg, "error");
export const toastInfo    = (msg) => toast(msg, "info");
