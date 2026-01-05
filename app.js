/* ===============================
   TELEGRAM SAFE INITIALIZATION
================================ */

let tg = null;
let initData = null;
let timerInt = null;

// Use same origin to avoid CORS / HTTPS issues
const API_BASE = window.location.origin;

function headers() {
  return { "Content-Type": "application/json" };
}

// Wait until Telegram injects WebApp object
function onTelegramReady() {
  if (!window.Telegram || !window.Telegram.WebApp) {
    setTimeout(onTelegramReady, 50);
    return;
  }

  tg = window.Telegram.WebApp;
  tg.expand();
  initData = tg.initData;

  console.log("Telegram WebApp ready");
  initApp();
}

onTelegramReady();

/* ===============================
   APP START
================================ */

function initApp() {
  refreshNode();
}

/* ===============================
   NAVIGATION
================================ */

function show(id) {
  document.querySelectorAll(".page").forEach(p =>
    p.classList.remove("active")
  );
  document.getElementById(id).classList.add("active");

  if (id === "signals") loadSignals();
  if (id === "alerts") loadAlerts();
  if (id === "profile") loadProfile();
}

/* ===============================
   MINING
================================ */

async function refreshNode() {
  if (!initData) return;

  const r = await fetch(`${API_BASE}/user/mining/stats`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ initData })
  });

  const d = await r.json();

  document.getElementById("nodeStatus").innerText =
    d.running ? "Node Running" : "Node Idle";

  if (d.running) {
    startBtn.disabled = true;
    startTimer(d.remaining);
  } else {
    startBtn.disabled = false;
    clearInterval(timerInt);
    timer.innerText = "";
  }
}

function startTimer(sec) {
  clearInterval(timerInt);
  timerInt = setInterval(() => {
    let h = Math.floor(sec / 3600);
    let m = Math.floor((sec % 3600) / 60);
    let s = sec % 60;
    timer.innerText = `${h}h ${m}m ${s}s`;
    sec--;
    if (sec < 0) {
      clearInterval(timerInt);
      refreshNode();
    }
  }, 1000);
}

async function startNode() {
  if (!initData) return;

  await fetch(`${API_BASE}/user/mining/start`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ initData })
  });

  refreshNode();
}

/* ===============================
   DAILY CLAIM
================================ */

async function dailyClaim() {
  if (!initData) return;

  const r = await fetch(`${API_BASE}/claim/daily`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ initData })
  });

  const d = await r.json();

  if (d.ok) {
    tg.HapticFeedback.notificationOccurred("success");
    dailyReward.innerText =
      `+${Math.min(500, 10 * Math.pow(2, d.streak - 1))} SP`;
  } else {
    alert("Already claimed today");
  }
}

/* ===============================
   SIGNALS (INFO ONLY)
================================ */

async function loadSignals() {
  const r = await fetch(`${API_BASE}/market/signals`);
  const d = await r.json();

  signalsList.innerHTML = d.map(s => `
    <div class="card">
      <b>${s.symbol}</b> (#${s.market_cap_rank})<br>
      Price: $${s.price}<br>
      24h Change: ${s.change_24h}%<br>
      Volume: $${(s.volume / 1e9).toFixed(2)}B
    </div>
  `).join("");
}

/* ===============================
   ALERTS
================================ */

async function createAlert() {
  if (!initData) return;

  const r = await fetch(`${API_BASE}/alert/create`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({
      initData,
      symbol: alertSymbol.value,
      target: alertTarget.value,
      condition: alertCond.value
    })
  });

  if (r.status === 403) {
    alert("Premium required for more alerts");
    return;
  }

  loadAlerts();
}

async function loadAlerts() {
  if (!initData) return;

  const r = await fetch(`${API_BASE}/alert/list`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ initData })
  });

  const d = await r.json();

  alertList.innerHTML = d.length
    ? d.map(a => `
        <div class="card">
          ${a[1]} ${a[3]} ${a[2]} (${a[4]})
          <button onclick="deleteAlert(${a[0]})">❌</button>
        </div>
      `).join("")
    : "No alerts";
}

async function deleteAlert(id) {
  if (!initData) return;

  await fetch(`${API_BASE}/alert/delete`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ initData, id })
  });

  loadAlerts();
}

/* ===============================
   PROFILE
================================ */

async function loadProfile() {
  if (!initData) return;

  const r = await fetch(`${API_BASE}/user/mining/stats`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ initData })
  });

  const d = await r.json();

  p_sp.innerText = d.sp;
  p_level.innerText = d.level;

  if (tg.initDataUnsafe && tg.initDataUnsafe.user) {
   refLink.value =
  `https://t.me/Whale_alert_info_bot?start=${tg.initDataUnsafe.user.id}`;

  }
}

function copyRef() {
  navigator.clipboard.writeText(refLink.value);
  tg.HapticFeedback.notificationOccurred("success");
}
