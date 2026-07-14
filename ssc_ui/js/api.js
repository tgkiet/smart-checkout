/**
 * api.js — Centralized API client for ssc_service
 * Base URL is configurable via window.SSC_API_BASE (set before this module is loaded)
 */

export const BASE_URL = window.SSC_API_BASE || "";

async function request(method, path, body = null, isFormData = false) {
    const opts = {
        method,
        headers: isFormData ? {} : { "Content-Type": "application/json" },
        body: body
            ? isFormData ? body : JSON.stringify(body)
            : undefined,
    };
    const res = await fetch(`${BASE_URL}${path}`, opts);
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
}

// ── Sessions ──────────────────────────────────────────────────────────────

/** Tạo phiên mới */
export const createSession = () => request("POST", "/sessions/");

/** Lấy chi tiết phiên (danh sách ảnh, status) */
export const getSession = (sessionId) => request("GET", `/sessions/${sessionId}`);

/** Xóa phiên */
export const deleteSession = (sessionId) => request("DELETE", `/sessions/${sessionId}`);

// ── Images ────────────────────────────────────────────────────────────────

/**
 * Upload nhiều ảnh vào phiên.
 * @param {string} sessionId
 * @param {File[]} files
 */
export async function uploadImages(sessionId, files) {
    const form = new FormData();
    for (const f of files) form.append("files", f);
    return request("POST", `/sessions/${sessionId}/images`, form, true);
}

/**
 * Xóa 1 ảnh khỏi phiên.
 */
export const deleteImage = (sessionId, imageId) =>
    request("DELETE", `/sessions/${sessionId}/images/${imageId}`);

// ── Checkout ──────────────────────────────────────────────────────────────

/**
 * Checkout tất cả ảnh PENDING trong phiên (song song phía backend).
 */
export const checkoutAll = (sessionId) =>
    request("POST", `/sessions/${sessionId}/checkout`);

/**
 * Checkout 1 ảnh cụ thể.
 */
export const checkoutOne = (sessionId, imageId) =>
    request("POST", `/sessions/${sessionId}/checkout/${imageId}`);

// ── Cart ──────────────────────────────────────────────────────────────────

/** Lấy giỏ hàng + tổng tiền */
export const getCart = (sessionId) => request("GET", `/sessions/${sessionId}/cart`);

/** Xác nhận thanh toán */
export const confirmCheckout = (sessionId) =>
    request("POST", `/sessions/${sessionId}/confirm`);
