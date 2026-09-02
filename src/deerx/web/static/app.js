/* DeerX web arayuzu.
   Derleme adimi yok: tek dosya, sade DOM. Sunucudan gelen her metin `esc()`
   ile kacislanir; yalnizca sunucunun uretttigi markdown HTML'i dogrudan
   yerlestirilir (markdown-it `html:false` ile calisir). */

"use strict";

// ─── Yardimcilar ──────────────────────────────────────────────────────────
const $  = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));

const fmtMoney = (n) => {
  const value = Number(n || 0);
  // Kurus altindaki gercek maliyetler 2 basamakta $0.00 gorunur; onlarda 4
  // basamak goster. Tam sifir ise 4 basamak gurultuden ibaret.
  if (value === 0) return "$0.00";
  return "$" + value.toFixed(value >= 0.1 ? 2 : 4);
};
const fmtTime  = (ts) => new Date(ts * 1000).toLocaleTimeString("tr-TR", { hour12: false });
const fmtBytes = (n) => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: options.body ? { "Content-Type": "application/json" } : {},
    ...options,
  });
  let payload = null;
  try { payload = await response.json(); } catch { /* govdesiz yanit */ }
  if (!response.ok) {
    throw new Error(payload?.error || `${response.status} ${response.statusText}`);
  }
  return payload;
}

const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body ?? {}) });

function toast(message, tone = "info", ttl = 4200) {
  const node = document.createElement("div");
  node.className = "toast";
  node.dataset.tone = tone;
  node.textContent = message;
  $("#toasts").append(node);
  setTimeout(() => {
    node.style.transition = "opacity .25s, transform .25s";
    node.style.opacity = "0";
    node.style.transform = "translateX(18px)";
    setTimeout(() => node.remove(), 260);
  }, ttl);
}

const emptyState = (title, hint = "") =>
  `<p class="empty"><strong>${esc(title)}</strong>${hint ? esc(hint) : ""}</p>`;

// ─── Durum ────────────────────────────────────────────────────────────────
const state = {
  // Sunucudan en son gorulen dil; yerel onizlemeyi ezmemek icin.
  serverLanguage: null,
  // Is akisi > adim (kosu) > faz: hangi seviyede oldugumuz.
  activeWorkflow: null,
  workflowDetail: null,
  overview: null,
  phases: [],
  view: "overview",
  analysisTab: "requirements",
  taskFilter: "",
  laneFilter: "",
  streamFilter: "",
  events: [],
  lastSeq: 0,
  source: null,
  reconnectDelay: 1000,
  approvals: [],
  approvalMode: "ask",
  activeArtifact: null,
  pollTimer: null,
  // Sorular teker teker sorulur; kuyrukta nerede oldugumuz ve yazilmis
  // ama gonderilmemis taslaklar burada durur.
  questions: [],
  questionIndex: 0,
  questionDrafts: {},
  // Sayfalama. `streamPage === null` "canli": her zaman son sayfayi gosterir.
  analysisItems: [],
  analysisPage: 1,
  analysisSize: 25,
  taskItems: [],
  taskPage: 1,
  taskSize: 25,
  docItems: [],
  docPage: 1,
  docSize: 25,
  streamPage: null,
  streamSize: 50,
  // Kosu gorunumu: acik adimlar kullanicinin sectigi gibi kalir.
  workflow: null,
  workflowOpen: new Set(),
  // Acik kosu; null ise kosu listesi gosterilir.
  activeRun: null,
  // Kimlik durumu: kim girdi, kurulum gerekiyor mu.
  auth: null,
  // Ciktilar: acik kosu gruplari. Bos ise en yeni kosu acik gelir.
  artifactGroups: [],
  openArtifactRuns: new Set(),
  // Kosu kaydindan onceki ciktilar varsayilan olarak gizli; kullanici
  // acikca isterse gosterilir.
  showOrphans: false,
  // Genel durum istegi neden dustu; ayarlar ekrani bunu yaziyor.
  overviewError: null,
  // Planlar: secili plan gorev listesini suzer, etkin plan ajanin
  // yeni gorevleri yazacagi yerdir.
  plans: [],
  selectedPlan: null,
  activePlan: null,
};

const GLYPH = {
  phase: "▸", agent: "◆", tool: "→", tool_error: "✗", error: "✗",
  warn: "!", done: "✓", cost: "$", approval: "?", message: "»",
};

// ─── Tema ─────────────────────────────────────────────────────────────────
function initTheme() {
  const saved = localStorage.getItem("deerx-theme");
  if (saved === "light" || saved === "dark") {
    document.documentElement.dataset.theme = saved;
  }
  $$("#lang-switch .lang-opt").forEach((button) => {
    button.addEventListener("click", () => changeLanguage(button.dataset.lang));
  });

  $("#theme-toggle").addEventListener("click", () => {
    const root = document.documentElement;
    const isDark = root.dataset.theme
      ? root.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    const next = isDark ? "light" : "dark";
    root.dataset.theme = next;
    localStorage.setItem("deerx-theme", next);
  });
}

// ─── Dil ──────────────────────────────────────────────────────────────────
/** Ust bardaki segmenti ve ayarlar ekranindaki secimi etkin dile getirir. */
function syncLanguageControls() {
  const lang = currentLanguage();
  $$("#lang-switch .lang-opt").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.lang === lang));
  });
  const select = $("#set-language");
  if (select) select.value = lang;
}

/** Arayuzu verilen dile cevirir. Sunucuya dokunmaz. */
function applyLanguageLocally(lang) {
  setLanguage(lang);
  applyTranslations();
  renderSettings();
  // Senkron EN SONA: `renderSettings` acilir listeyi sunucudan gelen
  // (henuz eski) degerle doldurur ve daha erken yapilan bir senkronu ezer.
  syncLanguageControls();
  if (state.overview) {
    renderGoalLine(state.overview.goal);
    renderWorkspace(state.overview.workspace);
    renderPhaseRail(state.phases);
    renderStats(state.overview);
    renderPhaseSummaries(state.phases);
    renderRunControls(state.overview);
    renderQuestions(state.overview.blocking_questions || []);
    syncRunState(state.overview.run);
  }
  renderFeed();
  refreshActiveView();
  loadUsers();
  loadAudit();
}

/**
 * Dili degistirir ve SUNUCUDA da kalici yapar.
 *
 * Yalnizca arayuzu cevirmek yarim bir gecis olurdu: olay akisi, arac
 * hatalari ve ajan yonergeleri sunucudan geliyor ve eski dilde kalirdi.
 * Sunucu reddederse arayuz de geri doner -- ekranin sunucuyla uyusmadigi
 * bir ara durum, hicbir sey yapmamaktan kotudur.
 */
async function changeLanguage(lang) {
  const previous = currentLanguage();
  if (lang === previous || !lang) return;

  const box = $("#lang-switch");
  box.dataset.busy = "1";
  applyLanguageLocally(lang);
  try {
    await post("/api/settings", { language: lang });
    state.serverLanguage = lang;
  } catch (error) {
    applyLanguageLocally(previous);
    toast(error.message, "err");
  } finally {
    box.dataset.busy = "0";
  }
}

// ─── Yonlendirme ──────────────────────────────────────────────────────────
const VIEWS = ["overview", "develop", "workflow", "knowledge", "analysis",
               "plan", "artifacts", "stream", "settings"];

function showView(name) {
  if (!VIEWS.includes(name)) name = "overview";
  state.view = name;
  $$(".view").forEach((section) => section.classList.toggle("is-active", section.id === `view-${name}`));
  $$(".rail-item").forEach((item) => item.classList.toggle("is-active", item.dataset.view === name));
  if (location.hash.replace("#", "") !== name) location.hash = name;

  if (name === "develop")   { loadOverview(); loadDocuments(); }
  if (name === "workflow")  loadWorkflow();
  if (name === "knowledge") loadDocuments();
  if (name === "analysis")  loadAnalysis();
  if (name === "plan")      { loadPlans(); loadTasks(); }
  if (name === "artifacts") { loadArtifacts(); loadDelivery(); }
  if (name === "stream")    renderFeed();
  // Form `state.overview.settings`ten dolar, ama bu sekme genel durumu HIC
  // yuklemiyordu. Yoklama henuz gelmediyse `renderSettings` sessizce cikip
  // formu bos birakiyor ve bir daha denemiyordu -- yorumunun soyledigi gibi
  // yalnizca sekmeye girerken ve kaydettikten sonra calisiyor. Sonuc: ayarlar
  // ekrani bombos aciliyordu. `develop` sekmesi bastan beri once yukluyor.
  if (name === "settings")  { loadOverview().then(renderSettings); loadUsers(); loadAudit(); }
}

function initRouting() {
  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-view]");
    if (trigger) showView(trigger.dataset.view);
  });
  // Tarayici geri/ileri tuslari ve dogrudan verilen #baglantilar da calissin.
  window.addEventListener("hashchange", () => showView(location.hash.replace("#", "")));
  showView(location.hash.replace("#", ""));
}

// ─── Genel bakis ──────────────────────────────────────────────────────────
/* Genel durumu ceker. Basarili olup olmadigini DONER: cagiran taraf
   yalnizca `then` ile beklerse, istek dustugunde de `then` calisir ve
   ekran sessizce bos kalir. Ayarlar ekraninda tam olarak bu oluyordu. */
async function loadOverview() {
  let data;
  try {
    data = await api("/api/overview");
  } catch (error) {
    state.overviewError = error.message;
    toast(t("app.serverUnreachable", { msg: error.message }), "err");
    return false;
  }
  state.overviewError = null;
  state.overview = data;
  state.phases = data.phases;

  $("#top-cost").textContent = fmtMoney(data.total_cost);
  // Dil sunucudaki ayardan gelir. Karsilastirma sunucunun *onceki* degeriyle
  // yapilir, arayuzun o anki diliyle degil: kullanici ayarlar ekranindan dili
  // secip henuz kaydetmediyse, iki saniye sonraki yoklama secimini geri
  // almamali. Sunucuda gercekten degistiginde (baska bir sekme, CLI) uyulur.
  const serverLang = data.settings.language;
  if (serverLang && serverLang !== state.serverLanguage) {
    state.serverLanguage = serverLang;
    if (serverLang !== currentLanguage()) {
      // Baska bir sekme ya da CLI degistirmis olabilir.
      applyLanguageLocally(serverLang);
    }
  }

  renderGoalLine(data.goal);

  $("#rail-models").innerHTML =
    `lead · ${esc(data.settings.model_lead)}<br>worker · ${esc(data.settings.model_worker)}`;
  renderWorkspace(data.workspace);
  $("#approval-mode").value = data.settings.approval_mode;
  state.approvalMode = data.settings.approval_mode;

  const counts = data.counts;
  $("#badge-docs").textContent      = data.knowledge_base.documents || "";
  $("#badge-analysis").textContent  = (counts.requirements + counts.gaps) || "";
  $("#badge-questions").textContent = counts.questions_open || "";
  $("#badge-questions").dataset.blocking = counts.questions_blocking ? "1" : "";
  $("#badge-tasks").textContent     = counts.tasks || "";
  $("#badge-artifacts").textContent = counts.artifacts || "";
  $("#c-req").textContent = counts.requirements;
  $("#c-q").textContent = counts.questions;
  $("#c-gap").textContent = counts.gaps;
  $("#c-dec").textContent = counts.decisions;
  $("#c-res").textContent = counts.research_notes;

  renderPhaseRail(data.phases);
  renderStats(data);
  renderPhaseSummaries(data.phases);
  renderRunControls(data);
  renderQuestions(data.blocking_questions || []);
  // Form DEGIL, yalnizca durum metinleri tazelenir; kaydedilmemis
  // duzenlemeler yerinde kalir.
  if (state.view === "settings") refreshSettingsStatus();
  syncRunState(data.run);
  return true;
}

/* Hedef satiri. Ayri bir fonksiyon, cunku dil degistiginde de yeniden
   cizilmesi gerekiyor: "Hedef: ..." oneki sozlukten geliyor ve yalnizca
   `loadOverview` icinde yazilsaydi, dil degistirdikten sonra bir sonraki
   yoklamaya kadar eski dilde kalirdi -- Ingilizce ekranda "Hedef:". */
function renderGoalLine(goal) {
  $("#goal-line").textContent = goal
    ? t("overview.goalPrefix", { goal })
    : t("overview.noGoal");
}

/* Sol alt köşe: hangi çalışma alanındayız.
   Aynı makinede birden çok çalışma alanı açık olabiliyor ve pencereler
   birbirinin aynı görünüyordu -- yanlış olanda "Başlat"a basmak, hangi
   projede olduğunuzu ekranda hiçbir yerde yazmamasının bedeliydi.

   Yalnızca KLASÖR ADI yazılır. Ayırt eden şey zaten o; tam yol iki satır
   yer kaplıyor, ekran görüntüsüne ev dizinini sokuyor ve soruyu
   cevaplamıyordu. Yol ipucunda duruyor, tıklayınca panoya gidiyor. */
function renderWorkspace(path) {
  const kutu = $("#rail-workspace");
  if (!kutu) return;
  kutu.hidden = !path;
  if (!path) return;

  // Yol ayırıcısı Windows'ta `\`, başka yerde `/`. İkisini de böl.
  const parcalar = String(path).split(/[\\/]/).filter(Boolean);
  $("#rail-ws-name").textContent = parcalar[parcalar.length - 1] || path;
  // İpucu iki satır: yolun kendisi, ve tıklayınca ne olacağı. Düğmenin
  // üstünde `data-i18n-title` YOK -- olsaydı yolu her dil değişiminde
  // silerdi; ikinci satırı burada birleştirip dil değişiminde bu
  // fonksiyonu yeniden çağırıyoruz.
  $("#rail-ws-copy").title = `${path}\n${t("nav.workspaceCopy")}`;
  state.workspacePath = path;
}

// On uc esit nokta, on uc esit derecede onemli gorunuyordu -- ve on
// ucuncusu ("Canli") kutuya sigmayip yatay kaydirmanin arkasinda kaliyordu.
// Seridi ust asamalara boluyoruz: goz on uc adim yerine dort obek gorur,
// ve izgara sabit sutunla kurulunca hepsi ekrana sigar.
function renderPhaseRail(phases) {
  const rail = $("#phase-rail");
  const stages = [];
  for (const phase of phases) {
    const last = stages[stages.length - 1];
    if (!last || last.name !== phase.stage) stages.push({ name: phase.stage, phases: [phase] });
    else last.phases.push(phase);
  }

  const terminal = (status) => ["done", "skipped"].includes(status);
  const done = phases.filter((p) => terminal(p.status)).length;
  rail.style.setProperty("--phases", String(phases.length));
  rail.dataset.progress = `${done}/${phases.length}`;

  // Iki satirlik izgara: once asama basliklari (her biri kendi faz sayisi
  // kadar sutun kaplar), sonra fazlar. Belge sirasi satirlari kendiliginden
  // dogru diziyor; ayrica konumlandirma gerekmiyor.
  const heads = stages.map((stage) => {
    const inStage = stage.phases.filter((p) => terminal(p.status)).length;
    return `
      <li class="phase-stage" style="grid-column: span ${stage.phases.length}"
          data-full="${inStage === stage.phases.length ? 1 : 0}">
        <span class="phase-stage-name">${esc(t("stage." + stage.name))}</span>
        <span class="phase-stage-count">${inStage}/${stage.phases.length}</span>
      </li>`;
  }).join("");

  const steps = phases.map((phase, index) => {
    const previousDone = index > 0 && terminal(phases[index - 1].status);
    return `
      <li class="phase" data-status="${esc(phase.status)}" data-done="${previousDone ? 1 : 0}"
          data-first="${index === 0 ? 1 : 0}" data-last="${index === phases.length - 1 ? 1 : 0}"
          title="${esc(t("overview.phaseTitle", {
            summary: phase.summary || t("produces." + phase.phase),
            agent: t("agent." + phase.phase) }))}">
        <span class="phase-dot">${phase.status === "done" ? "✓" : phase.index + 1}</span>
        <span class="phase-label">${esc(t("phase." + phase.phase))}</span>
        <span class="phase-meta">${phase.cost ? fmtMoney(phase.cost) : ""}</span>
      </li>`;
  }).join("");

  rail.innerHTML = heads + steps;
}

function renderStats(data) {
  const c = data.counts;
  const kb = data.knowledge_base;
  const criticalHint = c.gaps ? t("stat.gapsHint") : "";
  const cards = [
    { label: t("stat.documents"), value: kb.documents, hint: t(kb.chunks === 1 ? "stat.chunkOne" : "stat.chunks", { n: kb.chunks }) },
    { label: t("stat.requirements"), value: c.requirements, hint: t("stat.requirementsHint") },
    { label: t("stat.gaps"), value: c.gaps, hint: criticalHint, tone: c.gaps ? "warn" : "" },
    {
      label: t("stat.questions"),
      value: `${c.questions_open}/${c.questions}`,
      hint: c.questions_blocking
        ? t("stat.questionsBlocking", { n: c.questions_blocking })
        : t("stat.questionsHint"),
      tone: c.questions_blocking ? "err" : "",
    },
    { label: t("stat.decisions"), value: c.decisions, hint: t("stat.decisionsHint") },
    { label: t("stat.tasks"), value: `${c.tasks_done}/${c.tasks}`, hint: t("stat.tasksHint"), tone: c.tasks && c.tasks_done === c.tasks ? "ok" : "" },
    { label: t("stat.artifacts"), value: c.artifacts, hint: t("stat.artifactsHint") },
    { label: t("stat.cost"), value: fmtMoney(data.total_cost), hint: data.settings.cost_limit_usd ? t("stat.costCap", { n: fmtMoney(data.settings.cost_limit_usd) }) : t("stat.noCap") },
  ];
  $("#stat-grid").innerHTML = cards.map((card) => `
    <div class="stat"${card.tone ? ` data-tone="${card.tone}"` : ""}>
      <div class="stat-label">${esc(card.label)}</div>
      <div class="stat-value">${esc(card.value)}</div>
      <div class="stat-hint">${esc(card.hint)}</div>
    </div>`).join("");
}

// Genel bakis bir ozet panosudur: her fazin kendi ozetini burada okursunuz.
// Faz seridi *durumu*, bu panel *ne yapildigini* gosterir.
const SUMMARY_LIMIT = 420;

function renderPhaseSummaries(phases) {
  const target = $("#phase-summaries");
  const ran = phases.filter((p) => p.status !== "pending");
  const hint = $("#summary-hint");

  if (!ran.length) {
    hint.textContent = "";
    target.innerHTML = emptyState(
      t("overview.noPhases"), t("overview.noPhasesHint"),
    );
    return;
  }

  const done = ran.filter((p) => p.status === "done").length;
  hint.textContent = t("overview.phasesDone", { done, total: phases.length });

  // En yeni is en ustte: kullanici once son ne olduguna bakar.
  target.innerHTML = [...ran].reverse().map((phase) => {
    const summary = (phase.summary || "").trim();
    const clipped = summary.length > SUMMARY_LIMIT;
    return `
      <article class="phase-summary" data-status="${esc(phase.status)}">
        <header>
          <span class="phase-summary-index">${phase.index + 1}</span>
          <span class="phase-summary-label">${esc(t("phase." + phase.phase))}</span>
          <span class="phase-summary-agent">${esc(t("agent." + phase.phase))}</span>
          <span class="badge" data-v="${esc(phase.status)}">${esc(tv("status", phase.status))}</span>
          ${phase.cost ? `<span class="phase-summary-cost">${fmtMoney(phase.cost)}</span>` : ""}
        </header>
        <p>${summary ? esc(summary.slice(0, SUMMARY_LIMIT)) + (clipped ? " …" : "") : `<em>${esc(t("overview.noSummary"))}</em>`}</p>
      </article>`;
  }).join("");
}

// ─── Adim secimi ──────────────────────────────────────────────────────────
// Kullanici hangi adimlarin kosacagini tek tek secer. Ilk adim (`ingest`)
// zorunludur: bilgi tabani bos ise sonraki hicbir ajanin okuyacagi sey olmaz.
const REQUIRED_PHASE = "ingest";

const STEP_PRESETS = {
  all:      (phases) => phases.map((p) => p.phase),
  analysis: () => ["ingest", "analyze", "research", "assess"],
  build:    () => ["ingest", "mockup", "design", "plan", "implement", "qa", "review"],
  clear:    () => [REQUIRED_PHASE],
};

// Ilk yuklemede makul bir varsayilan: analizden plana kadar.
let selectedPhases = new Set(["ingest", "analyze", "research", "assess", "mockup", "design", "plan"]);

function chosenPhases() {
  // Boru hatti sirasina gore; kullanicinin tiklama sirasi degil.
  return state.phases.filter((p) => selectedPhases.has(p.phase)).map((p) => p.phase);
}

function renderStepList(phases) {
  const list = $("#run-phases");

  // Adimlar ust asamalara gruplanir: on uc satirlik duz bir liste, nerede
  // oldugunu kaybettiriyordu. Grup basliklari boru hattinin sekli oluyor.
  const stages = [];
  for (const phase of phases) {
    const last = stages[stages.length - 1];
    if (!last || last.name !== phase.stage) stages.push({ name: phase.stage, phases: [phase] });
    else last.phases.push(phase);
  }

  list.innerHTML = stages.map((stage) => {
    const picked = stage.phases.filter((p) => selectedPhases.has(p.phase) || p.phase === REQUIRED_PHASE);
    return `
    <li class="step-stage">
      <button class="step-stage-head" type="button" data-stage="${esc(stage.name)}"
              title="${esc(t("develop.selectStage"))}">
        <span class="step-stage-name">${esc(t("stage." + stage.name))}</span>
        <span class="step-stage-count">${picked.length}/${stage.phases.length}</span>
      </button>
      <ul>${stage.phases.map((p) => {
        const required = p.phase === REQUIRED_PHASE;
        const checked = required || selectedPhases.has(p.phase);
        return `
        <li class="step" data-status="${esc(p.status)}" data-on="${checked ? 1 : 0}"
            ${required ? 'data-required="1"' : ""}>
          <label>
            <input type="checkbox" value="${esc(p.phase)}"${checked ? " checked" : ""}
                   ${required ? "disabled" : ""} aria-label="${esc(t("phase." + p.phase))}">
            <span class="step-index">${p.index + 1}</span>
            <span class="step-main">
              <span class="step-title">
                <span class="step-label">${esc(t("phase." + p.phase))}</span>
                ${p.agent && p.agent !== "—"
                  ? `<span class="step-agent">${esc(t("agent." + p.phase))}</span>`
                  : `<span class="step-agent step-agent-none">${esc(t("develop.noModel"))}</span>`}
              </span>
              <span class="step-produces" title="${esc(t("produces." + p.phase))}">${esc(t("produces." + p.phase))}</span>
            </span>
            <span class="step-tail">
              ${required ? `<span class="step-note">${esc(t("develop.required"))}</span>` : ""}
              ${p.status === "done" ? `<span class="badge" data-v="done">${esc(t("status.done"))}</span>` : ""}
              ${p.cost ? `<span class="step-cost">${fmtMoney(p.cost)}</span>` : ""}
            </span>
          </label>
        </li>`;
      }).join("")}</ul>
    </li>`;
  }).join("");

  $$("[data-stage]", list).forEach((head) => {
    head.addEventListener("click", () => {
      const group = phases.filter((p) => p.stage === head.dataset.stage);
      const optional = group.filter((p) => p.phase !== REQUIRED_PHASE);
      const allOn = optional.every((p) => selectedPhases.has(p.phase));
      optional.forEach((p) => {
        if (allOn) selectedPhases.delete(p.phase);
        else selectedPhases.add(p.phase);
      });
      renderStepList(phases);
    });
  });

  syncStepCount();
}

function syncStepCount() {
  const chosen = chosenPhases();
  const labels = state.phases.filter((p) => chosen.includes(p.phase));
  $("#step-count").textContent = chosen.length
    ? t("develop.stepsSelected", { n: chosen.length })
    : t("develop.noSteps");
  const route = $("#run-route");
  // Tek satir, sabit yukseklik. Hap bulutu secime gore 23-84 piksel arasinda
  // gidip geliyor ve her tikta panelin altini oynatiyordu.
  const yol = labels.map((p) => t("phase." + p.phase)).join("  →  ");
  route.textContent = yol;
  route.title = yol;
  $("#btn-run").disabled = !chosen.length || Boolean(state.overview?.run?.running);
}

let runControlsReady = false;
function renderRunControls(data) {
  // Liste her tazelemede yeniden cizilir (durum rozetleri guncellensin diye),
  // ama kullanicinin secimi `selectedPhases` icinde durdugu icin korunur.
  renderStepList(data.phases);
  if (!runControlsReady) {
    $("#run-goal").value = data.goal || "";
    $("#run-brief").value = data.brief || "";
    runControlsReady = true;
  }
  $("#run-hint").textContent = data.settings.has_api_key
    ? t("develop.approvalAuto", { mode: t("nav.approval" + {
        ask: "Ask", auto: "Auto", "dry-run": "Dry" }[data.settings.approval_mode]) })
    : t("develop.noKey");
}

function initStepPicker() {
  $("#run-phases").addEventListener("change", (event) => {
    const box = event.target.closest("input[type=checkbox]");
    if (!box) return;
    if (box.checked) selectedPhases.add(box.value);
    else selectedPhases.delete(box.value);
    selectedPhases.add(REQUIRED_PHASE);
    syncStepCount();
  });

  $$("[data-preset]").forEach((button) => {
    button.addEventListener("click", () => {
      selectedPhases = new Set(STEP_PRESETS[button.dataset.preset](state.phases));
      selectedPhases.add(REQUIRED_PHASE);
      renderStepList(state.phases);
    });
  });
}

function syncRunState(run) {
  const pill = $("#run-pill");
  const running = run.running;
  const last = run.last;

  let stateName = "idle";
  let text = t("app.idle");
  if (running) {
    stateName = "running";
    const current = run.current;
    text = run.stopping
      ? t("app.stopping")
      : t("app.runningPhases", { n: current ? current.phases.length : "?" });
  } else if (last) {
    stateName = last.status;
    text = {
      done: t("app.doneRun"),
      failed: t("app.failedRun"),
      cancelled: t("app.cancelledRun"),
      needs_input: t("app.needsInputRun"),
    }[last.status] || tv("status", last.status);
  }
  pill.dataset.state = stateName;
  $("#run-pill-text").textContent = text;

  $("#btn-run").disabled = running || !chosenPhases().length;
  $("#btn-stop").disabled = !running;
  $("#rail-live").hidden = !running;
  $("#rail-running").hidden = !running;

  const note = $("#run-note");
  if (running && run.current) {
    note.dataset.tone = "";
    note.textContent = `${run.current.phases.join(" → ")} · ${run.current.elapsed}s`;
  } else if (last) {
    note.dataset.tone = last.status === "done" ? "ok" : last.status === "failed" ? "err" : "warn";
    if (last.status === "needs_input") {
      note.textContent =
        t("develop.runBlocked", { keys: last.pending_questions.join(", ") });
    } else {
      note.textContent = last.error
        ? last.error
        : t("develop.lastRun", {
            status: tv("status", last.status),
            cost: fmtMoney(last.cost), secs: last.elapsed });
    }
  }

  state.approvals = run.pending_approvals || [];
  renderApproval();

  if (running && state.view === "workflow") loadWorkflow();
  if (running && !state.pollTimer) {
    state.pollTimer = setInterval(loadOverview, 2500);
  } else if (!running && state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
    refreshActiveView();
  }
}

function refreshActiveView() {
  if (state.view === "analysis")  loadAnalysis();
  if (state.view === "plan")      { loadPlans(); loadTasks(); }
  if (state.view === "artifacts") loadArtifacts();
  if (state.view === "knowledge") loadDocuments();
  if (state.view === "develop")   loadDocuments();
  if (state.view === "workflow")  loadWorkflow();
}

function initRunControls() {
  $("#btn-run").addEventListener("click", async () => {
    const note = $("#run-note");
    const phases = chosenPhases();
    if (!phases.length) {
      note.dataset.tone = "err";
      note.textContent = t("develop.pickStep");
      return;
    }
    note.dataset.tone = "";
    note.textContent = t("develop.starting");
    try {
      await post("/api/run", {
        phases,
        goal: $("#run-goal").value.trim(),
        brief: $("#run-brief").value.trim(),
        force: $("#run-force").checked,
      });
      toast(t("develop.started", { n: phases.length }), "ok");
      loadOverview();
    } catch (error) {
      note.dataset.tone = "err";
      note.textContent = error.message;
      toast(error.message, "err", 7000);
    }
  });

  $("#btn-stop").addEventListener("click", async () => {
    try {
      await post("/api/run/stop");
      toast(t("develop.stopRequested"), "warn");
      loadOverview();
    } catch (error) {
      toast(error.message, "err");
    }
  });

  $("#approval-mode").addEventListener("change", async (event) => {
    try {
      await post("/api/settings", { approval_mode: event.target.value });
      toast(t("nav.approvalChanged", { mode: event.target.value }), "ok");
      loadOverview();
    } catch (error) {
      toast(error.message, "err");
    }
  });
}

// ─── Bilgi tabani ─────────────────────────────────────────────────────────
// Gelistirme sekmesindeki kompakt liste: "modele su an ne verilmis durumda".
function renderUploadedDocs(documents) {
  const target = $("#uploaded-docs");
  if (!target) return;
  target.innerHTML = documents.length
    ? `<ul class="doc-chips">${documents.map((doc) => `
        <li title="${esc(doc.source)}">
          <span class="badge">${esc(tv("kind", doc.kind))}</span>
          <span class="doc-chip-name">${esc(doc.title)}</span>
          <span class="doc-chip-meta">${esc(t(doc.n_chunks === 1 ? "stat.chunkOne" : "stat.chunks", { n: doc.n_chunks }))}</span>
        </li>`).join("")}</ul>`
    : `<p class="empty">${esc(t("develop.noDocs"))}</p>`;
}

async function loadDocuments() {
  const target = $("#doc-table");
  try {
    const data = await api("/api/documents");
    const stats = data.stats;
    $("#kb-sub").textContent = t("kb.stats", {
      docs: stats.documents, chunks: stats.chunks, model: stats.embedding_model });
    renderUploadedDocs(data.documents);
    state.docItems = data.documents;
    renderDocPage();
  } catch (error) {
    target.innerHTML = emptyState(t("app.failed"), error.message);
    $("#doc-pager").hidden = true;
  }
}

function renderDocPage() {
  const target = $("#doc-table");
  const all = state.docItems;
  if (!all.length) {
    target.innerHTML = emptyState(t("kb.empty"), t("kb.emptyHint"));
    $("#doc-pager").hidden = true;
    return;
  }

  const slice = slicePage(all, state.docPage, state.docSize);
  state.docPage = slice.page;

  target.innerHTML = `
    <div class="table-wrap"><table>
      <thead><tr><th>${esc(t("kb.document"))}</th><th>${esc(t("kb.type"))}</th><th style="text-align:right">${esc(t("kb.chunks"))}</th><th></th></tr></thead>
      <tbody>${slice.items.map((doc) => `
        <tr>
          <td><div>${esc(doc.title)}</div>
              <div style="font-size:11px;color:var(--text-3);overflow-wrap:anywhere">${esc(doc.source)}</div></td>
          <td><span class="badge">${esc(doc.kind)}</span></td>
          <td class="num">${doc.n_chunks}</td>
          <td><button class="btn btn-ghost btn-sm" data-forget="${esc(doc.source)}" type="button">${esc(t("kb.remove"))}</button></td>
        </tr>`).join("")}
      </tbody>
    </table></div>`;

  $$("[data-forget]", target).forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        const result = await post("/api/forget", { source: button.dataset.forget });
        toast(t("kb.removed", { n: result.removed_chunks }), "ok");
        loadDocuments();
        loadOverview();
      } catch (error) { toast(error.message, "err"); }
    });
  });

  renderPager($("#doc-pager"), {
    total: all.length,
    page: slice.page,
    size: state.docSize,
    unit: t("kb.docsUnit"),
    onPage: (page) => {
      state.docPage = page;
      renderDocPage();
      $("#doc-table").scrollIntoView({ block: "start", behavior: "smooth" });
    },
    onSize: (size) => {
      state.docSize = size;
      state.docPage = 1;
      renderDocPage();
    },
  });
}

function initKnowledge() {
  $("#ingest-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.target.querySelector("button");
    button.disabled = true;
    button.innerHTML = `<span class="spinner"></span> ${esc(t("kb.indexingNow"))}`;
    try {
      const result = await post("/api/ingest", {
        path: $("#ingest-path").value.trim(),
        force: $("#ingest-force").checked,
      });
      toast(result.summary || t("kb.indexDone"), "ok", 6000);
      loadDocuments();
      loadOverview();
    } catch (error) {
      toast(error.message, "err", 7000);
    } finally {
      button.disabled = false;
      button.textContent = t("kb.indexBtn");
    }
  });

  $("#search-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const query = $("#search-input").value.trim();
    if (!query) return;
    const target = $("#search-results");
    target.innerHTML = '<p class="empty"><span class="spinner"></span></p>';

    const kinds = $$("#search-kinds input:checked").map((input) => input.value);
    try {
      const data = await post("/api/search", { query, k: 8, kinds: kinds.length === 4 ? null : kinds });
      if (!data.hits.length) {
        target.innerHTML = emptyState(t("kb.noResults"), t("kb.noResultsHint"));
        return;
      }
      target.innerHTML = data.hits.map((hit) => `
        <article class="result">
          <div class="result-head">
            <span class="result-cite">${esc(hit.citation)}</span>
            <span class="result-score">${hit.score.toFixed(4)} · ${esc(hit.kind)}</span>
          </div>
          <div class="result-body">${esc(hit.text)}</div>
        </article>`).join("");
    } catch (error) {
      target.innerHTML = emptyState(t("kb.searchFailed"), error.message);
    }
  });
}

// ─── Analiz ───────────────────────────────────────────────────────────────
// ─── Kimlik dogrulama ─────────────────────────────────────────────────────
// Kimlik dogrulama yalnizca en az bir kullanici varsa devrededir: yerel tek
// kullanicili kurulum bugunku gibi calisir, disari acilan sunucu ise
// kullanicisiz hic baslamaz (sunucu tarafinda engellenir).

async function checkAuth() {
  try {
    const status = await api("/api/auth/status");
    state.auth = status;
    if (status.user || !status.configured) {
      $("#auth-gate").hidden = true;
      return true;
    }
  } catch (error) {
    // Durum alinamiyorsa da giris ekranini goster; uygulama zaten calismaz.
    state.auth = { configured: true, user: null };
  }
  showGate();
  return false;
}

function showGate() {
  const gate = $("#auth-gate");
  const setup = state.auth && !state.auth.configured;
  $("#setup-token-field").hidden = !setup;
  $("#gate-lead").textContent = setup ? t("auth.setupLead") : t("auth.lead");
  $("#login-submit").textContent = setup ? t("auth.createAdmin") : t("auth.login");
  $("#login-password").autocomplete = setup ? "new-password" : "current-password";
  gate.hidden = false;
  $("#login-username").focus();
}

function initAuth() {
  $("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const note = $("#login-note");
    const button = $("#login-submit");
    const setup = state.auth && !state.auth.configured;
    button.disabled = true;
    note.dataset.tone = "";
    note.innerHTML = `<span class="spinner"></span> ${esc(t("auth.checking"))}`;
    try {
      const payload = {
        username: $("#login-username").value.trim(),
        password: $("#login-password").value,
      };
      if (setup) payload.token = $("#setup-token").value.trim();
      await post(setup ? "/api/auth/setup" : "/api/auth/login", payload);
      $("#login-password").value = "";
      $("#setup-token").value = "";
      note.textContent = "";
      $("#auth-gate").hidden = true;
      await checkAuth();
      boot();
    } catch (error) {
      note.dataset.tone = "err";
      note.textContent = error.message;
      $("#login-password").select();
    } finally {
      button.disabled = false;
      if (!$("#auth-gate").hidden) $("#login-submit").textContent =
        (state.auth && !state.auth.configured) ? t("auth.createAdmin") : t("auth.login");
    }
  });

  $("#btn-logout").addEventListener("click", async () => {
    await post("/api/auth/logout");
    location.reload();
  });

  $("#btn-change-password").addEventListener("click", async () => {
    const note = $("#account-note");
    note.dataset.tone = "";
    try {
      await post("/api/auth/password", {
        current: $("#pw-current").value,
        password: $("#pw-new").value,
      });
      $("#pw-current").value = $("#pw-new").value = "";
      note.dataset.tone = "ok";
      note.textContent = t("account.changed");
    } catch (error) {
      note.dataset.tone = "err";
      note.textContent = error.message;
    }
  });

  $("#user-new").addEventListener("submit", async (event) => {
    event.preventDefault();
    const note = $("#users-note");
    note.dataset.tone = "";
    try {
      const user = await post("/api/users", {
        username: $("#new-username").value.trim(),
        password: $("#new-password").value,
        role: $("#new-role").value,
      });
      $("#new-username").value = $("#new-password").value = "";
      toast(t("users.added", { name: user.user.username }), "ok");
      loadUsers();
    } catch (error) {
      note.dataset.tone = "err";
      note.textContent = error.message;
    }
  });
}

async function loadUsers() {
  const me = state.auth?.user;
  $("#account-panel").hidden = !me;
  $("#users-panel").hidden = !(me && me.role === "admin");
  if (!me) return;

  $("#account-who").textContent = `${me.username} · ${me.role}`;
  if (me.role !== "admin") return;

  try {
    const data = await api("/api/users");
    $("#users-hint").textContent = t("users.count", { n: data.users.length });
    $("#users-table").innerHTML = `
      <div class="table-wrap"><table>
        <thead><tr><th>${esc(t("users.username"))}</th><th>${esc(t("users.role"))}</th><th>${esc(t("users.state"))}</th><th>${esc(t("users.lastLogin"))}</th><th></th></tr></thead>
        <tbody>${data.users.map((u) => `
          <tr${u.is_active ? "" : ' class="user-off"'}>
            <td>${esc(u.username)}${u.is_master ? ` <span class="badge" data-v="ready">${esc(t("users.master"))}</span>` : ""}</td>
            <td>${esc(u.role === "admin" ? t("users.roleAdmin") : t("users.roleUser"))}</td>
            <td>${u.is_active
              ? `<span class="badge" data-v="done">${esc(t("status.active"))}</span>`
              : `<span class="badge" data-v="blocked">${esc(t("status.inactive"))}</span>`}</td>
            <td>${u.last_login ? esc(fmtWhen(u.last_login)) : "—"}</td>
            <td class="user-actions">
              ${u.is_master || u.id === me.id ? "" : `
                <button class="btn btn-ghost btn-sm" data-active-for="${u.id}"
                        data-next="${u.is_active ? "0" : "1"}" data-name="${esc(u.username)}">
                  ${esc(u.is_active ? t("users.close") : t("users.openBtn"))}
                </button>`}
              ${u.is_master ? "" : `
                <button class="btn btn-ghost btn-sm" data-role-for="${u.id}"
                        data-next="${u.role === "admin" ? "user" : "admin"}">
                  ${esc(u.role === "admin" ? t("users.revokeAdmin") : t("users.makeAdmin"))}
                </button>`}
              ${u.id === me.id || u.is_master ? "" : `
                <button class="btn btn-ghost btn-sm" data-del-user="${u.id}"
                        data-name="${esc(u.username)}">${esc(t("app.delete"))}</button>`}
            </td>
          </tr>`).join("")}
        </tbody>
      </table></div>`;

    $$("[data-active-for]", $("#users-table")).forEach((button) => {
      button.addEventListener("click", async () => {
        const opening = button.dataset.next === "1";
        // Kapatmak acik oturumlari dusurur; kullanici bunu bilmeli.
        if (!opening && !confirm(
              t("users.closeConfirm", { name: button.dataset.name }))) return;
        try {
          await post(`/api/users/${button.dataset.activeFor}`, { active: opening });
          toast(t(opening ? "users.opened" : "users.closed",
                        { name: button.dataset.name }), opening ? "ok" : "warn");
          loadUsers();
        } catch (error) { toast(error.message, "err", 7000); }
      });
    });

    $$("[data-role-for]", $("#users-table")).forEach((button) => {
      button.addEventListener("click", async () => {
        try {
          await post(`/api/users/${button.dataset.roleFor}`, { role: button.dataset.next });
          loadUsers();
        } catch (error) { toast(error.message, "err", 7000); }
      });
    });
    $$("[data-del-user]", $("#users-table")).forEach((button) => {
      button.addEventListener("click", async () => {
        if (!confirm(t("users.deleteConfirm", { name: button.dataset.name }))) return;
        try {
          await api(`/api/users/${button.dataset.delUser}`, { method: "DELETE" });
          toast(t("users.deleted"), "warn");
          loadUsers();
        } catch (error) { toast(error.message, "err", 7000); }
      });
    });
  } catch (error) {
    $("#users-table").innerHTML = emptyState(t("app.failed"), error.message);
  }
}

// ─── Denetim günlüğü ──────────────────────────────────────────────────────
// "Kim ne zaman girdi, ne çalıştırdı." Kullanıcılar panelinin yanıtladığı
// soru "şu an kim var"; bu panelinki "ne oldu". İkisi ayrı: silinen bir
// hesabın izleri burada kalır, listede kalmaz.

/* Yola tıklayınca panoya kopyalanır: terminale yapıştırmak, ekrandaki
   yolu elle yazmaktan hem hızlı hem yanılmaz. */
function initWorkspace() {
  $("#rail-ws-copy").addEventListener("click", async () => {
    const yol = state.workspacePath;
    if (!yol) return;
    try {
      await navigator.clipboard.writeText(yol);
      toast(t("nav.workspaceCopied"), "ok");
    } catch {
      // Pano izni yoksa ya da sayfa güvenli bağlamda değilse yolu SÖYLE.
      // Sessizce hiçbir şey yapmamak düğmenin bozuk olduğunu düşündürür;
      // yol ekranda yazmadığı için de başka bir yerden okunamaz.
      toast(yol, "warn", 12000);
    }
  });
}

function initAudit() {
  $("#btn-audit-refresh").addEventListener("click", () => loadAudit());
  ["#audit-user", "#audit-action", "#audit-limit"].forEach((sel) => {
    $(sel).addEventListener("change", () => loadAudit());
  });
}

/** Süzgeç açılır listesini, seçili değeri KAYBETMEDEN doldurur. */
function fillFilter(select, values, allLabel, label) {
  const secili = select.value;
  select.innerHTML =
    `<option value="">${esc(allLabel)}</option>` +
    values.map((v) => `<option value="${esc(v)}">${esc(label(v))}</option>`).join("");
  // Seçilen değer artık listede yoksa "hepsi"ne düşer; sessizce başka bir
  // süzgece atlamak, yönetici baktığı şeyin değiştiğini fark etmez.
  select.value = values.includes(secili) ? secili : "";
}

async function loadAudit() {
  const panel = $("#audit-panel");
  const me = state.auth?.user;
  // Kimlik doğrulama kapalıyken (hiç kullanıcı yokken) sunucunun tamamı
  // zaten açık; günlüğü tek başına gizlemek hiçbir şey korumaz, yalnızca
  // yerel kurulumda paneli ölü bırakırdı.
  const gorunur = state.auth?.configured === false || me?.role === "admin";
  panel.hidden = !gorunur;
  if (!gorunur) return;

  const parametre = new URLSearchParams({ limit: $("#audit-limit").value });
  if ($("#audit-user").value) parametre.set("user", $("#audit-user").value);
  if ($("#audit-action").value) parametre.set("action", $("#audit-action").value);

  try {
    const data = await api(`/api/audit?${parametre}`);
    $("#audit-hint").textContent =
      t("audit.count", { shown: data.entries.length, total: data.total });
    $("#audit-note").textContent = t("audit.note", { kept: data.kept });

    // Süzgeç listeleri sunucudan gelir ve o an GÖRÜNEN satırlardan
    // üretilmez: "koşu" türünü seçmek kullanıcı listesini de koşu
    // başlatmış olanlara indirirdi ve ikinci bir süzgeç seçilemezdi.
    fillFilter($("#audit-user"), data.users, t("audit.allUsers"), (v) => v);
    // İşlem süzgecinde tür başına tek satır: `run.start` ve `run.stop`
    // yerine `run` seçilir, sunucu ikisini birden getirir.
    const turler = [...new Set(data.actions.map((a) => a.split(".")[0]))].sort();
    fillFilter($("#audit-action"), turler, t("audit.allActions"),
               (v) => tv("audit.grp", v));

    if (!data.entries.length) {
      $("#audit-table").innerHTML = emptyState(
        t(data.total ? "audit.empty" : "audit.emptyAll"));
      return;
    }

    $("#audit-table").innerHTML = `
      <div class="table-wrap audit-wrap"><table class="audit-table">
        <thead><tr>
          <th>${esc(t("audit.when"))}</th>
          <th>${esc(t("audit.who"))}</th>
          <th>${esc(t("audit.what"))}</th>
          <th>${esc(t("audit.detail"))}</th>
          <th>${esc(t("audit.from"))}</th>
        </tr></thead>
        <tbody>${data.entries.map((e) => `
          <tr${e.ok ? "" : ' class="audit-bad"'}>
            <td class="audit-when">${esc(fmtWhen(e.at))}</td>
            <td class="audit-who">${e.username ? esc(e.username) : "—"}${
              e.username && e.user_id === null && e.ok
                ? ` <span class="audit-gone" title="${esc(t("audit.gone"))}">†</span>`
                : ""}</td>
            <td>${esc(tv("audit.act", e.action))}${
              e.ok ? "" : ` <span class="badge" data-v="failed">${esc(t("audit.failed"))}</span>`}</td>
            <td class="audit-detail">${esc(auditDetail(e))}</td>
            <td class="audit-from" title="${esc(e.agent)}">${esc(e.ip || "—")}</td>
          </tr>`).join("")}
        </tbody>
      </table></div>`;
  } catch (error) {
    $("#audit-table").innerHTML = emptyState(t("app.failed"), error.message);
  }
}

/** Satırın ayrıntısı. Çevrilebilir hali varsa o kullanılır. */
function auditDetail(entry) {
  if (!entry.detail_key) return entry.detail || "";
  return runTitle({
    title: entry.detail,
    title_key: entry.detail_key,
    title_args: entry.detail_args,
  });
}

// ─── Ayarlar ──────────────────────────────────────────────────────────────
// Alanlar tek bir tablodan surulur: HTML kimligi -> ayar adi + tur. Yeni bir
// ayar eklemek burada da tek satir, sunucudaki tabloyla ayni sekilde.

const EFFORTS = ["low", "medium", "high", "max"];

// [kimlik, ayar adi, tur]  tur: text | number | float | bool | select
const SETTING_INPUTS = [
  ["#set-provider",        "provider",                "select"],
  ["#set-google-cx",       "google_cse_id",           "text"],
  ["#set-searxng-url",     "searxng_url",             "text"],
  ["#set-base-url",        "openai_base_url",         "text"],
  ["#set-model-lead",      "model_lead",              "text"],
  ["#set-model-worker",    "model_worker",            "text"],
  ["#set-model-fast",      "model_fast",              "text"],
  ["#set-effort-lead",     "effort_lead",             "select"],
  ["#set-effort-worker",   "effort_worker",           "select"],
  ["#set-effort-fast",     "effort_fast",             "select"],
  ["#set-max-tokens",      "max_tokens",              "number"],
  ["#set-timeout",         "request_timeout_seconds", "number"],
  ["#set-temperature",     "temperature",             "float"],
  ["#set-thinking",        "thinking_display",        "select"],
  ["#set-approval",        "approval_mode",           "select"],
  ["#set-iterations",      "max_iterations",          "number"],
  ["#set-cost",            "cost_limit_usd",          "float"],
  ["#set-tool-chars",      "max_tool_output_chars",   "number"],
  ["#set-turn-chars",      "max_turn_output_chars",   "number"],
  ["#set-web",             "enable_web",              "bool"],
  ["#set-search-provider", "search_provider",         "select"],
  ["#set-language",        "language",                "select"],
  ["#set-log-level",       "log_level",               "select"],
  ["#set-browser-channel", "browser_channel",         "select"],
  ["#set-browser-headless", "browser_headless",       "bool"],
  ["#set-browser-allow-preview", "browser_allow_preview", "bool"],
  ["#set-browser-idle",    "browser_idle_seconds",    "number"],
  ["#set-execution",       "execution",               "select"],
  ["#set-sandbox-image",   "sandbox_image",           "text"],
  ["#set-sandbox-setup",   "sandbox_setup",           "text"],
  ["#set-sandbox-port-base",  "sandbox_port_base",    "number"],
  ["#set-sandbox-port-count", "sandbox_port_count",   "number"],
  ["#set-sandbox-memory",  "sandbox_memory",          "text"],
  ["#set-sandbox-cpus",    "sandbox_cpus",            "float"],
  ["#set-sandbox-pids",    "sandbox_pids",            "number"],
];

// Bos birakildiginda "degistirme" anlamina gelen gizli alanlar.
const SECRET_INPUTS = [
  ["#set-openai-key",    "openai_api_key"],
  ["#set-anthropic-key", "anthropic_api_key"],
  ["#set-search-key",    "search_api_key"],
];

let effortsReady = false;

/* Ayarlar ekrani iki parcaya ayrildi.

   `renderSettings` FORMU doldurur ve yalnizca ekrana girildiginde ya da
   kaydettikten sonra calisir. Yoklamadan cagrilinca kullanicinin heniz
   kaydetmedigi her secimi geri aliyordu: saglayiciyi Groq yapip on iki
   saniye beklerseniz sessizce vLLM'e donuyordu -- ve neden dondugunu
   kimse anlamiyordu.

   `refreshSettingsStatus` yalnizca OKUNUR durum metinlerini tazeler
   (anahtar var mi, model hazir mi). Onlarin guncel kalmasi gerekiyor
   ama hicbiri kullanicinin yazdigi bir alan degil. */
function renderSettings() {
  const s = state.overview?.settings;
  // Veri yoksa form BOS kalmasin: kullanici bos bir formu "hicbir sey
  // ayarli degil" diye okur, oysa istek dusmustur. Ne oldugunu yaz.
  const uyari = $("#settings-unavailable");
  if (uyari) {
    uyari.hidden = Boolean(s);
    uyari.textContent = s ? "" : t("settings.unavailable", {
      msg: state.overviewError || t("app.loading"),
    });
  }
  $("#view-settings").dataset.ready = s ? "1" : "0";
  if (!s) return;

  if (!effortsReady) {
    for (const id of ["#set-effort-lead", "#set-effort-worker", "#set-effort-fast"]) {
      $(id).innerHTML = EFFORTS.map((e) => `<option value="${e}">${e}</option>`).join("");
    }
    effortsReady = true;
  }

  for (const [id, name, kind] of SETTING_INPUTS) {
    const node = $(id);
    if (kind === "bool") node.checked = Boolean(s[name]);
    else node.value = s[name] ?? "";
  }
  for (const [id, name] of SECRET_INPUTS) {
    $(id).value = "";
    $(id).placeholder = s[`has_${name}`] ? t("settings.keySet") : t("settings.keyUnset");
  }

  loadProviders().then(() => {
    // Dil degismis olabilir; etiketler ceviriden geciyor.
    renderProviderOptions();
    const key = (providerCatalog.providers.find(
      (p) => p.base_url && s.openai_base_url &&
             p.base_url.replace(/\/$/, "") === s.openai_base_url.replace(/\/$/, "")
    ) || {}).key || (s.provider === "anthropic" ? "anthropic" : "custom");
    $("#set-preset").value = key;
    describePreset(providerCatalog.providers.find((p) => p.key === key) || {});
  }).catch(() => { /* katalog gelmezse elle adres yazilir */ });

  // Anthropic'e gecince yerel uc alanlari anlamsiz kalir; tersi de gecerli.
  const local = s.provider === "openai";
  $("#field-base-url").hidden = !local;
  $("#field-openai-key").hidden = !local;
  $("#field-anthropic-key").hidden = local;

  refreshSettingsStatus();
}

// Bes arama saglayicisindan UCU anahtarsiz. Arayuz yalnizca `duckduckgo`yu
// anahtarsiz sayiyordu ve VARSAYILAN saglayici (`browser`) secildiginde
// ayarlar ekrani kirmizi "ANAHTAR YOK - arama calismaz" gosteriyordu -- oysa
// arama calisiyor. Her yeni kurulum bu yanlis uyariyla aciliyordu.
const ANAHTARSIZ_ARAMA = {
  browser: "settings.searchStateBrowser",
  searxng: "settings.searchStateSelfHosted",
  duckduckgo: "settings.keylessEndpoint",
};

/** Arama durumu: [metin, hatali mi]. */
function aramaDurumu(saglayici, anahtarVar, henuz) {
  const anahtarsiz = ANAHTARSIZ_ARAMA[saglayici];
  if (anahtarsiz) return [t(anahtarsiz), false];
  if (anahtarVar) return [t("settings.keyDefined"), false];
  return [t(henuz ? "settings.noKeyYet" : "settings.noKey"), true];
}


/** Hangi arama alani hangi saglayicida anlamli. */
function aramaAlanlariniGoster(saglayici) {
  // Anahtar isteyen saglayicilar disinda anahtar kutusu yalnizca kafa
  // karistirir; `cx` yalnizca Google'in, adres yalnizca SearXNG'nin isi.
  const anahtarli = saglayici === "brave" || saglayici === "tavily"
                 || saglayici === "google";
  const alan = (id) => document.querySelector(id);
  if (alan("#field-google-cx")) alan("#field-google-cx").hidden = saglayici !== "google";
  if (alan("#field-searxng-url")) alan("#field-searxng-url").hidden = saglayici !== "searxng";
  const anahtarAlani = alan("#set-search-key");
  if (anahtarAlani && anahtarAlani.closest(".field")) {
    anahtarAlani.closest(".field").hidden = !anahtarli;
  }
}


/* Yalitim: konak mi konteyner mi.

   Konak secildiginde imaj, port araligi ve kaynak sinirlari OKUNMAZ;
   gosterilmeleri "sinirlar uygulaniyor" izlenimi verirdi. Yalitimin ne
   koruyup ne korumadigi da burada acikca yazilir: calisma alani
   konteynere baglanir, yani makine korunur ama proje korunmaz. */
function yalitimiGoster(kip) {
  // Sunucu bu ayari hic bildirmiyorsa (arayuz yeni, sunucu eski) panel
  // sekiz bos kutu olarak duruyordu. Bos kutu, ayarin var olup
  // tanimsiz oldugunu dusundurur; yoklugu soylemek daha durust.
  const panel = $("#sandbox-fields")?.closest(".panel");
  if (panel) panel.hidden = kip === undefined || kip === null;
  if (kip === undefined || kip === null) return;

  const kabin = kip === "docker";
  const alanlar = $("#sandbox-fields");
  if (alanlar) alanlar.hidden = !kabin;
  const durum = $("#sandbox-state");
  if (durum) {
    durum.textContent = t(kabin ? "sandbox.stateDocker" : "sandbox.stateHost");
    durum.dataset.tone = "";
  }
  const not = $("#sandbox-note");
  if (not) not.textContent = t(kabin ? "sandbox.noteDocker" : "sandbox.noteHost");
}

function refreshSettingsStatus() {
  const s = state.overview?.settings;
  if (!s || !$("#llm-state")) return;

  $("#llm-state").textContent = s.has_api_key
    ? t("settings.ready") : (s.llm_hint || t("settings.notConfigured"));
  $("#llm-state").dataset.tone = s.has_api_key ? "" : "err";

  aramaAlanlariniGoster(s.search_provider);
  const [aramaMetni, aramaHatali] = aramaDurumu(
    s.search_provider, s.has_search_api_key, false);
  $("#search-state").textContent = aramaMetni;
  $("#search-state").dataset.tone = aramaHatali ? "err" : "";

  // Tarayici panelinin baslik satiri kardeslerinin aksine HIC doldurulmuyordu:
  // `#browser-state` bos bir <span> olarak duruyor, ne `data-i18n` tasiyor ne
  // de JS'de tek bir atifi vardi. Kullanici LLM ve arama icin durum gorurken
  // tarayici icin bosluk goruyordu. Gosterilen iki sey, davranisi degistiren
  // ve bir bakista dogrulanmak istenen ikisi.
  $("#browser-state").textContent = [
    t(s.browser_headless ? "browser.stateHeadless" : "browser.stateWindowed"),
    t(s.browser_allow_preview ? "browser.statePreviewOn" : "browser.statePreviewOff"),
  ].join(" · ");

  $("#settings-run-note").textContent = t(
    s.approval_mode === "auto" ? "settings.noteAuto"
    : s.approval_mode === "dry-run" ? "settings.noteDry" : "settings.noteAsk");

  yalitimiGoster(s.execution);

  $("#env-table").innerHTML = `
    <div class="table-wrap"><table>
      <tbody>
        ${[
          [t("settings.workspace"), s.workspace],
          [t("settings.embedding"), s.embedding_model || "—"],
          [t("settings.modelReady"), s.has_api_key
            ? t("settings.yes") : `${t("settings.no")} — ${s.llm_hint || ""}`],
        ].map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join("")}
      </tbody>
    </table></div>`;
}

function collectSettings() {
  const payload = {};
  for (const [id, name, kind] of SETTING_INPUTS) {
    const node = $(id);
    if (kind === "bool") payload[name] = node.checked;
    else if (kind === "number") payload[name] = Number(node.value);
    else if (kind === "float") payload[name] = node.value === "" ? "" : Number(node.value);
    else payload[name] = node.value;
  }
  // Bos birakilan gizli alan "degistirme" demektir; gonderirsek silerdik.
  for (const [id, name] of SECRET_INPUTS) {
    const value = $(id).value.trim();
    if (value) payload[name] = value;
  }
  return payload;
}

function initSettings() {
  $("#btn-save-settings").addEventListener("click", async () => {
    const button = $("#btn-save-settings");
    button.disabled = true;
    try {
      const result = await post("/api/settings", collectSettings());
      toast(t("settings.saved", { n: Object.keys(result.changed).length }), "ok");
      await loadOverview();
      renderSettings();
    } catch (error) {
      toast(error.message, "err", 8000);
    } finally {
      button.disabled = false;
    }
  });

  // Ust bardaki anahtarla ayni yol: aninda degisir ve sunucuda kalir.
  // "Kaydet"i beklemek, arayuzu Ingilizce olay akisini Turkce birakirdi.
  $("#set-language").addEventListener("change", (event) => {
    changeLanguage(event.target.value);
  });

  $("#set-preset").addEventListener("change", (event) => applyPreset(event.target.value));

  $("#btn-list-models").addEventListener("click", () =>
    probe($("#btn-list-models"), $("#llm-note"), "/api/settings/models", (r) => {
      if (!r.ok) return t("settings.modelsFailed", { msg: r.error });
      $("#model-options").innerHTML = r.models
        .map((m) => `<option value="${esc(m)}"></option>`).join("");
      return t("settings.modelsFetched", { n: r.models.length });
    }));

  $("#set-provider").addEventListener("change", () => {
    const local = $("#set-provider").value === "openai";
    $("#field-base-url").hidden = !local;
    $("#field-openai-key").hidden = !local;
    $("#field-anthropic-key").hidden = local;
  });

  // Kaydetmeden once de gorunsun: konteyneri secip alanlarin kapali
  // kalmasi, ayarin islemedigini dusundururdu.
  $("#set-execution").addEventListener("change", (event) => {
    yalitimiGoster(event.target.value);
  });

  $("#set-search-provider").addEventListener("change", () => {
    aramaAlanlariniGoster($("#set-search-provider").value);
    const [metin, hatali] = aramaDurumu(
      $("#set-search-provider").value,
      state.overview?.settings?.has_search_api_key,
      true);
    $("#search-state").textContent = metin;
    $("#search-state").dataset.tone = hatali ? "err" : "";
  });

  const probe = async (button, note, url, render) => {
    button.disabled = true;
    note.dataset.tone = "";
    note.innerHTML = `<span class="spinner"></span> ${esc(t("settings.trying"))}`;
    try {
      const result = await post(url, {});
      note.dataset.tone = result.ok ? "ok" : "err";
      note.textContent = render(result);
    } catch (error) {
      note.dataset.tone = "err";
      note.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  };

  $("#btn-test-llm").addEventListener("click", () =>
    probe($("#btn-test-llm"), $("#llm-note"), "/api/settings/test-llm", (r) =>
      r.ok
        ? `${r.provider} · ${r.model} · ${r.seconds}${t("app.unitSec")} · ${r.tokens} token · ${r.text}`
        : r.error));

  $("#btn-test-search").addEventListener("click", () =>
    probe($("#btn-test-search"), $("#search-note"), "/api/settings/test-search", (r) =>
      `${r.provider}: ${r.result}`));

  // Tarayici testi gercekten Chrome'u acip bir sayfa yukler. "Chrome kurulu"
  // demek yetmiyor: surucu eksik, profil yazilamaz ya da vekil port acamaz
  // olabilir; tek dogru cevap denemek.
  $("#btn-test-browser").addEventListener("click", () =>
    probe($("#btn-test-browser"), $("#browser-note"), "/api/settings/test-browser", (r) =>
      r.ok
        ? t("browser.testOk", { binary: r.binary, seconds: r.seconds, title: r.title })
        : `${r.error}${r.binary ? ` (${r.binary})` : ""}`));
}


// ─── Saglayici hazir secenekleri ──────────────────────────────────────────
// Model adlari kodda TUTULMAZ: saglayicilar onlari sik degistirir ve birkac
// ay eski bir liste kullaniciya var olmayan bir modeli onerir. Adresler
// sabittir ve olculdu; adlar ucun kendisinden canli gelir.
let providerCatalog = null;

async function loadProviders() {
  if (providerCatalog) return providerCatalog;
  providerCatalog = await api("/api/providers");
  renderProviderOptions();
  return providerCatalog;
}

/* Saglayici adi + dile bagli sifat.

   Etiket sunucudan marka adi olarak gelir ("vLLM"); "yerel" isareti
   ARAYUZDE eklenir. Eskiden sunucu "vLLM (yerel)" gonderiyordu ve
   Ingilizce arayuzde de oyle goruyordunuz. */
function presetLabel(preset) {
  const ad = preset.label_key ? t(preset.label_key) : (preset.label || preset.key);
  return preset.local ? t("settings.presetLocalLabel", { name: ad }) : ad;
}

/** Acilir listeyi yeniden cizer; dil degisince de cagrilir. */
function renderProviderOptions() {
  if (!providerCatalog) return;
  const select = $("#set-preset");
  const secili = select.value;
  select.innerHTML = providerCatalog.providers.map((p) =>
    `<option value="${esc(p.key)}">${esc(presetLabel(p))}</option>`).join("");
  if (secili) select.value = secili;
}

function describePreset(preset) {
  const bits = [];
  if (preset.local) bits.push(t("settings.presetLocal"));
  if (preset.note_key) bits.push(t(preset.note_key));
  if (providerCatalog?.no_listing?.includes(preset.key)) {
    bits.push(t("settings.presetNoListing"));
  }
  if (preset.keys_url && !preset.local) {
    bits.push(t("settings.presetKeys", { url: preset.keys_url }));
  }
  $("#preset-note").textContent = bits.join(" · ");
}

function applyPreset(key) {
  const preset = providerCatalog?.providers.find((p) => p.key === key);
  if (!preset) return;
  $("#set-provider").value = preset.protocol;
  // "Diger" secildiginde adres kullanicinindir; ustune yazmayiz.
  if (preset.base_url) $("#set-base-url").value = preset.base_url;
  const local = preset.protocol === "openai";
  $("#field-base-url").hidden = !local;
  $("#field-openai-key").hidden = !local;
  $("#field-anthropic-key").hidden = local;
  describePreset(preset);
}

// ─── Kosu (workflow) ──────────────────────────────────────────────────────
// Her kosu kendi kimligiyle saklanir. Ust duzeyde kosu listesi (#1, #2 ...),
// bir kosuya tiklayinca o kosunun adim adim dokumu. Adimlar kosunun kendi
// kaydindan gelir; faz durumu projeye aittir ve tekrar kosuda uzerine
// yazilir, yani gecmis bir kosuya bakarken yaniltirdi.

const fmtDuration = (seconds) => {
  if (seconds == null) return "";
  if (seconds < 60) return `${Math.round(seconds)} ${t("app.unitSec")}`;
  const m = Math.floor(seconds / 60);
  if (m < 60) return `${m} ${t("app.unitMin")} ${Math.round(seconds % 60)} ${t("app.unitSec")}`;
  return `${Math.floor(m / 60)} ${t("app.unitHour")} ${m % 60} ${t("app.unitMin")}`;
};

/* Kosu basligi kendi dilinde.

   Baslik sunucuda uretilir ve veritabanina metin olarak yazilir. Yalnizca
   o metne bakildiginda Ingilizce arayuzde kosu listesi Turkce goruntuluyordu
   -- hemen altindaki faz listesi cevriliyken. Sunucu artik anahtari ve
   parametrelerini de veriyor; yazilmis metin yalnizca eski kayitlar icin
   yedek. */
function runTitle(run) {
  const key = run?.title_key;
  if (!key) return run?.title || "";
  const args = { ...(run.title_args || {}) };
  // Parametreler faz KIMLIGI tasir ("ingest"); ekranda etiketi durmali.
  for (const alan of ["phase", "first", "last"]) {
    if (args[alan]) args[alan] = t("phase." + args[alan]);
  }
  return t(key, args);
}

// Tarih biciminde de dil izlenir: Ingilizce arayuzde "28.08.2026 14:03"
// okunmaz degil ama yerinde de degil.
const fmtWhen = (ts) => (ts ? new Date(ts * 1000).toLocaleString(t("app.locale")) : "—");

async function loadWorkflow() {
  if (state.activeRun) return loadRunDetail(state.activeRun);
  if (state.activeWorkflow) return loadWorkflowDetail(state.activeWorkflow);
  return loadWorkflowList();
}

/* Uc seviye: Is akisi -> Adim (kosu) -> Faz.

   Once yalnizca kosular vardi ve hangi kosunun hangi gelistirmeye ait
   oldugu hicbir yerde yazmiyordu; yirmi kosuluk bir listede is akisi
   kullanicinin kafasindaydi. Simdi her gelistirme bir is akisi ve kosular
   onun adimlari. */

// ── 1. seviye: is akislari ───────────────────────────────────────────────
async function loadWorkflowList() {
  const list = $("#run-list");
  $("#workflow-title").textContent = t("wf.title");
  $("#workflow-back").hidden = true;
  // Sohbetin kapsami TEK bir is akisidir; listede "hangisi hakkinda?"
  // sorusunun cevabi yok, o yuzden panel yalnizca detayda gorunur.
  $("#workflow-chat").hidden = true;
  $("#workflow-expand-label").hidden = true;
  $("#workflow-meta").innerHTML = "";
  $("#workflow-steps").innerHTML = "";

  try {
    const data = await api("/api/workflows");
    $("#workflow-sub").textContent = data.workflows.length
      ? t("wf.count", { n: data.workflows.length })
      : t("wf.empty");

    if (!data.workflows.length) {
      list.innerHTML = emptyState(t("wf.empty"), t("wf.emptyHint"));
      return;
    }

    list.innerHTML = data.workflows.map((wf) => `
      <button class="run-row" type="button" data-workflow="${esc(wf.id)}"
              data-status="${esc(wf.state)}">
        <span class="run-seq">#${wf.seq}</span>
        <span class="run-main">
          <span class="run-goal">${esc(wf.title || wf.goal || t("runs.noGoal"))}</span>
          <span class="run-phases">${esc(t("wf.stepsDone", {
            done: wf.runs_done, total: wf.runs }))}</span>
        </span>
        <span class="run-tail">
          <span>${fmtMoney(wf.cost)}</span>
          <span class="run-when">${esc(fmtWhen(wf.last_at))}</span>
          ${stateBadge(wf.state)}
        </span>
      </button>`).join("");

    $$("[data-workflow]", list).forEach((row) => {
      row.addEventListener("click", () => {
        state.activeWorkflow = row.dataset.workflow;
        loadWorkflowDetail(state.activeWorkflow);
      });
    });
    $("#rail-workflow").hidden = !data.running;
  } catch (error) {
    list.innerHTML = emptyState(t("app.failed"), error.message);
  }
}

/* Adimin hali tek kelimeyle. `needs_approval` ve `needs_input` veritabani
   durumu degil: kayitta "calisiyor" yazar ama gercekte sizi bekler.
   "Calisiyor" demek orada yanlis bilgi verirdi. */
function stateBadge(stateName) {
  return `<span class="badge" data-v="${esc(stateName)}">${esc(tv("wfState", stateName))}</span>`;
}

// ── 2. seviye: is akisinin adimlari ──────────────────────────────────────
async function loadWorkflowDetail(workflowId) {
  // Detay GOSTERILEN is akisi, etkin is akisidir. Bunu yalnizca listedeki
  // satirin tiklanmasina birakmak, goruntuye baska bir yoldan gelindiginde
  // sohbetin "hangi is akisi?" sorusuna cevapsiz kalmasi demekti --
  // gonder dugmesi sessizce hicbir sey yapmiyordu.
  state.activeWorkflow = workflowId;
  $("#workflow-steps").innerHTML = "";
  $("#workflow-expand-label").hidden = true;
  $("#workflow-back").hidden = false;
  $("#workflow-chat").hidden = false;
  loadChat(workflowId);
  try {
    const data = await api(`/api/workflows/${encodeURIComponent(workflowId)}`);
    state.workflowDetail = data;
    renderWorkflowDetail(data);
  } catch (error) {
    $("#run-list").innerHTML = emptyState(t("app.failed"), error.message);
  }
}

function renderWorkflowDetail(data) {
  const wf = data.workflow;
  const list = $("#run-list");
  $("#workflow-title").textContent = t("wf.one", { seq: wf.seq });
  $("#workflow-sub").textContent = wf.title || wf.goal || t("runs.noGoal");

  const done = data.steps.filter((s) => s.status === "done").length;
  $("#workflow-meta").innerHTML = `
    <div class="wf-summary" data-state="${esc(data.live ? "running" : wf.status)}">
      <span class="wf-chip">${esc(t("wf.stepsDone", { done, total: data.steps.length }))}</span>
      <span>${fmtMoney(data.cost)}</span>
      <span class="wf-note">${esc(fmtWhen(
        data.steps.length ? data.steps[0].started_at : wf.created_at))}</span>
    </div>`;

  if (!data.steps.length) {
    list.innerHTML = emptyState(t("wf.noSteps"), t("wf.noStepsHint"));
    return;
  }

  list.innerHTML = data.steps.map((step, index) => `
    <div class="wf-run" data-state="${esc(step.state)}">
      <button class="run-row" type="button" data-run="${esc(step.id)}"
              data-status="${esc(step.state)}">
        <span class="run-seq">${index + 1}</span>
        <span class="run-main">
          <span class="run-goal">${esc(runTitle(step) || t("runs.noGoal"))}</span>
          <span class="run-phases">${step.phases.map((p) => esc(t("phase." + p))).join(" → ")}</span>
        </span>
        <span class="run-tail">
          <span>${esc(t("runs.stepsDone", { done: step.steps_done, total: step.phases.length }))}</span>
          <span>${fmtDuration(step.elapsed)}</span>
          <span>${fmtMoney(step.cost)}</span>
          <span class="run-when">${esc(fmtWhen(step.started_at))}</span>
          ${stateBadge(step.state)}
        </span>
      </button>
      ${renderStepGate(step)}
    </div>`).join("");

  $$("[data-run]", list).forEach((row) => {
    row.addEventListener("click", () => {
      state.activeRun = row.dataset.run;
      loadRunDetail(state.activeRun);
    });
  });
  bindStepGates(list);
}

/* Adim sizi bekliyorsa ne bekledigi ve ne yapmaniz gerektigi ADIMDA yazar.

   Onceden bekleyen onay yalnizca ekranin ustune binen bir pencerede
   gorunuyordu: pencereyi kapatinca is akisi "calisiyor" gibi duruyor ama
   hicbir sey ilerlemiyordu ve nedeni hicbir yerde yazmiyordu. */
function renderStepGate(step) {
  if (step.state === "needs_approval" && step.approvals.length) {
    const request = step.approvals[0];
    return `
      <div class="wf-gate" data-kind="approval">
        <div class="wf-gate-head">
          <span class="wf-gate-badge">${esc(t("wfState.needs_approval"))}</span>
          <strong>${esc(request.action)}</strong>
          ${step.approvals.length > 1
            ? `<span class="wf-gate-more">${esc(t("approval.waiting", {
                 n: step.approvals.length }))}</span>` : ""}
        </div>
        <pre class="wf-gate-detail">${esc(request.detail || t("approval.noDetail"))}</pre>
        <div class="wf-gate-actions">
          <button class="btn btn-primary btn-sm" data-approve="${esc(request.id)}"
                  type="button">${esc(t("approval.accept"))}</button>
          <button class="btn btn-danger btn-sm" data-reject="${esc(request.id)}"
                  type="button">${esc(t("approval.reject"))}</button>
        </div>
      </div>`;
  }
  if (step.state === "needs_input") {
    return `
      <div class="wf-gate" data-kind="input">
        <div class="wf-gate-head">
          <span class="wf-gate-badge">${esc(t("wfState.needs_input"))}</span>
          <strong>${esc(t("wf.questionsWaiting", { keys: step.questions.join(", ") }))}</strong>
        </div>
        <div class="wf-gate-actions">
          <button class="btn btn-primary btn-sm" data-goto-questions type="button">
            ${esc(t("wf.answerNow"))}</button>
        </div>
      </div>`;
  }
  if (step.state === "stalled") {
    return `<div class="wf-gate" data-kind="stalled">
        <div class="wf-gate-head">
          <span class="wf-gate-badge">${esc(t("wfState.stalled"))}</span>
          <strong>${esc(t("wf.stalledNote"))}</strong>
        </div></div>`;
  }
  if (step.error) {
    // Hatayi gosterip kullaniciyi orada birakmak yarim is: gorunen tek
    // care butun gelistirmeyi bastan baslatmakti. Dugme kirilan yerden
    // devam eder.
    return `<div class="wf-gate" data-kind="error">
        <div class="wf-gate-head">
          <span class="wf-gate-badge">${esc(t("wfState.failed"))}</span>
          <strong>${esc(step.error)}</strong>
        </div>
        <div class="wf-gate-actions">
          <button class="btn btn-primary btn-sm" data-retry-run="${esc(step.id)}"
                  type="button">${esc(t("runs.retryRun"))}</button>
        </div></div>`;
  }
  return "";
}

function bindStepGates(root) {
  const resolve = async (id, granted) => {
    $$("[data-approve],[data-reject]", root).forEach((b) => { b.disabled = true; });
    try {
      await post(`/api/approvals/${id}`, { granted });
      toast(t(granted ? "approval.granted" : "approval.rejected"), granted ? "ok" : "warn");
    } catch (error) {
      toast(error.message, "err");
    }
    state.approvals = state.approvals.filter((item) => item.id !== id);
    renderApproval();
    loadWorkflow();
    loadOverview();
  };
  $$("[data-approve]", root).forEach((b) =>
    b.addEventListener("click", (e) => { e.stopPropagation(); resolve(b.dataset.approve, true); }));
  $$("[data-reject]", root).forEach((b) =>
    b.addEventListener("click", (e) => { e.stopPropagation(); resolve(b.dataset.reject, false); }));
  $$("[data-goto-questions]", root).forEach((b) =>
    b.addEventListener("click", (e) => { e.stopPropagation(); showView("overview"); }));
  $$("[data-retry-run]", root).forEach((b) =>
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      retryRun(b.dataset.retryRun, "", b);
    }));
}

/* Kirilan yerden devam. `phase` bos ise sunucu ilk sorunlu adimi kendi secer.

   Dugme cift tiklamaya karsi hemen kilitlenir: ayni anda tek kosu yurutulur
   ve ikinci istek 409 doner -- kullanicinin gordugu sey "calisiyor" degil,
   anlamsiz bir hata olurdu. */
async function retryRun(runId, phase, button) {
  if (button) button.disabled = true;
  try {
    const sonuc = await post(`/api/runs/${encodeURIComponent(runId)}/retry`,
                             phase ? { phase } : {});
    toast(t("runs.retryStarted", { phase: t("phase." + sonuc.from) }), "ok");
    showView("workflow");
    loadWorkflow();
    loadOverview();
  } catch (error) {
    toast(error.message, "err", 7000);
    if (button) button.disabled = false;
  }
}

// ── 3. seviye: adimin fazlari ────────────────────────────────────────────
async function loadRunDetail(runId) {
  $("#run-list").innerHTML = "";
  $("#workflow-back").hidden = false;
  $("#workflow-expand-label").hidden = false;
  try {
    const data = await api(`/api/runs/${encodeURIComponent(runId)}`);
    state.workflow = data;
    renderRunDetail(data);
  } catch (error) {
    $("#workflow-steps").innerHTML = emptyState(t("app.failed"), error.message);
    $("#workflow-meta").innerHTML = "";
  }
}

function renderRunDetail(data) {
  const run = data.run;
  const list = $("#workflow-steps");

  $("#workflow-title").textContent = t("runs.one", { seq: run.seq });
  $("#workflow-sub").textContent = runTitle(run) || run.goal || t("runs.noGoal");

  const durum = tv("status", data.live ? "running" : run.status);
  $("#workflow-meta").innerHTML = `
    <div class="wf-summary" data-state="${esc(data.live ? "running" : run.status)}">
      <span class="wf-chip">${esc(durum)}</span>
      <span>${esc(t(run.phases.length === 1 ? "runs.stepOne" : "runs.steps", { n: run.phases.length }))}</span>
      <span>${fmtDuration(run.elapsed)}</span>
      <span>${fmtMoney(run.cost)}</span>
      <span class="wf-note">${esc(fmtWhen(run.started_at))}</span>
      ${run.error ? `<span class="wf-error">${esc(run.error)}</span>` : ""}
    </div>`;

  if (!data.steps.length) {
    list.innerHTML = emptyState(t("runs.noSteps"));
    return;
  }

  const expandAll = $("#workflow-expand").checked;
  list.innerHTML = data.steps.map((step) => {
    // Calisan ve sorun cikaran adimlar varsayilan olarak acik gelir:
    // kullanicinin once bakacagi yer orasidir.
    const interesting = ["running", "failed", "blocked", "needs_input"].includes(step.status);
    // Tekrar YALNIZCA sorunlu adimlarda sunulur. `needs_input` sorun
    // degildir: ajan isini yapmis, kullanicidan cevap bekliyor; tekrar
    // kosmak ayni soruyu ikinci kez sormaktir.
    const retryable = ["failed", "blocked", "cancelled"].includes(step.status);
    const open = expandAll || interesting || state.workflowOpen.has(step.phase);
    const tools = step.counts.tool || 0;
    const messages = step.counts.message || 0;
    const errors = (step.counts.error || 0) + (step.counts.tool_error || 0);
    const warns = step.counts.warn || 0;

    return `
      <li class="wf-step" data-status="${esc(step.status)}" data-in-run="1">
        <button class="wf-head" type="button" data-toggle="${esc(step.phase)}"
                aria-expanded="${open ? "true" : "false"}">
          <span class="wf-index">${step.ordinal + 1}</span>
          <span class="wf-title">
            <span class="wf-label">${esc(t("phase." + step.phase))}</span>
            <span class="wf-agent">${esc(t("agent." + step.phase))}</span>
          </span>
          <span class="wf-tail">
            ${tools ? `<span title="${esc(t("runs.toolTitle"))}">${esc(t("runs.tools", { n: tools }))}</span>` : ""}
            ${messages ? `<span title="${esc(t("runs.messageTitle"))}">${esc(t("runs.replies", { n: messages }))}</span>` : ""}
            ${errors ? `<span class="wf-bad">${esc(t("runs.errors", { n: errors }))}</span>` : ""}
            ${warns ? `<span class="wf-warn">${esc(t("runs.warnings", { n: warns }))}</span>` : ""}
            ${step.cost ? `<span>${fmtMoney(step.cost)}</span>` : ""}
            ${step.elapsed != null ? `<span>${fmtDuration(step.elapsed)}</span>` : ""}
            <span class="badge" data-v="${esc(step.status)}">${
              esc(tv("status", step.status))}</span>
            <span class="wf-caret">${open ? "▾" : "▸"}</span>
          </span>
        </button>
        <div class="wf-body"${open ? "" : " hidden"}>
          ${step.error ? `<div class="wf-section"><h4>${esc(t("runs.error"))}</h4>
             <p class="wf-text wf-error">${esc(step.error)}</p></div>` : ""}
          ${retryable ? `<div class="wf-section wf-retry">
             <button class="btn btn-primary btn-sm" type="button"
                     data-retry-phase="${esc(step.phase)}">${esc(t("runs.retryFrom"))}</button>
             <p class="wf-note">${esc(t("runs.retryFromHint"))}</p></div>` : ""}
          ${step.summary ? `<div class="wf-section"><h4>${esc(t("runs.summary"))}</h4>
             <p class="wf-text">${esc(step.summary)}</p></div>` : ""}
          ${step.artifacts.length ? `<div class="wf-section"><h4>${esc(t("runs.produced"))}</h4>
             <div class="wf-artifacts">${step.artifacts.map((n) =>
               `<button class="wf-artifact" type="button" data-open-artifact="${esc(n)}">${esc(n)}</button>`
             ).join("")}</div></div>` : ""}
          ${step.events.length
            ? `<div class="wf-section"><h4>${esc(t("runs.events", { n: step.events.length }))}</h4>
                 <div class="feed wf-events">${step.events.map(eventRow).join("")}</div></div>`
            : `<p class="empty">${esc(t("runs.noEvents"))}</p>`}
        </div>
      </li>`;
  }).join("");

  $$("[data-toggle]", list).forEach((head) => {
    head.addEventListener("click", () => {
      const key = head.dataset.toggle;
      const body = head.nextElementSibling;
      body.hidden = !body.hidden;
      head.setAttribute("aria-expanded", String(!body.hidden));
      $(".wf-caret", head).textContent = body.hidden ? "▸" : "▾";
      if (body.hidden) state.workflowOpen.delete(key);
      else state.workflowOpen.add(key);
    });
  });

  $$("[data-open-artifact]", list).forEach((button) => {
    button.addEventListener("click", () => {
      state.activeArtifact = button.dataset.openArtifact;
      showView("artifacts");
    });
  });

  $$("[data-retry-phase]", list).forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      retryRun(run.id, button.dataset.retryPhase, button);
    });
  });
}

// ─── İş akışı sohbeti ─────────────────────────────────────────────────────
// Kullanici bir is akisi hakkinda konusur; danisman cevaplar ve istenirse
// durumu DEGISTIRIR. Degistirdikleri cevabin altinda ayrica gosterilir:
// kullanici neyin degistigini metnin icinde aramak zorunda kalmasin.

function renderChat(messages) {
  const log = $("#chat-log");
  if (!messages.length) {
    log.innerHTML = emptyState(t("chat.emptyTitle"), t("chat.emptyHint"));
    return;
  }
  log.innerHTML = messages.map((m) => {
    const who = m.role === "user" ? t("chat.you") : t("chat.agent");
    const changes = (m.changes || []).length
      ? `<div class="chat-changes">
           <span class="chat-changes-head">${esc(t("chat.changed"))}</span>
           ${m.changes.map((c) => `<span class="chat-change">${esc(c)}</span>`).join("")}
         </div>`
      : "";
    return `<article class="chat-msg" data-who="${esc(m.role)}">
        <span class="chat-who">${esc(who)}</span>
        <div class="chat-text">${esc(m.content)}</div>
        ${changes}
      </article>`;
  }).join("");
  // Son mesaj gorunur olsun; sohbette ilgilenilen yer daima sonudur.
  log.scrollTop = log.scrollHeight;
}

async function loadChat(workflowId) {
  try {
    const data = await api(`/api/workflows/${encodeURIComponent(workflowId)}/chat`);
    renderChat(data.messages || []);
  } catch (error) {
    $("#chat-log").innerHTML = emptyState(t("app.failed"), error.message);
  }
}

function chatBusy(busy) {
  $("#chat-send").disabled = busy;
  $("#chat-input").disabled = busy;
  const status = $("#chat-status");
  status.hidden = !busy;
  status.textContent = busy ? t("chat.thinking") : "";
}

async function sendChat() {
  const workflowId = state.activeWorkflow;
  const input = $("#chat-input");
  const message = input.value.trim();
  if (!workflowId || !message) return;

  chatBusy(true);
  try {
    const data = await post(
      `/api/workflows/${encodeURIComponent(workflowId)}/chat`, { message });
    input.value = "";
    renderChat(data.messages || []);
    if (data.changes && data.changes.length) {
      // Durum degisti: acik olan gorunumler eskimis veriyle kalmasin.
      refreshActiveView();
    }
  } catch (error) {
    toast(error.message, "err");
  } finally {
    chatBusy(false);
  }
}

function initChat() {
  $("#chat-form").addEventListener("submit", (event) => {
    event.preventDefault();
    sendChat();
  });
  // Enter gonderir, Shift+Enter satir atlar. Sohbet kutusunda beklenen bu;
  // cok satirli bir mesaj yazmak isteyen de yolunu bulabilmeli.
  $("#chat-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendChat();
    }
  });
  $("#chat-clear").addEventListener("click", async () => {
    if (!state.activeWorkflow) return;
    try {
      await api(`/api/workflows/${encodeURIComponent(state.activeWorkflow)}/chat`,
                { method: "DELETE" });
      renderChat([]);
    } catch (error) {
      toast(error.message, "err");
    }
  });
}

function initWorkflow() {
  $("#workflow-refresh").addEventListener("click", loadWorkflow);
  // Geri: fazlardan adimlara, adimlardan is akislarina.
  $("#workflow-back").addEventListener("click", () => {
    state.workflowOpen.clear();
    if (state.activeRun) {
      state.activeRun = null;
      loadWorkflow();
      return;
    }
    state.activeWorkflow = null;
    loadWorkflowList();
  });
  $("#workflow-expand").addEventListener("change", () => {
    if (state.workflow) renderRunDetail(state.workflow);
  });
}

// ─── Sayfalama ────────────────────────────────────────────────────────────
// Analiz kayitlari ve olay akisi yuzlerce satira ciker. Hepsini tek DOM'a
// basmak hem yavaslatir hem de okunmaz kilar; sayfalama ikisini de cozer.
const PAGE_SIZES = [25, 50, 100, 250];

/** Gorunen sayfa numaralari: bas, son ve gecerli sayfanin cevresi. */
function pageWindow(page, pages) {
  if (pages <= 7) return Array.from({ length: pages }, (_, i) => i + 1);
  const around = new Set([1, pages, page, page - 1, page + 1]);
  if (page <= 3) [2, 3, 4].forEach((n) => around.add(n));
  if (page >= pages - 2) [pages - 3, pages - 2, pages - 1].forEach((n) => around.add(n));

  const sorted = [...around].filter((n) => n >= 1 && n <= pages).sort((a, b) => a - b);
  const out = [];
  let previous = 0;
  for (const n of sorted) {
    if (previous && n - previous > 1) out.push("…");
    out.push(n);
    previous = n;
  }
  return out;
}

/** Toplam ve sayfa boyutundan gecerli dilimi hesaplar. */
function slicePage(items, page, size) {
  const pages = Math.max(1, Math.ceil(items.length / size));
  const current = Math.min(Math.max(1, page), pages);
  const start = (current - 1) * size;
  return { items: items.slice(start, start + size), page: current, pages, start };
}

/**
 * Sayfalayiciyi cizer ve olaylarini baglar.
 *
 * `total` 0 ya da tek sayfaysa gizlenir: iki dugmeli bir sayfalayici
 * gurultuden ibarettir.
 */
function renderPager(node, { total, page, size, onPage, onSize, unit }) {
  unit = unit || t("pager.rows");
  const pages = Math.max(1, Math.ceil(total / size));
  if (total <= PAGE_SIZES[0] && pages <= 1) {
    node.hidden = true;
    node.innerHTML = "";
    return;
  }
  node.hidden = false;

  const first = total ? (page - 1) * size + 1 : 0;
  const last = Math.min(page * size, total);

  node.innerHTML = `
    <button class="pager-btn" data-goto="${page - 1}" ${page <= 1 ? "disabled" : ""}
            type="button" aria-label="${esc(t("pager.previous"))}">‹</button>
    <span class="pager-pages">${pageWindow(page, pages).map((n) =>
      n === "…"
        ? '<span class="pager-gap">…</span>'
        : `<button class="pager-num${n === page ? " is-active" : ""}" data-goto="${n}"
                   type="button"${n === page ? ' aria-current="page"' : ""}>${n}</button>`
    ).join("")}</span>
    <button class="pager-btn" data-goto="${page + 1}" ${page >= pages ? "disabled" : ""}
            type="button" aria-label="${esc(t("pager.next"))}">›</button>
    <span class="pager-info">${first}–${last} / ${total}</span>
    <label class="pager-size">
      <select aria-label="${esc(t("pager.perPage", { unit }))}">
        ${PAGE_SIZES.map((n) => `<option value="${n}"${n === size ? " selected" : ""}>${n}</option>`).join("")}
      </select>
      <span>${unit}</span>
    </label>`;

  $$("[data-goto]", node).forEach((button) => {
    button.addEventListener("click", () => {
      const target = Number(button.dataset.goto);
      if (target >= 1 && target <= pages && target !== page) onPage(target);
    });
  });
  $("select", node).addEventListener("change", (event) => onSize(Number(event.target.value)));
}

const ANALYSIS_VIEWS = {
  requirements: {
    columns: () => [t("analysis.key"), t("analysis.title_"),
                    t("analysis.priority"), t("analysis.category")],
    row: (item) => `
      <td class="key">${esc(item.key)}</td>
      <td>${esc(item.title)}</td>
      <td><span class="badge" data-v="${esc(item.priority)}">${esc(tv("priority", item.priority))}</span></td>
      <td><span class="badge">${esc(tv("category", item.category))}</span></td>`,
    detail: (item) => [
      [t("analysis.description"), item.description],
      [t("analysis.evidence"), item.source_ref],
    ],
    label: (item) => `${item.key}: ${item.title}`,
    empty: () => [t("analysis.emptyRequirements"), t("analysis.emptyRequirementsHint")],
  },
  questions: {
    columns: () => [t("analysis.key"), t("analysis.question"),
                    t("analysis.status"), t("analysis.blocking")],
    row: (item) => `
      <td class="key">${esc(item.key)}</td>
      <td>${esc(item.question)}</td>
      <td><span class="badge" data-v="${item.status === "answered" ? "done" : item.status === "skipped" ? "medium" : "high"}">${esc(tv("status", item.status))}</span></td>
      <td>${item.blocking ? `<span class="badge" data-v="critical">${esc(t("status.blocked"))}</span>` : "—"}</td>`,
    detail: (item) => [
      [t("questions.why"), item.why],
      [t("analysis.answer"), item.answer],
      [t("questions.suggestion"), item.suggestion],
      [t("analysis.askedBy"), item.asked_by],
    ],
    label: (item) => `${item.key}: ${item.question}`,
    empty: () => [t("analysis.emptyQuestions"), t("analysis.emptyQuestionsHint")],
    // Acik soru buradan cevaplanir. Kapanmis soruda kutu gosterilmez:
    // cevabi zaten yukarida, ayrinti listesinde duruyor.
    actions: (item) => item.status !== "open" ? "" : `
      <div class="row-answer" data-answer-key="${esc(item.key)}">
        <textarea rows="3" data-answer-text
                  placeholder="${esc(t("questions.answerPlaceholder"))}"
                  aria-label="${esc(t("questions.answerLabel", { key: item.key }))}"></textarea>
        <div class="question-buttons">
          <button class="btn btn-primary btn-sm" data-answer-send type="button"
                  >${esc(t("questions.saveAnswer"))}</button>
          <button class="btn btn-sm" data-answer-skip type="button"
                  title="${esc(t("questions.markSkippedHint"))}">${esc(t("questions.markSkipped"))}</button>
        </div>
      </div>`,
  },
  gaps: {
    columns: () => [t("analysis.key"), t("analysis.title_"),
                    t("analysis.severity"), t("analysis.area")],
    row: (item) => `
      <td class="key">${esc(item.key)}</td>
      <td>${esc(item.title)}</td>
      <td><span class="badge" data-v="${esc(item.severity)}">${esc(tv("severity", item.severity))}</span></td>
      <td><span class="badge">${esc(item.area)}</span></td>`,
    detail: (item) => [
      [t("analysis.description"), item.description],
      [t("analysis.recommendation"), item.recommendation],
      [t("analysis.evidence"), item.evidence],
    ],
    label: (item) => `${item.key}: ${item.title}`,
    empty: () => [t("analysis.emptyGaps"), t("analysis.emptyGapsHint")],
  },
  decisions: {
    columns: () => [t("analysis.key"), t("analysis.topic"), t("analysis.choice")],
    row: (item) => `
      <td class="key">${esc(item.key)}</td>
      <td>${esc(item.title)}</td>
      <td><strong>${esc(item.choice)}</strong></td>`,
    detail: (item) => [
      [t("analysis.rationale"), item.rationale],
      [t("analysis.alternatives"), item.alternatives],
      [t("analysis.tradeoffs"), item.tradeoffs],
    ],
    label: (item) => `${item.key}: ${item.title}`,
    empty: () => [t("analysis.emptyDecisions"), t("analysis.emptyDecisionsHint")],
  },
  research: {
    columns: () => [t("analysis.topic"), t("analysis.finding"), t("analysis.confidence")],
    row: (item) => `
      <td>${esc(item.topic)}</td>
      <td>${esc(item.finding)}</td>
      <td><span class="badge" data-v="${item.confidence === "high" ? "done" : item.confidence === "low" ? "high" : "medium"}">${esc(item.confidence)}</span></td>`,
    detail: (item) => [[t("analysis.source"), item.url || t("analysis.noSource")]],
    label: (item) => item.topic,
    empty: () => [t("analysis.emptyResearch"), t("analysis.emptyResearchHint")],
  },
};

async function loadAnalysis() {
  const target = $("#analysis-body");
  try {
    const data = await api(`/api/state/${state.analysisTab}`);
    state.analysisItems = data.items;
    renderAnalysisPage();
  } catch (error) {
    target.innerHTML = emptyState(t("app.failed"), error.message);
    $("#analysis-pager").hidden = true;
  }
}

function renderAnalysisPage() {
  const view = ANALYSIS_VIEWS[state.analysisTab];
  const target = $("#analysis-body");
  const all = state.analysisItems;

  const columns = view.columns();
  if (!all.length) {
    const [title, hint] = view.empty();
    target.innerHTML = emptyState(title, hint);
    $("#analysis-pager").hidden = true;
    return;
  }

  const slice = slicePage(all, state.analysisPage, state.analysisSize);
  state.analysisPage = slice.page;

  // Satir kimligi sayfa icindeki sira degil, mutlak sira: sayfa degistiginde
  // acik ayrinti satirlari birbirine karismasin.
  target.innerHTML = `
    <div class="table-wrap"><table>
      <thead><tr>${columns.map((column) => `<th>${esc(column)}</th>`).join("")}</tr></thead>
      <tbody>${slice.items.map((item, index) => {
        const id = slice.start + index;
        return `
        <tr class="clickable" data-row="${id}" tabindex="0" role="button"
            aria-expanded="false"
            aria-label="${esc(view.label(item))}">${view.row(item)}</tr>
        <tr class="row-detail" data-detail="${id}" hidden>
          <td colspan="${columns.length}">
            <dl class="detail-grid">${view.detail(item)
              .filter(([, value]) => value)
              .map(([label, value]) => `<dt>${esc(label)}</dt><dd>${esc(value)}</dd>`)
              .join("") || `<dt>—</dt><dd>${esc(t("analysis.noExtra"))}</dd>`}</dl>
            ${view.actions ? view.actions(item) : ""}
          </td>
        </tr>`;
      }).join("")}
      </tbody>
    </table></div>`;

  const toggleRow = (row) => {
    const detail = $(`[data-detail="${row.dataset.row}"]`, target);
    detail.hidden = !detail.hidden;
    row.setAttribute("aria-expanded", String(!detail.hidden));
  };
  $$(".row-answer", target).forEach((box) => {
    box.addEventListener("click", (event) => event.stopPropagation());
    box.addEventListener("keydown", (event) => event.stopPropagation());
    const kutu = $("[data-answer-text]", box);
    const gonder = $("[data-answer-send]", box);
    const atla = $("[data-answer-skip]", box);
    const anahtar = box.dataset.answerKey;

    kutu.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        gonder.click();
      }
    });
    gonder.addEventListener("click", () => {
      const metin = kutu.value.trim();
      if (!metin) {
        toast(t("questions.needText"), "warn");
        kutu.focus();
        return;
      }
      soruyuKapat(box, anahtar, "answer", metin);
    });
    atla.addEventListener("click", () =>
      soruyuKapat(box, anahtar, "skip", kutu.value.trim()));
  });

  $$("[data-row]", target).forEach((row) => {
    row.addEventListener("click", () => toggleRow(row));
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggleRow(row);
      }
    });
  });

  renderPager($("#analysis-pager"), {
    total: all.length,
    page: slice.page,
    size: state.analysisSize,
    unit: t("analysis.records"),
    onPage: (page) => {
      state.analysisPage = page;
      renderAnalysisPage();
      $("#analysis-body").scrollIntoView({ block: "start", behavior: "smooth" });
    },
    onSize: (size) => {
      state.analysisSize = size;
      state.analysisPage = 1;
      renderAnalysisPage();
    },
  });
}

/* Analiz tablosundan soru kapatma.

   `resolveQuestion` engelleyen sorular panelinin DOM'una bagli (`#question-send`
   gibi tekil kimlikler); burada tabloda ayni anda birden cok kutu acik
   olabilir, o yuzden ayri ve kendi kendine yeten bir yol. */
async function soruyuKapat(box, key, action, text) {
  const dugmeler = $$("button", box);
  const basilan = action === "answer"
    ? $("[data-answer-send]", box) : $("[data-answer-skip]", box);
  const etiket = basilan.textContent;
  dugmeler.forEach((b) => { b.disabled = true; });
  basilan.innerHTML = `<span class="spinner"></span> ${esc(t("questions.saving"))}`;
  try {
    await post(`/api/questions/${encodeURIComponent(key)}`, { action, text });
    toast(
      t(action === "answer" ? "questions.answered" : "questions.skipped", { key }),
      action === "answer" ? "ok" : "warn",
    );
    // Kaydi tazele: durum rozeti ve cevap ayrinti listesinde gorunsun.
    await loadAnalysis();
    loadOverview();
  } catch (error) {
    dugmeler.forEach((b) => { b.disabled = false; });
    basilan.textContent = etiket;
    toast(error.message, "err", 7000);
  }
}

function initAnalysis() {
  $("#analysis-tabs").addEventListener("click", (event) => {
    const tab = event.target.closest(".tab");
    if (!tab) return;
    state.analysisTab = tab.dataset.tab;
    state.analysisPage = 1;  // yeni sekme, bastan basla
    $$(".tab", $("#analysis-tabs")).forEach((node) => node.classList.toggle("is-active", node === tab));
    loadAnalysis();
  });
}

// ── Planlar ──────────────────────────────────────────────────────────────
// Bir plan, adlandirilmis bagimsiz bir gorev grubudur: paralel is akislari,
// alternatif yaklasimlar ya da sartname degisince acilan yeni surum. Gorev
// anahtarlari proje capinda tekildir, boylece planlar arasi bagimlilik
// kurulabilir ve hicbir referans belirsiz kalmaz.

async function loadPlans() {
  const bar = $("#plan-tabs");
  try {
    const data = await api("/api/plans");
    state.plans = data.plans;
    // Secili plan silindiyse etkin plana don.
    if (!data.plans.some((p) => p.id === state.selectedPlan)) {
      state.selectedPlan = data.active;
    }
    state.activePlan = data.active;

    bar.innerHTML = data.plans.map((plan) => `
      <button class="plan-tab${plan.id === state.selectedPlan ? " is-active" : ""}"
              type="button" data-plan="${esc(plan.id)}"
              title="${esc(plan.description || plan.name)}">
        <span class="plan-name">${esc(plan.name)}</span>
        <span class="plan-count">${plan.tasks_done}/${plan.tasks}</span>
        ${plan.id === data.active ? `<span class="plan-live" title="${esc(t("plan.planLive"))}">●</span>` : ""}
        ${plan.tasks_failed ? `<span class="plan-bad">${plan.tasks_failed}</span>` : ""}
      </button>`).join("");

    $$("[data-plan]", bar).forEach((tab) => {
      tab.addEventListener("click", () => {
        state.selectedPlan = tab.dataset.plan;
        loadPlans();
        loadTasks();
      });
    });

    const selected = data.plans.find((p) => p.id === state.selectedPlan);
    $("#btn-delete-plan").disabled = data.plans.length <= 1;
    // Dogrudan baslatma: hazir gorev varsa kac tanesi oldugunu dugmede soyle,
    // yoksa neden baslatilamadigini -- pasif ve sessiz bir dugme sinir bozar.
    const ready = selected ? selected.ready : 0;
    const button = $("#btn-run-plan");
    button.disabled = !ready;
    button.textContent = ready
      ? t("plan.startPlanReady", { n: ready }) : t("plan.startPlan");
    // Otomatik modda ne olacagini basta soyle: kullanici her gorev icin
    // ayri bir onay bekliyor sanmasin.
    const note = $("#plan-auto-note");
    note.hidden = state.approvalMode !== "auto";
    note.textContent = t("plan.autoNote");
    button.title = ready ? "" : t(
      !selected || !selected.tasks ? "plan.noTasks" : "plan.noReady");
  } catch (error) {
    bar.innerHTML = `<span class="plan-error">${esc(
      t("plan.error", { msg: error.message }))}</span>`;
  }
}

function initPlans() {
  $("#btn-new-plan").addEventListener("click", async () => {
    const name = prompt(t("plan.newName"), "");
    if (!name || !name.trim()) return;
    try {
      const result = await post("/api/plans", { name: name.trim() });
      state.selectedPlan = result.plan.id;
      toast(t("plan.created", { name: result.plan.name }), "ok");
      await loadPlans();
      loadTasks();
    } catch (error) { toast(error.message, "err", 7000); }
  });

  $("#btn-rename-plan").addEventListener("click", async () => {
    const plan = state.plans.find((p) => p.id === state.selectedPlan);
    if (!plan) return;
    const name = prompt(t("plan.renameTo"), plan.name);
    if (!name || !name.trim() || name === plan.name) return;
    try {
      await post(`/api/plans/${encodeURIComponent(plan.id)}`, { name: name.trim() });
      toast(t("plan.renamed"), "ok");
      loadPlans();
    } catch (error) { toast(error.message, "err", 7000); }
  });

  $("#btn-delete-plan").addEventListener("click", async () => {
    const plan = state.plans.find((p) => p.id === state.selectedPlan);
    if (!plan) return;
    // Gorevleriyle birlikte gider; geri alinamaz.
    const warning = plan.tasks
      ? t("plan.deleteConfirm", { name: plan.name, n: plan.tasks })
      : t("plan.deleteConfirmEmpty", { name: plan.name });
    if (!confirm(warning)) return;
    try {
      const result = await api(`/api/plans/${encodeURIComponent(plan.id)}`, { method: "DELETE" });
      toast(t("plan.deleted", { n: result.removed_tasks }), "warn");
      state.selectedPlan = null;
      await loadPlans();
      loadTasks();
    } catch (error) { toast(error.message, "err", 7000); }
  });

  $("#btn-run-plan").addEventListener("click", async () => {
    const plan = state.plans.find((p) => p.id === state.selectedPlan);
    if (!plan) return;
    try {
      // Etkin plani da bu plana al: ajanin yazacagi yeni gorevler buraya dussun.
      await post(`/api/plans/${encodeURIComponent(plan.id)}`, { active: true });
      await post("/api/run", { phases: ["implement"], plan_id: plan.id });
      toast(t("plan.applying", { name: plan.name, n: plan.ready }), "ok");
      loadOverview();
    } catch (error) { toast(error.message, "err", 8000); }
  });
}

// ─── Plan ─────────────────────────────────────────────────────────────────
const TASK_STATUSES = ["pending", "running", "done", "blocked", "failed", "skipped"];

async function loadTasks() {
  const target = $("#task-list");
  try {
    const scope = state.selectedPlan
      ? `?plan=${encodeURIComponent(state.selectedPlan)}` : "";
    const data = await api(`/api/state/tasks${scope}`);
    state.taskItems = data.items;
    renderTaskPage();
  } catch (error) {
    target.innerHTML = emptyState(t("app.failed"), error.message);
    $("#task-pager").hidden = true;
  }
}

/* Kirk gorevlik bir plan tek DOM'a basildiginda hem yavaslatiyor hem de
   okunmaz oluyordu; analiz ve olay akisiyla ayni sayfalama. Ozet satiri
   ve filtreler HEP butun listeye bakar: "40 gorevden 12'si tamam" bilgisi
   sayfa degistikce degismemeli. */
function renderTaskPage() {
  const target = $("#task-list");
  const all = state.taskItems;

  let items = all;
  if (state.taskFilter) items = items.filter((task) => task.status === state.taskFilter);
  if (state.laneFilter) items = items.filter((task) => task.lane === state.laneFilter);

  const done = all.filter((task) => task.status === "done").length;
  const lanes = {};
  all.forEach((task) => { lanes[task.lane] = (lanes[task.lane] || 0) + 1; });
  const laneText = Object.entries(lanes)
    .map(([lane, n]) => `${tv("lane", lane)} ${n}`).join(" · ");
  $("#plan-sub").textContent = all.length
    ? `${t("plan.summary", {
        done, total: all.length,
        ready: all.filter((task) => task.ready).length })} · ${laneText}`
    : t("plan.empty");

  // Bos bir planin ustunde on bir filtre dugmesi gurultudur: hicbiri bir
  // sey yapmaz. Suzulecek gorev VARSA gosterilir -- suzgecin sonucu bos
  // ciktiginda kalirlar, yoksa geri donulemezdi.
  $(".plan-toolbar").hidden = !all.length;

  // Bos bir planin ustunde on bir filtre dugmesi gurultudur: hicbiri bir
  // sey yapmaz. Suzulecek gorev VARSA gosterilir -- suzgecin sonucu bos
  // ciktiginda kalirlar, yoksa geri donulemezdi.

  if (!items.length) {
    target.innerHTML = emptyState(
      t(all.length ? "plan.noMatch" : "plan.noTasks"),
      all.length ? "" : t("plan.noTaskHint"));
    $("#task-pager").hidden = true;
    return;
  }

  const slice = slicePage(items, state.taskPage, state.taskSize);
  state.taskPage = slice.page;

  target.innerHTML = `<div class="tasks">${slice.items.map((task, index) => {
    const id = slice.start + index;
    return `
      <article class="task" data-status="${esc(task.status)}" data-ready="${task.ready ? 1 : 0}">
        <button class="task-head" data-toggle="${id}" type="button"
                aria-expanded="false" aria-label="${esc(t("plan.taskLabel", {
                  key: task.key, title: task.title,
                  status: tv("status", task.status), lane: tv("lane", task.lane) }))}">
          <span class="task-key">${esc(task.key)}</span>
          <span class="task-title">${esc(task.title)}</span>
          ${task.deps.length ? `<span class="task-deps">← ${esc(task.deps.join(", "))}</span>` : ""}
          ${task.ready ? `<span class="badge" data-v="ready">${esc(t("status.ready"))}</span>` : ""}
          <span class="badge" data-v="${esc(task.status)}">${esc(tv("status", task.status))}</span>
          <span class="badge lane" data-lane="${esc(task.lane)}">${esc(tv("lane", task.lane))}</span>
        </button>
        <div class="task-body" data-body="${id}" hidden>
          ${task.description ? `<p>${esc(task.description)}</p>` : ""}
          ${task.files.length ? `<div class="task-files">${task.files.map((file) => `<span class="task-file">${esc(file)}</span>`).join("")}</div>` : ""}
          ${task.acceptance ? `<div class="task-accept"><strong>${esc(t("plan.acceptance"))}:</strong> ${esc(task.acceptance)}</div>` : ""}
          ${task.result ? `<p style="color:var(--text-3)"><strong>${esc(t("plan.result"))}:</strong> ${esc(task.result)}</p>` : ""}
          <div class="task-actions">
            <select data-status-for="${esc(task.key)}">
              ${TASK_STATUSES.map((status) => `<option value="${status}"${status === task.status ? " selected" : ""}>${esc(tv("status", status))}</option>`).join("")}
            </select>
            ${state.approvalMode === "auto" ? "" :
              `<button class="btn btn-sm" data-run-task="${esc(task.key)}" type="button">${esc(t("plan.runTask"))}</button>`}
          </div>
        </div>
      </article>`;
  }).join("")}</div>`;

  $$("[data-toggle]", target).forEach((button) => {
    button.addEventListener("click", () => {
      const body = $(`[data-body="${button.dataset.toggle}"]`, target);
      body.hidden = !body.hidden;
      button.setAttribute("aria-expanded", String(!body.hidden));
    });
  });

  $$("[data-status-for]", target).forEach((select) => {
    select.addEventListener("change", async () => {
      try {
        await post(`/api/tasks/${select.dataset.statusFor}`, { status: select.value });
        toast(t("plan.taskStatus", { key: select.dataset.statusFor,
                                     status: tv("status", select.value) }), "ok");
        loadTasks();
        loadOverview();
      } catch (error) { toast(error.message, "err"); }
    });
  });

  $$("[data-run-task]", target).forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        await post("/api/run", { phase: "implement", task_key: button.dataset.runTask });
        toast(t("plan.taskRunning", { key: button.dataset.runTask }), "ok");
        showView("workflow");
        loadOverview();
      } catch (error) { toast(error.message, "err", 7000); }
    });
  });

  renderPager($("#task-pager"), {
    total: items.length,
    page: slice.page,
    size: state.taskSize,
    unit: t("plan.tasksUnit"),
    onPage: (page) => {
      state.taskPage = page;
      renderTaskPage();
      $("#task-list").scrollIntoView({ block: "start", behavior: "smooth" });
    },
    onSize: (size) => {
      state.taskSize = size;
      state.taskPage = 1;
      renderTaskPage();
    },
  });
}

function initPlan() {
  $("#task-filters").addEventListener("click", (event) => {
    const chip = event.target.closest(".chip-btn");
    if (!chip) return;
    state.taskFilter = chip.dataset.status;
    state.taskPage = 1;   // filtre degisti, bastan basla
    $$(".chip-btn", $("#task-filters")).forEach((node) => node.classList.toggle("is-active", node === chip));
    loadTasks();
  });
  $("#lane-filters").addEventListener("click", (event) => {
    const chip = event.target.closest(".chip-btn");
    if (!chip) return;
    state.laneFilter = chip.dataset.lane;
    state.taskPage = 1;
    $$(".chip-btn", $("#lane-filters")).forEach((node) => node.classList.toggle("is-active", node === chip));
    loadTasks();
  });
}

// ─── Ciktilar ─────────────────────────────────────────────────────────────
async function loadArtifacts() {
  const list = $("#artifact-list");
  try {
    const data = await api(
      "/api/artifacts" + (state.showOrphans ? "?orphans=1" : ""));
    state.artifactGroups = data.groups;
    $("#artifacts-sub").textContent = data.total
      ? t("artifacts.count", { n: data.total, runs: data.groups.length })
      : t("artifacts.empty");

    // Gizlenen cikti VARSA soylenir. Eskiden bu bilgi yalnizca liste TAMAMEN
    // bosken cikiyordu: rozet 11 derken ekranda 1 cikti gorunuyor ve geri
    // kalanina ulasmanin hicbir yolu yoktu.
    const toggle = $("#btn-orphans");
    toggle.hidden = !(data.orphans || state.showOrphans);
    toggle.textContent = state.showOrphans
      ? t("artifacts.hideOrphans")
      : t("artifacts.showOrphans", { n: data.orphans });

    if (!data.total) {
      list.innerHTML = emptyState(
        t("artifacts.empty"),
        data.orphans
          ? t("artifacts.hiddenOrphans", { n: data.orphans })
          : t("artifacts.emptyHint"));
      $("#artifact-view").innerHTML = emptyState(t("artifacts.empty"));
      return;
    }

    // Her cikti bir kosunun urunudur. Kosu satiri acilir-kapanir; en yeni
    // kosu acik gelir, digerleri kapali -- yirmi kosuluk bir listede hepsi
    // acik olsa asil aradiginiz gorunmez.
    // Detay kutusu bir satirin altina tasinmis olabilir; listeyi yeniden
    // cizmeden once kendi yuvasina alinir, yoksa `innerHTML` onu siler ve
    // bir dahaki acilista `$("#artifact-view")` null doner.
    $("#artifact-layout").append($("#artifact-view"));

    list.innerHTML = data.groups.map((group, index) => {
      const open = state.openArtifactRuns.size
        ? state.openArtifactRuns.has(group.run_id)
        : index === 0;
      const attachments = group.items.filter((i) => i.format === "archive").length;
      // Koşu bir İŞ AKIŞININ adımı; çıktı da o iş akışına ait. Numarası
      // başlıkta yazar ve tıklanınca o akışa götürür -- "bu mockup hangi
      // akıştan çıkmıştı" sorusunun cevabı başka bir ekranda aranmasın.
      // Eski kayıtlarda akış numarası yok; o zaman rozet de yok, çünkü
      // uydurulmuş bir numara olmayan bir akışa götürür.
      //
      // Açıklama şablonun DIŞINDA: `${/* ... */}` biçimindeki bir yorum
      // tarayıcı için yorum ama sözlük denetimi için sıradan bir satır,
      // ve testi kırıyor.
      return `
      <div class="artifact-group" data-open="${open ? 1 : 0}">
        <button class="artifact-group-head" type="button" data-group="${esc(group.run_id)}"
                aria-expanded="${open ? "true" : "false"}">
          <span class="artifact-caret">${open ? "▾" : "▸"}</span>
          ${group.workflow_seq == null ? "" : `
            <span class="artifact-wf" data-goto-wf="${esc(group.workflow_id)}"
                  title="${esc(t("wf.one", { seq: group.workflow_seq }))}"
                  role="link" tabindex="0">${esc(t("artifacts.wfShort", { seq: group.workflow_seq }))}</span>`}
          ${group.seq === null ? "" : `<span class="artifact-run">#${group.seq}</span>`}
          <span class="artifact-group-goal">${esc(group.seq === null
            ? t("artifacts.beforeRuns")
            : (runTitle(group) || group.goal || t("runs.noGoal")))}</span>
          ${attachments ? `<span class="artifact-attach" title="${esc(t("artifacts.attachment"))}">🗜 ${attachments}</span>` : ""}
          <span class="artifact-group-count">${group.items.length}</span>
        </button>
        <div class="artifact-group-body"${open ? "" : " hidden"}>
          ${group.items.map((item) => `
            <button class="artifact-item${item.name === state.activeArtifact ? " is-active" : ""}"
                    data-artifact="${esc(item.name)}" type="button">
              <span class="artifact-name">${esc(item.name)}</span>
              <span class="artifact-meta">${
                item.phase ? `${esc(t("phase." + item.phase))} · ` : ""}${esc(tv("kind", item.kind))} · ${fmtBytes(item.bytes)}</span>
            </button>`).join("")}
        </div>
      </div>`;
    }).join("");

    $$("[data-group]", list).forEach((head) => {
      head.addEventListener("click", () => {
        const key = head.dataset.group;
        const wrap = head.parentElement;
        const body = head.nextElementSibling;
        body.hidden = !body.hidden;
        wrap.dataset.open = body.hidden ? "0" : "1";
        head.setAttribute("aria-expanded", String(!body.hidden));
        $(".artifact-caret", head).textContent = body.hidden ? "▸" : "▾";
        // Ilk tiklamada varsayilani birak, secimi kullanicidan al.
        if (!state.openArtifactRuns.size) {
          data.groups.forEach((g, i) => { if (i === 0) state.openArtifactRuns.add(g.run_id); });
        }
        if (body.hidden) state.openArtifactRuns.delete(key);
        else state.openArtifactRuns.add(key);
      });
    });

    // İş akışı rozeti: tıklanınca o akışın adımlarına götürür.
    // `stopPropagation` şart -- rozet grup başlığının İÇİNDE ve onsuz
    // tıklama grubu açıp kapatırdı.
    $$("[data-goto-wf]", list).forEach((rozet) => {
      const git = (event) => {
        event.stopPropagation();
        state.activeWorkflow = rozet.dataset.gotoWf;
        state.activeRun = null;
        showView("workflow");
      };
      rozet.addEventListener("click", git);
      rozet.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") git(event);
      });
    });

    $$("[data-artifact]", list).forEach((button) => {
      button.addEventListener("click", () => openArtifact(button.dataset.artifact));
    });

    const visible = data.groups.flatMap((g) => g.items);
    if (state.activeArtifact && visible.some((i) => i.name === state.activeArtifact)) {
      openArtifact(state.activeArtifact);
    } else {
      openArtifact(visible[0].name);
    }
  } catch (error) {
    list.innerHTML = emptyState(t("app.failed"), error.message);
  }
}

function closeArtifact() {
  state.activeArtifact = null;
  $("#artifact-layout").dataset.detail = "closed";
  // Detay kutusu listenin icine tasinmis olabilir; kendi yerine doner ki
  // bir sonraki liste cizimi onu silmesin.
  const yuva = $("#artifact-layout");
  if (yuva) yuva.append($("#artifact-view"));
  $("#artifact-view").innerHTML = emptyState(
    t("artifacts.closed"), t("artifacts.closedHint"));
  $$("[data-artifact]").forEach((node) => node.classList.remove("is-active"));
}

async function openArtifact(name) {
  state.activeArtifact = name;
  $("#artifact-layout").dataset.detail = "open";
  $$("[data-artifact]").forEach((node) => node.classList.toggle("is-active", node.dataset.artifact === name));

  const target = $("#artifact-view");
  // Detay, listenin YANINDA degil, secilen satirin ALTINDA acilir. Yan
  // panel ekran boyuydu ve tek bir dosya adi icin yarim ekran harcıyordu;
  // ayrica hangi satira ait oldugu ancak vurgudan anlasiliyordu.
  const satir = $(`[data-artifact="${CSS.escape(name)}"]`);
  if (satir) {
    // Kosu grubu kapaliysa detay gorunmez bir yerde acilirdi: kutu
    // yerlesir, ekranda hicbir sey degismez. Grubu ac.
    const govde = satir.closest(".artifact-group-body");
    if (govde?.hidden) {
      govde.hidden = false;
      const bas = govde.previousElementSibling;
      if (bas) {
        bas.parentElement.dataset.open = "1";
        bas.setAttribute("aria-expanded", "true");
        const ok = $(".artifact-caret", bas);
        if (ok) ok.textContent = "▾";
        state.openArtifactRuns.add(bas.dataset.group);
      }
    }
    if (satir.nextElementSibling !== target) satir.after(target);
  }
  target.innerHTML = '<p class="empty"><span class="spinner"></span></p>';
  try {
    const data = await api(`/api/artifacts/${encodeURIComponent(name)}`);

    // Zip ve diger ikili ciktilar metin degildir: ham baytlari ekrana dokmek
    // anlamsiz karakter yigini uretir. Bunlar ek dosya olarak durur, altinda
    // projede ne yapildigini anlatan rapor gosterilir.
    if (data.format === "archive" || data.format === "binary") {
      target.innerHTML = renderAttachment(data);
      $$("[data-close-artifact]", target).forEach((button) => {
        button.addEventListener("click", closeArtifact);
      });
      return;
    }

    // Ekran goruntusu gosterilir. `browser_screenshot` "kullanici arayuzde
    // gorur" diyor; goruntu `binary` sayildigi surece bu dogru degildi --
    // ekranda yalnizca bir indirme kutusu vardi.
    if (data.format === "image") {
      target.innerHTML = renderImage(data);
      $$("[data-close-artifact]", target).forEach((button) => {
        button.addEventListener("click", closeArtifact);
      });
      return;
    }

    const toolbar = `
      <div class="artifact-toolbar">
        <button class="artifact-close" type="button" data-close-artifact
                title="${esc(t("artifacts.closeDetail"))}"
                aria-label="${esc(t("artifacts.closeDetail"))}">✕</button>
        <div class="chips">
          <button class="chip chip-btn is-active" data-mode="render" type="button">${esc(t("artifacts.render"))}</button>
          <button class="chip chip-btn" data-mode="raw" type="button">${esc(t("artifacts.source"))}</button>
        </div>
      </div>`;

    let rendered;
    if (data.format === "markdown") {
      rendered = `<div class="prose">${data.html}</div>`;
    } else if (data.format === "html") {
      rendered = `<iframe class="mockup-frame" sandbox="allow-scripts"
                    srcdoc="${esc(data.raw)}" title="${esc(data.name)}"></iframe>`;
    } else {
      rendered = `<pre class="prose"><code>${esc(data.raw)}</code></pre>`;
    }
    const raw = `<pre class="prose" hidden data-raw><code>${esc(data.raw)}</code></pre>`;
    target.innerHTML = toolbar + `<div data-render>${rendered}</div>` + raw;

    $$("[data-close-artifact]", target).forEach((button) => {
      button.addEventListener("click", closeArtifact);
    });

    $$("[data-mode]", target).forEach((button) => {
      button.addEventListener("click", () => {
        const showRaw = button.dataset.mode === "raw";
        $("[data-render]", target).hidden = showRaw;
        $("[data-raw]", target).hidden = !showRaw;
        $$("[data-mode]", target).forEach((node) => node.classList.toggle("is-active", node === button));
      });
    });
  } catch (error) {
    target.innerHTML = emptyState(t("artifacts.cannotOpen"), error.message);
  }
}

/* Goruntu ciktisi. Ozet altta durur: ekran goruntusunun ozeti hangi
   sayfanin cekildigidir ve goruntuye bakarken ise yarar.

   Gecikmeli yukleme (lazy) BILEREK yok: olculdu, innerHTML ile bir kaydirma
   kabinin icine konunca Chrome istegi hic acmiyor ve goruntu 2x2 bos bir
   kutu olarak kaliyor. Ekranda tek goruntu var; ertelemenin kazanci yok. */
function renderImage(data) {
  const meta = [t("artifacts.image"), fmtBytes(data.bytes)].filter(Boolean).join(" · ");
  return `
    <div class="artifact-toolbar">
      <button class="artifact-close" type="button" data-close-artifact
              title="${esc(t("artifacts.closeDetail"))}"
              aria-label="${esc(t("artifacts.closeDetail"))}">✕</button>
      <a class="btn btn-ghost btn-sm" href="${esc(data.download)}" download>${esc(t("app.download"))}</a>
    </div>
    <figure class="artifact-image">
      <img src="${esc(data.src)}" alt="${esc(data.summary || data.name)}">
      <figcaption>${esc([meta, data.summary].filter(Boolean).join(" · "))}</figcaption>
    </figure>`;
}

// Ek dosya + rapor. Paketin kendisi indirilir, altinda TESLIMAT.md render edilir.
function renderAttachment(data) {
  const isArchive = data.format === "archive";
  const label = t(data.kind === "package" ? "artifacts.deliveryArchive"
    : isArchive ? "artifacts.archive" : "artifacts.attachment");
  const meta = [
    label,
    fmtBytes(data.bytes),
    isArchive && data.entry_count ? t(data.entry_count === 1 ? "artifacts.fileOne" : "artifacts.files", { n: data.entry_count }) : "",
  ].filter(Boolean).join(" · ");

  const card = `
    <div class="attachment">
      <span class="attachment-icon" aria-hidden="true">${isArchive ? "🗜" : "📎"}</span>
      <div class="attachment-body">
        <div class="attachment-name">${esc(data.name)}</div>
        <div class="attachment-meta">${meta}</div>
      </div>
      <a class="btn btn-primary" href="${esc(data.download)}" download>${esc(t("app.download"))}</a>
    </div>`;

  const contents = isArchive && data.entries?.length
    ? `<details class="attachment-contents">
         <summary>${esc(t("artifacts.contents", { n: data.entry_count }))}</summary>
         <ul>${data.entries.map((e) =>
           `<li><code>${esc(e.name)}</code><span>${fmtBytes(e.bytes)}</span></li>`).join("")}</ul>
       </details>`
    : "";

  const report = data.html
    ? `<section class="attachment-report">
         <h3>${esc(t("artifacts.whatWasDone"))}</h3>
         <div class="prose">${data.html}</div>
       </section>`
    : (isArchive
        ? `<p class="empty">${esc(t("artifacts.noReport"))}</p>`
        : "");

  return `
    <div class="artifact-toolbar">
      <button class="artifact-close" type="button" data-close-artifact
              title="${esc(t("artifacts.closeDetail"))}"
              aria-label="${esc(t("artifacts.closeDetail"))}">✕</button>
      <!-- Ad BURADA duruyor ama render/goruntu detaylarinda durmuyor:
           orada cubuk zaten cipleri ya da indir dugmesini tasiyor. Burada
           cubugun baska icerigi yok; adi da alirsak geriye yalnizca bir
           cizgi kalirdi. -->
      <h2>${esc(data.name)}</h2>
    </div>
    ${card}${contents}${report}`;
}

// ─── Canli akis ───────────────────────────────────────────────────────────
/* Diskteki olay gunlugunun sonunu yukler.

   `seq` YOK bu kayitlarda; `state.lastSeq` bilerek elle surulmez, yoksa
   canli akis kendi imlecini gecmise kaydirip yeni olaylari atlardi. */
async function loadStreamHistory() {
  try {
    const data = await api("/api/events/history?limit=400");
    if (!data.events?.length) return;
    state.events = data.events.concat(state.events);
    renderFeed();
    // Genel bakistaki ve gelistirme sekmesindeki ozet akislar da dolsun:
    // sayfa yenilendiginde "Henuz olay yok" yaziyorlardi, oysa gunluk
    // yerinde duruyordu.
    const son = state.events.slice(-40).map(eventRow).join("");
    for (const selector of ["#mini-feed", "#develop-feed"]) {
      const mini = $(selector);
      if (!mini) continue;
      mini.innerHTML = son;
      mini.scrollTop = mini.scrollHeight;
    }
  } catch {
    /* Gunluk okunamiyorsa canli akis yine calisir; sessizce gecilir. */
  }
}

function connectStream() {
  if (state.source) state.source.close();

  const source = new EventSource(`/api/events?since=${state.lastSeq}`);
  state.source = source;

  source.addEventListener("deerx", (message) => {
    state.reconnectDelay = 1000;
    let event;
    try { event = JSON.parse(message.data); } catch { return; }
    state.lastSeq = Math.max(state.lastSeq, event.seq);
    state.events.push(event);
    if (state.events.length > 3000) state.events.splice(0, state.events.length - 3000);

    appendEvent(event);
    if (event.kind === "approval") loadOverview();
    if (event.actor === "run" && (event.kind === "done" || event.kind === "warn")) loadOverview();
  });

  source.addEventListener("ping", () => { state.reconnectDelay = 1000; });

  source.onerror = () => {
    source.close();
    state.source = null;
    // Ustel geri cekilme; sunucu yeniden basladiginda kendiliginden baglanir.
    setTimeout(connectStream, state.reconnectDelay);
    state.reconnectDelay = Math.min(state.reconnectDelay * 2, 15000);
  };
}

function eventRow(event) {
  const glyph = GLYPH[event.kind] || "·";
  return `<div class="ev" data-kind="${esc(event.kind)}">
    <span class="ev-time">${fmtTime(event.ts)}</span>
    <span class="ev-glyph">${esc(glyph)}</span>
    <span class="ev-actor">${esc(event.actor)}</span>
    <span class="ev-msg">${esc(event.message)}</span>
  </div>`;
}

function passesFilter(event) {
  if (!state.streamFilter) return true;
  if (state.streamFilter === "error") return ["error", "tool_error"].includes(event.kind);
  if (state.streamFilter === "tool")  return ["tool", "tool_error"].includes(event.kind);
  return event.kind === state.streamFilter;
}

function appendEvent(event) {
  if (passesFilter(event)) {
    // Sayfalanmis akista yeni olay yalnizca son sayfayi etkiler. Kullanici
    // gecmise gittiyse sayfasi altindan kaymamali; yalnizca sayfalayicinin
    // toplami tazelenir ve "canliya don" dugmesi belirir.
    if (streamIsLive()) renderFeed();
    else renderStreamPager();
  }

  // Ayni ozet akis iki yerde gorunur: genel bakista "son olaylar",
  // gelistirme sekmesinde kosunun kendi akisi.
  for (const selector of ["#mini-feed", "#develop-feed"]) {
    const mini = $(selector);
    if (!mini) continue;
    if ($(".empty", mini)) mini.innerHTML = "";
    mini.insertAdjacentHTML("beforeend", eventRow(event));
    while (mini.children.length > 40) mini.firstElementChild.remove();
    mini.scrollTop = mini.scrollHeight;
  }
}

/** Akis "canli" ise son sayfadadir ve yeni olaylar dogrudan gorunur. */
function streamIsLive() {
  return state.streamPage === null;
}

function visibleEvents() {
  return state.events.filter(passesFilter);
}

function renderFeed() {
  const feed = $("#feed");
  const visible = visibleEvents();

  if (!visible.length) {
    feed.innerHTML = emptyState(t("stream.empty"), t("stream.emptyHint"));
    $("#stream-pager").hidden = true;
    return;
  }

  const pages = Math.max(1, Math.ceil(visible.length / state.streamSize));
  const page = streamIsLive() ? pages : Math.min(state.streamPage, pages);
  const slice = slicePage(visible, page, state.streamSize);

  feed.innerHTML = slice.items.map(eventRow).join("");
  // Otomatik kaydirma yalnizca canli sayfada anlamli; gecmis sayfada kullanici
  // nereye baktiysa orada kalmali.
  if (streamIsLive() && $("#autoscroll").checked) feed.scrollTop = feed.scrollHeight;
  else feed.scrollTop = 0;

  renderStreamPager();
}

function renderStreamPager() {
  const visible = visibleEvents();
  const pages = Math.max(1, Math.ceil(visible.length / state.streamSize));
  const page = streamIsLive() ? pages : Math.min(state.streamPage, pages);

  renderPager($("#stream-pager"), {
    total: visible.length,
    page,
    size: state.streamSize,
    unit: t("pager.events"),
    onPage: (target) => {
      // Son sayfaya donmek "canli"ya donmektir: sonraki olaylar yine akar.
      state.streamPage = target >= pages ? null : target;
      renderFeed();
    },
    onSize: (size) => {
      state.streamSize = size;
      state.streamPage = null;
      renderFeed();
    },
  });

  const live = $("#stream-live-badge");
  live.hidden = streamIsLive();
}

function initStream() {
  $("#stream-filters").addEventListener("click", (event) => {
    const chip = event.target.closest(".chip-btn");
    if (!chip) return;
    state.streamFilter = chip.dataset.kind;
    state.streamPage = null;  // filtre degisti; kuyruga geri don
    $$(".chip-btn", $("#stream-filters")).forEach((node) => node.classList.toggle("is-active", node === chip));
    renderFeed();
  });
  $("#stream-live-badge").addEventListener("click", () => {
    state.streamPage = null;
    renderFeed();
  });
  $("#clear-feed").addEventListener("click", () => {
    state.events = [];
    renderFeed();
  });
}

// ─── Onay penceresi ───────────────────────────────────────────────────────
function renderApproval() {
  const overlay = $("#approval-overlay");
  const pending = state.approvals;
  if (!pending.length) {
    overlay.hidden = true;
    return;
  }
  const request = pending[0];
  $("#approval-title").textContent = request.action;
  $("#approval-detail").textContent = request.detail || t("approval.noDetail");
  $("#approval-count").textContent =
    pending.length > 1 ? t("approval.waiting", { n: pending.length }) : "";
  overlay.hidden = false;
  overlay.dataset.id = request.id;
}

function initApproval() {
  const resolve = async (granted) => {
    const id = $("#approval-overlay").dataset.id;
    if (!id) return;
    $("#approval-accept").disabled = true;
    $("#approval-reject").disabled = true;
    try {
      await post(`/api/approvals/${id}`, { granted });
    } catch (error) {
      toast(error.message, "err");
    } finally {
      $("#approval-accept").disabled = false;
      $("#approval-reject").disabled = false;
      state.approvals = state.approvals.filter((item) => item.id !== id);
      renderApproval();
      loadOverview();
    }
  };
  $("#approval-accept").addEventListener("click", () => resolve(true));
  $("#approval-reject").addEventListener("click", () => resolve(false));
  document.addEventListener("keydown", (event) => {
    if ($("#approval-overlay").hidden) return;
    if (event.key === "Escape") resolve(false);
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) resolve(true);
  });
}

// ─── Baslangic ────────────────────────────────────────────────────────────
async function init() {
  initTheme();
  // Sabit metinler once cevrilir: giris ekrani da dogru dilde acilsin.
  // Sunucudaki dil ayari `loadOverview` ile gelir ve gerekirse uzerine yazar.
  applyTranslations();
  syncLanguageControls();
  initAuth();
  // Giris gerekiyorsa uygulama hic acilmaz: veri cekmeye calisip 401
  // toast'lari yagdirmak yerine kapida bekleriz.
  if (!(await checkAuth())) return;
  boot();
}

function boot() {
  initRouting();
  initRunControls();
  initKnowledge();
  initAnalysis();
  initPlan();
  initStream();
  initApproval();
  initUpload();
  initDelivery();
  initStepPicker();
  initQuestionNav();
  initPlans();
  initWorkflow();
  initChat();
  initSettings();
  initAudit();
  initWorkspace();
  loadOverview();
  // Once diskteki gecmis, sonra canli akis. Akis yalnizca bellekteki
  // tampondan besleniyordu: sunucu yeniden basladiginda ekran bosalir,
  // "ajan ne yapmisti" sorusunun cevabi arayuzde kalmazdi.
  loadStreamHistory().finally(connectStream);
  // Koşu yokken de arka planda hafif bir tazeleme: dışarıdan (CLI/MCP)
  // yapılan değişiklikler arayüze yansısın.
  setInterval(() => { if (!state.pollTimer) loadOverview(); }, 12000);
}

document.addEventListener("DOMContentLoaded", init);

// ─── Kullaniciya sorulan sorular ──────────────────────────────────────────
// Boru hattini durduran sorular genel bakisin en ustunde gosterilir: kullanici
// baska bir yere bakmadan once bunu gormeli.
function renderQuestions(items) {
  const panel = $("#questions-panel");
  const list = $("#questions-list");

  // Sunucudan gelen sira dogrudur; ama kullanicinin bulundugu soruyu kaybetme.
  const previousKey = state.questions[state.questionIndex]?.key;
  state.questions = items;

  if (!items.length) {
    panel.hidden = true;
    list.innerHTML = "";
    state.questionIndex = 0;
    return;
  }

  const sameKey = items.findIndex((q) => q.key === previousKey);
  state.questionIndex = sameKey >= 0
    ? sameKey
    : Math.min(state.questionIndex, items.length - 1);

  renderCurrentQuestion();
  panel.hidden = false;
}

// Sorular teker teker sorulur. Hepsini bir liste halinde yigmak, bes soruluk
// bir kuyrukta kullaniciyi hangisini cevapladigini sasirtiyordu; tek soru +
// ilerleme cubugu, "sirada ne var" sorusunu her adimda cevapliyor.
function renderCurrentQuestion() {
  const items = state.questions;
  const index = state.questionIndex;
  const q = items[index];
  if (!q) return;

  $("#questions-title").textContent = items.length === 1
    ? t("questions.countOne")
    : t("questions.count", { n: items.length });
  $("#questions-step").textContent = `${index + 1} / ${items.length}`;
  $("#questions-bar-fill").style.width = `${(index / items.length) * 100}%`;

  $("#questions-queue").innerHTML = items.map((item, i) => `
    <button class="queue-dot${i === index ? " is-active" : ""}" type="button"
            data-goto="${i}" title="${esc(item.key)}"
            aria-label="${esc(item.key)}">${i + 1}</button>`).join("");

  $("#question-prev").disabled = index === 0;
  $("#question-next").disabled = index >= items.length - 1;

  $("#questions-list").innerHTML = `
    <article class="question" data-key="${esc(q.key)}">
      <div class="question-head">
        <span class="question-key">${esc(q.key)}</span>
        <h3>${esc(q.question)}</h3>
      </div>
      ${q.why ? `<p class="question-why">${esc(q.why)}</p>` : ""}
      ${q.suggestion ? `<p class="question-suggestion">${esc(
          t("questions.suggestionPrefix", { text: q.suggestion }))}</p>` : ""}
      <div class="question-actions">
        <textarea rows="3" id="question-answer"
                  placeholder="${esc(t("questions.answerPlaceholder"))}"
                  aria-label="${esc(t("questions.answerLabel", { key: q.key }))}"></textarea>
        <div class="question-buttons">
          <button class="btn btn-primary" id="question-send" type="button">${esc(t("questions.confirm"))}</button>
          <button class="btn" id="question-skip" type="button"
                  title="${esc(t("questions.skipHint"))}">${esc(t("questions.skip"))}</button>
        </div>
      </div>
    </article>`;

  const box = $("#question-answer");
  box.value = state.questionDrafts[q.key] || "";
  box.addEventListener("input", () => { state.questionDrafts[q.key] = box.value; });
  box.focus();

  box.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      $("#question-send").click();
    }
  });

  $("#question-send").addEventListener("click", () => {
    const text = box.value.trim();
    if (!text) {
      toast(t("questions.needText"), "warn");
      box.focus();
      return;
    }
    resolveQuestion(q.key, "answer", text);
  });
  $("#question-skip").addEventListener("click", () => {
    resolveQuestion(q.key, "skip", box.value.trim());
  });

  $$("[data-goto]", $("#questions-queue")).forEach((dot) => {
    dot.addEventListener("click", () => {
      state.questionIndex = Number(dot.dataset.goto);
      renderCurrentQuestion();
    });
  });
}

async function resolveQuestion(key, action, text) {
  // Cevap bilgi tabanina da yazilir; gomme hesabi birkac saniye surebilir.
  // Gorunur bir isaret olmadan arayuz "tiklamadi mi?" hissi veriyordu.
  const buttons = [$("#question-send"), $("#question-skip")];
  const pressed = action === "answer" ? buttons[0] : buttons[1];
  const label = pressed.textContent;
  buttons.forEach((b) => { b.disabled = true; });
  pressed.innerHTML = `<span class="spinner"></span> ${esc(t("questions.saving"))}`;
  try {
    const result = await post(`/api/questions/${encodeURIComponent(key)}`, { action, text });
    toast(
      t(action === "answer" ? "questions.answered" : "questions.skipped", { key }),
      action === "answer" ? "ok" : "warn",
    );
    delete state.questionDrafts[key];

    // Cevaplanan soruyu kuyruktan cikar ve dogrudan sonrakine gec — sunucu
    // yanitini beklemeden, cunku kullanicinin akisi burada kopmamali.
    const removed = state.questions.findIndex((q) => q.key === key);
    if (removed >= 0) state.questions.splice(removed, 1);
    state.questionIndex = Math.min(state.questionIndex, state.questions.length - 1);

    if (!state.questions.length) {
      $("#questions-panel").hidden = true;
      toast(
        result.remaining_blocking.length
          ? t("questions.remaining", { n: result.remaining_blocking.length })
          : t("questions.allDone"),
        result.remaining_blocking.length ? "warn" : "ok",
        6000,
      );
    } else {
      renderCurrentQuestion();
    }
    loadOverview();
  } catch (error) {
    buttons.forEach((b) => { b.disabled = false; });
    pressed.textContent = label;
    toast(error.message, "err", 7000);
  }
}

function initQuestionNav() {
  $("#question-prev").addEventListener("click", () => {
    if (state.questionIndex > 0) {
      state.questionIndex -= 1;
      renderCurrentQuestion();
    }
  });
  $("#question-next").addEventListener("click", () => {
    if (state.questionIndex < state.questions.length - 1) {
      state.questionIndex += 1;
      renderCurrentQuestion();
    }
  });
}


// ─── Dokuman yukleme ──────────────────────────────────────────────────────
async function uploadFiles(files) {
  const status = $("#upload-status");
  const list = [...files];
  if (!list.length) return;

  for (const [index, file] of list.entries()) {
    status.innerHTML =
      `<span class="spinner"></span> ${esc(t("kb.uploading", {
        name: file.name, i: index + 1, total: list.length }))}`;
    try {
      const result = await api(`/api/upload?name=${encodeURIComponent(file.name)}`, {
        method: "POST",
        body: file,
      });
      status.innerHTML =
        `<span class="upload-ok">${esc(t("kb.indexed", {
          name: result.name, n: result.chunks }))}</span>`;
    } catch (error) {
      status.innerHTML = `<span class="upload-err">${esc(t("upload.failed", {
        name: file.name, msg: error.message }))}</span>`;
      toast(error.message, "err", 7000);
      break;
    }
  }
  loadDocuments();
  loadOverview();
}

function initUpload() {
  const zone = $("#dropzone");
  const input = $("#file-input");

  input.addEventListener("change", () => {
    uploadFiles(input.files);
    input.value = "";  // ayni dosya tekrar secilebilsin
  });

  ["dragenter", "dragover"].forEach((name) => {
    zone.addEventListener(name, (event) => {
      event.preventDefault();
      zone.classList.add("is-over");
    });
  });
  ["dragleave", "drop"].forEach((name) => {
    zone.addEventListener(name, (event) => {
      event.preventDefault();
      zone.classList.remove("is-over");
    });
  });
  zone.addEventListener("drop", (event) => {
    if (event.dataTransfer?.files?.length) uploadFiles(event.dataTransfer.files);
  });
}

// ─── Teslimat paketi ──────────────────────────────────────────────────────
async function loadDelivery() {
  const panel = $("#delivery-panel");
  const status = $("#delivery-status");
  const issues = $("#delivery-issues");
  const list = $("#delivery-list");

  try {
    const data = await api("/api/package");
    panel.dataset.ready = data.ready ? "1" : "0";
    status.textContent = t(data.ready ? "delivery.ready" : "delivery.notReady");

    const rows = [
      ...data.blockers.map((m) => ({ kind: "blocker", text: m })),
      ...data.warnings.map((m) => ({ kind: "warning", text: m })),
    ];
    issues.innerHTML = rows.length
      ? `<div class="issue-list">${rows.map((r) => `
          <div class="issue" data-kind="${r.kind}">
            <span class="issue-mark">${r.kind === "blocker" ? "✗" : "!"}</span>
            <span>${esc(r.text)}</span>
          </div>`).join("")}</div>`
      : "";

    syncPackageButton();

    // Paketler artik kendi kosularinin altinda, ek olarak listeleniyor;
    // ayrica ustte tekrar gostermek ayni seyi iki yere koymakti.
    list.innerHTML = data.packages.length
      ? `<p class="delivery-hint">${esc(
          t("delivery.packagesMade", { n: data.packages.length }))}</p>`
      : "";
  } catch (error) {
    status.textContent = t("artifacts.deliveryStatusFailed", { msg: error.message });
  }
}

// Zorlama bilincli bir secim olmali: engelli bir projede dugme, kutu
// isaretlenene kadar kapali kalir. Aksi halde "Yine de paketle" yazan bir
// dugme tiklaninca hata veriyordu.
function syncPackageButton() {
  const panel = $("#delivery-panel");
  const button = $("#btn-package");
  const force = $("#package-force");
  const ready = panel.dataset.ready === "1";

  $("#package-force-label").hidden = ready;
  button.textContent = t(ready ? "delivery.package" : "delivery.packageAnyway");
  button.disabled = !ready && !force.checked;
  button.title = button.disabled ? t("delivery.forceHint") : "";
}

function initDelivery() {
  $("#btn-orphans").addEventListener("click", () => {
    state.showOrphans = !state.showOrphans;
    // Acilan/kapanan gruplarin secimi sifirlanir: aksi halde "goster"e
    // basildiginda yeni grup kapali gelir ve hicbir sey olmamis gibi durur.
    state.openArtifactRuns.clear();
    loadArtifacts();
  });
  $("#package-force").addEventListener("change", syncPackageButton);
  $("#btn-package").addEventListener("click", async () => {
    const button = $("#btn-package");
    button.disabled = true;
    button.innerHTML = `<span class="spinner"></span> ${esc(t("delivery.packaging"))}`;
    try {
      const result = await post("/api/package", { force: $("#package-force").checked });
      toast(t("delivery.done", { name: result.name, n: result.file_count }), "ok", 6000);
      if (result.excluded_secrets?.length) {
        toast(t("delivery.secretsExcluded",
                { n: result.excluded_secrets.length }), "warn", 7000);
      }
      // Yeni paketi dogrudan ac ve kosusunu genislet.
      state.activeArtifact = result.name;
      if (result.run_id) state.openArtifactRuns.add(result.run_id);
      loadDelivery();
      loadArtifacts();
    } catch (error) {
      toast(error.message, "err", 8000);
      loadDelivery();
    } finally {
      syncPackageButton();
    }
  });
}
