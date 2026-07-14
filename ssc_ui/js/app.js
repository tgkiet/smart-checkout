/**
 * app.js — Main application logic (ES6 module entry point)
 *
 * State machine:
 *   null → CREATE SESSION → OPEN → UPLOAD IMAGES → CHECKOUT ALL → COMPLETED → CONFIRM → CHECKED_OUT
 *                                   ↑ (add more images at any time while OPEN/COMPLETED)
 */

import * as api from "./api.js";
import { toastSuccess, toastError, toastInfo } from "./toast.js";

// ── State ─────────────────────────────────────────────────────────────────
let state = {
    sessionId:  null,
    sessionStatus: null,
    images: {},      // imageId → { filename, status, product, error, previewUrl }
};

// ── DOM refs ──────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);

const elWelcome          = $("welcome-screen");
const elWorkspace        = $("workspace");
const elWorkspaceSession = $("workspace-session-id");
const elSidebarSession   = $("sidebar-session-id");
const elSidebarInfo      = $("session-info-panel");
const elSessionStatus    = $("session-status-pill");
const elStatTotal        = $("stat-total");
const elStatDone         = $("stat-done");
const elStatFail         = $("stat-fail");

const elDropTarget  = $("drop-target");
const elFileInput   = $("file-input");
const elImageGrid   = $("image-grid");
const elGridHeader  = $("grid-header");
const elImgCount    = $("img-count");
const elCartBadge   = $("cart-badge");

const elCartEmpty   = $("cart-empty");
const elCartContent = $("cart-content");
const elCartItems   = $("cart-items-list");
const elSubtotal    = $("subtotal-price");
const elTotalPrice  = $("total-price-display");
const elCartLabel   = $("cart-session-label");
const elConfirmBtn  = $("btn-confirm-pay");

const elSuccessModal  = $("success-modal");
const elModalTotalTxt = $("modal-total-text");

// ── View routing ──────────────────────────────────────────────────────────
// Layout changed to side-by-side, no longer need to switch views.
// We just toggle visibility of panels based on session state.

// ── Formatters ────────────────────────────────────────────────────────────
const fmt = (price) =>
    new Intl.NumberFormat("vi-VN", { style: "currency", currency: "VND" }).format(price);

// ── Session management ────────────────────────────────────────────────────
async function createSession() {
    try {
        const session = await api.createSession();
        state.sessionId     = session.session_id;
        state.sessionStatus = session.status;
        state.images        = {};
        elWelcome.classList.add("hidden");
        elWorkspace.classList.remove("hidden");
        $("view-cart").classList.remove("hidden"); // Show cart panel
        elSidebarInfo.style.display = "";
        elWorkspaceSession.textContent = `Session: ${session.session_id}`;
        elSidebarSession.textContent   = session.session_id;
        refreshCartView(); // Initialize empty cart
        updateSidebarStats();
        updateSessionPill(session.status);
        toastSuccess("Phiên tính toán mới đã được tạo!");
    } catch (e) {
        toastError(`Không thể tạo phiên: ${e.message}`);
    }
}

function resetToWelcome() {
    state = { sessionId: null, sessionStatus: null, images: {} };
    elWorkspace.classList.add("hidden");
    $("view-cart").classList.add("hidden"); // Hide cart panel
    elWelcome.classList.remove("hidden");
    elSidebarInfo.style.display = "none";
    elImageGrid.innerHTML = "";
    elGridHeader.style.display = "none";
    updateCartBadge(0);
}

// ── Image upload ──────────────────────────────────────────────────────────
async function handleFilesSelected(files) {
    if (!state.sessionId) {
        toastError("Chưa có phiên làm việc. Vui lòng tạo phiên mới.");
        return;
    }
    if (!files || files.length === 0) return;

    const fileArr = Array.from(files);
    // Optimistic UI: add cards as "pending" immediately
    for (const f of fileArr) {
        const tempId = `temp_${Date.now()}_${Math.random()}`;
        const previewUrl = URL.createObjectURL(f);
        state.images[tempId] = { filename: f.filename || f.name, status: "pending", previewUrl, tempId, file: f };
        renderImageCard(tempId, state.images[tempId]);
    }
    updateGridHeader();

    try {
        const result = await api.uploadImages(state.sessionId, fileArr);
        // Reconcile temp cards with real IDs from server
        const tempIds = Object.keys(state.images).filter(k => k.startsWith("temp_"));
        result.added.forEach((added, i) => {
            const tempId = tempIds[i];
            if (!tempId) return;
            const item = state.images[tempId];
            item.image_id = added.image_id;
            item.filename = added.filename;
            delete state.images[tempId];
            state.images[added.image_id] = item;
            // Re-render card with new ID to update event listeners
            const card = document.querySelector(`[data-temp="${tempId}"]`);
            if (card) card.remove();
            renderImageCard(added.image_id, item);
        });
        toastSuccess(`Đã upload ${result.added.length} ảnh`);
        updateGridHeader();
        updateSidebarStats();
    } catch (e) {
        toastError(`Upload thất bại: ${e.message}`);
    }
}

// ── Render image card ─────────────────────────────────────────────────────
function renderImageCard(id, item) {
    // Remove existing card if re-rendering
    const existing = elImageGrid.querySelector(`[data-image-id="${id}"]`);
    if (existing) { existing.remove(); }

    const isTemp = id.startsWith("temp_");
    const card = document.createElement("div");
    card.className = `image-card ${item.status}`;
    if (isTemp) card.dataset.temp = id;
    else        card.dataset.imageId = id;

    card.innerHTML = `
        <div class="thumb-wrap">
            <img src="${item.previewUrl || ""}" alt="${item.filename}" loading="lazy">
            <div class="status-overlay">${statusEmoji(item.status)}</div>
            <div class="spinner-overlay"><div class="spinner"></div></div>
            <div class="card-actions">
                ${item.status !== "processing" ? `
                    <button class="icon-btn primary btn-checkout-one" title="Checkout ảnh này">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
                    </button>
                    <button class="icon-btn danger btn-delete-img" title="Xóa ảnh">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4h6v2"/></svg>
                    </button>
                ` : ""}
            </div>
        </div>
        <div class="card-body">
            <div class="img-filename">${item.filename}</div>
            ${item.status === "done" && item.product ? `
                <div class="product-name">${item.product.name}</div>
                <div class="product-price">${fmt(item.product.price)}</div>
            ` : ""}
            ${item.status === "failed" ? `<div class="img-error">${item.error || "Không nhận diện được"}</div>` : ""}
            ${item.status === "pending" ? `<div class="img-filename" style="color:var(--text-muted)">Chờ checkout</div>` : ""}
        </div>
    `;

    // Events
    card.querySelector(".btn-checkout-one")?.addEventListener("click", async (e) => {
        e.stopPropagation();
        await checkoutOneImage(id);
    });
    card.querySelector(".btn-delete-img")?.addEventListener("click", async (e) => {
        e.stopPropagation();
        await deleteOneImage(id);
    });

    elImageGrid.appendChild(card);
}

function statusEmoji(status) {
    return { done: "✓", failed: "✕", processing: "", pending: "" }[status] || "";
}

function refreshImageCard(id) {
    const item = state.images[id];
    if (!item) return;
    renderImageCard(id, item);
}

// ── Checkout one image ────────────────────────────────────────────────────
async function checkoutOneImage(imageId) {
    if (!state.sessionId || imageId.startsWith("temp_")) return;
    const item = state.images[imageId];
    if (!item) return;

    item.status = "processing";
    refreshImageCard(imageId);

    try {
        const result = await api.checkoutOne(state.sessionId, imageId);
        item.status  = result.status;
        item.product = result.product;
        item.error   = result.error;
        refreshImageCard(imageId);
        if (result.status === "done") {
            toastSuccess(`Nhận diện: ${result.product?.name}`);
            refreshCartView();
        } else {
            toastError(`Không nhận diện được: ${result.error || "Lỗi không xác định"}`);
        }
    } catch (e) {
        item.status = "failed";
        item.error  = e.message;
        refreshImageCard(imageId);
        toastError(`Lỗi checkout: ${e.message}`);
    }
    updateSidebarStats();
}

// ── Checkout ALL pending ──────────────────────────────────────────────────
async function checkoutAllImages() {
    if (!state.sessionId) return;

    const pending = Object.entries(state.images).filter(([,v]) => v.status === "pending");
    if (pending.length === 0) { toastInfo("Không có ảnh nào cần checkout"); return; }

    // Set UI to processing
    pending.forEach(([id, item]) => {
        item.status = "processing";
        refreshImageCard(id);
    });
    updateSessionPill("processing");
    $("btn-checkout-all").disabled = true;

    try {
        const result = await api.checkoutAll(state.sessionId);
        toastInfo(`Đang xử lý ${pending.length} ảnh, đang tải kết quả…`);
        // Poll session to get updated image statuses
        await pollSessionUntilDone();
        state.sessionStatus = result.summary?.processing === 0 ? "completed" : "open";
        updateSessionPill(state.sessionStatus);
        toastSuccess(`Checkout hoàn tất! ${fmt(result.total_price)}`);
        refreshCartView();
    } catch (e) {
        toastError(`Checkout thất bại: ${e.message}`);
    }
    $("btn-checkout-all").disabled = false;
    updateSidebarStats();
}

async function pollSessionUntilDone(maxAttempts = 30, intervalMs = 1500) {
    for (let i = 0; i < maxAttempts; i++) {
        await new Promise(r => setTimeout(r, intervalMs));
        try {
            const session = await api.getSession(state.sessionId);
            const summary = session.summary;
            // Update all cards from session data
            for (const img of session.images) {
                if (state.images[img.image_id]) {
                    state.images[img.image_id].status  = img.status;
                    state.images[img.image_id].product = img.product;
                    state.images[img.image_id].error   = img.error;
                    refreshImageCard(img.image_id);
                }
            }
            updateSidebarStats();
            if (summary.processing === 0 && summary.pending === 0) return;
        } catch (_) { /* ignore poll errors */ }
    }
}

// ── Delete one image ──────────────────────────────────────────────────────
async function deleteOneImage(imageId) {
    if (!state.sessionId || imageId.startsWith("temp_")) return;
    try {
        await api.deleteImage(state.sessionId, imageId);
        const card = elImageGrid.querySelector(`[data-image-id="${imageId}"]`);
        if (card) {
            card.style.transition = "all 0.25s ease";
            card.style.opacity = "0";
            card.style.transform = "scale(0.85)";
            setTimeout(() => card.remove(), 250);
        }
        delete state.images[imageId];
        updateGridHeader();
        updateSidebarStats();
        refreshCartView();
        toastInfo("Đã xóa ảnh");
    } catch (e) {
        toastError(`Không thể xóa: ${e.message}`);
    }
}

// ── Cart view ─────────────────────────────────────────────────────────────
async function refreshCartView() {
    if (!state.sessionId) {
        elCartEmpty.classList.remove("hidden");
        elCartContent.classList.add("hidden");
        elCartLabel.textContent = "—";
        return;
    }
    try {
        const cart = await api.getCart(state.sessionId);
        elCartLabel.textContent = `Phiên: ${state.sessionId.slice(0, 8)}…`;

        if (!cart.items || cart.items.length === 0) {
            elCartEmpty.classList.remove("hidden");
            elCartContent.classList.add("hidden");
            return;
        }

        elCartEmpty.classList.add("hidden");
        elCartContent.classList.remove("hidden");

        elCartItems.innerHTML = cart.items.map(item => `
            <div class="cart-item-row">
                <div class="cart-item-icon">${item.name.charAt(0).toUpperCase()}</div>
                <div class="cart-item-info">
                    <div class="cart-item-name">${item.name}</div>
                    <div class="cart-item-meta">${item.sku || ""}${item.platform ? " · " + item.platform : ""}</div>
                </div>
                <div class="cart-item-qty">×${item.quantity}</div>
                <div class="cart-item-price">
                    <div class="subtotal">${fmt(item.subtotal)}</div>
                    <div class="unit">${fmt(item.price)} / cái</div>
                </div>
            </div>
        `).join("");

        elSubtotal.textContent    = fmt(cart.total_price);
        elTotalPrice.textContent  = fmt(cart.total_price);
        elConfirmBtn.disabled     = cart.status === "checked_out" || cart.items.length === 0;
        if (cart.status === "checked_out") {
            elConfirmBtn.textContent = "Đã thanh toán";
        }
    } catch (e) {
        toastError(`Không thể tải giỏ hàng: ${e.message}`);
    }
}

// ── Confirm checkout ──────────────────────────────────────────────────────
async function confirmCheckout() {
    if (!state.sessionId) return;
    elConfirmBtn.disabled = true;
    try {
        const result = await api.confirmCheckout(state.sessionId);
        elSuccessModal.classList.remove("hidden");
        elModalTotalTxt.textContent = `Tổng thanh toán: ${fmt(result.total_price)}`;
        state.sessionStatus = "checked_out";
        updateSessionPill("checked_out");
        refreshCartView();
    } catch (e) {
        toastError(`Thanh toán thất bại: ${e.message}`);
        elConfirmBtn.disabled = false;
    }
}

// ── Helpers ───────────────────────────────────────────────────────────────
function updateGridHeader() {
    const count = Object.keys(state.images).length;
    if (count > 0) {
        elGridHeader.style.display = "";
        elImgCount.textContent = count;
    } else {
        elGridHeader.style.display = "none";
    }
}

function updateSessionPill(status) {
    elSessionStatus.textContent = status.charAt(0).toUpperCase() + status.slice(1);
    elSessionStatus.className = `status-pill ${status}`;
}

function updateSidebarStats() {
    const imgs = Object.values(state.images);
    elStatTotal.textContent = imgs.length;
    elStatDone.textContent  = imgs.filter(i => i.status === "done").length;
    elStatFail.textContent  = imgs.filter(i => i.status === "failed").length;
}

function updateCartBadge(count) {
    const n = count !== undefined
        ? count
        : Object.values(state.images).filter(i => i.status === "done").length;
    if (n > 0) {
        elCartBadge.style.display = "";
        elCartBadge.textContent   = n;
    } else {
        elCartBadge.style.display = "none";
    }
}

// ── Event bindings ────────────────────────────────────────────────────────
$("btn-create-session").addEventListener("click", createSession);
$("btn-new-session-sidebar").addEventListener("click", async () => {
    if (state.sessionId && !confirm("Tạo phiên mới sẽ đóng phiên hiện tại. Tiếp tục?")) return;
    resetToWelcome();
    await createSession();
});

$("nav-session").addEventListener("click", () => { /* No-op */ });
// Remove cart nav listener and go-session logic as it's a unified view now.

$("btn-browse").addEventListener("click", () => elFileInput.click());
$("btn-add-more").addEventListener("click", () => elFileInput.click());
elFileInput.addEventListener("change", e => {
    handleFilesSelected(e.target.files);
    e.target.value = "";
});

// Drag and drop
elDropTarget.addEventListener("dragover", e => { e.preventDefault(); elDropTarget.classList.add("drag-over"); });
elDropTarget.addEventListener("dragleave", () => elDropTarget.classList.remove("drag-over"));
elDropTarget.addEventListener("drop", e => {
    e.preventDefault();
    elDropTarget.classList.remove("drag-over");
    handleFilesSelected(e.dataTransfer.files);
});

$("btn-checkout-all").addEventListener("click", checkoutAllImages);
$("btn-confirm-pay").addEventListener("click", confirmCheckout);

$("modal-close-btn").addEventListener("click", () => {
    elSuccessModal.classList.add("hidden");
    resetToWelcome();
});
