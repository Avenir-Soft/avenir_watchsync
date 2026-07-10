// ТЕЛЕФОН: адрес сервера = LAN-IP компа.
const SERVER = "http://192.168.1.8:8765";

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  const url = sender.tab && sender.tab.url;
  const title = sender.tab && sender.tab.title;
  if (!url) return;

  if (msg.type === "progress") {
    fetch(SERVER + "/api/progress", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, title, time: msg.time, duration: msg.duration, device: msg.device, episode: msg.episode }),
    }).catch(() => {});
    return;
  }

  if (msg.type === "meta") {
    fetch(SERVER + "/api/meta", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, poster: msg.poster, ep_label: msg.ep_label }),
    }).catch(() => {});
    return;
  }

  if (msg.type === "getResume") {
    fetch(SERVER + "/api/item?url=" + encodeURIComponent(url))
      .then((r) => r.json())
      .then((d) => sendResponse(d))
      .catch(() => sendResponse(null));
    return true; // async
  }
});
