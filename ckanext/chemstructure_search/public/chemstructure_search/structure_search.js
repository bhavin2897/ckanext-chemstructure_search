(function () {
  function showMessage(message, type) {
    var el = document.getElementById("chemstructure-message");

    if (!el) {
      console.log("CHEMSTRUCTURE:", message);
      return;
    }

    el.innerHTML =
      '<div class="alert alert-' + (type || "info") + '">' +
      message +
      "</div>";
  }

  function clearResults() {
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
      emptyState.innerHTML = "Searching molecule packages...";
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
        '<td>' +
          '<a href="' + moleculeUrl + '" target="_blank" rel="noopener noreferrer">' +
            '<strong>' + escapeHtml(moleculeName) + '</strong>' +
          '</a>' +
        '</td>' +
        '<td>' + escapeHtml(item.title || "") + '</td>' +
        '<td>' + escapeHtml(item.canonical_smiles || "") + '</td>';

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

  async function getSmilesFromKetcher() {
    var iframe = document.getElementById("ketcher-frame");

    if (!iframe || !iframe.contentWindow || !iframe.contentWindow.ketcher) {
      showMessage("Ketcher is not ready yet. Please try again after the editor has loaded.", "warning");
      return;
    }

    try {
      var smiles = await iframe.contentWindow.ketcher.getSmiles();
      var input = document.getElementById("chemstructure-smiles");

      if (!input) {
        showMessage("SMILES input field was not found.", "danger");
        return;
      }

      input.value = smiles;
      showMessage("SMILES exported from Ketcher.", "success");
    } catch (err) {
      console.error("Could not export SMILES from Ketcher:", err);
      showMessage("Could not export SMILES from Ketcher.", "danger");
    }
  }

  async function runSearch() {
    clearResults();

    var input = document.getElementById("chemstructure-smiles");
    var modeSelect = document.getElementById("chemstructure-mode");

    if (!input) {
      showMessage("SMILES / SMARTS input field was not found.", "danger");
      return;
    }

    var smiles = input.value.trim();
    var mode = modeSelect ? modeSelect.value : "exact";

    if (!smiles) {
      showMessage("Please provide a SMILES or SMARTS query first.", "warning");
      renderResults([]);
      return;
    }

    showMessage("Searching molecule packages...", "info");

    try {
      var response = await fetch("/api/3/action/chemstructure_rdkit_search", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          query: smiles,
          mode: mode,
          threshold: 0.7,
          rows: 50
        })
      });

      var payload = await response.json();

      if (!payload.success) {
        console.error("CHEMSTRUCTURE search failed:", payload);
        showMessage("Search failed. Please check the query or CKAN logs.", "danger");
        renderResults([]);
        return;
      }

      var result = payload.result || {};
      renderResults(result.results || []);

      showMessage(
        "Found " + (result.count || 0) + " matching molecule(s).",
        "success"
      );
    } catch (err) {
      console.error("CHEMSTRUCTURE search request failed:", err);
      showMessage("Search request failed.", "danger");
      renderResults([]);
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    var getSmilesBtn = document.getElementById("chemstructure-get-smiles");
    var searchBtn = document.getElementById("chemstructure-search");

    if (getSmilesBtn) {
      getSmilesBtn.addEventListener("click", function (event) {
        event.preventDefault();
        getSmilesFromKetcher();
      });
    }

    if (searchBtn) {
      searchBtn.addEventListener("click", function (event) {
        event.preventDefault();
        runSearch();
      });
    }
  });
})();