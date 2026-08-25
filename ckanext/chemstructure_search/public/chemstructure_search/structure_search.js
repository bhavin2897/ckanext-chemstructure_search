(function () {
  var chemstructureAutoSyncTimer = null;
  var chemstructureLastSmiles = "";
  var chemstructureSearchInProgress = false;
  var chemstructureKetcherMutationInProgress = false;
  var chemstructureClearInProgress = false;

  var CHEMSTRUCTURE_LAST_QUERY_KEY = "chemstructure_last_query";
  var CHEMSTRUCTURE_LAST_MODE_KEY = "chemstructure_last_mode";
  var CHEMSTRUCTURE_LAST_THRESHOLD_KEY = "chemstructure_last_threshold";
  var CHEMSTRUCTURE_LAST_KET_KEY = "chemstructure_last_ket";

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

  function setSearchLoading(isLoading) {
    var searchBtn = document.getElementById("chemstructure-search");
    var loadingOverlay = document.getElementById(
      "chemstructure-search-loading"
    );

    chemstructureSearchInProgress = isLoading;

    if (searchBtn) {
      var originalLabel = searchBtn.getAttribute(
        "data-chemstructure-original-label"
      );

      if (!originalLabel) {
        originalLabel = searchBtn.innerHTML;
        searchBtn.setAttribute(
          "data-chemstructure-original-label",
          originalLabel
        );
      }

      searchBtn.disabled = isLoading;
      searchBtn.setAttribute("aria-busy", isLoading ? "true" : "false");
      searchBtn.innerHTML = isLoading
        ? '<i class="fa fa-spinner fa-spin" aria-hidden="true"></i> Searching&hellip;'
        : originalLabel;
    }

    if (loadingOverlay) {
      loadingOverlay.hidden = !isLoading;
      loadingOverlay.setAttribute("aria-hidden", isLoading ? "false" : "true");
    }
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

  async function getStructureFromKetcherSilently(mode) {
    var iframe = document.getElementById("ketcher-frame");

    if (!iframe || !iframe.contentWindow || !iframe.contentWindow.ketcher) {
      return null;
    }

    try {
      var ketcher = iframe.contentWindow.ketcher;
      var format = mode === "substructure" ? "SMARTS" : "SMILES";
      var structure;

      if (mode === "substructure") {
        if (typeof ketcher.getSmarts !== "function") {
          console.warn(
            "CHEMSTRUCTURE: This Ketcher version does not support SMARTS export."
          );
          return null;
        }

        structure = await ketcher.getSmarts();
      } else {
        structure = await ketcher.getSmiles();
      }

      if (!structure || !structure.trim()) {
        return "";
      }

      structure = structure.trim();

      if (isMolfileLike(structure)) {
        console.warn(
          "CHEMSTRUCTURE: Ignoring molfile-like value returned as " +
            format +
            "."
        );
        return null;
      }

      return structure;
    } catch (err) {
      console.warn(
        "CHEMSTRUCTURE: Could not read " +
          (mode === "substructure" ? "SMARTS" : "SMILES") +
          " from Ketcher:",
        err
      );
      return null;
    }
  }

  async function getKetFromKetcherSilently() {
    var iframe = document.getElementById("ketcher-frame");
    var ketcher = iframe && iframe.contentWindow
      ? iframe.contentWindow.ketcher
      : null;

    if (!ketcher || typeof ketcher.getKet !== "function") {
      return null;
    }

    try {
      return await ketcher.getKet();
    } catch (err) {
      console.warn("CHEMSTRUCTURE: Could not preserve Ketcher document:", err);
      return null;
    }
  }

  async function syncSmilesFromKetcher() {
    var input = document.getElementById("chemstructure-smiles");

    if (!input || chemstructureKetcherMutationInProgress) {
      return null;
    }

    var mode = getSelectedSearchMode();
    var smiles = await getStructureFromKetcherSilently(mode);

    if (smiles === null) {
      return null;
    }

    if (!smiles) {
      if (chemstructureLastSmiles) {
        resetStructureQueryState();
      }
      return "";
    }

    if (smiles === chemstructureLastSmiles) {
      return smiles;
    }

    chemstructureLastSmiles = smiles;
    input.value = smiles;

    console.log(
      "CHEMSTRUCTURE: " +
        (mode === "substructure" ? "SMARTS" : "SMILES") +
        " auto-updated:",
      smiles
    );

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
      ".chemstructure-search-mode-input:checked"
    );

    return selected ? selected.value : DEFAULT_MODE;
  }

  function restoreSelectedSearchMode(mode) {
    var safeMode = mode || DEFAULT_MODE;

    var selected = document.querySelector(
      '.chemstructure-search-mode-input[value="' + safeMode + '"]'
    );

    if (selected) {
      uncheckOtherSearchModes(selected);
      selected.checked = true;
      return;
    }

    var fallback = document.querySelector(
      '.chemstructure-search-mode-input[value="' + DEFAULT_MODE + '"]'
    );

    if (fallback) {
      uncheckOtherSearchModes(fallback);
      fallback.checked = true;
    }
  }

  function uncheckOtherSearchModes(selected) {
    var modeRadios = document.querySelectorAll(
      ".chemstructure-search-mode-input"
    );

    Array.prototype.forEach.call(modeRadios, function (radio) {
      if (radio !== selected) {
        radio.checked = false;
      }
    });
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

  function saveLastStructureSearch(query, mode, threshold, ket) {
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
      if (ket) {
        window.localStorage.setItem(CHEMSTRUCTURE_LAST_KET_KEY, ket);
      } else {
        window.localStorage.removeItem(CHEMSTRUCTURE_LAST_KET_KEY);
      }
    } catch (err) {
      console.warn("CHEMSTRUCTURE: Could not save last search:", err);
    }
  }

  function clearLastStructureSearch() {
    try {
      window.localStorage.removeItem(CHEMSTRUCTURE_LAST_QUERY_KEY);
      window.localStorage.removeItem(CHEMSTRUCTURE_LAST_MODE_KEY);
      window.localStorage.removeItem(CHEMSTRUCTURE_LAST_THRESHOLD_KEY);
      window.localStorage.removeItem(CHEMSTRUCTURE_LAST_KET_KEY);
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
      try {
        if (
          window.localStorage.getItem(CHEMSTRUCTURE_LAST_QUERY_KEY) ===
          fromUrl.query
        ) {
          fromUrl.ket = window.localStorage.getItem(CHEMSTRUCTURE_LAST_KET_KEY);
        }
      } catch (err) {
        console.warn("CHEMSTRUCTURE: Could not restore Ketcher document:", err);
      }
      return fromUrl;
    }

    try {
      var query = window.localStorage.getItem(CHEMSTRUCTURE_LAST_QUERY_KEY);
      var mode = window.localStorage.getItem(CHEMSTRUCTURE_LAST_MODE_KEY);
      var threshold = window.localStorage.getItem(
        CHEMSTRUCTURE_LAST_THRESHOLD_KEY
      );
      var ket = window.localStorage.getItem(CHEMSTRUCTURE_LAST_KET_KEY);

      if (!query) {
        return null;
      }

      return {
        query: query,
        mode: mode || DEFAULT_MODE,
        threshold: normalizeThreshold(threshold || DEFAULT_THRESHOLD),
        ket: ket
      };
    } catch (err) {
      console.warn("CHEMSTRUCTURE: Could not read last search:", err);
      return null;
    }
  }

  function redirectToMoleculeStructureSearch(query, mode, ket) {
    var threshold = mode === "similarity" ? getSelectedThreshold() : "";

    saveLastStructureSearch(query, mode, threshold, ket);

    var params = new URLSearchParams();

    params.set("structure_query", query);
    params.set("structure_mode", mode);
    /*
     * A structure query is an active search, so do not inherit the
     * name-ascending default used by the empty molecule listing.
     */
    params.set("sort", "score desc, metadata_modified desc");

    if (mode === "similarity") {
      params.set("threshold", threshold);
    }

    console.log(
      "CHEMSTRUCTURE: Redirecting to molecule search:",
      query,
      mode,
      threshold
    );

    var destination = "/molecule?" + params.toString();

    /*
     * Give the browser a chance to paint the loading UI before starting the
     * potentially slow, server-rendered molecule search.
     */
    window.requestAnimationFrame(function () {
      window.requestAnimationFrame(function () {
        window.location.href = destination;
      });
    });
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
          chemstructureKetcherMutationInProgress = true;

          try {
            await ketcher.setMolecule(smiles);
          } finally {
            chemstructureKetcherMutationInProgress = false;
          }

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

    restoreMoleculeInKetcher(lastSearch.ket || lastSearch.query);
  }

  async function runSearch(modeOverride) {
    if (chemstructureSearchInProgress) {
      return;
    }

    var input = document.getElementById("chemstructure-smiles");

    if (!input) {
      showMessage("SMILES / SMARTS input field was not found.", "danger");
      return;
    }

    var mode = modeOverride || getSelectedSearchMode();
    var structureFromKetcher = null;
    var ketFromKetcher = null;

    if (!chemstructureClearInProgress) {
      structureFromKetcher = await getStructureFromKetcherSilently(mode);
      ketFromKetcher = await getKetFromKetcherSilently();
    }

    if (mode === "substructure" && structureFromKetcher === null) {
      showMessage(
        "SMARTS export is not available in this Ketcher version.",
        "danger"
      );
      return;
    }

    if (structureFromKetcher) {
      input.value = structureFromKetcher;
      chemstructureLastSmiles = structureFromKetcher;
    }

    var query = input.value.trim();

    if (!query || isMolfileLike(query)) {
      showMessage(
        "Please draw a structure in Ketcher or paste a valid SMILES/SMARTS query first.",
        "warning"
      );
      return;
    }

    setSearchLoading(true);
    redirectToMoleculeStructureSearch(query, mode, ketFromKetcher);
  }

  async function clearKetcher() {
    var iframe = document.getElementById("ketcher-frame");

    if (
      iframe &&
      iframe.contentWindow &&
      iframe.contentWindow.ketcher
    ) {
      try {
        var ketcher = iframe.contentWindow.ketcher;

        if (ketcher.editor && typeof ketcher.editor.clear === "function") {
          await ketcher.editor.clear();
        } else if (typeof ketcher.setMolecule === "function") {
          await ketcher.setMolecule("");
        }
      } catch (err) {
        console.warn("CHEMSTRUCTURE: Could not clear Ketcher:", err);
      }
    }
  }

  function resetStructureQueryState() {
    var input = document.getElementById("chemstructure-smiles");

    if (input) {
      input.value = "";
    }

    chemstructureLastSmiles = "";

    clearLastStructureSearch();
  }

  async function clearStructureSearch() {
    var message = document.getElementById("chemstructure-message");

    chemstructureClearInProgress = true;
    chemstructureKetcherMutationInProgress = true;
    resetStructureQueryState();

    try {
      await clearKetcher();
    } finally {
      /* Discard any value observed while Ketcher was completing its clear. */
      resetStructureQueryState();
      chemstructureKetcherMutationInProgress = false;
      chemstructureClearInProgress = false;
    }

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
      ".chemstructure-search-mode-input"
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
        uncheckOtherSearchModes(radio);
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
          query: query,
          mode: structureSearch.mode,
          structure_mode: structureSearch.mode
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
    var activeClearBtn = document.querySelector(
      ".chemstructure-active-search__clear"
    );
    var activeSearchReopen = document.querySelector(
      ".chemstructure-active-search__reopen"
    );
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
        clearStructureSearch();
      });
    }

    if (activeClearBtn) {
      activeClearBtn.addEventListener("click", function () {
        clearStructureSearch();
      });
    }

    if (activeSearchReopen) {
      activeSearchReopen.addEventListener("keydown", function (event) {
        if (event.key !== "Enter" && event.key !== " ") {
          return;
        }

        event.preventDefault();
        activeSearchReopen.click();
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

  /*
   * A page restored from the back-forward cache retains its DOM state.
   * Make the search controls usable again when the user returns.
   */
  window.addEventListener("pageshow", function () {
    setSearchLoading(false);
  });
})();
