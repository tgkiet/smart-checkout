export function renderItems(itemsListEl, items) {
    itemsListEl.innerHTML = '';
    items.forEach((item, index) => {
        const itemEl = document.createElement('div');
        itemEl.className = 'item-card';
        itemEl.style.animationDelay = `${index * 0.1}s`;
        
        itemEl.innerHTML = `
            <div class="item-info">
                <span class="item-name">${item.name}</span>
                <span class="item-sku">SKU: ${item.sku || 'N/A'}</span>
            </div>
            <div class="item-price">$${item.price.toFixed(2)}</div>
        `;
        itemsListEl.appendChild(itemEl);
    });
}
