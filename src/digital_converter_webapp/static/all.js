// eslint-disable-next-line no-unused-vars
function openModal() {
    document.getElementById("termsModal").classList.replace("hidden", "flex");
}

// eslint-disable-next-line no-unused-vars
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

async function downloadWhenReady(fileUrl, spinnerText) {
    const MAX_WAIT_MS      = 60000;
    const INITIAL_POLL_MS  = 500;
    const MAX_POLL_MS      = 5000;
    const POLL_BACKOFF     = 1.5;
    const SPINNER_DELAY_MS = 300;

    const spinnerTimeout = setTimeout(
        () => showSpinner(spinnerText || "Downloading..."),
        SPINNER_DELAY_MS
    );

    let pollingInterval = INITIAL_POLL_MS;
    const startTime = Date.now();

    try {
        while (Date.now() - startTime < MAX_WAIT_MS) {
            const response = await fetch(fileUrl, { method: "HEAD" });

            if (response.status === 200 && response.headers.get("X-File-Ready") === "true") {
                window.location.href = fileUrl;
                return;
            }

            if (response.status >= 400) {
                let errorBody;
                const rawText = await response.text();
                try {
                    const jsonResponse = JSON.parse(rawText);
                    errorBody = jsonResponse.error ?? JSON.stringify(jsonResponse, null, 2);
                } catch (e) {
                    errorBody = `Failed to parse JSON: ${e.message}. Response body: ${rawText}`;
                }

                throw new Error(`Server error ${response.status}: ${errorBody}`);
            }

            await new Promise(resolve => setTimeout(resolve, pollingInterval));
            pollingInterval = Math.min(pollingInterval * POLL_BACKOFF, MAX_POLL_MS);
        }

        showErrorModal("The file could not be generated in time.");

    } catch (error) {
        console.error("Error waiting for file:", error);
        showErrorModal(`Error: ${error.message}`);
    } finally {
        clearTimeout(spinnerTimeout);
        hideSpinner();
    }
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
            await downloadWhenReady(downloadLink.href, downloadLink.dataset.spinnerText);
        });
    });
});
