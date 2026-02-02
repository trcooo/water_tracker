const tg = window.Telegram.WebApp;

function qs(sel){ return document.querySelector(sel); }
function qsa(sel){ return Array.from(document.querySelectorAll(sel)); }

const fg = document.querySelector("circle.fgc");
const CIRC = 302;

function setProgress(today, goal){
  const pct = goal > 0 ? Math.min(1, today / goal) : 0;
  fg.style.strokeDashoffset = String(CIRC * (1 - pct));
  qs("#pct").textContent = `${Math.round(pct * 100)}%`;
  qs("#todayMl").textContent = `${today} мл`;
  qs("#goalMl").textContent = `из ${goal} мл`;
}

function fmtTime(isoUtc){
  const d = new Date(isoUtc);
  return d.toLocaleTimeString([], {hour:"2-digit", minute:"2-digit"});
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
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify(payload)
  });
  if (!res.ok){
    const t = await res.text();
    throw new Error(t);
  }
  return await res.json();
}

function getTzOffsetMin(){
  return -new Date().getTimezoneOffset();
}

async function loadState(){
  const data = await api("/api/state", {
    initData: tg.initData,
    tzOffsetMin: getTzOffsetMin(),
  });

  const u = tg.initDataUnsafe?.user;
  const name = u ? [u.first_name, u.last_name].filter(Boolean).join(" ") : "Пользователь";
  qs("#userLine").textContent = `${name} • сегодня`;

  setProgress(data.today_ml, data.goal_ml);
  renderEntries(data.entries);

  qs("#goalInput").value = data.goal_ml;

  // Линия с формулой/профилем
  if (data.weight_kg){
    qs("#formulaLine").textContent = `Норма: ${data.weight_kg} кг × ${data.ml_per_kg} мл = ${data.goal_ml} мл/день`;
  } else {
    qs("#formulaLine").textContent = "Укажи вес в боте: /setweight 70";
  }
}

async function addWater(amount){
  const data = await api("/api/add", {
    initData: tg.initData,
    tzOffsetMin: getTzOffsetMin(),
    amountMl: amount
  });
  setProgress(data.today_ml, data.goal_ml);
  renderEntries(data.entries);
  tg.HapticFeedback?.impactOccurred("light");
}

async function saveGoal(goal){
  const data = await api("/api/goal", {
    initData: tg.initData,
    tzOffsetMin: getTzOffsetMin(),
    goalMl: goal
  });
  setProgress(data.today_ml, data.goal_ml);
  renderEntries(data.entries);
  tg.HapticFeedback?.notificationOccurred("success");
  // обновим формулу
  if (data.weight_kg){
    qs("#formulaLine").textContent = `Норма: ${data.weight_kg} кг × ${data.ml_per_kg} мл = ${data.goal_ml} мл/день`;
  }
}

function main(){
  tg.ready();
  tg.expand();

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

  loadState().catch(err => {
    console.error(err);
    alert("Ошибка загрузки. Открой Mini App из Telegram и проверь, что сервер доступен по HTTPS.");
  });
}

main();
