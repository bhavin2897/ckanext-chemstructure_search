"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const script = fs.readFileSync(
  path.join(__dirname, "../public/chemstructure_search/structure_search.js"),
  "utf8"
);

function element(value) {
  return {
    value: value || "",
    checked: false,
    disabled: false,
    hidden: true,
    innerHTML: "",
    style: {},
    attributes: {},
    listeners: {},
    addEventListener(type, callback) {
      this.listeners[type] = callback;
    },
    getAttribute(name) {
      return this.attributes[name] || null;
    },
    setAttribute(name, value) {
      this.attributes[name] = value;
    }
  };
}

function createPage(options) {
  options = options || {};
  let smiles = options.smiles || "";
  let clearStructure = options.clearStructure || function () {
    smiles = "";
    return Promise.resolve();
  };
  const intervals = [];
  const storage = new Map([
    ["chemstructure_last_query", options.storedSmiles || ""],
    ["chemstructure_last_mode", options.mode || "similarity"],
    ["chemstructure_last_threshold", "0.25"],
    ["chemstructure_last_ket", options.storedKet || ""]
  ]);
  const input = element(options.input || "");
  const message = element();
  const search = element();
  search.innerHTML = "Search";
  const clear = element();
  const threshold = element("0.25");
  const thresholdValue = element();
  const thresholdWrapper = element();
  const overlay = element();
  const modes = ["exact", "similarity", "substructure"].map(
    function (mode) {
      const radio = element(mode);
      radio.checked = mode === (options.mode || "similarity");
      return radio;
    }
  );
  const ketcher = {
    getSmiles: async function () {
      return smiles;
    },
    getSmarts: async function () {
      return options.smarts === undefined ? smiles : options.smarts;
    },
    getKet: async function () {
      return options.ket || "";
    },
    setMolecule: async function (value) {
      smiles = value;
      this.loadedStructure = value;
    },
    editor: {
      clear: async function () {
        await clearStructure();
        smiles = "";
      }
    }
  };
  const elements = {
    "chemstructure-smiles": input,
    "chemstructure-message": message,
    "chemstructure-search": search,
    "chemstructure-clear": clear,
    "chemstructure-threshold": threshold,
    "chemstructure-threshold-value": thresholdValue,
    "chemstructure-threshold-wrapper": thresholdWrapper,
    "chemstructure-search-loading": overlay,
    "ketcher-frame": { contentWindow: { ketcher: ketcher } }
  };
  const documentListeners = {};
  const document = {
    addEventListener(type, callback) {
      documentListeners[type] = callback;
    },
    createElement() {
      return element();
    },
    getElementById(id) {
      return elements[id] || null;
    },
    querySelector(selector) {
      if (selector === ".chemstructure-search-mode-input:checked") {
        return modes.find(function (radio) { return radio.checked; }) || null;
      }
      const match = selector.match(/value="([^"]+)"/);
      return match
        ? modes.find(function (radio) { return radio.value === match[1]; }) || null
        : null;
    },
    querySelectorAll(selector) {
      return selector.indexOf("chemstructure-search-mode-input") !== -1
        ? modes
        : [];
    }
  };
  const location = { search: "", href: "" };
  const window = {
    location: location,
    localStorage: {
      getItem(key) { return storage.get(key) || null; },
      setItem(key, value) { storage.set(key, value); },
      removeItem(key) { storage.delete(key); }
    },
    addEventListener() {},
    clearInterval() {},
    setInterval(callback) {
      intervals.push(callback);
      return intervals.length;
    },
    setTimeout(callback) { callback(); },
    requestAnimationFrame(callback) { callback(); }
  };
  const context = {
    console: { log() {}, warn() {} },
    document: document,
    fetch: function () { throw new Error("unexpected fetch"); },
    URLSearchParams: URLSearchParams,
    window: window
  };

  vm.runInNewContext(script, context);
  documentListeners.DOMContentLoaded();

  return {
    clear: clear,
    input: input,
    intervals: intervals,
    ketcher: ketcher,
    location: location,
    message: message,
    modes: modes,
    search: search,
    storage: storage,
    setSmiles(value) { smiles = value; }
  };
}

function click(target) {
  return target.listeners.click({ preventDefault() {} });
}

async function pollKetcher(page) {
  await page.intervals[0]();
  await new Promise(setImmediate);
}

test("drawing and Ketcher toolbar clear synchronize the SMILES field", async function () {
  const page = createPage();
  page.setSmiles("CCc1ccccc1");
  await pollKetcher(page);
  assert.equal(page.input.value, "CCc1ccccc1");

  page.setSmiles("");
  await pollKetcher(page);
  assert.equal(page.input.value, "");
  assert.equal(page.storage.has("chemstructure_last_query"), false);
});

test("custom Clear immediately resets editor, field, cache, and stale search", async function () {
  let finishClear;
  const page = createPage({
    smiles: "CCc1ccccc1",
    input: "CCc1ccccc1",
    storedSmiles: "CCc1ccccc1",
    clearStructure: function () {
      return new Promise(function (resolve) { finishClear = resolve; });
    }
  });

  click(page.clear);
  assert.equal(page.input.value, "");
  assert.equal(page.storage.has("chemstructure_last_query"), false);

  click(page.search);
  await new Promise(setImmediate);
  assert.equal(page.location.href, "");
  assert.match(page.message.innerHTML, /Please draw a structure/);

  finishClear();
  await new Promise(setImmediate);
  assert.equal(await page.ketcher.getSmiles(), "");
  assert.equal(page.input.value, "");
});

for (const mode of ["exact", "similarity"]) {
  test("after clearing, " + mode + " search uses only the newly drawn structure", async function () {
    const page = createPage({
      smiles: "CCc1ccccc1",
      input: "CCc1ccccc1",
      storedSmiles: "CCc1ccccc1",
      mode: mode
    });
    click(page.clear);
    await new Promise(setImmediate);
    page.modes.forEach(function (radio) {
      radio.checked = radio.value === mode;
    });
    page.setSmiles("c1ccccc1");
    await pollKetcher(page);
    click(page.search);
    await new Promise(setImmediate);

    const query = new URL(page.location.href, "http://example.test").searchParams;
    assert.equal(query.get("structure_query"), "c1ccccc1");
    assert.equal(query.get("structure_mode"), mode);
    assert.equal(page.location.href.includes("CCc1ccccc1"), false);
  });
}

test("substructure mode exports SMARTS from Ketcher", async function () {
  const page = createPage({
    smiles: "C1=C(*)C=CC=C1 |$;;;;;;$|",
    smarts: "c1ccccc1*",
    ket: '{"root":{"nodes":[{"type":"atom","label":"A"}]}}',
    mode: "substructure"
  });

  click(page.search);
  await new Promise(setImmediate);

  const query = new URL(page.location.href, "http://example.test").searchParams;
  assert.equal(query.get("structure_query"), "c1ccccc1*");
  assert.equal(query.get("structure_mode"), "substructure");
  assert.match(page.storage.get("chemstructure_last_ket"), /"label":"A"/);
});

test("reopening Ketcher restores its KET document", async function () {
  const ket = '{"root":{"nodes":[{"type":"atom","label":"A"}]}}';
  const page = createPage({
    storedSmiles: "[*]",
    storedKet: ket,
    mode: "substructure"
  });

  await page.intervals[1]();
  await new Promise(setImmediate);

  assert.equal(page.ketcher.loadedStructure, ket);
  assert.equal(page.input.value, "[*]");
});

test("empty editor does not erase a text-entered SMILES/SMARTS query", async function () {
  const page = createPage({ input: "[#6]" });
  await pollKetcher(page);
  assert.equal(page.input.value, "[#6]");
});
