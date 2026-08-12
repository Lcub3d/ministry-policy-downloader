const sourceNames = {
  ndrc: "国家发展改革委",
  mee: "生态环境部",
  mnr: "自然资源部",
  mof: "财政部",
};

const state = { jobId: null, timer: null, logLines: [] };
const form = document.querySelector("#job-form");
const scanButton = document.querySelector("#scan-button");
const stopButton = document.querySelector("#stop-button");
const startButton = document.querySelector("#start-button");
const formError = document.querySelector("#form-error");
const emptyState = document.querySelector("#empty-state");
const runContent = document.querySelector("#run-content");
const overallStatus = document.querySelector("#overall-status");
const resultRows = document.querySelector("#result-rows");
const log = document.querySelector("#log code");

function monthStart() {
  const now = new Date();
  const local = new Date(now.getFullYear(), now.getMonth(), 1);
  return `${local.getFullYear()}-${String(local.getMonth() + 1).padStart(2, "0")}-01`;
}

document.querySelector("#since").value = monthStart();

document.querySelectorAll('input[name="source"]').forEach((input) => {
  input.addEventListener("change", () => {
    input.closest(".source-row").querySelector(".source-state").textContent = input.checked ? "已启用" : "未启用";
  });
});

function payload(mode) {
  const data = new FormData(form);
  return {
    mode,
    sources: data.getAll("source"),
    since: data.get("since"),
    output: data.get("output").trim(),
    delay: Number(data.get("delay")),
  };
}

function validate(config) {
  if (!config.sources.length) return "请至少选择一个部委。";
  if (!/^\d{4}-\d{2}-\d{2}$/.test(config.since)) return "请选择有效的起始日期。";
  if (!config.output) return "请填写输出目录。";
  if (!Number.isFinite(config.delay) || config.delay < 0.2 || config.delay > 30) return "请求间隔应在 0.2 到 30 秒之间。";
  return "";
}

function showError(message) {
  formError.textContent = message;
  formError.hidden = !message;
}

function setRunning(running) {
  document.body.classList.toggle("is-running", running);
  startButton.disabled = running;
  scanButton.disabled = running;
  stopButton.disabled = !running;
  form.querySelectorAll("input").forEach((input) => { input.disabled = running; });
}

function setOverall(status, text) {
  overallStatus.className = `status ${status}`;
  overallStatus.innerHTML = `<span aria-hidden="true"></span>${text}`;
}

function startView(config, jobId) {
  emptyState.hidden = true;
  runContent.hidden = false;
  document.querySelector("#job-id").textContent = jobId || "准备中";
  document.querySelector("#job-range").textContent = `${config.sources.length} 个来源 · ${config.since} 起`;
  document.querySelector("#job-output").textContent = config.output;
  document.querySelector("#progress-bar").style.width = "2%";
  document.querySelector("#progress-copy").textContent = "正在建立安全写入目录…";
  resultRows.replaceChildren(...config.sources.map(makeResultRow));
  state.logLines = [];
  renderLog(["任务已提交，等待第一条记录…"]);
  setOverall("running", config.mode === "scan" ? "正在扫描" : "正在下载");
}

function makeResultRow(code) {
  const row = document.createElement("div");
  row.className = "result-row";
  row.dataset.source = code;
  row.innerHTML = `
    <span class="ministry"><i class="row-indicator" aria-hidden="true"></i>${sourceNames[code] || code}</span>
    <span data-key="discovered">0</span>
    <span data-key="saved">0</span>
    <span data-key="skipped">0</span>
    <span data-key="failed">0</span>
    <span class="row-status" data-key="status">等待</span>`;
  return row;
}

function updateResults(results = {}) {
  Object.entries(results).forEach(([source, values]) => {
    const row = resultRows.querySelector(`[data-source="${CSS.escape(source)}"]`);
    if (!row) return;
    ["discovered", "saved", "skipped", "failed"].forEach((key) => {
      row.querySelector(`[data-key="${key}"]`).textContent = values[key] ?? 0;
    });
    const status = values.status || "waiting";
    const labels = { waiting: "等待", running: "处理中", success: "完成", failed: "失败", cancelled: "已停止" };
    row.querySelector('[data-key="status"]').textContent = labels[status] || status;
    row.querySelector(".row-indicator").className = `row-indicator ${status}`;
  });
}

function renderLog(lines) {
  state.logLines = lines;
  log.textContent = lines.join("\n");
  log.parentElement.scrollTop = log.parentElement.scrollHeight;
}

async function submit(mode) {
  const config = payload(mode);
  const error = validate(config);
  showError(error);
  if (error) return;
  setRunning(true);
  startView(config, "准备中");
  try {
    const response = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || `服务器返回 ${response.status}`);
    state.jobId = body.job_id;
    document.querySelector("#job-id").textContent = body.job_id;
    schedulePoll(250);
  } catch (error) {
    finish("failed", `无法启动：${error.message}`);
  }
}

async function poll() {
  if (!state.jobId) return;
  try {
    const response = await fetch(`/api/jobs/${encodeURIComponent(state.jobId)}`);
    const job = await response.json();
    if (!response.ok) throw new Error(job.error || `服务器返回 ${response.status}`);
    document.querySelector("#progress-bar").style.width = `${Math.max(2, Math.min(100, job.progress || 0))}%`;
    document.querySelector("#progress-copy").textContent = job.message || "正在处理…";
    updateResults(job.results);
    renderLog(job.logs?.length ? job.logs : ["等待任务输出…"]);
    if (["success", "failed", "cancelled"].includes(job.status)) {
      finish(job.status, job.message);
    } else {
      schedulePoll(800);
    }
  } catch (error) {
    finish("failed", `状态读取失败：${error.message}`);
  }
}

function schedulePoll(delay) {
  clearTimeout(state.timer);
  state.timer = setTimeout(poll, delay);
}

function finish(status, message) {
  clearTimeout(state.timer);
  setRunning(false);
  const labels = { success: "任务完成", failed: "任务失败", cancelled: "已停止" };
  setOverall(status === "cancelled" ? "failed" : status, labels[status] || status);
  document.querySelector("#progress-copy").textContent = message || labels[status] || status;
  if (status === "success") document.querySelector("#progress-bar").style.width = "100%";
}

form.addEventListener("submit", (event) => { event.preventDefault(); submit("update"); });
scanButton.addEventListener("click", () => submit("scan"));
stopButton.addEventListener("click", async () => {
  if (!state.jobId) return;
  stopButton.disabled = true;
  try {
    await fetch(`/api/jobs/${encodeURIComponent(state.jobId)}/cancel`, { method: "POST" });
    document.querySelector("#progress-copy").textContent = "正在安全停止…";
    schedulePoll(250);
  } catch (error) {
    showError(`停止请求失败：${error.message}`);
    stopButton.disabled = false;
  }
});

document.querySelector("#copy-log").addEventListener("click", async (event) => {
  try {
    await navigator.clipboard.writeText(state.logLines.join("\n"));
    const button = event.currentTarget;
    button.textContent = "已复制";
    setTimeout(() => { button.textContent = "复制日志"; }, 1400);
  } catch (_) {
    showError("浏览器未允许复制，请在日志中全选复制。 ");
  }
});
