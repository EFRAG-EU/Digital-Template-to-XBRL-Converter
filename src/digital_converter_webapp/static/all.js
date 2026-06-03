function openModal() {
    document.getElementById("termsModal").classList.replace("hidden", "flex");
}

function closeModal() {
    document.getElementById("termsModal").classList.replace("flex", "hidden");
}

function showSpinner(message) {
    const spinner = document.getElementById("loadingSpinner");
    const spinnerText = document.getElementById("spinner-text");

    if (!spinner || !spinnerText) {
        console.warn("Spinner not found! Proceeding without showing spinner.");
        return;
    }

    if (typeof message === "string" && message.trim()) {
        spinnerText.textContent = message;
    }

    spinner.classList.replace("hidden", "flex");
}

function hideSpinner() {
    const spinner = document.getElementById("loadingSpinner");
    if (!spinner) {
        return;
    }
    spinner.classList.replace("flex", "hidden");
}

function showErrorModal(message) {
    document.getElementById("errorMessage").textContent = message;
    document.getElementById("errorModal").classList.replace("hidden", "flex");
}

document.addEventListener("DOMContentLoaded", () => {
    const closeBtn = document.getElementById("closeModal");
    if (closeBtn) {
        closeBtn.addEventListener("click", () => {
            document.getElementById("errorModal").classList.replace("flex", "hidden");
        });
    }

    const links = document.querySelectorAll(".download-handler");

    links.forEach(downloadLink => {
        // Skip disabled links
        if (downloadLink.getAttribute("aria-disabled") === "true") {
            return;
        }

        downloadLink.addEventListener("click", async (event) => {
            event.preventDefault();

            const fileUrl = downloadLink.href;
            const spinnerTimeout = setTimeout(
                () => showSpinner(downloadLink.dataset.spinnerText || "Downloading..."),
                300
            );

            let pollingInterval = 500;
            let fileReady = false;
            const startTime = Date.now();

            try {
                while (Date.now() - startTime < 60000) {
                    const response = await fetch(fileUrl, { method: "HEAD" });

                    if (response.status === 200 && response.headers.get("X-File-Ready") === "true") {
                        fileReady = true;
                        break;
                    }

                    if (response.status >= 400) {
                        let errorBody;
                        const fallBackResponse = response.clone();
                        try {
                            const jsonResponse = await response.json();
                            errorBody = jsonResponse.error ? jsonResponse.error : JSON.stringify(jsonResponse, null, 2);
                        } catch (e) {
                            const fallbackText = await fallBackResponse.text();
                            errorBody = `Failed to parse JSON: ${e.message}. Response body: ${fallbackText}`;
                        }

                        throw new Error(`Server error ${response.status}: ${errorBody}`);
                    }

                    await new Promise(resolve => setTimeout(resolve, pollingInterval));
                    pollingInterval = Math.min(pollingInterval * 1.5, 5000);
                }

                clearTimeout(spinnerTimeout);
                hideSpinner();

                if (fileReady) {
                    window.location.href = fileUrl;
                } else {
                    showErrorModal("The file could not be generated in time.");
                }

            } catch (error) {
                clearTimeout(spinnerTimeout);
                hideSpinner();
                console.error("Error waiting for file:", error);
                showErrorModal(`Error: ${error.message}`);
            }
        });
    });
});
