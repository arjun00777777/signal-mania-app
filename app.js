let tg, initData;
const API = window.location.origin;

function waitTG(){
  if(!window.Telegram?.WebApp){setTimeout(waitTG,50);return;}
  tg = Telegram.WebApp;
  initData = tg.initData;
  refresh();
}
waitTG();

function show(id){
  document.querySelectorAll(".page").forEach(p=>p.classList.remove("active"));
  document.getElementById(id).classList.add("active");
  if(id==="signals") loadSignals();
  if(id==="alerts") loadAlerts();
  if(id==="tasks") loadTasks();
  if(id==="profile") loadProfile();
}

async function refresh(){
  const r = await fetch(`${API}/user/mining/stats`,{
    method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({initData})
  });
  const d = await r.json();
  nodeStatus.innerText = d.running?"Running":"Idle";
}

async function startNode(){
  await fetch(`${API}/user/mining/start`,{
    method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({initData})
  });
  refresh();
}

async function dailyClaim(){
  await fetch(`${API}/claim/daily`,{
    method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({initData})
  });
  loadTasks();
}

async function loadTasks(){
  const r = await fetch(`${API}/tasks/history`,{
    method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({initData})
  });
  const d = await r.json();
  taskHistory.innerHTML = d.map(t=>`${t[0]} +${t[1]}`).join("<br>");
}

async function loadSignals(){
  const r = await fetch(`${API}/market/signals`);
  const d = await r.json();
  signalsList.innerHTML = d.map(s=>`${s.symbol} $${s.price}`).join("<br>");
}

async function createAlert(){
  await fetch(`${API}/alert/create`,{
    method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({
      initData,
      symbol:alertSymbol.value,
      target:alertTarget.value,
      condition:alertCond.value
    })
  });
  loadAlerts();
}

async function loadAlerts(){
  const r = await fetch(`${API}/alert/list`,{
    method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({initData})
  });
  const d = await r.json();
  alertList.innerHTML = d.map(a=>`${a[1]} ${a[3]} ${a[2]}`).join("<br>");
}

async function loadProfile(){
  const r = await fetch(`${API}/user/mining/stats`,{
    method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({initData})
  });
  const d = await r.json();
  p_sp.innerText=d.sp;
  p_level.innerText=d.level;
  refLink.value=`https://t.me/Whale_alert_info_bot?start=${tg.initDataUnsafe.user.id}`;
}

function copyRef(){navigator.clipboard.writeText(refLink.value);}
