// ---- Calculator UI ----
(function () {
  const $ = (id) => document.getElementById(id);
  const form = $("calc");
  if (!form) return;
  let method = "acreage";
  const acresFmt = (q) => {
    const s = Q.fixed(q, 4).replace(/\.?0+$/, "");
    return s === "" ? "0" : s;
  };
  function syncFields() {
    const t = $("c-type").value;
    $("c-burden-label").hidden = !["npri_fixed", "npri_float", "orri", "wi"].includes(t);
    $("c-orri-label").hidden = t !== "wi";
    const burdenText = { npri_fixed: "Fixed NPRI (of production)", npri_float: "Floating NPRI (of royalty)", orri: "ORRI (of 8/8)", wi: "Working interest in the lease" }[t];
    if (burdenText) $("c-burden-label").firstChild.nodeValue = burdenText;
    $("c-owned-label").firstChild.nodeValue = ["orri", "wi"].includes(t) ? "Share of minerals the lease covers" : "Undivided mineral interest";
    $("c-acreage").hidden = method !== "acreage";
    $("c-lateral").hidden = method !== "lateral";
  }
  function run() {
    syncFields();
    const out = $("c-out");
    try {
      const r = titleCalc({
        type: $("c-type").value, acres: $("c-acres").value, owned: $("c-owned").value,
        royalty: $("c-royalty").value, burden: $("c-burden").value, orriBurden: $("c-orri").value,
        method, tractInUnit: $("c-inunit").value, unitAcres: $("c-unit").value,
        tractLateral: $("c-tl").value, totalLateral: $("c-al").value,
      });
      const label = $("c-type").value === "wi" ? "Net revenue interest" : "Decimal interest";
      let rows = `<dt>Exact fraction</dt><dd>${Q.str(r.decimal)}</dd>`;
      if (r.wi) rows += `<dt>Working interest</dt><dd>${Q.fixed(r.wi)}</dd>`;
      rows += `<dt>Net mineral acres</dt><dd>${acresFmt(r.nma)}</dd>`;
      if (r.nra) rows += `<dt>NRA (1/8 basis)</dt><dd>${acresFmt(r.nra)}</dd>`;
      rows += `<dt>Participation</dt><dd>${Q.fixed(r.part)} <span class="muted">(${Q.str(r.part)})</span></dd>`;
      out.innerHTML = `<div class="small muted">${label}</div><div class="big">${Q.fixed(r.decimal)}</div><dl>${rows}</dl>` +
        `<div class="math">${r.partNote}.<br>${r.math}.</div>` + (r.warn ? `<p class="err">${r.warn}</p>` : "");
    } catch (e) {
      out.innerHTML = `<p class="err">${e.message}</p>`;
    }
  }
  form.addEventListener("input", run);
  form.addEventListener("submit", (e) => e.preventDefault());
  form.querySelectorAll("[data-method]").forEach((b) => b.addEventListener("click", () => {
    method = b.dataset.method;
    form.querySelectorAll("[data-method]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    run();
  }));
  run();
})();

// ---- County finder ----
(function () {
  const body = document.getElementById("county-body");
  if (!body || typeof COUNTIES === "undefined") return;
  const q = document.getElementById("county-q");
  const count = document.getElementById("county-count");
  let state = "all";
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const linkify = (s) => esc(s).replace(/(https?:\/\/[^\s,;)]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
  function render() {
    const term = (q.value || "").trim().toLowerCase();
    const rows = COUNTIES.filter((c) => (state === "all" || c.st === state) &&
      (!term || Object.values(c).join(" ").toLowerCase().includes(term)));
    body.innerHTML = rows.map((c) => `<tr>
      <td><strong>${esc(c.county)}</strong> <span class="pill ${c.st.toLowerCase()}">${c.st}</span><br><span class="muted">${esc(c.area)}</span></td>
      <td>${linkify(c.portal)}</td>
      <td>${linkify(c.aggregators)}</td>
      <td>${linkify(c.appraisal)}</td>
      <td>${linkify(c.notes)}</td></tr>`).join("");
    count.textContent = `${rows.length} of ${COUNTIES.length} counties`;
  }
  q.addEventListener("input", render);
  document.querySelectorAll("[data-state]").forEach((b) => b.addEventListener("click", () => {
    state = b.dataset.state;
    document.querySelectorAll("[data-state]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    render();
  }));
  render();
})();

// ---- Copy buttons ----
document.querySelectorAll(".codeblock").forEach((block) => {
  const btn = block.querySelector(".copy");
  const pre = block.querySelector("pre");
  btn.addEventListener("click", () => {
    const done = () => { btn.textContent = "Copied"; btn.classList.add("done"); setTimeout(() => { btn.textContent = "Copy"; btn.classList.remove("done"); }, 1600); };
    const fallback = () => { const r = document.createRange(); r.selectNodeContents(pre); const s = getSelection(); s.removeAllRanges(); s.addRange(r); btn.textContent = "Selected. Press Ctrl+C"; };
    try { navigator.clipboard.writeText(pre.innerText).then(done, fallback); } catch (e) { fallback(); }
  });
});

// ---- Checklist memory (this browser only) ----
document.querySelectorAll(".checklist[data-store]").forEach((list) => {
  const key = "permian-title-" + list.dataset.store;
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(key) || "{}"); } catch (e) { saved = {}; }
  list.querySelectorAll("input[type=checkbox]").forEach((cb) => {
    cb.checked = !!saved[cb.id];
    cb.addEventListener("change", () => {
      saved[cb.id] = cb.checked;
      try { localStorage.setItem(key, JSON.stringify(saved)); } catch (e) { /* storage blocked */ }
    });
  });
});
