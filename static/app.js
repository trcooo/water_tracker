const tg = window.Telegram.WebApp;

function qs(s){ return document.querySelector(s); }
function qsa(s){ return Array.from(document.querySelectorAll(s)); }

const fg = document.querySelector("circle.fgc");
const CIRC = 302;

function getTzOffsetMin(){
  return -new Date().getTimezoneOffset(); // Москва => +180
}

function fmtTime(isoUtc){
  const d = new Date(isoUtc);
  return d.toLocaleTimeString([], {hour:"2-digit", minute:"2-digit"});
}

function pctOf(today, goal){
  if (!goal || goal <= 0) return 0;
  return Math.max(0, Math.min(1, today / goal));
}

function setProgress(today, goal){
  const p = pctOf(today, goal);
  fg.style.strokeDashoffset = String(CIRC * (1 - p));
  qs("#pct").textContent = `${Math.round(p * 100)}%`;
  qs("#todayMl").textContent = `${today} мл`;
  qs("#goalMl").textContent = `из ${goal} мл`;
}

function renderEntries(entries){
  const box = qs("#entries");
  if (!entries || entries.length === 0){
    box.textContent = "Пока нет записей.";
    return;
  }
  box.innerHTML = "";
  entries.forEach(e => {
    const div = document.createElement("div");
    div.className = "item";
    div.innerHTML = `<div class="t">${e.amount_ml} мл</div><div class="s">${fmtTime(e.ts)}</div>`;
    box.appendChild(div);
  });
}

async function api(path, payload){
  const res = await fetch(path, {
    method:"POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify(payload)
  });
  if (!res.ok){
    const txt = await res.text();
    throw new Error(txt);
  }
  return await res.json();
}

/* Tabs */
function setTab(tabName){
  qsa(".view").forEach(v => v.classList.remove("active"));
  qs(`#view-${tabName}`).classList.add("active");

  qsa(".tabbar .tab").forEach(b => b.classList.remove("active"));
  qs(`.tabbar .tab[data-tab="${tabName}"]`).classList.add("active");

  tg.HapticFeedback?.impactOccurred("light");
}

function initTabs(){
  qsa(".tabbar .tab").forEach(btn => {
    btn.addEventListener("click", () => setTab(btn.dataset.tab));
  });
}

/* Calendar */
let calYear = null;
let calMonth = null; // 1..12
let calendarCache = {}; // date -> {total_ml, goal_ml}

function monthName(y, m){
  const d = new Date(y, m-1, 1);
  return d.toLocaleDateString("ru-RU", {month:"long", year:"numeric"});
}

function buildCalendarGrid(y, m){
  // Monday-first calendar
  const first = new Date(y, m-1, 1);
  const last = new Date(y, m, 0);
  const daysInMonth = last.getDate();

  // JS: getDay() Sunday=0..Saturday=6
  const firstDow = (first.getDay() + 6) % 7; // Monday=0
  const totalCells = Math.ceil((firstDow + daysInMonth) / 7) * 7;

  const grid = qs("#calGrid");
  grid.innerHTML = "";

  for (let i=0; i<totalCells; i++){
    const cell = document.createElement("div");
    cell.className = "day";

    const dayNum = i - firstDow + 1;
    if (dayNum < 1 || dayNum > daysInMonth){
      cell.classList.add("mute");
      cell.innerHTML = `<div class="d"> </div><div class="badge"></div>`;
      grid.appendChild(cell);
      continue;
    }

    const dateStr = `${y}-${String(m).padStart(2,"0")}-${String(dayNum).padStart(2,"0")}`;
    const stat = calendarCache[dateStr] || null;

    let cls = "";
    let badgeTitle = "нет данных";
    if (stat){
      if (stat.total_ml >= stat.goal_ml) { cls = "ok"; badgeTitle = "норма"; }
      else if (stat.total_ml > 0) { cls = "some"; badgeTitle = "пил воду"; }
    }
    if (cls) cell.classList.add(cls);

    cell.innerHTML = `<div class="d">${dayNum}</div><div class="badge" title="${badgeTitle}"></div>`;
    cell.addEventListener("click", () => openDayModal(dateStr, stat));
    grid.appendChild(cell);
  }
}

async function loadCalendar(y, m){
  qs("#monthLabel").textContent = monthName(y, m);
  const data = await api("/api/calendar", {
    initData: tg.initData,
    tzOffsetMin: getTzOffsetMin(),
    year: y,
    month: m
  });
  calendarCache = data.days || {};
  buildCalendarGrid(y, m);
}

/* Modal */
function openDayModal(dateStr, stat){
  qs("#modal").style.display = "flex";
  qs("#mDate").textContent = new Date(dateStr).toLocaleDateString("ru-RU", {day:"numeric", month:"long", year:"numeric"});

  if (!stat){
    qs("#mSub").textContent = "Нет данных";
    qs("#mTotal").textContent = "0 мл";
    qs("#mGoal").textContent = "—";
    qs("#mStatus").textContent = "—";
    return;
  }

  const total = stat.total_ml;
  const goal = stat.goal_ml;
  qs("#mSub").textContent = `Прогресс: ${Math.round(pctOf(total, goal)*100)}%`;
  qs("#mTotal").textContent = `${total} мл`;
  qs("#mGoal").textContent = `${goal} мл`;
  qs("#mStatus").textContent = (total >= goal) ? "✅ Норма выполнена" : (total > 0 ? "💧 Есть вода" : "—");
}

function closeModal(){
  qs("#modal").style.display = "none";
}

/* Stats (7-day bars) */
function renderChart7(last7){
  const wrap = qs("#chart7");
  wrap.innerHTML = "";

  const maxVal = Math.max(...last7.map(x => x.goal_ml || 0), ...last7.map(x => x.total_ml || 0), 1);

  last7.forEach(x => {
    const bar = document.createElement("div");
    bar.className = "bar";

    const fill = document.createElement("div");
    fill.className = "barFill";
    const h = Math.round((x.total_ml / maxVal) * 100);
    fill.style.height = `${Math.max(2, Math.min(100, h))}%`;

    bar.appendChild(fill);

    const col = document.createElement("div");
    col.style.flex = "1 1 0";
    col.style.display = "flex";
    col.style.flexDirection = "column";
    col.style.alignItems = "stretch";
    col.style.gap = "6px";

    col.appendChild(bar);

    const lbl = document.createElement("div");
    lbl.className = "barLbl";
    const d = new Date(x.date);
    lbl.textContent = d.toLocaleDateString("ru-RU", {weekday:"short"}).replace(".", "");

    col.appendChild(lbl);
    wrap.appendChild(col);
  });
}

/* Profile render */
function renderProfile(state){
  qs("#pWeight").textContent = state.weight_kg ? `${state.weight_kg} кг` : "не указан";
  qs("#pFactor").textContent = `${state.ml_per_kg} мл/кг`;
  qs("#pGoal").textContent = `${state.goal_ml} мл/день`;

  qs("#goalInput").value = state.goal_ml || 2000;
  if (state.weight_kg) qs("#weightInput").value = state.weight_kg;
  qs("#factorInput").value = state.ml_per_kg || 33;
}

/* State render */
function renderState(state){
  setProgress(state.today_ml, state.goal_ml);
  renderEntries(state.entries);

  // header
  const u = tg.initDataUnsafe?.user;
  const name = u ? [u.first_name, u.last_name].filter(Boolean).join(" ") : "Пользователь";
  qs("#userLine").textContent = `${name} • AquaFlow`;

  qs("#todayDate").textContent = new Date(state.today_local_date).toLocaleDateString("ru-RU", {day:"numeric", month:"long"});

  // formula line
  if (state.weight_kg){
    qs("#formulaLine").textContent = `Норма: ${state.weight_kg} × ${state.ml_per_kg} = ${state.goal_ml} мл/день`;
  } else {
    qs("#formulaLine").textContent = "Укажи вес в боте: /setweight 70";
  }

  // streak pills
  if (state.current_streak && state.current_streak > 0){
    qs("#streakPill").style.display = "inline-flex";
    qs("#streakVal").textContent = state.current_streak;
  } else {
    qs("#streakPill").style.display = "none";
  }

  if (state.best_streak && state.best_streak > 0){
    qs("#bestPill").style.display = "inline-flex";
    qs("#bestVal").textContent = state.best_streak;
  } else {
    qs("#bestPill").style.display = "none";
  }

  // stats view
  qs("#avg7").textContent = `${state.stats.avg_7} мл`;
  qs("#bestDay").textContent = state.stats.best_day?.date ? `${state.stats.best_day.total_ml} мл` : "—";
  qs("#curStreak").textContent = `${state.stats.current_streak}`;
  qs("#bestStreak").textContent = `${state.stats.best_streak}`;
  renderChart7(state.stats.last7);

  // calendar state
  if (calYear === null || calMonth === null){
    const d = new Date(state.today_local_date);
    calYear = d.getFullYear();
    calMonth = d.getMonth() + 1;
  }

  // profile view
  renderProfile(state);
}

async function loadState(){
  return await api("/api/state", {
    initData: tg.initData,
    tzOffsetMin: getTzOffsetMin()
  });
}

async function addWater(amount){
  const data = await api("/api/add", {
    initData: tg.initData,
    tzOffsetMin: getTzOffsetMin(),
    amountMl: amount
  });
  tg.HapticFeedback?.impactOccurred("light");
  renderState(data.state);
  // обновим календарь если открыт
  await loadCalendar(calYear, calMonth);
}

async function saveGoal(goal){
  const data = await api("/api/goal", {
    initData: tg.initData,
    tzOffsetMin: getTzOffsetMin(),
    goalMl: goal
  });
  tg.HapticFeedback?.notificationOccurred("success");
  renderState(data.state);
  await loadCalendar(calYear, calMonth);
}

async function saveProfile(payload){
  const data = await api("/api/profile", {
    initData: tg.initData,
    tzOffsetMin: getTzOffsetMin(),
    ...payload
  });
  tg.HapticFeedback?.notificationOccurred("success");
  renderState(data.state);
  await loadCalendar(calYear, calMonth);
}

/* Main */
async function main(){
  tg.ready();
  tg.expand();

  initTabs();

  qs("#closeBtn").addEventListener("click", () => tg.close());

  qsa("button[data-add]").forEach(btn => {
    btn.addEventListener("click", () => addWater(parseInt(btn.dataset.add, 10)));
  });

  qs("#addCustom").addEventListener("click", () => {
    const val = parseInt(qs("#customMl").value || "0", 10);
    if (!val || val <= 0) return;
    addWater(val);
    qs("#customMl").value = "";
  });

  qs("#saveGoal").addEventListener("click", () => {
    const goal = parseInt(qs("#goalInput").value || "0", 10);
    if (!goal || goal < 500) return;
    saveGoal(goal);
  });

  qs("#saveWeight").addEventListener("click", () => {
    const w = parseInt(qs("#weightInput").value || "0", 10);
    if (!w || w < 20 || w > 300) return;
    saveProfile({weightKg: w});
  });

  qs("#saveFactor").addEventListener("click", () => {
    const k = parseInt(qs("#factorInput").value || "0", 10);
    if (!k || k < 30 || k > 35) return;
    saveProfile({mlPerKg: k});
  });

  qs("#mClose").addEventListener("click", closeModal);
  qs("#modal").addEventListener("click", (e) => {
    if (e.target && e.target.id === "modal") closeModal();
  });

  qs("#prevMonth").addEventListener("click", async () => {
    calMonth -= 1;
    if (calMonth < 1){ calMonth = 12; calYear -= 1; }
    await loadCalendar(calYear, calMonth);
  });

  qs("#nextMonth").addEventListener("click", async () => {
    calMonth += 1;
    if (calMonth > 12){ calMonth = 1; calYear += 1; }
    await loadCalendar(calYear, calMonth);
  });

  try{
    const state = await loadState();
    renderState(state);
    await loadCalendar(calYear, calMonth);
  } catch(err){
    console.error(err);
    alert("Ошибка загрузки. Открой Mini App из Telegram и проверь HTTPS/переменные на Railway.");
  }
}

main();
