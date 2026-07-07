export async function processCheckout(imageFile) {
    // Return mock data for UI development
    return new Promise((resolve) => {
        setTimeout(() => {
            resolve({
                items: [
                    { name: "Organic Bananas", sku: "FRU-001", price: 2.99, quantity: 1, subtotal: 2.99 },
                    { name: "Whole Milk 1L", sku: "DAI-042", price: 3.49, quantity: 2, subtotal: 6.98 },
                    { name: "Sourdough Bread", sku: "BAK-019", price: 4.50, quantity: 1, subtotal: 4.50 },
                    { name: "Coca Cola 330ml", sku: "BEV-099", price: 1.25, quantity: 3, subtotal: 3.75 }
                ],
                total_price: 18.22
            });
        }, 1500); // 1.5s delay to simulate network request and see the loading spinner
    });

    /* 
    // Original API Call
    const formData = new FormData();
    formData.append('image', imageFile);

    const response = await fetch('http://localhost:8100/checkout/', {
        method: 'POST',
        body: formData
    });

    if (!response.ok) {
        throw new Error('Network response was not ok: ' + response.statusText);
    }

    return await response.json();
    */
}
