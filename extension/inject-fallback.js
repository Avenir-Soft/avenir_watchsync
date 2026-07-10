// ISOLATED, document_start — запасной путь для браузеров без world:"MAIN"
// (старый Chromium: Kiwi/Mises). Вставляем патч attachShadow в MAIN-world через <script>.
(function () {
  try {
    const s = document.createElement("script");
    s.textContent =
      "(function(){try{var o=Element.prototype.attachShadow;" +
      "Element.prototype.attachShadow=function(i){return o.call(this,Object.assign({},i||{},{mode:'open'}))};" +
      "document.documentElement.dataset.wsInject='1';}catch(e){}})();";
    (document.head || document.documentElement).appendChild(s);
    s.remove();
  } catch (e) {}
})();
