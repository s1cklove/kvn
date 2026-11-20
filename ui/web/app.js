const $ = (id) => document.getElementById(id);

let activeTab = "status";
let cachedServers = [];
let lastStatus = null;
let toastTimer = null;

function toast(msg) {
  const el = $("toast");
  el.textContent = msg;
  el.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 2600);
}

function api() {
  return window.pywebview && window.pywebview.api;
}

function ago(ts) {
  if (!ts) return "never";
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 120) return `${s}s ago`;
  if (s < 7200) return `${Math.floor(s / 60)}m ago`;
  if (s < 172800) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function copyText(text) {
  if (navigator.clipboard) {
    navigator.clipboard.writeText(text).then(() => toast(`Copied ${text}`));
  }
}

async function refreshStatus() {
  if (!api()) return;
  const st = await api().get_status();
  lastStatus = st;
  $("hdr-ver").textContent = st.version ? `v${st.version}` : "";

  const dot = $("hdr-dot");
  const orb = $("status-orb");
  dot.className = "dot";
  orb.className = "orb";

  const powerBtn = $("btn-power");
  if (!st.running) {
    $("hdr-state").textContent = "Stopped";
    $("hero-title").textContent = "Proxy stopped";
    $("hero-sub").textContent = st.has_sub_url === false
      ? "Add your subscription URL in Settings to start"
      : "Press Start to launch sing-box";
    powerBtn.textContent = "Start";
    powerBtn.className = "btn primary";
    $("btn-reconnect").disabled = true;
  } else if (st.proxy_ok) {
    dot.classList.add("ok");
    orb.classList.add("ok");
    $("hdr-state").textContent = "Connected";
    $("hero-title").textContent = st.server || "Connected";
    const delayStr = st.delay_ms ? `${st.delay_ms} ms` : "";
    $("hero-sub").textContent = [delayStr, st.mode].filter(Boolean).join(" · ");
    powerBtn.textContent = "Stop";
    powerBtn.className = "btn danger";
    $("btn-reconnect").disabled = false;
  } else {
    dot.classList.add("warn");
    orb.classList.add("warn");
    $("hdr-state").textContent = "Reconnecting";
    $("hero-title").textContent = "Starting / Reconnecting…";
    $("hero-sub").textContent = st.server ? `Trying ${st.server}` : "Measuring servers…";
    powerBtn.textContent = "Stop";
    powerBtn.className = "btn danger";
    $("btn-reconnect").disabled = false;
  }

  // Route & Ports
  const ch = st.chain || {};
  if (!st.running) {
    $("route-box").textContent = "Stopped";
  } else if (ch.active) {
    const cPing = ch.latency_ms ? ` (${ch.latency_ms} ms)` : "";
    const prefix = ch.via === "direct" ? "" : `${st.server || "subscription"} → `;
    $("route-box").textContent = `${prefix}${ch.active}${cPing}`;
  } else {
    $("route-box").textContent = st.server ? `${st.server} (${st.mode})` : "Selecting fastest server…";
  }

  const ports = $("ports-row");
  ports.innerHTML = "";
  const mainAddr = `${st.listen}:${st.port}`;
  const addChip = (addr, label) => {
    const b = document.createElement("button");
    b.className = "port-chip";
    b.innerHTML = `<span>${addr}</span><span class="tag">${label}</span>`;
    b.onclick = () => copyText(addr);
    ports.appendChild(b);
  };
  addChip(mainAddr, ch.port ? "subscription" : (ch.active ? "chained" : "proxy"));
  if (ch.configured && ch.port) {
    const cState = ch.works === true ? `${ch.latency_ms}ms` : (ch.enabled ? "chain" : "off");
    addChip(`${st.listen}:${ch.port}`, cState);
  }

  // Toggles
  const m = st.master || {};
  $("toggle-master").checked = !!m.enabled;
  $("toggle-master").disabled = !m.configured;
  $("master-desc").textContent = m.configured
    ? `/${m.pattern}/ (≤${m.max_ratio}× or ≤${m.max_lag} ms)`
    : "Disabled in config";

  $("toggle-chain").checked = !!ch.enabled;
  $("toggle-chain").disabled = !ch.configured;
  $("chain-desc").textContent = ch.configured
    ? (ch.active ? `Active: ${ch.active} (via ${ch.via})` : `Configured (via ${ch.via})`)
    : "Add chain_url in Settings";

  // Subscription footer
  const sub = st.subscription || {};
  if (sub.fetched_at) {
    let txt = `${sub.count || "?"} servers · fetched ${ago(sub.fetched_at)}`;
    if (sub.expire) {
      const days = Math.floor((sub.expire - Date.now() / 1000) / 86400);
      txt += days < 0 ? " · EXPIRED" : ` · ${days}d left`;
    }
    $("sub-meta").textContent = txt;
  } else {
    $("sub-meta").textContent = "Not fetched yet";
  }
}

function renderServers() {
  const q = $("srv-search").value.trim().toLowerCase();
  const list = $("server-list");
  const filtered = cachedServers.filter((s) => !q || s.tag.toLowerCase().includes(q));
  if (!filtered.length) {
    list.innerHTML = `<div class="empty">${cachedServers.length ? "No match" : "Start kvn to see servers."}</div>`;
    return;
  }
  list.innerHTML = "";
  for (const s of filtered) {
    const row = document.createElement("div");
    row.className = "server-item" + (s.active ? " active" : "");
    const cls = !s.alive ? "dead" : (s.delay_ms < 180 ? "fast" : "mid");
    const label = s.alive ? `${s.delay_ms} ms` : "dead";
    row.innerHTML = `<span class="server-name">${s.active ? "★ " : ""}${s.tag}</span><span class="ping ${cls}">${label}</span>`;
    row.onclick = async () => {
      const r = await api().use_server(s.tag);
      toast(r.ok ? `Pinned: ${s.tag}` : (r.error || "Failed"));
      refreshServers();
      refreshStatus();
    };
    list.appendChild(row);
  }
}

async function refreshServers() {
  if (!api()) return;
  const data = await api().get_servers();
  cachedServers = data.servers || [];
  renderServers();
}

async function loadSettingsView() {
  if (!api()) return;
  const cfg = await api().get_config();
  const eff = cfg.effective || {};
  $("managed-banner").classList.toggle("hidden", !cfg.managed_externally);
  for (const el of $("settings-fields").querySelectorAll("input,select")) {
    el.disabled = !!cfg.managed_externally;
  }

  $("sub-badge").textContent = cfg.has_sub_url ? "saved" : "missing";
  $("sub-badge").className = "badge" + (cfg.has_sub_url ? " ok" : "");
  $("chain-badge").textContent = cfg.has_chain_url
    ? `${(cfg.chain_servers || []).length} server(s)`
    : "none";
  $("chain-badge").className = "badge" + (cfg.has_chain_url ? " ok" : "");

  $("in-port").value = eff.port || 10808;
  $("in-chain-port").value = eff.chain_port || "";
  $("in-chain-via").value = eff.chain_via || "chain";
  $("in-master").value = eff.master || "";
  $("in-exclude").value = eff.exclude || "";
  $("in-bypass-ru").checked = !!eff.bypass_russia;
  $("in-notify").checked = !!eff.notify_switch;

  const addr = `${eff.listen || "127.0.0.1"}:${eff.port || 10808}`;
  $("guide-port").textContent = addr;
  $("guide-port-2").textContent = addr;
}

async function refreshLogs() {
  if (!api()) return;
  const data = await api().get_logs(150);
  const lines = data.lines || [];
  const box = $("log-box");
  box.textContent = lines.length ? lines.join("\n") : "No logs yet.";
  box.scrollTop = box.scrollHeight;
}

function switchTab(name) {
  activeTab = name;
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${name}`));
  if (name === "status") refreshStatus();
  if (name === "servers") refreshServers();
  if (name === "settings") loadSettingsView();
  if (name === "logs") refreshLogs();
}

function wireEvents() {
  document.querySelectorAll(".tab").forEach((b) => {
    b.onclick = () => switchTab(b.dataset.tab);
  });

  $("btn-power").onclick = async () => {
    const btn = $("btn-power");
    btn.disabled = true;
    const res = lastStatus && lastStatus.running
      ? await api().stop_service()
      : await api().start_service();
    btn.disabled = false;
    toast(res.ok ? (res.warning || "Done") : (res.error || "Failed"));
    refreshStatus();
  };

  $("btn-reconnect").onclick = async () => {
    const btn = $("btn-reconnect");
    btn.disabled = true;
    toast("Testing all servers…");
    const res = await api().connect_now();
    btn.disabled = false;
    toast(res.ok ? `Connected: ${res.current} (${res.delay_ms} ms)` : (res.error || "No live servers"));
    refreshStatus();
  };

  $("btn-fetch").onclick = async () => {
    const btn = $("btn-fetch");
    btn.disabled = true;
    const res = await api().fetch_subscription();
    btn.disabled = false;
    toast(res.ok ? `Fetched ${res.count} servers` : (res.error || "Fetch failed"));
    refreshStatus();
  };

  $("toggle-master").onchange = async (e) => {
    const res = await api().set_master(e.target.checked);
    toast(res.ok ? `Master ${res.enabled ? "on" : "off"}` : (res.error || "Failed"));
    refreshStatus();
  };

  $("toggle-chain").onchange = async (e) => {
    const res = await api().set_chain(e.target.checked);
    toast(res.ok ? `Chain ${res.enabled ? "on" : "off"}` : (res.error || "Failed"));
    refreshStatus();
  };

  $("srv-search").oninput = renderServers;
  $("btn-use-auto").onclick = async () => {
    const res = await api().use_server("auto");
    toast(res.ok ? "Back to automatic selection" : (res.error || "Failed"));
    refreshServers();
    refreshStatus();
  };
  $("btn-srv-test").onclick = async () => {
    toast("Re-testing servers…");
    const res = await api().connect_now();
    toast(res.ok ? `${res.alive}/${res.total} alive` : (res.error || "Failed"));
    refreshServers();
  };

  $("btn-save-cfg").onclick = async () => {
    const cfg = await api().get_config();
    const payload = {};
    if (!cfg.managed_externally) {
      const ov = { ...(cfg.overrides || {}) };
      ov.port = parseInt($("in-port").value, 10) || 10808;
      const cp = $("in-chain-port").value.trim();
      ov.chain_port = cp ? parseInt(cp, 10) : null;
      ov.chain_via = $("in-chain-via").value;
      ov.master = $("in-master").value.trim();
      ov.exclude = $("in-exclude").value.trim();
      ov.bypass_russia = $("in-bypass-ru").checked;
      ov.notify_switch = $("in-notify").checked;
      payload.overrides = ov;
    }
    const sub = $("in-sub-url").value.trim();
    if (sub) payload.sub_url = sub;
    const chRaw = $("in-chain-url").value.trim();
    if (chRaw === "CLEAR") payload.chain_url = "";
    else if (chRaw) payload.chain_url = chRaw;

    const res = await api().save_config(payload);
    if (res.ok) {
      $("in-sub-url").value = "";
      $("in-chain-url").value = "";
      toast("Saved & applied");
      loadSettingsView();
      refreshStatus();
    } else {
      toast(res.error || "Failed to save");
    }
  };

  $("btn-install").onclick = async () => {
    const res = await api().install_autostart();
    toast(res.ok ? res.message : (res.error || "Failed"));
  };
  $("btn-uninstall").onclick = async () => {
    const res = await api().uninstall_autostart();
    toast(res.ok ? res.message : (res.error || "Failed"));
  };

  $("btn-logs-refresh").onclick = refreshLogs;
  $("btn-logs-copy").onclick = () => copyText($("log-box").textContent);
}

window.addEventListener("pywebviewready", () => {
  wireEvents();
  refreshStatus();
  setInterval(() => {
    if (activeTab === "status") refreshStatus();
    else if (activeTab === "logs") refreshLogs();
  }, 5000);
});
