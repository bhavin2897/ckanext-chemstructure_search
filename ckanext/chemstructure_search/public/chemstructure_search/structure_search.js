(function () {
  var chemstructureAutoSyncTimer = null;
  var chemstructureLastSmiles = "";

  var CHEMSTRUCTURE_LAST_QUERY_KEY = "chemstructure_last_query";
  var CHEMSTRUCTURE_LAST_MODE_KEY = "chemstructure_last_mode";
  var CHEMSTRUCTURE_LAST_THRESHOLD_KEY = "chemstructure_last_threshold";

  var DEFAULT_MODE = "similarity";
  var DEFAULT_THRESHOLD = "0.25";

  /*
   * Expected backend endpoint.
   *
   * Preferred:
   * Add a hidden input in the active-search snippet:
   *
   * <input
   *   type="hidden"
   *   id="chemstructure-render-image-url"
   *   value="{{ h.url_for('chemstructure_search.render_query_image') }}">
   *
   * If that input is not present, JS falls back to this CKAN action URL.
   */
  var DEFAULT_RENDER_IMAGE_URL =
    "/api/3/action/chemstructure_render_query_image";

  function showMessage(message, type) {
    var el = document.getElementById("chemstructure-message");

    if (!el) {
      console.log("CHEMSTRUCTURE:", message);
      return;
    }

    el.innerHTML =
      '<div class="alert alert-' + (type || "info") + '">' +
      escapeHtml(message) +
      "</div>";
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function isMolfileLike(value) {
    if (!value) {
      return false;
    }

    return (
      value.indexOf("M  END") !== -1 ||
      value.indexOf("V2000") !== -1 ||
      value.indexOf("V3000") !== -1
    );
  }

  function normalizeThreshold(value) {
    var numberValue = parseFloat(value);

    if (isNaN(numberValue)) {
      numberValue = parseFloat(DEFAULT_THRESHOLD);
    }

    if (numberValue < 0.05) {
      numberValue = 0.05;
    }

    if (numberValue > 1.0) {
      numberValue = 1.0;
    }

    return numberValue.toFixed(2);
  }

  async function getSmilesFromKetcherSilently() {
    var iframe = document.getElementById("ketcher-frame");

    if (!iframe || !iframe.contentWindow || !iframe.contentWindow.ketcher) {
      return null;
    }

    try {
      var smiles = await iframe.contentWindow.ketcher.getSmiles();

      if (!smiles || !smiles.trim()) {
        return null;
      }

      smiles = smiles.trim();

      if (isMolfileLike(smiles)) {
        console.warn(
          "CHEMSTRUCTURE: Ignoring molfile-like value returned as SMILES."
        );
        return null;
      }

      return smiles;
    } catch (err) {
      console.warn("CHEMSTRUCTURE: Could not read SMILES from Ketcher:", err);
      return null;
    }
  }

  async function syncSmilesFromKetcher() {
    var input = document.getElementById("chemstructure-smiles");

    if (!input) {
      return null;
    }

    var smiles = await getSmilesFromKetcherSilently();

    if (!smiles) {
      return null;
    }

    if (smiles === chemstructureLastSmiles) {
      return smiles;
    }

    chemstructureLastSmiles = smiles;
    input.value = smiles;

    console.log("CHEMSTRUCTURE: SMILES auto-updated:", smiles);

    return smiles;
  }

  function startKetcherAutoSync() {
    var iframe = document.getElementById("ketcher-frame");

    if (!iframe) {
      return;
    }

    window.clearInterval(chemstructureAutoSyncTimer);

    chemstructureAutoSyncTimer = window.setInterval(function () {
      syncSmilesFromKetcher();
    }, 700);

    console.log("CHEMSTRUCTURE: Ketcher auto-sync started");
  }

  function getSelectedSearchMode() {
    var selected = document.querySelector(
      'input[name="chemstructure-search-mode"]:checked'
    );

    return selected ? selected.value : DEFAULT_MODE;
  }

  function restoreSelectedSearchMode(mode) {
    var safeMode = mode || DEFAULT_MODE;

    var selected = document.querySelector(
      'input[name="chemstructure-search-mode"][value="' + safeMode + '"]'
    );

    if (selected) {
      selected.checked = true;
      return;
    }

    var fallback = document.querySelector(
      'input[name="chemstructure-search-mode"][value="' + DEFAULT_MODE + '"]'
    );

    if (fallback) {
      fallback.checked = true;
    }
  }

  function getSelectedThreshold() {
    var thresholdInput = document.getElementById("chemstructure-threshold");

    if (!thresholdInput) {
      return DEFAULT_THRESHOLD;
    }

    return normalizeThreshold(thresholdInput.value || DEFAULT_THRESHOLD);
  }

  function setThresholdValue(value) {
    var thresholdInput = document.getElementById("chemstructure-threshold");

    if (!thresholdInput) {
      return;
    }

    thresholdInput.value = normalizeThreshold(value || DEFAULT_THRESHOLD);
    updateThresholdValueLabel();
  }

  function updateThresholdValueLabel() {
    var thresholdInput = document.getElementById("chemstructure-threshold");
    var thresholdValue = document.getElementById(
      "chemstructure-threshold-value"
    );

    if (!thresholdInput || !thresholdValue) {
      return;
    }

    thresholdValue.textContent = normalizeThreshold(thresholdInput.value);
  }

  function updateThresholdVisibility() {
    var mode = getSelectedSearchMode();
    var wrapper = document.getElementById("chemstructure-threshold-wrapper");

    if (!wrapper) {
      return;
    }

    wrapper.style.display = mode === "similarity" ? "flex" : "none";
  }

  function saveLastStructureSearch(query, mode, threshold) {
    try {
      window.localStorage.setItem(CHEMSTRUCTURE_LAST_QUERY_KEY, query || "");
      window.localStorage.setItem(
        CHEMSTRUCTURE_LAST_MODE_KEY,
        mode || DEFAULT_MODE
      );
      window.localStorage.setItem(
        CHEMSTRUCTURE_LAST_THRESHOLD_KEY,
        normalizeThreshold(threshold || DEFAULT_THRESHOLD)
      );
    } catch (err) {
      console.warn("CHEMSTRUCTURE: Could not save last search:", err);
    }
  }

  function clearLastStructureSearch() {
    try {
      window.localStorage.removeItem(CHEMSTRUCTURE_LAST_QUERY_KEY);
      window.localStorage.removeItem(CHEMSTRUCTURE_LAST_MODE_KEY);
      window.localStorage.removeItem(CHEMSTRUCTURE_LAST_THRESHOLD_KEY);
    } catch (err) {
      console.warn("CHEMSTRUCTURE: Could not clear last search:", err);
    }
  }

  function getStructureSearchFromUrl() {
    var params = new URLSearchParams(window.location.search);

    var query = params.get("structure_query");
    var mode = params.get("structure_mode");
    var threshold = params.get("threshold");

    if (!query) {
      return null;
    }

    return {
      query: query,
      mode: mode || DEFAULT_MODE,
      threshold: normalizeThreshold(threshold || DEFAULT_THRESHOLD)
    };
  }

  function getLastStructureSearch() {
    var fromUrl = getStructureSearchFromUrl();

    if (fromUrl) {
      return fromUrl;
    }

    try {
      var query = window.localStorage.getItem(CHEMSTRUCTURE_LAST_QUERY_KEY);
      var mode = window.localStorage.getItem(CHEMSTRUCTURE_LAST_MODE_KEY);
      var threshold = window.localStorage.getItem(
        CHEMSTRUCTURE_LAST_THRESHOLD_KEY
      );

      if (!query) {
        return null;
      }

      return {
        query: query,
        mode: mode || DEFAULT_MODE,
        threshold: normalizeThreshold(threshold || DEFAULT_THRESHOLD)
      };
    } catch (err) {
      console.warn("CHEMSTRUCTURE: Could not read last search:", err);
      return null;
    }
  }

  function redirectToMoleculeStructureSearch(query, mode) {
    var threshold = mode === "similarity" ? getSelectedThreshold() : "";

    saveLastStructureSearch(query, mode, threshold);

    var params = new URLSearchParams();

    params.set("structure_query", query);
    params.set("structure_mode", mode);
    params.set("sort", "title_string asc");

    if (mode === "similarity") {
      params.set("threshold", threshold);
    }

    console.log(
      "CHEMSTRUCTURE: Redirecting to molecule search:",
      query,
      mode,
      threshold
    );

    window.location.href = "/molecule?" + params.toString();
  }

  async function restoreMoleculeInKetcher(smiles) {
    var iframe = document.getElementById("ketcher-frame");

    if (!iframe || !smiles) {
      return;
    }

    var attempts = 0;

    var timer = window.setInterval(async function () {
      attempts += 1;

      if (!iframe.contentWindow || !iframe.contentWindow.ketcher) {
        if (attempts >= 20) {
          window.clearInterval(timer);
        }
        return;
      }

      try {
        var ketcher = iframe.contentWindow.ketcher;

        if (typeof ketcher.setMolecule === "function") {
          await ketcher.setMolecule(smiles);

          window.setTimeout(function () {
            try {
              var currentKetcher =
                iframe.contentWindow && iframe.contentWindow.ketcher;

              if (
                currentKetcher &&
                currentKetcher.editor &&
                typeof currentKetcher.editor.zoom === "function"
              ) {
                currentKetcher.editor.zoom(1.0);
              }

              if (
                currentKetcher &&
                currentKetcher.editor &&
                currentKetcher.editor.render &&
                typeof currentKetcher.editor.render.update === "function"
              ) {
                currentKetcher.editor.render.update();
              }
            } catch (err) {
              console.warn("CHEMSTRUCTURE: Could not reset Ketcher zoom:", err);
            }
          }, 300);

          chemstructureLastSmiles = smiles;
          window.clearInterval(timer);

          console.log("CHEMSTRUCTURE: Restored molecule in Ketcher:", smiles);
        }
      } catch (err) {
        console.warn(
          "CHEMSTRUCTURE: Could not restore molecule in Ketcher:",
          err
        );
        window.clearInterval(timer);
      }

      if (attempts >= 20) {
        window.clearInterval(timer);
      }
    }, 300);
  }

  function restoreLastStructureSearch() {
    var lastSearch = getLastStructureSearch();

    if (!lastSearch || !lastSearch.query) {
      updateThresholdVisibility();
      updateThresholdValueLabel();
      return;
    }

    var input = document.getElementById("chemstructure-smiles");

    if (input) {
      input.value = lastSearch.query;
      chemstructureLastSmiles = lastSearch.query;
    }

    restoreSelectedSearchMode(lastSearch.mode);
    setThresholdValue(lastSearch.threshold);

    updateThresholdVisibility();
    updateThresholdValueLabel();

    restoreMoleculeInKetcher(lastSearch.query);
  }

  async function runSearch(modeOverride) {
    var input = document.getElementById("chemstructure-smiles");

    if (!input) {
      showMessage("SMILES / SMARTS input field was not found.", "danger");
      return;
    }

    var smilesFromKetcher = await getSmilesFromKetcherSilently();

    if (smilesFromKetcher) {
      input.value = smilesFromKetcher;
      chemstructureLastSmiles = smilesFromKetcher;
    }

    var mode = modeOverride || getSelectedSearchMode();
    var query = input.value.trim();

    if (!query || isMolfileLike(query)) {
      showMessage(
        "Please draw a structure in Ketcher or paste a valid SMILES/SMARTS query first.",
        "warning"
      );
      return;
    }

    redirectToMoleculeStructureSearch(query, mode);
  }

  function clearKetcher() {
    var iframe = document.getElementById("ketcher-frame");

    if (
      iframe &&
      iframe.contentWindow &&
      iframe.contentWindow.ketcher &&
      typeof iframe.contentWindow.ketcher.setMolecule === "function"
    ) {
      try {
        iframe.contentWindow.ketcher.setMolecule("");
      } catch (err) {
        console.warn("CHEMSTRUCTURE: Could not clear Ketcher:", err);
      }
    }
  }

  function clearSearchUi() {
    var input = document.getElementById("chemstructure-smiles");
    var message = document.getElementById("chemstructure-message");

    if (input) {
      input.value = "";
    }

    chemstructureLastSmiles = "";

    clearLastStructureSearch();
    clearKetcher();

    restoreSelectedSearchMode(DEFAULT_MODE);
    setThresholdValue(DEFAULT_THRESHOLD);
    updateThresholdVisibility();
    updateThresholdValueLabel();

    if (message) {
      message.innerHTML = "";
    }
  }

  function bindThresholdEvents() {
    var thresholdInput = document.getElementById("chemstructure-threshold");
    var modeRadios = document.querySelectorAll(
      'input[name="chemstructure-search-mode"]'
    );

    if (thresholdInput) {
      thresholdInput.addEventListener("input", function () {
        updateThresholdValueLabel();
      });

      thresholdInput.addEventListener("change", function () {
        updateThresholdValueLabel();
      });
    }

    Array.prototype.forEach.call(modeRadios, function (radio) {
      radio.addEventListener("change", function () {
        updateThresholdVisibility();
        updateThresholdValueLabel();
      });
    });

    updateThresholdVisibility();
    updateThresholdValueLabel();
  }

  function getRenderImageUrl() {
    var input = document.getElementById("chemstructure-render-image-url");

    if (input && input.value) {
      return input.value;
    }

    return DEFAULT_RENDER_IMAGE_URL;
  }

  function extractBase64ImageFromResponse(payload) {
    /*
     * Supported backend return formats:
     *
     * 1. Raw base64 string:
     *    iVBORw0KGgo...
     *
     * 2. CKAN action JSON:
     *    { success: true, result: "iVBORw0KGgo..." }
     *
     * 3. CKAN action JSON object:
     *    { success: true, result: { image: "iVBORw0KGgo..." } }
     *
     * 4. Already complete data URL:
     *    data:image/png;base64,iVBORw0KGgo...
     */

    if (!payload) {
      return null;
    }

    if (typeof payload === "string") {
      return payload;
    }

    if (payload.result) {
      if (typeof payload.result === "string") {
        return payload.result;
      }

      if (payload.result.image) {
        return payload.result.image;
      }

      if (payload.result.image_base64) {
        return payload.result.image_base64;
      }

      if (payload.result.png) {
        return payload.result.png;
      }

      if (payload.result.svg) {
        return payload.result.svg;
      }
    }

    if (payload.image) {
      return payload.image;
    }

    if (payload.image_base64) {
      return payload.image_base64;
    }

    return null;
  }

  function setImageElementSource(img, imageValue) {
    if (!img || !imageValue) {
      return;
    }

    if (
      imageValue.indexOf("data:image/") === 0 ||
      imageValue.indexOf("<svg") !== -1 ||
      imageValue.indexOf("<?xml") !== -1
    ) {
      if (imageValue.indexOf("<svg") !== -1 || imageValue.indexOf("<?xml") !== -1) {
        var container = document.getElementById(
          "chemstructure-active-query-image"
        );

        if (container) {
          container.innerHTML = imageValue;
          container.style.display = "block";
        }

        return;
      }

      img.src = imageValue;
      return;
    }

    img.src = "data:image/png;base64," + imageValue;
  }

  async function renderActiveStructureImage() {
    var container = document.getElementById("chemstructure-active-query-image");

    if (!container) {
      return;
    }

    var structureSearch = getStructureSearchFromUrl();

    if (!structureSearch || !structureSearch.query) {
      return;
    }

    var query = structureSearch.query;

    container.style.display = "block";
    container.innerHTML =
      '<div class="chemstructure-active-query-image__loading">' +
      "Rendering structure..." +
      "</div>";

    try {
      var renderUrl = getRenderImageUrl();

      var response = await fetch(renderUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          smiles: query,
          structure_query: query,
          query: query
        })
      });

      if (!response.ok) {
        throw new Error("Image render request failed: HTTP " + response.status);
      }

      var contentType = response.headers.get("content-type") || "";
      var imageValue = null;

      if (contentType.indexOf("application/json") !== -1) {
        var payload = await response.json();
        imageValue = extractBase64ImageFromResponse(payload);
      } else {
        imageValue = await response.text();
      }

      if (!imageValue) {
        throw new Error("Image render response did not contain image data.");
      }

      container.innerHTML = "";

      var img = document.createElement("img");
      img.alt = "Structure query";
      img.className = "chemstructure-active-query-image__img";

      container.appendChild(img);
      setImageElementSource(img, imageValue);

      console.log("CHEMSTRUCTURE: Rendered active structure image.");
    } catch (err) {
      console.warn("CHEMSTRUCTURE: Could not render active structure image:", err);

      container.innerHTML =
        '<div class="chemstructure-active-query-image__error">' +
        "Image unavailable" +
        "</div>";
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    var searchBtn = document.getElementById("chemstructure-search");
    var clearBtn = document.getElementById("chemstructure-clear");
    var modal = document.getElementById("chemstructure-home-modal");

    if (searchBtn) {
      searchBtn.addEventListener("click", function (event) {
        event.preventDefault();

        var mode = getSelectedSearchMode();
        runSearch(mode);
      });
    }

    if (clearBtn) {
      clearBtn.addEventListener("click", function (event) {
        event.preventDefault();
        clearSearchUi();
      });
    }

    bindThresholdEvents();

    /*
     * Render active structure image on /molecule result page.
     * This now comes from backend RDKit rendering, not from Ketcher.
     */
    renderActiveStructureImage();

    /*
     * Start auto-sync only if Ketcher iframe exists.
     */
    startKetcherAutoSync();

    /*
     * Restore previous query/mode/threshold.
     */
    restoreLastStructureSearch();

    /*
     * When Bootstrap modal opens, Ketcher may become ready only then.
     */
    if (modal && window.jQuery) {
      window.jQuery(modal).on("shown.bs.modal", function () {
        startKetcherAutoSync();
        restoreLastStructureSearch();
      });
    }
  });
})();