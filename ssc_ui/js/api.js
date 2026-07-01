export async function processCheckout(imageFile) {
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
}
