// static/main.js

async function pollForPhotos() {
    let ready = false;
    while (!ready) {
        const response = await fetch("/check_session_status");
        if (response.ok) {
            const data = await response.json();
            if (data.ready) {
                // Once ready, redirect to slideshow
                window.location.href = "/slideshow";
                return;
            } else {
                // Not ready yet, wait a few seconds and try again
                await new Promise(r => setTimeout(r, 5000));
            }
        } else {
            console.error("Error polling session:", await response.text());
            break;
        }
    }
}

// Call this after user selects photos and you're ready to poll
// You can call pollForPhotos() when user returns from the picker page.

function loadImages() {
    // On the slideshow page, we assume `media_items` is available
    // We do not need to fetch if we are using <img src="/photo/<id>" directly.
    // If we needed to do fetch + blob, that would be a different approach.
    // But currently, we rely on the /photo/<media_id> route directly in <img> tags.
}

// If you need to use fetch and object URLs (not required here since /photo/<media_id> returns directly),
// you can implement a function like this:

// async function loadImageIntoImg(imgId, mediaId) {
//     const imgElement = document.getElementById(imgId);
//     imgElement.src = `/photo/${mediaId}`;
// }

