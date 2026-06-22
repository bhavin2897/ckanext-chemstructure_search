(function () {
  var chemstructureAutoSyncTimer = null;
  var chemstructureLastSmiles = "";

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

  function clearResults(searching) {
    var table = document.getElementById("chemstructure-results-table");
    var body = document.getElementById("chemstructure-results-body");
    var emptyState = document.getElementById("chemstructure-empty-state");

    if (body) {
      body.innerHTML = "";
    }

    if (table) {
      table.style.display = "none";
    }

    if (emptyState) {
      emptyState.style.display = "block";
      emptyState.innerHTML = searching
        ? "Searching molecules..."
        : "No search has been executed yet.";
    }
  }

  function renderResults(results) {
    var table = document.getElementById("chemstructure-results-table");
    var body = document.getElementById("chemstructure-results-body");
    var emptyState = document.getElementById("chemstructure-empty-state");

    if (!table || !body || !emptyState) {
      console.error("CHEMSTRUCTURE: results elements missing");
      return;
    }

    body.innerHTML = "";

    if (!results || !results.length) {
      table.style.display = "none";
      emptyState.style.display = "block";
      emptyState.innerHTML = "No matching molecule(s) found.";
      return;
    }

    results.forEach(function (item) {
      var row = document.createElement("tr");

      var moleculeId = item.id || item.name || "";
      var moleculeName = item.name || item.id || "";
      var moleculeUrl = "/molecule/" + encodeURIComponent(moleculeId);

      row.innerHTML =
        "<td>" +
          '<a href="' + moleculeUrl + '" target="_blank" rel="noopener noreferrer">' +
            "<strong>" + escapeHtml(moleculeName) + "</strong>" +
          "</a>" +
        "</td>" +
        "<td>" + escapeHtml(item.title || "") + "</td>" +
        "<td>" + escapeHtml(item.canonical_smiles || "") + "</td>";

      body.appendChild(row);
    });

    emptyState.style.display = "none";
    table.style.display = "table";
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
    var params = new URLSearchParams();

    params.set("structure_query", query);
    params.set("structure_mode", mode);
    params.set("sort", "title_string asc");

    if (mode === "similarity") {
      params.set("threshold", "0.25");
    }

    window.location.href = "/molecule?" + params.toString();
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
    var table = document.getElementById("chemstructure-results-table");
    var body = document.getElementById("chemstructure-results-body");
    var emptyState = document.getElementById("chemstructure-empty-state");
    var message = document.getElementById("chemstructure-message");

    if (input) {
      input.value = "";
    }

    chemstructureLastSmiles = "";

    if (body) {
      body.innerHTML = "";
    }

    if (table) {
      table.style.display = "none";
    }

    if (emptyState) {
      emptyState.style.display = "block";
      emptyState.innerHTML = "No search has been executed yet.";
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
      });
    }

    /*
     * Fallback for the full-page mode where there is no modal.
     */
    startKetcherAutoSync();
  });
})();