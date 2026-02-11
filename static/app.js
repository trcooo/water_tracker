/* AquaFlow — previous stable version */

const tg = window.Telegram?.WebApp;
if (tg) { tg.ready(); tg.expand(); }

const qs = (s) => document.querySelector(s);
const qsa = (s) => Array.from(document.querySelectorAll(s));

let STATE = null;
let CURRENT_MONTH = null;

function localISODate(d = new Date()) { return d.toLocaleDateString("sv-SE"); }
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
  return fetch(path, { method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify(payload) })
    .then(async (r) => {
      if (!r.ok) {
        let d = {}; try { d = await r.json(); } catch {}
        throw new Error(d.detail || "API error");
      }
      return r.json();
    });
}
function setTab(tab) {
  qsa(".view").forEach(v => v.classList.remove("active"));
  qs(`#view-${tab}`).classList.add("active");
  qsa(".tab").forEach(b => b.classList.remove("active"));
  qsa(`.tab[data-tab="${tab}"]`).forEach(b => b.classList.add("active"));
  document.documentElement.scrollLeft = 0;
  document.body.scrollLeft = 0;
  tg?.HapticFeedback?.impactOccurred("light");
}
function showGoalToast() {
  const t = qs("#goalToast");
  t.style.display = "block";
  setTimeout(() => { t.style.display = "none"; }, 2200);
}
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
let confetti = [];
let confAnim = null;
function burstConfetti(count = 70) {
  resizeConfetti();
  const cx = window.innerWidth / 2;
  const cy = window.innerHeight * 0.25;
  for (let i=0;i<count;i++){
    confetti.push({
      x: cx, y: cy,
      vx: (Math.random()*2-1) * (3 + Math.random()*3),
      vy: - (5 + Math.random()*6),
      g: 0.22 + Math.random()*0.15,
      r: 2 + Math.random()*3,
      life: 80 + Math.random()*40,
      rot: Math.random()*Math.PI,
      vr: (Math.random()*2-1)*0.12
    });
  }
  if (!confAnim) confAnim = requestAnimationFrame(tickConfetti);
}
function tickConfetti() {
  const c = qs("#confetti");
  const ctx = c.getContext("2d");
  ctx.clearRect(0,0,window.innerWidth,window.innerHeight);
  confetti = confetti.filter(p => p.life > 0);
  for (const p of confetti) {
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
  if (confetti.length > 0) confAnim = requestAnimationFrame(tickConfetti);
  else { confAnim = null; ctx.clearRect(0,0,window.innerWidth,window.innerHeight); }
}
function setRing(total, goal) {
  const pct = (goal > 0) ? Math.max(0, Math.min(1, total / goal)) : 0;
  const pctInt = Math.round(pct * 100);
  qs("#pct").textContent = `${pctInt}%`;
  qs("#todayMl").textContent = `${total} мл`;
  qs("#goalMl").textContent = `из ${goal} мл`;
  const dash = 302;
  const offset = dash - (dash * pct);
  qs("circle.fgc").style.strokeDashoffset = String(offset);
  return pctInt;
}
function renderEntries(entries) {
  const box = qs("#entries");
  if (!entries || entries.length === 0) {
    box.innerHTML = `<div class="item"><div class="t">Пока нет записей.</div><div class="s"></div></div>`;
    return;
  }
  box.innerHTML = entries.map(e => {
    const time = new Date(e.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    return `<div class="item">
      <div>
        <div class="t">💧 +${e.ml} мл</div>
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
        <div class="achSub">${sub}</div>
      </div>
    </div>`;
  }).join("");
}
function renderChart7(last7) {
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
    const dd = new Date(d.date + "T12:00:00");
    lbl.textContent = dd.toLocaleDateString("ru-RU", { weekday: "short" }).replace(".", "");
    col.appendChild(lbl);
    chart.appendChild(col);
  });
}
function renderCalendar(calData) {
  qs("#monthLabel").textContent = formatMonthLabel(calData.month);
  const grid = qs("#calGrid");
  grid.innerHTML = "";
  calData.days.forEach(d => {
    const cell = document.createElement("div");
    cell.className = "day";
    if (!d.in_month) cell.classList.add("mute");
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
  qs("#modal").style.display = "flex";
  const dd = new Date(d.date + "T12:00:00");
  qs("#mDate").textContent = dd.toLocaleDateString("ru-RU", { day:"2-digit", month:"long", year:"numeric" });
  qs("#mSub").textContent = `Прогресс: ${Math.round((d.goal_ml>0 ? (d.total_ml/d.goal_ml) : 0)*100)}%`;
  qs("#mTotal").textContent = `${d.total_ml} мл`;
  qs("#mGoal").textContent = `${d.goal_ml} мл`;
  qs("#mStatus").textContent = (d.goal_ml>0 && d.total_ml>=d.goal_ml) ? "Норма ✅" : (d.total_ml>0 ? "Есть данные" : "Нет данных");
}
function closeModal(){ qs("#modal").style.display = "none"; }
function renderState(state) {
  STATE = state;
  const user = state.user;
  const prof = state.profile;
  const today = state.today;
  const st = state.stats;
  qs("#userLine").textContent = user.username ? `${user.first_name} • @${user.username}` : `${user.first_name}`;
  qs("#todayDate").textContent = new Date(today.date + "T12:00:00").toLocaleDateString("ru-RU", { day:"2-digit", month:"long" });
  qs("#pWeight").textContent = prof.weight_kg ? `${prof.weight_kg} кг` : "—";
  qs("#pFactor").textContent = `${prof.factor_ml} мл/кг`;
  qs("#pGoal").textContent = prof.goal_ml ? `${prof.goal_ml} мл/день` : "—";
  qs("#streakPill").style.display = "inline-flex";
  qs("#streakVal").textContent = String(st.current_streak || 0);
  qs("#bestPill").style.display = "inline-flex";
  qs("#bestVal").textContent = String(st.best_streak || 0);
  setRing(today.total_ml, today.goal_ml);
  renderEntries(today.entries);
  qs("#avg7").textContent = `${st.avg7} мл`;
  qs("#curStreak").textContent = `${st.current_streak}`;
  qs("#bestStreak").textContent = `${st.best_streak}`;
  renderChart7(st.last7);
  renderAchievements(state.achievements);
  renderCalendar(state.calendar);
}
async function refreshState() {
  const payload = { initData: tg?.initData || "", month: CURRENT_MONTH, client_date: localISODate() };
  const state = await api("/api/state", payload);
  renderState(state);
}
async function addWater(ml) {
  try {
    const res = await api("/api/add", { initData: tg?.initData || "", ml, client_date: localISODate(), client_ts: new Date().toISOString() });
    tg?.HapticFeedback?.impactOccurred("medium");
    if (res.goal_completed_today) {
      showGoalToast();
      burstConfetti(75);
      tg?.HapticFeedback?.notificationOccurred("success");
    }
    await refreshState();
  } catch (e) {
    console.error(e);
    tg?.HapticFeedback?.notificationOccurred("error");
  }
}
function setupEvents() {
  qs("#closeBtn").addEventListener("click", () => tg?.close());
  qsa(".tab").forEach(b => b.addEventListener("click", () => setTab(b.dataset.tab)));
  qsa(".chip").forEach(b => b.addEventListener("click", () => addWater(parseInt(b.dataset.add, 10))));
  qs("#addCustom").addEventListener("click", () => {
    const v = parseInt(qs("#customMl").value || "0", 10);
    if (v > 0) addWater(v);
    qs("#customMl").value = "";
  });
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
  qs("#mClose").addEventListener("click", closeModal);
  qs("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
  window.addEventListener("resize", () => resizeConfetti());
}
(async function boot(){
  try {
    setupEvents();
    CURRENT_MONTH = monthYM(new Date());
    await refreshState();
  } catch (e) {
    console.error(e);
    qs("#userLine").textContent = "Ошибка: " + (e?.message || "неизвестно");
  }
})();
