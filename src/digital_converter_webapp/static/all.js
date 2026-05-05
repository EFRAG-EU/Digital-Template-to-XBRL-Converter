  function openModal() {
    document.getElementById('termsModal').classList.remove('hidden');
    document.getElementById('termsModal').classList.add('flex');
  }
  function closeModal() {
    document.getElementById('termsModal').classList.add('hidden');
    document.getElementById('termsModal').classList.remove('flex');
  }
  function showSpinner() {
    document.getElementById('loadingSpinner').classList.remove('hidden');
    document.getElementById('loadingSpinner').classList.add('flex');
  }

function showErrorModal(message) {
    document.getElementById("errorMessage").textContent = message;
    document.getElementById("errorModal").classList.remove("hidden");
}

document.getElementById("closeModal").addEventListener("click", () => {
    document.getElementById("errorModal").classList.add("hidden");
});

document.addEventListener("DOMContentLoaded", () => {
    const links = document.querySelectorAll(".download-handler");

    links.forEach(downloadLink => {
        // Skip disabled links
        if (downloadLink.getAttribute("aria-disabled") === "true") {
            return;
        }


        downloadLink.addEventListener("click", async (event) => {
            event.preventDefault();

            const fileUrl = downloadLink.href;
            const spinner = document.getElementById("spinner");
            const spinnerText = document.getElementById("spinner-text");

            let spinnerTimeout;

            // Timer to show spinner after 300ms delay
            const showSpinner = () => {
                if (spinner && spinnerText) {
                    spinnerText.textContent = downloadLink.dataset.spinnerText || "Downloading...";
                    spinner.classList.remove("hidden");
                } else {
                    console.warn("Spinner not found! Proceeding without showing spinner.");
                }
            };
            spinnerTimeout = setTimeout(showSpinner, 300);

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

                // Cancel spinner timeout if not yet shown
                clearTimeout(spinnerTimeout);

                // Hide spinner if visible
                if (spinner) {
                    spinner.classList.add("hidden");
                }

                if (fileReady) {
                    window.location.href = fileUrl;
                } else {
                    showErrorModal("The file could not be generated in time.");
                }

            } catch (error) {
                clearTimeout(spinnerTimeout);
                if (spinner) {
                    spinner.classList.add("hidden");
                }
                console.error("Error waiting for file:", error);
                showErrorModal(`Error: ${error.message}`);
            }
        });
    });
});
