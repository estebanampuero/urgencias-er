// ============ Urgencias · JS de UI ============

// === CSRF helper: agrega X-CSRFToken a todos los fetch no-GET ===
const CSRF = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
(function patchFetch() {
  const orig = window.fetch;
  window.fetch = function(input, init) {
    init = init || {};
    const method = (init.method || (typeof input === 'object' && input.method) || 'GET').toUpperCase();
    if (method !== 'GET' && method !== 'HEAD' && CSRF) {
      init.headers = init.headers || {};
      // Si headers es Headers obj
      if (init.headers instanceof Headers) {
        if (!init.headers.has('X-CSRFToken')) init.headers.set('X-CSRFToken', CSRF);
      } else {
        if (!('X-CSRFToken' in init.headers) && !('x-csrftoken' in init.headers)) {
          init.headers['X-CSRFToken'] = CSRF;
        }
      }
    }
    return orig.call(this, input, init);
  };
})();


// Auto-cierre de flashes
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".flash").forEach(f => {
    setTimeout(() => { f.style.transition = "opacity .4s"; f.style.opacity = "0"; }, 4000);
    setTimeout(() => f.remove(), 4500);
  });

  // Confirmación de cambio de estado destructivo
  document.querySelectorAll('select[name="estado"]').forEach(sel => {
    sel.addEventListener("change", e => {
      const v = e.target.value;
      if (v === "fallecido") {
        if (!confirm("¿Confirmar estado FALLECIDO? Esta acción quedará registrada.")) {
          e.target.value = e.target.dataset.prev || "en_atencion";
        }
      }
      e.target.dataset.prev = e.target.value;
    });
  });

  // Auto-refresh del dashboard cada 60s
  if (location.pathname === "/" && !location.search) {
    setTimeout(() => location.reload(), 60000);
  }

  // STT (dictado por voz)
  initSTT();

  // FAB de navegación móvil
  initFabMenu();

  // Sugerencia ESI en vivo en form de paciente
  initEsiSuggest();
});


// ============ Sugerencia ESI en vivo (form de paciente) ============
function initEsiSuggest() {
  const hint = document.getElementById("esiHint");
  if (!hint) return;
  const catEl = document.getElementById("esiHintCat");
  const razonEl = document.getElementById("esiHintRazon");
  const applyBtn = document.getElementById("esiHintApply");

  const fields = ["motivo_consulta","antecedentes","edad","pa","fc","fr","temp","sato2","glasgow"];
  const inputs = fields
    .map(n => document.querySelector(`[name="${n}"]`))
    .filter(Boolean);

  let last = null;
  let timer = null;

  const consultar = async () => {
    const data = {};
    inputs.forEach(el => { data[el.name] = el.value; });
    // Sólo consultar si hay al menos motivo o algún signo vital
    const algo = data.motivo_consulta?.length > 3
              || data.pa || data.fc || data.fr || data.sato2 || data.temp || data.glasgow;
    if (!algo) { hint.classList.remove("show"); return; }
    try {
      const r = await fetch("/api/sugerir-esi", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
      if (!r.ok) return;
      const j = await r.json();
      if (j.categoria !== last) {
        last = j.categoria;
        catEl.className = "esi esi-" + j.categoria;
        catEl.textContent = j.categoria;
        razonEl.textContent = (j.razones && j.razones[0]) ? "· " + j.razones[0] : "";
        hint.classList.add("show");
      }
    } catch {}
  };

  const onChange = () => {
    clearTimeout(timer);
    timer = setTimeout(consultar, 350);
  };
  inputs.forEach(el => {
    el.addEventListener("input", onChange);
    el.addEventListener("change", onChange);
  });

  applyBtn.addEventListener("click", () => {
    if (!last) return;
    const radio = document.querySelector(`input[name="categoria_esi"][value="${last}"]`);
    if (radio) radio.checked = true;
    applyBtn.textContent = "✓ Aplicada";
    setTimeout(() => { applyBtn.textContent = "Usar sugerencia"; }, 1500);
  });
}


// ============ FAB de navegación (móvil) ============
function initFabMenu() {
  const fab = document.getElementById("fabMenu");
  if (!fab) return;

  // 1) Clonar las opciones del topbar a un panel propio del FAB
  //    Evita conflictos de stacking/specificity entre desktop y móvil.
  const sourceMenu = document.querySelector(".topbar .menu");
  let panel = document.getElementById("fabPanel");
  if (!panel && sourceMenu) {
    panel = document.createElement("div");
    panel.id = "fabPanel";
    panel.className = "fab-panel";
    panel.setAttribute("role", "menu");
    sourceMenu.querySelectorAll("a").forEach(a => {
      const clone = a.cloneNode(true);
      clone.setAttribute("role", "menuitem");
      panel.appendChild(clone);
    });
    document.body.appendChild(panel);
  }

  const close = () => {
    document.body.classList.remove("menu-open");
    fab.setAttribute("aria-expanded", "false");
  };
  const open = () => {
    document.body.classList.add("menu-open");
    fab.setAttribute("aria-expanded", "true");
  };

  fab.addEventListener("click", e => {
    e.preventDefault();
    e.stopPropagation();
    document.body.classList.contains("menu-open") ? close() : open();
  });

  // Cerrar al tocar fuera o tocar una opción del panel
  document.addEventListener("click", e => {
    if (!document.body.classList.contains("menu-open")) return;
    if (e.target.closest(".fab-menu")) return;
    if (e.target.closest("#fabPanel a")) {
      // dejar que el link navegue, luego cerrar
      setTimeout(close, 0);
      return;
    }
    close();
  });

  // Esc cierra
  document.addEventListener("keydown", e => {
    if (e.key === "Escape") close();
  });
}


// ============ STT: dictado por voz ============

async function initSTT() {
  const fields = document.querySelectorAll("textarea[data-stt], input[data-stt]");
  if (!fields.length) return;
  if (!navigator.mediaDevices || !window.MediaRecorder) return;

  // Verificar disponibilidad del backend antes de inyectar botones
  let status;
  try {
    const r = await fetch("/api/stt/status");
    status = await r.json();
  } catch { return; }
  if (!status.available) return;

  fields.forEach(setupMicForField);
}


function setupMicForField(el) {
  // Envolver el field para posicionar el botón mic encima
  const wrap = document.createElement("div");
  wrap.className = "stt-wrap";
  if (el.tagName === "INPUT") wrap.classList.add("stt-wrap-input");
  el.parentNode.insertBefore(wrap, el);
  wrap.appendChild(el);

  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "mic-btn";
  btn.setAttribute("aria-label", "Dictar");
  btn.title = "Click para grabar · click otra vez para detener";
  btn.innerHTML = micIcon();
  wrap.appendChild(btn);

  let recorder = null, stream = null, chunks = [], recording = false;

  const start = async () => {
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      toast("Sin acceso al micrófono.", "error");
      return;
    }
    const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
      ? "audio/webm;codecs=opus"
      : (MediaRecorder.isTypeSupported("audio/mp4") ? "audio/mp4" : "");
    recorder = mime ? new MediaRecorder(stream, { mimeType: mime })
                    : new MediaRecorder(stream);
    chunks = [];
    recorder.ondataavailable = e => e.data.size && chunks.push(e.data);
    recorder.onstop = () => upload();
    recorder.start();
    btn.classList.add("recording");
    btn.innerHTML = stopIcon();
    recording = true;
  };

  const stop = () => {
    if (!recording) return;
    if (recorder && recorder.state !== "inactive") recorder.stop();
    if (stream) stream.getTracks().forEach(t => t.stop());
    recording = false;
  };

  const upload = async () => {
    const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
    btn.classList.remove("recording");
    btn.classList.add("processing");
    btn.innerHTML = spinnerIcon();

    const fd = new FormData();
    const ext = (recorder.mimeType || "").includes("mp4") ? "mp4" : "webm";
    fd.append("audio", blob, `rec.${ext}`);
    fd.append("contexto", el.dataset.stt || "general");

    try {
      const r = await fetch("/api/transcribir", { method: "POST", body: fd });
      const data = await r.json();
      if (data.texto) {
        insertAtCursor(el, data.texto);
        toast("Transcripción agregada.", "ok");
      } else if (data.error) {
        toast("STT: " + data.error, "error");
      }
    } catch (e) {
      toast("Error de transcripción: " + e.message, "error");
    } finally {
      btn.classList.remove("processing");
      btn.innerHTML = micIcon();
    }
  };

  btn.addEventListener("click", e => {
    e.preventDefault();
    recording ? stop() : start();
  });
}


function insertAtCursor(el, text) {
  const cur = el.selectionStart ?? el.value.length;
  const pre = el.value.slice(0, cur);
  const post = el.value.slice(cur);
  const sep = pre && !/\s$/.test(pre) ? " " : "";
  el.value = pre + sep + text + post;
  el.focus();
  const newPos = (pre + sep + text).length;
  try { el.setSelectionRange(newPos, newPos); } catch {}
  el.dispatchEvent(new Event("input", { bubbles: true }));
}


function toast(msg, cat = "ok") {
  let zone = document.querySelector(".flashes");
  if (!zone) {
    zone = document.createElement("div");
    zone.className = "flashes";
    document.body.appendChild(zone);
  }
  const div = document.createElement("div");
  div.className = "flash flash-" + cat;
  div.textContent = msg;
  zone.appendChild(div);
  setTimeout(() => { div.style.transition = "opacity .4s"; div.style.opacity = "0"; }, 3500);
  setTimeout(() => div.remove(), 4000);
}


// ===== Iconos SVG =====
function micIcon() {
  return `<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true">
    <path d="M12 14a3 3 0 0 0 3-3V6a3 3 0 1 0-6 0v5a3 3 0 0 0 3 3z"/>
    <path d="M19 11a1 1 0 1 0-2 0 5 5 0 0 1-10 0 1 1 0 1 0-2 0 7 7 0 0 0 6 6.92V20H8a1 1 0 1 0 0 2h8a1 1 0 1 0 0-2h-3v-2.08A7 7 0 0 0 19 11z"/>
  </svg>`;
}
function stopIcon() {
  return `<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true">
    <rect x="6" y="6" width="12" height="12" rx="2"/>
  </svg>`;
}
function spinnerIcon() {
  return `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true">
    <path d="M12 3 a9 9 0 1 1 -9 9" stroke-linecap="round"/>
  </svg>`;
}
