/* AquaFlow Mini App (no frameworks) */

const tg = window.Telegram?.WebApp;
if (tg) {
  tg.ready();
  tg.expand();
}

const qs = (s) => document.querySelector(s);
const qsa = (s) => Array.from(document.querySelectorAll(s));

let STATE = null;
let CURRENT_TAB = "today";
let CURRENT_MONTH = null;
let SELECTED_TYPE = "water";

// Undo
let lastUndo = { entry_id: null, text: "" };
let undoTimer = null;
let undoSeconds = 0;

// Confetti
let confettiParticles = [];
let confettiAnim = null;

function localISODate(d = new Date()) {
  // "sv-SE" returns YYYY-MM-DD in local TZ
  return d.toLocaleDateString("sv-SE");
}

function monthYM(d = new Date()) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  return `${y}-${m}`;
}

function formatMonthLabel(ym) {
  const [y, m] = ym.split("-").map(Number);
  const months = ["январь","февраль","март","апрель","май","июнь","июль","август","сентябрь","октябрь","ноябрь","декабрь"];
  return `${months[m-1]} ${y} г.`;
}

function api(path, payload) {
  return fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || "API error");
    return r.json();
  });
}

function setTab(tab) {
  CURRENT_TAB = tab;
  qsa(".view").forEach(v => v.classList.remove("active"));
  qs(`#view-${tab}`).classList.add("active");

  qsa(".tab").forEach(b => b.classList.remove("active"));
  qsa(`.tab[data-tab="${tab}"]`).forEach(b => b.classList.add("active"));

  // ensure no horizontal drift
  document.documentElement.scrollLeft = 0;
  document.body.scrollLeft = 0;

  tg?.HapticFeedback?.impactOccurred("light");
}

function showGoalToast() {
  const t = qs("#goalToast");
  t.style.display = "block";
  setTimeout(() => { t.style.display = "none"; }, 2200);
}

function showUndoBar(text, entry_id) {
  lastUndo.entry_id = entry_id;
  lastUndo.text = text;

  qs("#undoText").textContent = text;
  qs("#undoBar").style.display = "block";

  undoSeconds = 5;
  qs("#undoSec").textContent = String(undoSeconds);

  if (undoTimer) clearInterval(undoTimer);
  undoTimer = setInterval(() => {
    undoSeconds -= 1;
    qs("#undoSec").textContent = String(Math.max(0, undoSeconds));
    if (undoSeconds <= 0) {
      hideUndoBar();
    }
  }, 1000);
}

function hideUndoBar() {
  qs("#undoBar").style.display = "none";
  lastUndo.entry_id = null;
  lastUndo.text = "";
  if (undoTimer) clearInterval(undoTimer);
  undoTimer = null;
}

async function undoLast() {
  if (!lastUndo.entry_id) return;
  try {
    const res = await api("/api/undo", {
      initData: tg?.initData || "",
      entry_id: lastUndo.entry_id,
      client_date: localISODate(),
    });
    hideUndoBar();
    await refreshState();
    tg?.HapticFeedback?.notificationOccurred("success");
  } catch (e) {
    console.error(e);
    hideUndoBar();
  }
}

function setDrinkType(t) {
  SELECTED_TYPE = t;
  qsa(".segBtn").forEach(b => b.classList.remove("active"));
  qsa(`.segBtn[data-type="${t}"]`).forEach(b => b.classList.add("active"));
  tg?.HapticFeedback?.impactOccurred("light");
}

function setRing(total, goal) {
  const pct = (goal > 0) ? Math.max(0, Math.min(1, total / goal)) : 0;
  const pctInt = Math.round(pct * 100);
  qs("#pct").textContent = `${pctInt}%`;
  qs("#todayMl").textContent = `${total} мл`;
  qs("#goalMl").textContent = `из ${goal} мл`;

  // 302 is circumference-ish in our SVG
  const dash = 302;
  const offset = dash - (dash * pct);
  qs("circle.fgc").style.strokeDashoffset = String(offset);

  return pctInt;
}

function jumpRing() {
  const ring = qs("#ring");
  ring.classList.add("jump");
  setTimeout(()=> ring.classList.remove("jump"), 400);
}

function renderEntries(entries) {
  const box = qs("#entries");
  if (!entries || entries.length === 0) {
    box.innerHTML = `<div class="item"><div class="t">Пока нет записей.</div><div class="s"></div></div>`;
    return;
  }

  box.innerHTML = entries.map(e => {
    const time = new Date(e.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    const eff = (e.effective_ml !== e.raw_ml) ? ` → ${e.effective_ml}` : "";
    return `<div class="item">
      <div>
        <div class="t">${e.icon} +${e.raw_ml} мл${eff}</div>
        <div class="s">${time}</div>
      </div>
      <div class="s">#${e.id}</div>
    </div>`;
  }).join("");
}

function renderAchievements(list) {
  const box = qs("#achGrid");
  box.innerHTML = (list || []).map(a => {
    const cls = a.unlocked ? "ach" : "ach locked";
    const sub = a.unlocked ? "Открыто ✅" : `Нужно: ${a.threshold} дней`;
    return `<div class="${cls}">
      <div class="achIcon">${a.icon}</div>
      <div>
        <div class="achTitle">${a.title}</div>
        <div class="achSub">${a.subtitle} • ${sub}</div>
      </div>
    </div>`;
  }).join("");
}

function renderChart7(last7, movingAvg7) {
  const chart = qs("#chart7");
  chart.innerHTML = "";

  const maxVal = Math.max(1, ...last7.map(d => d.total_ml), ...last7.map(d => d.goal_ml || 0));
  last7.forEach(d => {
    const col = document.createElement("div");
    col.style.display = "flex";
    col.style.flexDirection = "column";
    col.style.alignItems = "center";
    col.style.flex = "1 1 0";

    const bar = document.createElement("div");
    bar.className = "bar";

    const fill = document.createElement("div");
    fill.className = "barFill";
    fill.style.height = `${Math.round((d.total_ml / maxVal) * 100)}%`;

    bar.appendChild(fill);
    col.appendChild(bar);

    const lbl = document.createElement("div");
    lbl.className = "barLbl";
    // show day of week (Mon..)
    const dd = new Date(d.date + "T12:00:00");
    lbl.textContent = dd.toLocaleDateString("ru-RU", { weekday: "short" }).replace(".", "");
    col.appendChild(lbl);

    chart.appendChild(col);
  });

  // moving average line overlay
  const overlay = document.createElement("div");
  overlay.className = "maLine";
  const svgNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("viewBox", "0 0 100 100");
  svg.setAttribute("preserveAspectRatio", "none");

  const pts = movingAvg7 || [];
  const maxMA = maxVal;
  let dPath = "";
  pts.forEach((v, i) => {
    const x = (i / (pts.length - 1)) * 100;
    const y = 100 - ((v / maxMA) * 100);
    dPath += (i === 0 ? "M" : "L") + x.toFixed(2) + " " + y.toFixed(2) + " ";
  });

  const path = document.createElementNS(svgNS, "path");
  path.setAttribute("d", dPath.trim());
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "rgba(255,255,255,.85)");
  path.setAttribute("stroke-width", "1.2");
  svg.appendChild(path);
  overlay.appendChild(svg);
  chart.appendChild(overlay);
}

function renderDrinks(drinks7) {
  const box = qs("#drinkChart");
  const effTotal = Object.values(drinks7 || {}).reduce((a,x)=>a+(x.effective||0),0) || 1;

  const items = [
    { type:"water", label:"Вода", icon:"💧" },
    { type:"tea", label:"Чай", icon:"🍵" },
    { type:"coffee", label:"Кофе", icon:"☕" },
  ];

  box.innerHTML = items.map(it => {
    const v = drinks7?.[it.type]?.effective || 0;
    const pct = Math.round((v / effTotal) * 100);
    return `<div class="drinkCard">
      <div class="drinkTop"><span>${it.icon} ${it.label}</span><span>${pct}%</span></div>
      <div class="drinkBar"><div style="width:${pct}%"></div></div>
      <div class="drinkSub">${v} мл (в зачёт)</div>
    </div>`;
  }).join("");
}

function renderWeeklyWOW(stats) {
  const week_total = stats.week_total || 0;
  const week_goal = stats.week_goal || 0;
  const pctInt = stats.week_pct || 0;

  const water = qs("#weekWater");
  water.style.height = `${pctInt}%`;

  qs("#weekPctText").textContent = `${pctInt}%`;
  qs("#weekBig").textContent = `${pctInt}% недели`;
  qs("#weekSmall").textContent = `${week_total} / ${week_goal} мл`;
  const left = Math.max(0, week_goal - week_total);
  qs("#weekHint").textContent = (left === 0 && week_goal > 0) ? "Идеальная неделя ✅" : `До идеальной недели осталось ${left} мл`;

  // speed up waves after 80%
  const waves = qsa(".wave");
  if (pctInt >= 80) {
    waves.forEach(w => w.style.animationDuration = (w.classList.contains("wave1") ? "2.0s" : "3.0s"));
  } else {
    waves.forEach(w => w.style.animationDuration = "");
  }

  // Confetti at 100%
  if (pctInt >= 100 && week_goal > 0) {
    burstConfetti(90);
  }
}

function renderCalendar(calData, goal) {
  qs("#monthLabel").textContent = formatMonthLabel(calData.month);
  const grid = qs("#calGrid");
  grid.innerHTML = "";

  calData.days.forEach(d => {
    const cell = document.createElement("div");
    cell.className = "day";
    if (!d.in_month) cell.classList.add("mute");

    const ratio = d.ratio || 0; // 0..2
    // heat intensity 0..1 based on ratio (cap at 1)
    const intensity = Math.max(0, Math.min(1, ratio));
    // set background intensity like github
    const alpha = d.total_ml <= 0 ? 0.12 : (0.18 + intensity * 0.55);
    cell.style.background = `rgba(34, 211, 238, ${alpha})`;

    if (d.total_ml > 0 && d.goal_ml > 0) {
      if (d.total_ml >= d.goal_ml) cell.classList.add("ok");
      else cell.classList.add("some");
    }

    const left = document.createElement("div");
    left.className = "d";
    left.textContent = String(d.day);

    const badge = document.createElement("div");
    badge.className = "badge";

    cell.appendChild(left);
    cell.appendChild(badge);

    cell.addEventListener("click", () => openDayModal(d));
    grid.appendChild(cell);
  });
}

function openDayModal(d) {
  // show modal with day stats
  qs("#modal").style.display = "flex";
  const dd = new Date(d.date + "T12:00:00");
  qs("#mDate").textContent = dd.toLocaleDateString("ru-RU", { day:"2-digit", month:"long", year:"numeric" });
  qs("#mSub").textContent = `Прогресс: ${Math.round((d.goal_ml>0 ? (d.total_ml/d.goal_ml) : 0)*100)}%`;
  qs("#mTotal").textContent = `${d.total_ml} мл`;
  qs("#mGoal").textContent = `${d.goal_ml} мл`;
  qs("#mStatus").textContent = (d.goal_ml>0 && d.total_ml>=d.goal_ml) ? "Норма ✅" : (d.total_ml>0 ? "Есть данные" : "Нет данных");
}

function closeModal() {
  qs("#modal").style.display = "none";
}

// ---------------- Confetti ----------------
function resizeConfetti() {
  const c = qs("#confetti");
  const dpr = window.devicePixelRatio || 1;
  c.width = Math.floor(window.innerWidth * dpr);
  c.height = Math.floor(window.innerHeight * dpr);
  c.style.width = window.innerWidth + "px";
  c.style.height = window.innerHeight + "px";
  const ctx = c.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function burstConfetti(count=80) {
  resizeConfetti();
  const c = qs("#confetti");
  const cx = window.innerWidth / 2;
  const cy = window.innerHeight * 0.25;

  for (let i=0;i<count;i++){
    confettiParticles.push({
      x: cx,
      y: cy,
      vx: (Math.random()*2-1) * (3 + Math.random()*4),
      vy: - (5 + Math.random()*6),
      g: 0.22 + Math.random()*0.15,
      r: 2 + Math.random()*3,
      life: 80 + Math.random()*40,
      rot: Math.random()*Math.PI,
      vr: (Math.random()*2-1)*0.12
    });
  }

  if (!confettiAnim) {
    confettiAnim = requestAnimationFrame(tickConfetti);
  }
}

function tickConfetti() {
  const c = qs("#confetti");
  const ctx = c.getContext("2d");
  ctx.clearRect(0,0,window.innerWidth,window.innerHeight);

  confettiParticles = confettiParticles.filter(p => p.life > 0);
  for (const p of confettiParticles) {
    p.life -= 1;
    p.vy += p.g;
    p.x += p.vx;
    p.y += p.vy;
    p.rot += p.vr;

    ctx.save();
    ctx.translate(p.x, p.y);
    ctx.rotate(p.rot);
    ctx.globalAlpha = Math.max(0, Math.min(1, p.life/120));
    ctx.fillStyle = "rgba(255,255,255,.85)";
    ctx.fillRect(-p.r, -p.r, p.r*2.2, p.r*1.3);
    ctx.restore();
  }

  if (confettiParticles.length > 0) {
    confettiAnim = requestAnimationFrame(tickConfetti);
  } else {
    confettiAnim = null;
    ctx.clearRect(0,0,window.innerWidth,window.innerHeight);
  }
}

// ---------------- State rendering ----------------
function renderState(state) {
  STATE = state;
  const user = state.user;
  const prof = state.profile;
  const today = state.today;
  const st = state.stats;

  qs("#userLine").textContent = user.username ? `${user.first_name} • @${user.username}` : `${user.first_name}`;

  qs("#todayDate").textContent = new Date(today.date + "T12:00:00").toLocaleDateString("ru-RU", { day:"2-digit", month:"long" });

  // profile block
  qs("#pWeight").textContent = prof.weight_kg ? `${prof.weight_kg} кг` : "—";
  qs("#pFactor").textContent = `${prof.factor_ml} мл/кг`;
  qs("#pGoal").textContent = prof.goal_ml ? `${prof.goal_ml} мл/день` : "—";

  // level + streak
  if (prof.level && prof.level !== "—") {
    qs("#levelPill").style.display = "inline-flex";
    qs("#levelVal").textContent = prof.level;
  } else {
    qs("#levelPill").style.display = "none";
  }

  qs("#streakPill").style.display = "inline-flex";
  qs("#streakVal").textContent = String(st.current_streak || 0);

  if (prof.to_next && prof.to_next > 0) {
    qs("#nextLine").style.display = "block";
    qs("#toNext").textContent = String(prof.to_next);
  } else {
    qs("#nextLine").style.display = "none";
  }

  // today ring
  const pct = setRing(today.total_ml, today.goal_ml);

  // entries
  renderEntries(today.entries);

  // stats
  qs("#avg7").textContent = `${st.avg7} мл`;
  qs("#median7").textContent = `${st.median7} мл`;
  qs("#aboveBelow").textContent = `${st.above} / ${st.below}`;
  qs("#bestDay").textContent = `${st.best_day} мл`;
  qs("#curStreak").textContent = `${st.current_streak}`;
  qs("#bestStreak").textContent = `${st.best_streak}`;

  renderChart7(st.last7, st.moving_avg7);
  renderDrinks(st.drinks7);
  renderWeeklyWOW(st);

  renderAchievements(state.achievements);

  // calendar
  renderCalendar(state.calendar, prof.goal_ml);
}

async function refreshState() {
  const payload = {
    initData: tg?.initData || "",
    month: CURRENT_MONTH,
    client_date: localISODate(),
  };
  const state = await api("/api/state", payload);
  renderState(state);
}

async function addWater(ml) {
  try {
    const res = await api("/api/add", {
      initData: tg?.initData || "",
      ml: ml,
      type: SELECTED_TYPE,
      client_date: localISODate(),
      client_ts: new Date().toISOString(),
    });

    jumpRing();
    tg?.HapticFeedback?.impactOccurred("medium");

    // show goal toast
    if (res.goal_completed_today) {
      showGoalToast();
      burstConfetti(65);
      tg?.HapticFeedback?.notificationOccurred("success");
    }

    // weekly confetti - light
    if (res.week_completed) {
      burstConfetti(90);
    }

    // show undo
    const icon = (SELECTED_TYPE === "tea") ? "🍵" : (SELECTED_TYPE === "coffee") ? "☕" : "💧";
    showUndoBar(`${icon} Добавлено: +${ml} мл`, res.entry_id);

    await refreshState();
  } catch (e) {
    console.error(e);
    tg?.HapticFeedback?.notificationOccurred("error");
  }
}

function setupEvents() {
  qs("#closeBtn").addEventListener("click", () => {
    tg?.close();
  });

  // tabs
  qsa(".tab").forEach(b => {
    b.addEventListener("click", () => setTab(b.dataset.tab));
  });

  // chips
  qsa(".chip").forEach(b => {
    b.addEventListener("click", () => addWater(parseInt(b.dataset.add, 10)));
  });

  // custom
  qs("#addCustom").addEventListener("click", () => {
    const v = parseInt(qs("#customMl").value || "0", 10);
    if (v > 0) addWater(v);
    qs("#customMl").value = "";
  });

  // drink type
  qsa(".segBtn").forEach(b => {
    b.addEventListener("click", () => setDrinkType(b.dataset.type));
  });

  // profile saves
  qs("#saveWeight").addEventListener("click", async () => {
    const v = parseInt(qs("#weightInput").value || "0", 10);
    await api("/api/profile", { initData: tg?.initData || "", weight_kg: v, client_date: localISODate() });
    qs("#weightInput").value = "";
    await refreshState();
  });

  qs("#saveFactor").addEventListener("click", async () => {
    const v = parseInt(qs("#factorInput").value || "33", 10);
    await api("/api/profile", { initData: tg?.initData || "", factor_ml: v, client_date: localISODate() });
    qs("#factorInput").value = "";
    await refreshState();
  });

  qs("#saveGoal").addEventListener("click", async () => {
    const v = parseInt(qs("#goalInput").value || "0", 10);
    await api("/api/profile", { initData: tg?.initData || "", goal_ml: v, client_date: localISODate() });
    qs("#goalInput").value = "";
    await refreshState();
  });

  // calendar month nav
  qs("#prevMonth").addEventListener("click", async () => {
    const [y, m] = CURRENT_MONTH.split("-").map(Number);
    const d = new Date(y, m-2, 1);
    CURRENT_MONTH = monthYM(d);
    await refreshState();
    tg?.HapticFeedback?.impactOccurred("light");
  });

  qs("#nextMonth").addEventListener("click", async () => {
    const [y, m] = CURRENT_MONTH.split("-").map(Number);
    const d = new Date(y, m, 1);
    CURRENT_MONTH = monthYM(d);
    await refreshState();
    tg?.HapticFeedback?.impactOccurred("light");
  });

  // modal
  qs("#mClose").addEventListener("click", closeModal);
  qs("#modal").addEventListener("click", (e) => {
    if (e.target.id === "modal") closeModal();
  });

  // undo
  qs("#undoBtn").addEventListener("click", undoLast);

  // export
  qs("#csvBtn").addEventListener("click", () => downloadExport("csv"));
  qs("#pdfBtn").addEventListener("click", () => downloadExport("pdf"));

  window.addEventListener("resize", () => resizeConfetti());
}

function downloadExport(kind) {
  const initData = encodeURIComponent(tg?.initData || "");
  const url = kind === "pdf"
    ? `/export/pdf?initData=${initData}&month=${CURRENT_MONTH}`
    : `/export/csv?initData=${initData}&month=${CURRENT_MONTH}`;
  window.open(url, "_blank");
}

// ---------------- Boot ----------------
(async function boot(){
  try {
    setupEvents();
    CURRENT_MONTH = monthYM(new Date());
    await refreshState();
  } catch (e) {
    console.error(e);
    qs("#userLine").textContent = "Ошибка загрузки. Проверь BOT_TOKEN/доступ.";
  }
})();