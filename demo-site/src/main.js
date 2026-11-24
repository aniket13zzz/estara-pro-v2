// Sunrise Developers demo site: 3D skyline of the projects, filterable list, live call with Priya.
import * as THREE from "three";
import Vapi from "@vapi-ai/web";
import { PROJECTS } from "./data.js";

const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

function money(inr) {
  if (inr >= 10_000_000) return `₹${(inr / 10_000_000).toFixed(2).replace(/\.?0+$/, "")} Cr`;
  return `₹${Math.round(inr / 100_000)} L`;
}
function possession(p) {
  const d = new Date(p.possession + "T00:00:00");
  if (d <= new Date()) return { text: "Ready to move", ready: true };
  return { text: `Possession ${d.toLocaleDateString("en-IN", { month: "short", year: "numeric" })}`, ready: false };
}
const bhkText = (list) => list.map((b) => `${b} BHK`).join(", ").replace(/, ([^,]*)$/, " and $1");

// ================================================================== skyline (three.js)
let voiceLevel = 0; // 0..1 while Priya speaks; drives the city's windows
const skyline = (() => {
  const canvas = $("#skyline");
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  } catch {
    return null; // no WebGL: the hero keeps its gradient
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.75));
  const scene = new THREE.Scene();
  scene.fog = new THREE.Fog(0x16213a, 30, 95);
  const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 200);

  scene.add(new THREE.HemisphereLight(0x8a7fc7, 0x0e1b2c, 0.9));
  const sun = new THREE.DirectionalLight(0xffc98a, 1.1);
  sun.position.set(-30, 18, -40);
  scene.add(sun);

  // Ground and the river (the Hooghly) in front of the city.
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(400, 400),
    new THREE.MeshStandardMaterial({ color: 0x0c1626, roughness: 1 }));
  ground.rotation.x = -Math.PI / 2;
  scene.add(ground);
  const river = new THREE.Mesh(new THREE.PlaneGeometry(400, 9),
    new THREE.MeshStandardMaterial({ color: 0x142238, roughness: 0.4, metalness: 0.2, emissive: 0x2a2547, emissiveIntensity: 0.08 }));
  river.rotation.x = -Math.PI / 2;
  river.position.set(0, 0.02, 9);
  scene.add(river);

  // Window texture: a grid of lit and dark windows, reused (with different offsets) by every tower.
  function windowTexture(seed, litShare) {
    const c = document.createElement("canvas");
    c.width = 64; c.height = 256;
    const g = c.getContext("2d");
    g.fillStyle = "#101a2b"; g.fillRect(0, 0, 64, 256);
    let s = seed;
    const rnd = () => ((s = (s * 9301 + 49297) % 233280) / 233280);
    for (let y = 6; y < 252; y += 10) {
      for (let x = 5; x < 60; x += 11) {
        const lit = rnd() < litShare;
        g.fillStyle = lit ? (rnd() < 0.8 ? "#ffcf7d" : "#bfe3ff") : "#1a2740";
        g.fillRect(x, y, 6, 5);
      }
    }
    const t = new THREE.CanvasTexture(c);
    t.wrapS = t.wrapT = THREE.RepeatWrapping;
    t.colorSpace = THREE.SRGBColorSpace;
    return t;
  }

  const windowMaterials = [];
  function towerMaterial(seed, lit, w, h) {
    const tex = windowTexture(seed, lit);
    tex.repeat.set(Math.max(1, w / 1.4), h / 5.2);
    const m = new THREE.MeshStandardMaterial({
      color: 0x24324d, roughness: 0.6, metalness: 0.2, map: tex, emissiveMap: tex,
      emissive: 0xffffff, emissiveIntensity: 0.55,
    });
    windowMaterials.push(m);
    return m;
  }

  // Filler city behind the projects.
  const filler = new THREE.Group();
  for (let i = 0; i < 70; i++) {
    const w = 1.5 + Math.random() * 2.2, d = 1.5 + Math.random() * 2.2, h = 2 + Math.random() * 9;
    const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), towerMaterial(i + 7, 0.18, w, h));
    m.position.set((Math.random() - 0.5) * 120, h / 2, -12 - Math.random() * 40);
    filler.add(m);
  }
  scene.add(filler);

  // One tower per project: height by starting price, spread along a gentle arc.
  const prices = PROJECTS.map((p) => p.from);
  const lo = Math.min(...prices), hi = Math.max(...prices);
  const towers = PROJECTS.map((p, i) => {
    const t = (Math.log(p.from) - Math.log(lo)) / (Math.log(hi) - Math.log(lo));
    const h = 7 + t * 17;
    const w = 2.6 + (p.kinds.includes("villa") ? 2.4 : 0);
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(w, h, 2.8), towerMaterial(101 + i * 13, 0.42, w, h));
    const x = (i - (PROJECTS.length - 1) / 2) * 5.6;
    mesh.position.set(x, h / 2, -2 - Math.abs(x) * 0.18 + (i % 2) * 2.2);
    mesh.userData.project = p;
    // A thin amber crown that lights up on hover.
    const crown = new THREE.Mesh(new THREE.BoxGeometry(w + 0.18, 0.18, 2.98),
      new THREE.MeshBasicMaterial({ color: 0xf2b04a, transparent: true, opacity: 0.25 }));
    crown.position.y = h / 2 + 0.09;
    mesh.add(crown);
    mesh.userData.crown = crown;
    scene.add(mesh);
    return mesh;
  });

  // Interaction: point at a tower to see its project; click to open it in the list.
  const ray = new THREE.Raycaster();
  const pointer = new THREE.Vector2(2, 2);
  const tip = $("#tip");
  let hovered = null;
  let parallax = { x: 0, y: 0 };

  canvas.addEventListener("pointermove", (e) => {
    const r = canvas.getBoundingClientRect();
    pointer.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    parallax = { x: pointer.x, y: pointer.y };
  });
  canvas.addEventListener("pointerleave", () => { pointer.set(2, 2); });
  canvas.addEventListener("click", () => { if (hovered) openProject(hovered.userData.project.code, true); });

  function setHovered(mesh) {
    if (hovered === mesh) return;
    if (hovered) hovered.userData.crown.material.opacity = 0.25;
    hovered = mesh;
    canvas.style.cursor = mesh ? "pointer" : "";
    if (!mesh) { tip.hidden = true; return; }
    mesh.userData.crown.material.opacity = 1;
    const p = mesh.userData.project;
    tip.innerHTML = `<strong>${p.name}</strong><span>${p.area}</span><br><span>${bhkText(p.bhks)}, from </span><b>${p.fromText}</b>`;
    tip.hidden = false;
  }

  function resize() {
    const { clientWidth: w, clientHeight: h } = canvas;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    // Narrow screens: step back so every tower stays in view.
    camera.position.set(0, 9, w < 700 ? 64 : 42);
    camera.updateProjectionMatrix();
  }
  window.addEventListener("resize", resize);
  resize();

  let visible = true;
  new IntersectionObserver(([e]) => { visible = e.isIntersecting; }).observe(canvas);

  const target = new THREE.Vector3(0, 7, -6);
  const clock = new THREE.Clock();
  const top = new THREE.Vector3();
  function frame() {
    requestAnimationFrame(frame);
    if (!visible) return;
    const t = clock.getElapsedTime();
    const base = camera.position.z;
    if (!reduceMotion) {
      camera.position.x += ((Math.sin(t * 0.07) * 6 + parallax.x * 4) - camera.position.x) * 0.03;
      camera.position.y += ((9 + parallax.y * 2) - camera.position.y) * 0.03;
    }
    camera.position.z = base;
    camera.lookAt(target);
    // Windows breathe with Priya's voice while she speaks.
    const glow = 0.55 + voiceLevel * 1.6;
    for (const m of windowMaterials) m.emissiveIntensity += (glow - m.emissiveIntensity) * 0.2;
    ray.setFromCamera(pointer, camera);
    const hit = ray.intersectObjects(towers, false)[0];
    setHovered(hit ? hit.object : null);
    if (hovered) {
      hovered.getWorldPosition(top);
      top.y = hovered.geometry.parameters.height;
      top.project(camera);
      tip.style.left = `${((top.x + 1) / 2) * canvas.clientWidth}px`;
      tip.style.top = `${((1 - top.y) / 2) * canvas.clientHeight}px`;
    }
    renderer.render(scene, camera);
  }
  frame();
  return { towers };
})();

// ================================================================== homes list
const list = $("#list"), count = $("#count");
const budget = $("#budget"), budgetOut = $("#budget-out"), area = $("#area");
for (const name of [...new Set(PROJECTS.map((p) => p.area))].sort()) {
  area.add(new Option(name, name));
}

function render() {
  const max = Number(budget.value) >= 330 ? Infinity : Number(budget.value) * 100_000;
  budgetOut.textContent = max === Infinity ? "any" : money(max);
  const bhk = Number($("input[name=bhk]:checked").value);
  const where = area.value;
  const shown = PROJECTS.filter((p) => p.homes.some((h) => h.min <= max && (!bhk || h.bhk === bhk))
    && (!where || p.area === where));
  count.textContent = shown.length === PROJECTS.length
    ? `All ${shown.length} projects`
    : `${shown.length} of ${PROJECTS.length} projects match`;
  list.innerHTML = "";
  if (!shown.length) {
    list.innerHTML = `<li class="empty">Nothing matches exactly. Priya can suggest the closest options, often a nearby area or the next budget step.</li>`;
    return;
  }
  for (const p of shown) {
    const pos = possession(p);
    const li = document.createElement("li");
    li.className = "project";
    li.id = `p-${p.code}`;
    li.innerHTML = `
      <button type="button" aria-expanded="false" aria-controls="d-${p.code}">
        <span class="p-name">${p.name}<span class="p-area">${p.area}</span></span>
        <span class="p-homes">${bhkText(p.bhks)}${p.kinds.includes("villa") ? " villas" : ""}${p.kinds.includes("penthouse") ? ", penthouse" : ""}</span>
        <span class="p-from"><small>from</small>${p.fromText}</span>
        <span class="p-state ${pos.ready ? "ready" : ""}">${pos.text}</span>
      </button>
      <div class="detail" id="d-${p.code}">
        <div>
          <p>${p.about}</p>
          <table>
            <thead><tr><th scope="col">Home</th><th scope="col">Carpet area</th><th scope="col">Price</th><th scope="col">Available</th></tr></thead>
            <tbody>${p.homes.map((h) => `<tr><td>${h.name}</td><td class="num">${h.sqft.toLocaleString("en-IN")} sq ft</td><td class="num">${money(h.min)} to ${money(h.max)}</td><td>${h.available || "Sold out"}</td></tr>`).join("")}</tbody>
          </table>
        </div>
        <div>
          <ul class="amen" aria-label="Amenities">${p.amenities.map((a) => `<li>${a}</li>`).join("")}</ul>
          ${p.offer ? `<p class="offer">Current offer: ${p.offer}</p>` : ""}
          <button class="talk" type="button" data-talk>Ask Priya about ${p.name}</button>
        </div>
      </div>`;
    const toggle = li.querySelector("button");
    toggle.addEventListener("click", () => {
      const open = li.classList.toggle("open");
      toggle.setAttribute("aria-expanded", String(open));
    });
    list.append(li);
  }
  $$("[data-talk]", list).forEach((b) => b.addEventListener("click", startCall));
}
$("#filters").addEventListener("input", render);
render();

function openProject(code, scroll) {
  for (const input of [budget]) input.value = input.max;
  $("input[name=bhk][value='0']").checked = true;
  area.value = "";
  render();
  const li = $(`#p-${code}`);
  if (!li) return;
  li.classList.add("open");
  li.querySelector("button").setAttribute("aria-expanded", "true");
  if (scroll) li.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "center" });
}

// ================================================================== the call
const settings = window.ESTARA_DEMO || {};
// Comparison mode for the owner: add ?v=b to the address to call assistant B instead of A.
const useB = new URLSearchParams(location.search).get("v") === "b";
const assistantId = useB ? settings.assistantIdB : settings.assistantId;
const panel = $("#call"), statusEl = $("#call-status"), transcript = $("#transcript");
let vapi = null, live = false, partial = null;

// A free backend host sleeps when idle: wake it as soon as the page opens, so it is ready by the
// time the visitor starts talking. No-cors: nothing is read, the request only wakes it.
function wakeBackend() {
  if (!settings.apiUrl) return;
  fetch(String(settings.apiUrl).replace(/\/$/, "") + "/api/v1/health/live",
        { mode: "no-cors", cache: "no-store" }).catch(() => {});
}
wakeBackend();

function line(role, text, isPartial = false) {
  const li = document.createElement("li");
  li.className = role + (isPartial ? " partial" : "");
  li.textContent = text;
  transcript.append(li);
  transcript.scrollTop = transcript.scrollHeight;
  return li;
}
function setStatus(text) { statusEl.textContent = text; }
function setBusy(on) { $$("[data-talk]").forEach((b) => b.setAttribute("aria-busy", String(on))); }

async function startCall() {
  if (live) { panel.hidden = false; return; }
  panel.hidden = false;
  transcript.innerHTML = "";
  if (settings.enabled === false) {
    setStatus("Demo paused");
    line("system", "The voice demo is paused right now. Please try again a little later.");
    return;
  }
  wakeBackend();
  if (!settings.vapiPublicKey || !assistantId) {
    setStatus("Not set up yet");
    line("system", "Add the Vapi public key and the assistant ID in config.js, then deploy again.");
    return;
  }
  setStatus("Connecting");
  setBusy(true);
  try {
    vapi = vapi || new Vapi(settings.vapiPublicKey);
    vapi.removeAllListeners?.();
    vapi.on("call-start", () => { live = true; setBusy(false); setStatus("Listening"); });
    vapi.on("call-end", () => {
      live = false; voiceLevel = 0; setBusy(false); setStatus("Call ended");
      line("system", "Thank you for talking to Priya. Our team will be in touch about any visit you requested.");
    });
    vapi.on("speech-start", () => setStatus("Priya is speaking"));
    vapi.on("speech-end", () => { voiceLevel = 0; setStatus("Listening"); });
    vapi.on("volume-level", (v) => { voiceLevel = Math.max(0, Math.min(1, Number(v) || 0)); });
    vapi.on("message", (m) => {
      if (m?.type !== "transcript" || !m.transcript) return;
      const role = m.role === "user" ? "user" : "assistant";
      if (m.transcriptType === "partial") {
        if (!partial || partial.dataset.role !== role) { partial = line(role, m.transcript, true); partial.dataset.role = role; }
        else partial.textContent = m.transcript;
        transcript.scrollTop = transcript.scrollHeight;
      } else if (m.transcriptType === "final") {
        if (partial && partial.dataset.role === role) { partial.remove(); partial = null; }
        line(role, m.transcript);
      }
    });
    vapi.on("error", () => {
      live = false; setBusy(false); setStatus("Call could not continue");
      line("system", "The call could not connect. Check your microphone permission and try again.");
    });
    await vapi.start(assistantId);
  } catch {
    setBusy(false); setStatus("Call could not start");
    line("system", "The call could not start. Allow the microphone for this site and try again.");
  }
}
function endCall() {
  if (vapi && live) vapi.stop();
  else panel.hidden = true;
}
$$("[data-talk]").forEach((b) => b.addEventListener("click", startCall));
$("#end").addEventListener("click", endCall);
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !live) panel.hidden = true; });

// Voice orb: a small amber sphere that swells with Priya's voice.
(() => {
  const c = $("#orb"), g = c.getContext("2d");
  let level = 0;
  function draw(t) {
    requestAnimationFrame(draw);
    if (panel.hidden) return;
    level += (voiceLevel - level) * 0.25;
    g.clearRect(0, 0, 96, 96);
    const r = 22 + level * 16 + (reduceMotion || !live ? 0 : Math.sin(t / 600) * 1.5);
    const grad = g.createRadialGradient(40, 38, 4, 48, 48, r);
    grad.addColorStop(0, "#ffe2a8");
    grad.addColorStop(0.55, "#f2b04a");
    grad.addColorStop(1, "rgba(242,176,74,0)");
    g.fillStyle = grad;
    g.beginPath(); g.arc(48, 48, r + 10, 0, Math.PI * 2); g.fill();
  }
  requestAnimationFrame(draw);
})();

// Solid header once the page scrolls, so it never sits on top of text.
{
  const top = document.querySelector(".top");
  const onScroll = () => top.classList.toggle("scrolled", window.scrollY > 40);
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();
}

void skyline;
