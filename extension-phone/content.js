// Watch Sync (phone) — ловит <video> (в т.ч. Shadow DOM/iframe), шлёт позицию, перематывает,
// и поддерживает сериалы (запоминает серию по URL kodik-плеера и переключает её).
const DEVICE = "📱 Телефон";
const REPORT_MS = 5000;
const MIN_DUR = 90;
const NO_DUR_MIN_TIME = 120;
let lastSent = 0;
let didResume = false;

function send(msg, cb) {
  try { chrome.runtime.sendMessage(msg, cb); } catch (e) {}
}

function epKey(u) {
  const m = String(u || "").match(/kodik[^/]*\/(?:seria|serial|video|uv)\/[^/?#]+\/[^/?#]+/i);
  return m ? m[0] : "";
}

function getDur(v) {
  const d = v.duration;
  if (isFinite(d) && d > 0) return d;
  try {
    const sk = v.seekable;
    if (sk && sk.length) {
      const e = sk.end(sk.length - 1);
      if (isFinite(e) && e > 0) return e;
    }
  } catch (e) {}
  return 0;
}

function report(v, force) {
  if (!v) return;
  const dur = getDur(v);
  if (dur ? dur < MIN_DUR : v.currentTime < NO_DUR_MIN_TIME) return;
  if (v.currentTime < 5) return;
  const now = Date.now();
  if (!force && now - lastSent < REPORT_MS) return;
  lastSent = now;
  send({ type: "progress", time: v.currentTime, duration: dur, device: DEVICE, episode: location.href });
}

function tryResume(v) {
  if (didResume || v.__wsTried) return;
  const dur = getDur(v);
  if (dur && dur < MIN_DUR) return;
  v.__wsTried = true;
  send({ type: "getResume" }, (res) => {
    if (chrome.runtime.lastError || !res || !res.time) return;
    const savedEp = epKey(res.episode), hereEp = epKey(location.href);
    if (savedEp && hereEp && savedEp !== hereEp) return;
    if (dur && res.duration && Math.abs(res.duration - dur) > Math.max(30, 0.05 * res.duration)) return;
    const target = res.time;
    if (target <= 10) return;
    if (dur && target >= dur - 10) return;
    if (dur) didResume = true;
    let tries = 0;
    const iv = setInterval(() => {
      if (Math.abs(v.currentTime - target) < 3 || ++tries > 10) { clearInterval(iv); return; }
      try { v.currentTime = target; } catch (e) {}
    }, 700);
  });
}

function hook(v) {
  if (v.__wsHooked) return;
  v.__wsHooked = true;
  v.addEventListener("timeupdate", () => report(v, false));
  v.addEventListener("pause", () => report(v, true));
  v.addEventListener("seeked", () => report(v, true));
  v.addEventListener("ended", () => report(v, true));
  v.addEventListener("loadedmetadata", () => tryResume(v));
  v.addEventListener("canplay", () => tryResume(v));
  v.addEventListener("durationchange", () => { tryResume(v); report(v, true); });
  v.addEventListener("progress", () => tryResume(v));
  tryResume(v);
}

function collectVideos(root, acc) {
  if (!root) return;
  let all;
  try {
    root.querySelectorAll("video").forEach((v) => acc.push(v));
    all = root.querySelectorAll("*");
  } catch (e) { return; }
  for (const el of all) if (el.shadowRoot) collectVideos(el.shadowRoot, acc);
}

function scan() {
  const acc = [];
  collectVideos(document, acc);
  acc.forEach(hook);
}

new MutationObserver(scan).observe(document.documentElement, { childList: true, subtree: true });
setInterval(scan, 2000);
scan();

if (window === window.top) {
  const curEp = () => {
    const act = document.querySelector('[onclick*="kodik"].active');
    if (!act) return "";
    const t = (act.textContent || "").replace(/\s+/g, " ").trim();
    const m = t.match(/\d+/);
    return m ? m[0] : t.slice(0, 16);
  };
  const sendMeta = () => {
    const el = document.querySelector('meta[property="og:image"], meta[name="og:image"]');
    const poster = (el && el.content) || "";
    const ep = curEp();
    if (poster || ep) send({ type: "meta", poster: poster, ep_label: ep });
  };
  sendMeta();
  setInterval(sendMeta, 8000);

  let epSwitched = false;
  const trySwitchEpisode = () => {
    if (epSwitched) return;
    send({ type: "getResume" }, (res) => {
      if (chrome.runtime.lastError || !res) return;
      const key = epKey(res.episode);
      if (!key) { epSwitched = true; return; }
      const els = document.querySelectorAll('[onclick*="kodik"]');
      for (const el of els) {
        if ((el.getAttribute("onclick") || "").indexOf(key) !== -1) {
          if (!el.classList.contains("active")) { didResume = false; el.click(); }
          epSwitched = true;
          return;
        }
      }
    });
  };
  trySwitchEpisode();
  const epIv = setInterval(() => { if (epSwitched) clearInterval(epIv); else trySwitchEpisode(); }, 1500);
  setTimeout(() => clearInterval(epIv), 25000);
}
