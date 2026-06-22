(function () {
  var chemstructureAutoSyncTimer = null;
  var chemstructureLastSmiles = "";
  var CHEMSTRUCTURE_LAST_QUERY_KEY = "chemstructure_last_query";
  var CHEMSTRUCTURE_LAST_MODE_KEY = "chemstructure_last_mode";
  var CHEMSTRUCTURE_LAST_THRESHOLD_KEY = "chemstructure_last_threshold";

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

    return selected ? selected.value : "similarity";
  }

  function redirectToMoleculeStructureSearch(query, mode) {
    var threshold = mode === "similarity" ? "0.25" : "";

    saveLastStructureSearch(query, mode, threshold);

    var params = new URLSearchParams();

    params.set("structure_query", query);
    params.set("structure_mode", mode);
    params.set("sort", "title_string asc");

    if (mode === "similarity") {
      params.set("threshold", threshold);
    }

    window.location.href = "/molecule?" + params.toString();
  }

  function saveLastStructureSearch(query, mode, threshold) {
    try {
      window.localStorage.setItem(CHEMSTRUCTURE_LAST_QUERY_KEY, query || "");
      window.localStorage.setItem(CHEMSTRUCTURE_LAST_MODE_KEY, mode || "similarity");
      window.localStorage.setItem(CHEMSTRUCTURE_LAST_THRESHOLD_KEY, threshold || "0.25");
    } catch (err) {
      console.warn("CHEMSTRUCTURE: Could not save last search:", err);
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
      mode: mode || "similarity",
      threshold: threshold || "0.25"
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
      var threshold = window.localStorage.getItem(CHEMSTRUCTURE_LAST_THRESHOLD_KEY);

      if (!query) {
        return null;
      }

      return {
        query: query,
        mode: mode || "similarity",
        threshold: threshold || "0.25"
      };
    } catch (err) {
      console.warn("CHEMSTRUCTURE: Could not read last search:", err);
      return null;
    }
    }

  function restoreSelectedSearchMode(mode) {
    var selected = document.querySelector(
      'input[name="chemstructure-search-mode"][value="' + mode + '"]'
    );

    if (selected) {
      selected.checked = true;
    }
  }

  async function restoreMoleculeInKetcher(smiles) {
    var iframe = document.getElementById("ketcher-frame");

    if (!iframe || !smiles) {
      return;
    }

    /*
     * Ketcher may not be ready immediately when the Bootstrap modal opens.
     * Try several times before giving up.
     */
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
        if (typeof iframe.contentWindow.ketcher.setMolecule === "function") {
          await iframe.contentWindow.ketcher.setMolecule(smiles);

          window.setTimeout(function () {
            try {
              var ketcher = iframe.contentWindow && iframe.contentWindow.ketcher;

              if (
                ketcher &&
                ketcher.editor &&
                typeof ketcher.editor.zoom === "function"
              ) {
                ketcher.editor.zoom(1.0);
              }

              if (
                ketcher &&
                ketcher.editor &&
                ketcher.editor.render &&
                typeof ketcher.editor.render.update === "function"
              ) {
                ketcher.editor.render.update();
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
      return;
    }

    var input = document.getElementById("chemstructure-smiles");

    if (input) {
      input.value = lastSearch.query;
      chemstructureLastSmiles = lastSearch.query;
    }

    restoreSelectedSearchMode(lastSearch.mode);
    restoreMoleculeInKetcher(lastSearch.query);
  }

  async function runSearch(modeOverride) {
    var input = document.getElementById("chemstructure-smiles");
    var modeSelect = document.getElementById("chemstructure-mode");

    if (!input) {
      showMessage("SMILES / SMARTS input field was not found.", "danger");
      return;
    }

    /*
     * Before redirecting, always fetch the latest structure from Ketcher.
     * This keeps the URL query in sync with the drawn molecule.
     */
    var smilesFromKetcher = await getSmilesFromKetcherSilently();

    if (smilesFromKetcher) {
      input.value = smilesFromKetcher;
      chemstructureLastSmiles = smilesFromKetcher;
    }

    var mode = modeOverride || (modeSelect ? modeSelect.value : "similarity");
    var query = input.value.trim();
    
    if (!query) {
      showMessage(
        "Please draw a structure in Ketcher or paste a SMILES/SMARTS query first.",
        "warning"
      );
      return;
    }

    redirectToMoleculeStructureSearch(query, mode);
  }

  function clearSearchUi() {
    var input = document.getElementById("chemstructure-smiles");
    var message = document.getElementById("chemstructure-message");
    var iframe = document.getElementById("ketcher-frame");

    if (input) {
      input.value = "";
    }

    chemstructureLastSmiles = "";

    try {
      window.localStorage.removeItem(CHEMSTRUCTURE_LAST_QUERY_KEY);
      window.localStorage.removeItem(CHEMSTRUCTURE_LAST_MODE_KEY);
      window.localStorage.removeItem(CHEMSTRUCTURE_LAST_THRESHOLD_KEY);
    } catch (err) {
      console.warn("CHEMSTRUCTURE: Could not clear last search:", err);
    }

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

    if (message) {
      message.innerHTML = "";
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    var searchBtn = document.getElementById("chemstructure-search");
    var clearBtn = document.getElementById("chemstructure-clear");

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

    /*
     * Start auto-sync when the homepage modal is opened.
     * This is important because Ketcher may not be ready when the page first loads.
     */
    var modal = document.getElementById("chemstructure-home-modal");

    if (modal && window.jQuery) {
    window.jQuery(modal).on("shown.bs.modal", function () {
      startKetcherAutoSync();
      restoreLastStructureSearch();
    });
    }

    /*
     * Fallback for the full-page mode where there is no modal.
     */
    startKetcherAutoSync();
    restoreLastStructureSearch();
  });
})();