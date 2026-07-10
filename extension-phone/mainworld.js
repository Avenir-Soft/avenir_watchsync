// world:"MAIN", document_start — форсит "открытый" Shadow DOM (современный Chrome, обходит CSP).
(function () {
  try {
    const orig = Element.prototype.attachShadow;
    Element.prototype.attachShadow = function (init) {
      return orig.call(this, Object.assign({}, init || {}, { mode: "open" }));
    };
    if (document.documentElement) document.documentElement.dataset.wsMain = "1";
  } catch (e) {}
})();
