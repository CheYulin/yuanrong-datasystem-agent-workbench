(function () {
  "use strict";
  const data = window.KVC_DATA;
  const products = ["datasystem", "mooncake", "lmcache"];
  const labels = {datasystem: "DataSystem", mooncake: "Mooncake", lmcache: "LMCache"};
  const colors = ["#2878b5", "#e18a25", "#3b9c83"];
  const batches = [1, 16, 256, 4096];
  const charts = new Map();
  const byId = id => document.getElementById(id);
  const finite = value => typeof value === "number" && Number.isFinite(value);
  const fmt = value => finite(value) ? new Intl.NumberFormat("zh-CN", {maximumFractionDigits: 3}).format(value) : "未观测";
  const escape = value => String(value === undefined || value === null ? "未提供" : value).replace(/[&<>"']/g, s => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[s]));
  function chart(id, option) {
    let instance = charts.get(id);
    if (!instance) {
      instance = echarts.init(byId(id), null, {renderer: "canvas"});
      charts.set(id, instance);
    }
    instance.setOption(Object.assign({animation: false, color: colors, textStyle: {fontFamily: "system-ui"}, tooltip: {trigger: "axis", confine: true}, legend: {top: 4}, grid: {top: 55, left: 22, right: 20, bottom: 34, containLabel: true}}, option), true);
  }
  function aggregate(product, batch, operation) {
    return (data.aggregates || []).find(row => row.product === product && row.batch === batch && row.operation === operation);
  }
  function metric(product, batch, operation, key) {
    const row = aggregate(product, batch, operation);
    const rows = (data.rows || []).filter(item => item.product === product && item.batch === batch && item.operation === operation);
    const complete = rows.length === data.repeats && Array.from({length: data.repeats}, (_, repeat) => rows.filter(item => item.repeat === repeat && item.status === "valid").length === 1).every(Boolean);
    const value = complete && row && row.status === "complete" && row.metrics[key] ? row.metrics[key].median : null;
    return finite(value) ? value : null;
  }
  function bars(id, metricKey, unit, operation) {
    chart(id, {xAxis: {type: "category", data: batches.map(String), name: "每批 keys", nameLocation: "middle", nameGap: 25}, yAxis: {type: "value", name: unit, axisLabel: {formatter: value => value >= 1e6 ? (value / 1e6).toFixed(1) + "M" : value >= 1e3 ? (value / 1e3).toFixed(1) + "K" : value}},
      series: products.map(product => ({name: labels[product], type: "bar", barMaxWidth: 45, data: batches.map(batch => metric(product, batch, operation, metricKey))}))});
  }
  function renderCoverage(operation) {
    const selected = (data.rows || []).filter(row => row.operation === operation);
    const expected = products.length * batches.length * data.repeats;
    const count = status => selected.filter(row => row.status === status).length;
    const operationName = byId("operation").selectedOptions[0].textContent;
    byId("operationCoverage").textContent = operationName + "：预期 " + expected + " 条，实际 " + selected.length + " 条；有效 " + count("valid") + "，失败 " + count("failed") + "，不支持 " + count("not_supported") + "。下表逐组合说明图中空位，可展开每轮查看部分成功请求与错误。";
    byId("resultRows").innerHTML = products.flatMap(product => batches.map(batch => {
      const rows = selected.filter(row => row.product === product && row.batch === batch);
      const repeats = Array.from({length: data.repeats}, (_, repeat) => rows.filter(row => row.repeat === repeat));
      const complete = rows.length === data.repeats && repeats.every(items => items.length === 1);
      const valid = rows.filter(row => row.status === "valid").length;
      const failed = rows.filter(row => row.status === "failed").length;
      const unsupported = rows.filter(row => row.status === "not_supported").length;
      const accepted = complete && valid === data.repeats && aggregate(product, batch, operation)?.status === "complete";
      const status = !complete ? "缺记录 / 重复记录" : failed ? "原生失败（" + failed + "/" + data.repeats + "）" : unsupported === data.repeats ? "不支持" : accepted ? "有效" : "未形成有效汇总";
      const missing = !complete ? "无完整结果" : failed ? "失败，不计入有效性能" : unsupported ? "不支持" : "无有效汇总";
      const metrics = ["keys_s", "batch_qps", "p99_us"].map(key => "<td>" + (accepted ? fmt(metric(product, batch, operation, key)) : escape(missing)) + "</td>").join("");
      const evidence = repeats.map((items, repeat) => {
        if (items.length !== 1) return "<p>R" + (repeat + 1) + "：" + (items.length ? "重复记录" : "缺记录") + "</p>";
        const row = items[0];
        const href = typeof row.evidence === "string" && /^results\/[^?#]*\.json$/.test(row.evidence) ? row.evidence : "";
        const state = ({valid: "有效", failed: "失败", not_supported: "不支持"})[row.status] || row.status;
        const reason = row.status === "failed" ? JSON.stringify(row.native_errors || row.issues || []) : row.reason || "";
        return '<details><summary>R' + (repeat + 1) + "：" + escape(state) + '</summary><p>' + (href ? '<a href="' + escape(href) + '">原始 JSON</a>' : "原件未提供") + "</p><p>成功调用：" + escape(row.success) + "；失败调用：" + escape(row.failures) + "</p>" + (reason ? "<p>" + escape(reason) + "</p>" : "") + "</details>";
      }).join("");
      return '<tr data-product="' + product + '" data-batch="' + batch + '"><td>' + escape(labels[product]) + "</td><td>" + batch + '</td><td class="' + (accepted ? "" : "failure") + '">' + escape(status) + "</td><td>" + valid + "/" + data.repeats + "</td>" + metrics + "<td>" + evidence + "</td></tr>";
    })).join("");
  }
  function update() {
    const operation = byId("operation").value;
    const batch = Number(byId("batch").value);
    renderCoverage(operation);
    bars("keys", "keys_s", "keys/s", operation);
    bars("qps", "batch_qps", "batch QPS", operation);
    bars("p99", "p99_us", "整批 P99 µs", operation);
    bars("cpu", "total_cpu_cores", "CPU 核数", operation);
    bars("memory", "peak_charged_memory_gib", "GiB", operation);
    chart("disk", {xAxis: {type: "category", data: products.map(p => labels[p])}, yAxis: {type: "value", name: "MiB/s"},
      series: ["read", "write"].map((direction, index) => ({name: direction === "read" ? "磁盘读" : "磁盘写", type: "bar", data: products.map(product => metric(product, batch, operation, "total_" + direction + "_mib_s"))}))});
    chart("scatter", {tooltip: {trigger: "item", confine: true, formatter: item => escape(item.seriesName) + "<br>B=" + item.value[2] + "<br>" + fmt(item.value[0]) + " keys/s<br>P99 " + fmt(item.value[1]) + " µs"},
      xAxis: {type: "value", name: "keys/s", axisLabel: {formatter: value => value >= 1e6 ? (value / 1e6).toFixed(1) + "M" : value}}, yAxis: {type: "value", name: "整批 P99 µs"},
      series: products.map(product => ({name: labels[product], type: "scatter", symbolSize: 12,
        data: batches.map(size => [metric(product, size, operation, "keys_s"), metric(product, size, operation, "p99_us"), size]).filter(row => finite(row[0]) && finite(row[1]))}))});
  }
  const count = status => finite(data.counts && data.counts[status]) ? data.counts[status] : Array.isArray(data.rows) ? data.rows.filter(row => row.status === status).length : null;
  byId("summary").innerHTML = [["有效", count("valid")], ["原生失败", count("failed")], ["N/A", count("not_supported")], ["重复", data.repeats]].map(row => '<div class="card"><span>' + escape(row[0]) + "</span><b>" + escape(row[1]) + "</b></div>").join("");
  byId("failureRows").innerHTML = (data.rows || []).filter(row => row.status !== "valid").map(row => {
    const href = typeof row.evidence === "string" && /^results\/[^?#]*\.json$/.test(row.evidence) ? row.evidence : "";
    const reason = row.status === "failed" ? JSON.stringify(row.native_errors || []) : row.reason || row.issues || "无等价原生接口";
    return "<tr><td>" + escape(row.identity) + '</td><td class="' + (row.status === "failed" ? "failure" : "na") + '">' + escape(row.status) + "</td><td>" + escape(reason) + "</td><td>" + (href ? '<a href="' + escape(href) + '">JSON</a>' : "未提供") + "</td></tr>";
  }).join("") || '<tr><td colspan="4">未记录原生失败或 N/A。</td></tr>';
  byId("buildRows").innerHTML = (data.builds || []).map(row => "<tr><td>" + escape(labels[row.product]) + "</td><td>" + escape(row.tag) + "<br><code>" + escape(row.sha) + "</code></td><td>exit=" + escape(row.build_exit) + "<br>" + escape(row.compiler) + "<br>" + escape(row.build_recipe) + "</td><td>" + fmt(row.build_wall_seconds) + " s</td></tr>").join("");
  const configuration = Array.isArray(data.audit.configuration) ? data.audit.configuration : [];
  byId("configuration").innerHTML = '<table><tbody>' + configuration.map(row => "<tr><th>" + escape(row.name) + "</th><td>" + escape(typeof row.value === "object" ? JSON.stringify(row.value) : row.value) + "</td></tr>").join("") + "</tbody></table>";
  byId("documents").innerHTML = (data.documents || []).map(doc => '<a href="' + escape(doc.href) + '">' + escape(doc.title) + "</a>").join("");
  const steps = data.workflow && data.workflow.steps || [];
  chart("steps", {grid: {left: 90, right: 20, top: 20, bottom: 40, containLabel: true}, xAxis: {type: "value", name: "秒"}, yAxis: {type: "category", inverse: true, data: steps.map(row => row.id || row.step_id)}, series: [{type: "bar", name: "步骤耗时", data: steps.map(row => finite(row.duration_s) ? row.duration_s : null)}]});
  byId("stepRows").innerHTML = steps.map(row => '<div class="step"><b>' + escape(row.id || row.step_id) + "</b><span>" + escape(row.title) + "</span><span>执行 " + escape(row.execution) + "</span><span>验收 " + escape(row.acceptance) + "</span><span>" + fmt(row.duration_s) + " s</span>" + Object.entries(row.evidence || {}).map(([name, href]) => '<a href="' + escape(href) + '">' + escape(({"inputs.json":"输入", "result.json":"中间结果", "artifacts.json":"产物清单", "acceptance.json":"逐项验收", "summary.md":"摘要", "commit.json":"完整性回执"})[name] || name) + "</a>").join(" · ") + "</div>").join("");
  const cpuServer = [], cpuClient = [], memory = [];
  let previous = null;
  for (const sample of data.samples || []) {
    if (previous && sample.prev_mono_ns !== previous.mono_ns) {
      for (const series of [cpuServer, cpuClient, memory]) series.push([sample.wall_ms - 1, null]);
    }
    const server = sample.server || {}, client = sample.client || {};
    cpuServer.push([sample.wall_ms, finite(server.cpu_usec_per_s) ? server.cpu_usec_per_s / 1e6 : null]);
    cpuClient.push([sample.wall_ms, finite(client.cpu_usec_per_s) ? client.cpu_usec_per_s / 1e6 : null]);
    memory.push([sample.wall_ms, finite(server.memory) && finite(client.memory) ? (server.memory + client.memory) / 2 ** 30 : null]);
    previous = sample;
  }
  chart("resourceTimeline", {xAxis: {type: "time"}, yAxis: [{type: "value", name: "CPU 核"}, {type: "value", name: "GiB"}], dataZoom: [{type: "inside"}, {type: "slider", bottom: 0}], grid: {top: 55, left: 20, right: 20, bottom: 65, containLabel: true},
    series: [{name: "服务端 CPU", data: cpuServer, type: "line", showSymbol: false, connectNulls: false}, {name: "客户端 CPU", data: cpuClient, type: "line", showSymbol: false, connectNulls: false}, {name: "总 charged memory", data: memory, type: "line", yAxisIndex: 1, showSymbol: false, connectNulls: false}]});
  byId("operation").addEventListener("change", update);
  byId("batch").addEventListener("change", update);
  byId("menu").addEventListener("click", () => {
    const open = byId("navigation").classList.toggle("open");
    byId("menu").setAttribute("aria-expanded", String(open));
  });
  document.querySelectorAll("#navigation a").forEach(anchor => anchor.addEventListener("click", () => {
    byId("navigation").classList.remove("open");
    byId("menu").setAttribute("aria-expanded", "false");
  }));
  const observer = new IntersectionObserver(entries => entries.forEach(entry => {
    if (entry.isIntersecting) document.querySelectorAll("#navigation a").forEach(anchor => anchor.classList.toggle("active", anchor.hash === "#" + entry.target.id));
  }), {rootMargin: "-15% 0px -65% 0px"});
  document.querySelectorAll("main section").forEach(section => observer.observe(section));
  window.addEventListener("resize", () => charts.forEach(instance => instance.resize()));
  update();
  window.KVC_REPORT_READY = true;
})();
