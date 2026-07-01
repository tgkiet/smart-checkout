import { processCheckout } from './api.js';
import { renderItems } from './ui.js';

document.addEventListener('DOMContentLoaded', () => {
    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');
    const browseBtn = document.getElementById('browse-btn');
    
    const previewSection = document.getElementById('preview-section');
    const imagePreview = document.getElementById('image-preview');
    const reselectBtn = document.getElementById('reselect-btn');
    const checkoutBtn = document.getElementById('checkout-btn');
    
    const resultsSection = document.getElementById('results-section');
    const loadingSpinner = document.getElementById('loading-spinner');
    const itemsList = document.getElementById('items-list');
    const totalPriceEl = document.getElementById('total-price');
    const payBtn = document.getElementById('pay-btn');

    let currentFile = null;

    browseBtn.addEventListener('click', () => fileInput.click());
    
    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            handleFile(e.target.files[0]);
        }
    });

    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.classList.add('dragover');
    });

    dropZone.addEventListener('dragleave', () => {
        dropZone.classList.remove('dragover');
    });

    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('dragover');
        if (e.dataTransfer.files.length > 0) {
            handleFile(e.dataTransfer.files[0]);
        }
    });

    function handleFile(file) {
        if (!file.type.startsWith('image/')) {
            alert('Please select an image file.');
            return;
        }
        currentFile = file;
        const reader = new FileReader();
        reader.onload = (e) => {
            imagePreview.src = e.target.result;
            dropZone.classList.add('hidden');
            previewSection.classList.remove('hidden');
            
            resultsSection.classList.add('hidden');
            itemsList.innerHTML = '';
            totalPriceEl.textContent = '$0.00';
            payBtn.disabled = true;
        };
        reader.readAsDataURL(file);
    }

    reselectBtn.addEventListener('click', () => {
        currentFile = null;
        fileInput.value = '';
        previewSection.classList.add('hidden');
        dropZone.classList.remove('hidden');
        resultsSection.classList.add('hidden');
    });

    checkoutBtn.addEventListener('click', async () => {
        if (!currentFile) return;

        resultsSection.classList.remove('hidden');
        loadingSpinner.classList.remove('hidden');
        itemsList.innerHTML = '';
        checkoutBtn.disabled = true;
        reselectBtn.disabled = true;
        payBtn.disabled = true;
        totalPriceEl.textContent = '$0.00';

        try {
            const data = await processCheckout(currentFile);
            
            loadingSpinner.classList.add('hidden');
            
            if (data.items && data.items.length > 0) {
                renderItems(itemsList, data.items);
                totalPriceEl.textContent = `$${data.total_price.toFixed(2)}`;
                payBtn.disabled = false;
            } else {
                itemsList.innerHTML = `<p style="text-align:center; color: var(--text-muted); padding: 1rem;">No items detected in the image.</p>`;
            }
        } catch (error) {
            console.error('Error during checkout:', error);
            loadingSpinner.classList.add('hidden');
            itemsList.innerHTML = `<p style="text-align:center; color: #ef4444; padding: 1rem;">Error processing image. Is backend running?</p>`;
        } finally {
            checkoutBtn.disabled = false;
            reselectBtn.disabled = false;
        }
    });
});
