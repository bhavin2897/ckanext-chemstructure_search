(function () {
  var chemstructureAutoSyncTimer = null;
  var chemstructureLastSmiles = "";

  var CHEMSTRUCTURE_LAST_QUERY_KEY = "chemstructure_last_query";
  var CHEMSTRUCTURE_LAST_MODE_KEY = "chemstructure_last_mode";
  var CHEMSTRUCTURE_LAST_THRESHOLD_KEY = "chemstructure_last_threshold";
  var CHEMSTRUCTURE_QUERY_IMAGE_KEY = "chemstructure_query_image";

  var DEFAULT_MODE = "similarity";
  var DEFAULT_THRESHOLD = "0.25";

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

    if (!iframe) {
      return null;
    }

    if (!iframe.contentWindow || !iframe.contentWindow.ketcher) {
      return null;
    }

    try {
      var smiles = await iframe.contentWindow.ketcher.getSmiles();

      if (!smiles || !smiles.trim()) {
        return null;
      }

      return smiles.trim();
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
    var thresholdValue = document.getElementById("chemstructure-threshold-value");

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
      window.sessionStorage.removeItem(CHEMSTRUCTURE_QUERY_IMAGE_KEY);
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

  function normalizeKetcherImage(image) {
    return new Promise(function (resolve) {
      if (!image) {
        resolve(null);
        return;
      }

      /*
       * Case 1: Ketcher returns SVG string or data URL string.
       */
      if (typeof image === "string") {
        resolve(image);
        return;
      }

      /*
       * Case 2: Ketcher returns Blob.
       */
      if (window.Blob && image instanceof Blob) {
        var reader = new FileReader();

        reader.onload = function () {
          resolve(reader.result);
        };

        reader.onerror = function () {
          console.warn("CHEMSTRUCTURE: Could not read Ketcher image blob.");
          resolve(null);
        };

        reader.readAsDataURL(image);
        return;
      }

      console.warn("CHEMSTRUCTURE: Unsupported Ketcher image format:", image);
      resolve(null);
    });
  }

  async function getStructureImageFromKetcherSilently() {
    var iframe = document.getElementById("ketcher-frame");

    if (!iframe || !iframe.contentWindow || !iframe.contentWindow.ketcher) {
      console.warn("CHEMSTRUCTURE: Ketcher iframe is not ready for image export.");
      return null;
    }

    try {
      var ketcher = iframe.contentWindow.ketcher;

      /*
       * Ketcher builds differ. Try SVG first, then PNG.
       * Image export is optional. Search must still work if this fails.
       */
      if (typeof ketcher.generateImage === "function") {
        try {
          var svgImage = await ketcher.generateImage("svg");

          if (svgImage) {
            console.log("CHEMSTRUCTURE: Ketcher SVG image exported.");
            return await normalizeKetcherImage(svgImage);
          }
        } catch (svgErr) {
          console.warn("CHEMSTRUCTURE: SVG export failed, trying PNG:", svgErr);
        }

        try {
          var pngImage = await ketcher.generateImage("png");

          if (pngImage) {
            console.log("CHEMSTRUCTURE: Ketcher PNG image exported.");
            return await normalizeKetcherImage(pngImage);
          }
        } catch (pngErr) {
          console.warn("CHEMSTRUCTURE: PNG export failed:", pngErr);
        }
      }

      console.warn("CHEMSTRUCTURE: ketcher.generateImage is not available.");
    } catch (err) {
      console.warn("CHEMSTRUCTURE: Could not export Ketcher image:", err);
    }

    return null;
  }

  async function saveStructureImageForResultPage() {
    try {
      var image = await getStructureImageFromKetcherSilently();

      if (!image) {
        console.warn("CHEMSTRUCTURE: No query image was exported.");
        window.sessionStorage.removeItem(CHEMSTRUCTURE_QUERY_IMAGE_KEY);
        return;
      }

      window.sessionStorage.setItem(CHEMSTRUCTURE_QUERY_IMAGE_KEY, image);

      console.log(
        "CHEMSTRUCTURE: Query image saved for result page. Length:",
        image.length
      );
    } catch (err) {
      console.warn("CHEMSTRUCTURE: Could not save query image:", err);
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
        console.warn("CHEMSTRUCTURE: Could not restore molecule in Ketcher:", err);
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

    /*
     * Always fetch latest SMILES from Ketcher before redirect.
     */
    var smilesFromKetcher = await getSmilesFromKetcherSilently();

    if (smilesFromKetcher) {
      input.value = smilesFromKetcher;
      chemstructureLastSmiles = smilesFromKetcher;
    }

    var mode = modeOverride || getSelectedSearchMode();
    var query = input.value.trim();

    if (!query) {
      showMessage(
        "Please draw a structure in Ketcher or paste a SMILES/SMARTS query first.",
        "warning"
      );
      return;
    }

    /*
     * Image export is optional. Even if image export fails, search continues.
     */
    try {
      await saveStructureImageForResultPage();
    } catch (err) {
      console.warn("CHEMSTRUCTURE: Image export failed, continuing search:", err);
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

  function renderActiveStructureImage() {
    var container = document.getElementById("chemstructure-active-query-image");

    if (!container) {
      return;
    }

    try {
      var image = window.sessionStorage.getItem(CHEMSTRUCTURE_QUERY_IMAGE_KEY);

      if (!image) {
        console.warn("CHEMSTRUCTURE: No query image found in sessionStorage.");
        return;
      }

      container.style.display = "block";
      container.innerHTML = "";

      /*
       * Inline SVG string.
       */
      if (image.indexOf("<svg") !== -1 || image.indexOf("<?xml") !== -1) {
        container.innerHTML = image;
        console.log("CHEMSTRUCTURE: Rendered query SVG image.");
        return;
      }

      /*
       * Data URL, usually PNG.
       */
      var img = document.createElement("img");
      img.src = image;
      img.alt = "Structure query";
      img.className = "chemstructure-active-query-image__img";

      container.appendChild(img);

      console.log("CHEMSTRUCTURE: Rendered query image.");
    } catch (err) {
      console.warn("CHEMSTRUCTURE: Could not render query image:", err);
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
     * Render image on /molecule result page if available.
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