import { FrameBridge } from "./frame-bridge.mjs";
import {
  STAGES,
  SEVERITIES,
  list,
  statusOf,
  labelOf,
  percentOf,
  countOf,
  latestRun,
  secondsBetween,
  durationLabel,
  dateLabel,
  promptFor,
  rowsFromCounts,
  totalCounts,
  matchesTask,
  stageRows,
  documentPaging,
  completedStageDurations,
  isRunning,
  pollDelay,
  detailAction,
  runStatisticsKey,
} from "./view-model.mjs";

const $ = (id) => document.getElementById(id);
const element = (tag, className = "", text = null) => {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text !== null) result.textContent = String(text);
  return result;
};
const append = (parent, ...children) => {
  parent.append(...children.filter(Boolean));
  return parent;
};
const empty = (text) => element("p", "empty", text);
const pill = (status) =>
  element("span", `pill ${statusOf(status)}`, labelOf(status));
const button = (text, action, secondary = true) => {
  const result = element(
    "button",
    `button${secondary ? " secondary" : ""}`,
    text,
  );
  result.type = "button";
  result.addEventListener("click", action);
  return result;
};
const state = {
  tasks: [],
  statistics: null,
  // run id -> { key, stats }; the key changes whenever the run's numbers can.
  runStatistics: new Map(),
  runStatisticsLoading: new Set(),
  selected: null,
  detail: null,
  snapshot: null,
  selection: 0,
  refresh: null,
  documentRequest: 0,
  offset: 0,
  documents: [],
  timer: null,
};
const welcome = $("workspace").firstElementChild.cloneNode(true);
// Fred serves this frame through its own gateway, so the host's origin is the
// frame's; the referrer names whichever page embeds it and is never trusted.
const bridge = new FrameBridge({
  origin: location.origin,
  onContext,
});

function showMessage(id, text) {
  $(id).textContent = text;
  $(id).hidden = !text;
}
function clearVisibleData() {
  state.tasks = [];
  state.statistics = null;
  state.detail = null;
  state.snapshot = null;
  $("task-list").replaceChildren(
    empty("No review data is currently available."),
  );
  $("task-count").textContent = "—";
  $("workspace").replaceChildren(welcome.cloneNode(true));
  renderMetrics(null);
}
function clearSelection() {
  state.selected = null;
  state.selection += 1;
  state.detail = null;
  state.snapshot = null;
  $("workspace").replaceChildren(welcome.cloneNode(true));
}
function onContext(context, changed) {
  // The host owns the palette, and republishes its context when the user
  // changes it, so a theme switch in Fred reaches this frame live.
  window.fredTheme(context?.theme);
  if (changed) {
    state.selection += 1;
    state.documentRequest += 1;
    state.selected = null;
    state.refresh = null;
    clearTimeout(state.timer);
    clearVisibleData();
    $("create-dialog").close();
    $("create-form").reset();
    showMessage("error", "");
    showMessage("notice", "");
    $("updated").textContent = "Not synced yet";
  }
  const enabled = Boolean(bridge.teamId);
  $("refresh").disabled = !enabled;
  $("new-task").disabled = !enabled;
  $("team-name").textContent =
    context?.team?.name ?? "No collaborative team selected";
  $("connection").textContent = enabled
    ? "Connected to Fred"
    : "Choose a collaborative team";
  $("connection").classList.toggle("connected", enabled);
  if (enabled) refresh();
  else
    showMessage(
      "notice",
      "Document review is available in collaborative teams. Select a team in Fred.",
    );
}
function reportError(error) {
  if (error.status === 401 || error.status === 403) {
    state.selected = null;
    state.selection += 1;
    clearVisibleData();
    showMessage(
      "error",
      error.status === 401
        ? "Your session needs attention. Sign in again in Fred, then refresh this application."
        : "Access is no longer available. Check your team, application grant and source-document permissions.",
    );
  } else showMessage("error", error.message || "Could not load review data.");
  $("connection").textContent = "Refresh needed";
  $("connection").classList.remove("connected");
}

function renderMetrics(stats) {
  const values = stats
    ? [
        stats.scope?.tasks_included,
        countOf(stats.task_status_counts?.running),
        countOf(stats.task_status_counts?.completed),
        totalCounts(stats.severity_counts),
      ]
    : [null, null, null, null];
  const definitions = [
    ["Reviews", "▤", "Accessible tasks in this view"],
    ["In progress", "↗", "Persisted running status"],
    ["Completed", "✓", "Coordinator finished the review"],
    ["Findings", "⌕", "Saved by the risk reviewer"],
  ];
  $("metrics").replaceChildren(
    ...definitions.map(([label, symbol, note], index) =>
      append(
        element("article", "metric"),
        append(
          element("div", "metric-top"),
          element("span", "", label),
          element("span", "metric-symbol", symbol),
        ),
        element("strong", "metric-value", values[index] ?? "—"),
        element("span", "metric-note", note),
      ),
    ),
  );
  $("scope").textContent = stats
    ? `${stats.scope?.label ?? "Accessible tasks"} · ${stats.scope?.tasks_included ?? 0} tasks${stats.scope?.truncated ? ` · Limited to ${stats.scope.limit}; not all-time totals` : ""}`
    : "Statistics are loaded from the application backend.";
}

function meter(value, name, indeterminate = false) {
  const percent = percentOf(value);
  const track = element("div", `meter${indeterminate ? " indeterminate" : ""}`);
  track.setAttribute("role", "progressbar");
  track.setAttribute("aria-label", name);
  track.setAttribute("aria-valuemin", "0");
  track.setAttribute("aria-valuemax", "100");
  if (!indeterminate && percent !== null)
    track.setAttribute("aria-valuenow", String(percent));
  else
    track.setAttribute(
      "aria-valuetext",
      indeterminate
        ? "Running; progress is indeterminate"
        : "Progress not recorded",
    );
  const fill = element("span");
  if (!indeterminate) fill.style.width = `${percent ?? 0}%`;
  return append(track, fill);
}

function renderTasks() {
  const visible = state.tasks.filter((task) =>
    matchesTask(task, $("task-search").value),
  );
  $("task-count").textContent = String(state.tasks.length);
  $("task-list").replaceChildren(
    ...(visible.length
      ? visible.map((task) => {
          const item = element(
            "button",
            `task${task.task_id === state.selected ? " selected" : ""}`,
          );
          item.type = "button";
          item.setAttribute(
            "aria-pressed",
            String(task.task_id === state.selected),
          );
          append(
            item,
            pill(task.status ?? "queued"),
            element("span", "task-title", task.title),
            element(
              "span",
              "task-doc",
              task.document
                ? `▤ ${task.document.document_name}`
                : "Legacy reporter task",
            ),
            append(
              element("span", "task-meta"),
              element("span", "", dateLabel(task.created_at)),
              element(
                "span",
                "",
                percentOf(task.progress_percent) === null
                  ? ""
                  : `${task.progress_percent}%`,
              ),
            ),
          );
          item.addEventListener("click", () => selectTask(task.task_id));
          return item;
        })
      : [
          empty(
            state.tasks.length
              ? "No reviews match your search."
              : "No reviews yet. Choose New review to select a corpus document.",
          ),
        ]),
  );
}

async function selectTask(taskId) {
  state.selected = taskId;
  state.selection += 1;
  state.snapshot = null;
  state.detail = null;
  renderTasks();
  $("workspace").replaceChildren(
    append(element("section", "panel review-header"), empty("Loading review…")),
  );
  await loadDetail();
  // Selecting live work should not wait out the idle interval.
  if (isRunning(state.detail) && !state.refresh) scheduleRefresh();
}

async function refresh(force = false) {
  if (!bridge.teamId || state.refresh) return;
  const token = {};
  state.refresh = token;
  const generation = bridge.generation,
    selection = state.selection;
  $("refresh").disabled = true;
  try {
    const listing = await bridge.request("tasks");
    if (generation !== bridge.generation) return;
    state.tasks = list(listing?.items);
    state.statistics = listing?.statistics ?? null;
    // A selection made while the list was loading may not be in it yet.
    const action =
      selection === state.selection
        ? detailAction(state.tasks, state.selected, state.detail)
        : "none";
    if (action === "clear") clearSelection();
    renderMetrics(state.statistics);
    renderTasks();
    if (action === "reload" || (force && action === "keep")) {
      if (!(await loadDetail())) return;
    } else {
      if (state.detail) showDetail(state.detail);
      showMessage("error", "");
    }
    if (generation !== bridge.generation) return;
    $("updated").textContent =
      `Synced ${dateLabel(new Date().toISOString(), true)}`;
    $("connection").textContent = "Connected to Fred";
    $("connection").classList.add("connected");
  } catch (error) {
    if (generation === bridge.generation) reportError(error);
  } finally {
    if (state.refresh === token) {
      state.refresh = null;
      $("refresh").disabled = !bridge.teamId;
      scheduleRefresh();
    }
  }
}
function scheduleRefresh() {
  clearTimeout(state.timer);
  state.timer = setTimeout(
    () => {
      if (!document.hidden) refresh();
      else scheduleRefresh();
    },
    pollDelay(state.tasks, state.detail),
  );
}
function showDetail(task) {
  const snapshot = JSON.stringify([task, state.statistics]);
  if (snapshot === state.snapshot) return;
  state.snapshot = snapshot;
  renderDetail(task);
}
async function loadDetail() {
  const selected = state.selected,
    selection = state.selection,
    generation = bridge.generation;
  if (!selected) return;
  try {
    const task = await bridge.request(`tasks/${encodeURIComponent(selected)}`);
    if (selection !== state.selection || generation !== bridge.generation)
      return false;
    state.detail = task;
    showDetail(task);
    showMessage("error", "");
    return true;
  } catch (error) {
    if (selection !== state.selection || generation !== bridge.generation)
      return;
    // A missing/revoked source must not leave its previously loaded results on screen.
    state.detail = null;
    state.snapshot = null;
    $("workspace").replaceChildren(
      append(
        element("section", "panel review-header"),
        empty(
          "This review is unavailable. Its source may have moved or your access may have changed.",
        ),
      ),
    );
    reportError(error);
    return false;
  }
}

function section(title, subtitle, className = "chart-panel") {
  const panel = element("section", `panel ${className}`);
  append(panel, element("h2", "", title));
  if (subtitle) append(panel, element("p", "chart-subtitle", subtitle));
  return panel;
}
function barChart(title, subtitle, rows, formatter = String) {
  const panel = section(title, subtitle),
    max = Math.max(0, ...rows.map((row) => row.value));
  if (!rows.length || max === 0)
    return append(panel, empty("No recorded data yet."));
  for (const row of rows) {
    const fill = element(
      "div",
      `bar-fill ${SEVERITIES.includes(row.key) ? row.key : ""}`,
    );
    fill.style.width = `${(row.value / max) * 100}%`;
    append(
      panel,
      append(
        element("div", "bar-row"),
        element("span", "bar-label", row.label),
        append(element("div", "bar-track"), fill),
        element("span", "bar-value", formatter(row.value)),
      ),
    );
  }
  return panel;
}

function runStatsPanel(stats) {
  const scope = stats?.scope ?? {};
  const panel = element("div", "run-statistics");
  append(
    panel,
    element(
      "p",
      "muted",
      `${countOf(scope.completed_stages)} of ${countOf(scope.expected_stages)} stages saved` +
        (stats?.outcome ? ` · outcome ${labelOf(stats.outcome)}` : ""),
    ),
    severityChart(stats, "Findings saved by this run's specialists"),
  );
  const priorities = rowsFromCounts(stats?.action_priority_counts);
  if (priorities.length)
    append(
      panel,
      append(
        section("Actions by priority", "Proposed by this run's specialists"),
        ...priorities.map(([priority, count]) =>
          append(
            element("div", "legend-row"),
            element("span", "legend-key", priority),
            element("strong", "", countOf(count)),
          ),
        ),
      ),
    );
  return panel;
}

async function loadRunStatistics(taskId, run, redraw) {
  const key = runStatisticsKey(run);
  if (
    state.runStatistics.get(run.run_id)?.key === key ||
    state.runStatisticsLoading.has(key)
  )
    return;
  state.runStatisticsLoading.add(key);
  try {
    const stats = await bridge.request(
      `tasks/${encodeURIComponent(taskId)}/review/${encodeURIComponent(run.run_id)}/statistics`,
    );
    state.runStatistics.set(run.run_id, { key, stats });
    redraw();
  } catch {
    // A run whose numbers cannot be read still shows its saved decisions.
  } finally {
    state.runStatisticsLoading.delete(key);
  }
}

function severityChart(stats, subtitle = "Risk-review findings · latest accessible task set") {
  const panel = section("Findings by severity", subtitle),
    total = totalCounts(stats?.severity_counts);
  // Fred design tokens, not literals: styles.css derives one --severity-* per
  // level from the semantic risk colours, so the donut and its legend follow
  // the active palette instead of staying light-theme red-to-teal.
  const colors = SEVERITIES.map((severity) => `var(--severity-${severity})`);
  const donut = element("div", "donut");
  donut.setAttribute("aria-hidden", "true");
  let cumulative = 0;
  if (total)
    donut.style.background = `conic-gradient(${SEVERITIES.map(
      (severity, index) => {
        const start = cumulative;
        cumulative +=
          (countOf(stats?.severity_counts?.[severity]) / total) * 100;
        return `${colors[index]} ${start}% ${cumulative}%`;
      },
    ).join(",")})`;
  append(
    donut,
    append(
      element("div", "donut-center"),
      element("span", "donut-number", total),
      element("span", "donut-label", "findings"),
    ),
  );
  const legend = element("div", "chart-legend");
  SEVERITIES.forEach((severity, index) => {
    const dot = element("span", "legend-dot");
    dot.style.background = colors[index];
    dot.setAttribute("aria-hidden", "true");
    append(
      legend,
      append(
        element("div", "legend-row"),
        append(
          element("span", "legend-key"),
          dot,
          element("span", "", severity),
        ),
        element("strong", "", countOf(stats?.severity_counts?.[severity])),
      ),
    );
  });
  return append(panel, append(element("div", "donut-layout"), donut, legend));
}

function evidenceBlock(evidence) {
  return append(
    element("blockquote", "evidence"),
    element("cite", "", evidence.location || "Location not recorded"),
    element("p", "", evidence.excerpt),
  );
}
function resultContent(result) {
  const content = element("div");
  append(
    content,
    element("p", "decision", result.decision),
    element("p", "rationale", result.rationale),
  );
  if (result.outcome)
    append(
      content,
      element(
        "span",
        `pill ${["accepted", "needs_attention", "blocked"].includes(result.outcome) ? result.outcome : "unknown"}`,
        labelOf(result.outcome),
      ),
    );
  list(result.evidence).forEach((item) => append(content, evidenceBlock(item)));
  if (list(result.findings).length) {
    const findings = append(
      element("div", "result-section"),
      element("h4", "", "Findings"),
    );
    for (const finding of result.findings) {
      const severity = SEVERITIES.includes(finding.severity)
        ? finding.severity
        : "info";
      append(
        findings,
        append(
          element("div", "finding"),
          append(
            element("div", "finding-title"),
            element("span", `pill ${severity}`, severity),
            element("span", "", finding.title),
          ),
          element("p", "", finding.detail),
          ...list(finding.evidence).map(evidenceBlock),
        ),
      );
    }
    append(content, findings);
  }
  if (list(result.actions).length) {
    const actions = append(
      element("div", "result-section"),
      element("h4", "", "Recommended actions"),
    );
    for (const action of result.actions)
      append(
        actions,
        append(
          element("div", "action"),
          append(
            element("div", "finding-title"),
            element("span", "pill", `${labelOf(action.priority)} priority`),
            element("span", "", action.title),
          ),
          element("p", "", action.detail),
        ),
      );
    append(content, actions);
  }
  if (list(result.limitations).length)
    append(
      content,
      append(
        element("div", "result-section"),
        element("h4", "", "Limitations"),
        append(
          element("ul", "limitations"),
          ...result.limitations.map((item) => element("li", "", item)),
        ),
      ),
    );
  return content;
}

function specialistCard(stage) {
  const card = element("article", "panel specialist");
  append(
    card,
    append(
      element("div", "specialist-top"),
      element("span", "agent-icon", stage.symbol),
      append(
        element("div", "specialist-name"),
        element("h3", "", stage.name),
        element("p", "", stage.specialty),
      ),
      pill(stage.status),
    ),
  );
  if (stage.status === "running")
    append(
      card,
      meter(null, `${stage.name} working`, true),
      element(
        "p",
        "small muted",
        "Working on the document. No estimated percentage is assigned to a model call.",
      ),
    );
  if (stage.result) append(card, resultContent(stage.result));
  else if (stage.error)
    append(card, element("p", "error-text small", stage.error));
  else if (stage.status !== "running")
    append(
      card,
      element(
        "p",
        "small muted",
        stage.status === "queued"
          ? "Waiting for the preceding step. No decision recorded yet."
          : "No decision recorded for this step.",
      ),
    );
  append(
    card,
    append(
      element("div", "stage-meta"),
      element("span", "", `Started ${dateLabel(stage.started_at)}`),
      element(
        "span",
        "",
        stage.finished_at
          ? `Duration ${durationLabel(secondsBetween(stage.started_at, stage.finished_at))}`
          : "Not finished",
      ),
    ),
  );
  return card;
}

function renderDetail(task) {
  const openDetails = new Set(
    [...$("workspace").querySelectorAll("details[open]")].map(
      (item) => item.dataset.key,
    ),
  );
  const focusedAction = document.activeElement?.dataset.action;
  // Statistics can arrive after the reader moved on; only the shown task redraws.
  const redraw = () => {
    if (state.detail?.task_id === task.task_id) renderDetail(state.detail);
  };
  const run = latestRun(task),
    stages = stageRows(task),
    root = document.createDocumentFragment();
  const header = element("section", "panel review-header");
  append(
    header,
    append(
      element("div", "panel-heading"),
      element(
        "span",
        "eyebrow",
        task.document ? "CORPUS DOCUMENT REVIEW" : "LEGACY PROGRESS TRACKER",
      ),
      pill(task.status ?? "queued"),
    ),
    element("h2", "review-title", task.title),
    element(
      "p",
      "document-line",
      task.document
        ? `▤ ${task.document.document_name}`
        : "This older task has no linked corpus document.",
    ),
    element("p", "task-reference", `Task ${task.task_id}`),
  );
  if (task.document) {
    const mode = ["interrupted", "failed"].includes(task.status)
      ? "resume"
      : ["completed", "cancelled"].includes(task.status)
        ? "restart"
        : "start";
    const prompt = promptFor(task, mode);
    const copy = button(
      mode === "resume"
        ? "Copy resume request"
        : mode === "restart"
          ? "Copy new-run request"
          : "Copy coordinator request",
      () => copyPrompt(prompt),
      false,
    );
    copy.dataset.action = "copy";
    const chat = button("Open Fred chat ↗", () =>
      bridge.openChat(),
    );
    chat.dataset.action = "chat";
    const actions = append(element("div", "review-controls"), copy, chat);
    if (run && ["running", "interrupted"].includes(run.status)) {
      const cancel = button("Cancel run", () => cancelRun(task, run));
      cancel.className = "button danger";
      cancel.dataset.action = "cancel";
      append(actions, cancel);
    }
    const details = element("details", "prompt-details");
    details.dataset.key = "prompt";
    append(
      details,
      element("summary", "", "How to start or resume this review"),
      element(
        "p",
        "small muted",
        "Open Fred chat, choose the Document Review Coordinator agent, then paste this request. Opening chat does not select an agent or send a message automatically.",
      ),
      element("code", "prompt-text", prompt),
    );
    append(header, actions, details);
  }
  append(root, header);
  if (task.document) {
    const percent = percentOf(task.progress_percent),
      finished = stages.filter((stage) => stage.status === "completed").length;
    const flow = section("Review progress", "", "progress-panel");
    flow.firstChild.remove();
    append(
      flow,
      append(
        element("div", "panel-heading"),
        append(
          element("div"),
          element("h2", "", "Review progress"),
          element(
            "p",
            "progress-caption",
            `${finished} of ${stages.length} expected steps complete${run ? "" : " · ready for the coordinator"}`,
          ),
        ),
        element(
          "strong",
          "overall-number",
          percent === null ? "—" : `${percent}%`,
        ),
      ),
      meter(percent, "Overall review completion"),
    );
    append(
      flow,
      append(
        element("div", "stage-flow"),
        ...stages.map((stage) =>
          append(
            element("div", `flow-stage ${statusOf(stage.status)}`),
            element("span", "flow-number", stage.symbol),
            element("span", "flow-name", stage.short),
            pill(stage.status),
          ),
        ),
      ),
    );
    append(root, flow);
    if (run && ["interrupted", "failed", "cancelled"].includes(run.status))
      append(
        root,
        element(
          "p",
          "notice",
          run.status === "cancelled"
            ? "This run was cancelled. Its recorded decisions remain below. Cancellation is checked between steps; it does not guarantee an in-flight model call stops immediately."
            : "The review did not finish. Saved decisions are preserved; start a fresh Fred chat turn and ask the coordinator to resume this task.",
        ),
      );
    // The coordinator has no card in the specialist grid, so its own section
    // carries the working state the three specialists show on theirs.
    const coordinator = stages.find((stage) => stage.id === "summary");
    if (coordinator?.result)
      append(
        root,
        append(
          section(
            "Coordinator decision",
            "Saved final synthesis",
            "result-summary",
          ),
          resultContent(coordinator.result),
        ),
      );
    else if (coordinator?.status === "running")
      append(
        root,
        append(
          section(
            "Coordinator decision",
            "Synthesis in progress",
            "result-summary",
          ),
          meter(null, `${coordinator.name} working`, true),
          element(
            "p",
            "small muted",
            "Synthesising the saved specialist decisions. No estimated percentage is assigned to a model call.",
          ),
        ),
      );
    const durations = completedStageDurations(stages);
    // This chart sits beside a per-run one, so it reports the same run rather
    // than every task's latest run, which read as this run's own total.
    if (run) loadRunStatistics(task.task_id, run, redraw);
    append(
      root,
      append(
        element("div", "charts-grid"),
        barChart(
          "Time by specialist",
          "Selected run · completed steps only",
          durations,
          durationLabel,
        ),
        severityChart(
          run ? state.runStatistics.get(run.run_id)?.stats : null,
          "Selected run · findings saved by its specialists",
        ),
      ),
    );
    append(
      root,
      append(
        element("div", "cards-heading"),
        element("h2", "", "Specialist decisions"),
        element(
          "span",
          "small muted",
          "Persisted results, not hidden reasoning",
        ),
      ),
      append(
        element("div", "specialist-grid"),
        ...stages.slice(0, 3).map(specialistCard),
      ),
    );
    const timelinePanel = section(
      "Flow timeline",
      run
        ? `Run ${run.run_id} · ${dateLabel(run.started_at)}`
        : "No run started yet",
      "timeline-panel",
    );
    const timeline = element("ol", "timeline");
    for (const event of list(run?.events))
      append(
        timeline,
        append(
          element("li"),
          element("time", "", dateLabel(event.at, true)),
          element("span", "timeline-dot"),
          append(
            element("div"),
            element("span", "timeline-name", labelOf(event.event)),
            element(
              "span",
              "timeline-meta",
              [
                STAGES.find((stage) => stage.id === event.stage_id)?.name,
                event.reported_by ? `User ${event.reported_by}` : null,
              ]
                .filter(Boolean)
                .join(" · "),
            ),
          ),
        ),
      );
    append(
      timelinePanel,
      timeline.children.length
        ? timeline
        : empty(
            "Once the coordinator starts, saved workflow transitions will appear here.",
          ),
    );
    const oldRuns = list(task.review?.runs).filter(
      (item) => item.run_id !== run?.run_id,
    );
    if (oldRuns.length) {
      const history = element("details", "history-strip");
      history.dataset.key = "history";
      append(
        history,
        element(
          "summary",
          "",
          `${oldRuns.length} previous run${oldRuns.length === 1 ? "" : "s"}`,
        ),
        ...oldRuns.map((item) => {
          const previous = element("details", "history-strip");
          previous.dataset.key = item.run_id;
          append(
            previous,
            element(
              "summary",
              "",
              `${labelOf(item.status)} · ${dateLabel(item.started_at)}`,
            ),
            element("code", "task-reference", item.run_id),
          );
          // Opening a run is the request for its own numbers; they are fetched
          // once and kept, so reopening it costs nothing.
          const runStats = state.runStatistics.get(item.run_id)?.stats;
          if (runStats) append(previous, runStatsPanel(runStats));
          previous.addEventListener("toggle", () => {
            if (previous.open) loadRunStatistics(task.task_id, item, redraw);
          });
          for (const stage of list(item.stages)) {
            const definition = STAGES.find(
              (entry) => entry.id === stage.stage_id,
            );
            if (stage.result && definition)
              append(previous, specialistCard({ ...definition, ...stage }));
          }
          return previous;
        }),
      );
      append(timelinePanel, history);
    }
    append(root, timelinePanel);
  } else {
    const legacy = section(
      "Agent reports",
      "Existing task · latest saved state for each reporter",
      "timeline-panel",
    );
    for (const report of list(task.progress))
      append(
        legacy,
        append(
          element("article", "result-section"),
          append(
            element("div", "panel-heading"),
            element("h3", "", report.agent_label),
            pill(report.status),
          ),
          meter(
            report.progress_percent,
            `${report.agent_label} reported progress`,
            report.status === "running" &&
              percentOf(report.progress_percent) === null,
          ),
          element("p", "rationale", report.detail),
          element(
            "p",
            "small muted",
            `Updated ${dateLabel(report.updated_at)}`,
          ),
        ),
      );
    if (!list(task.progress).length)
      append(
        legacy,
        empty("No reporter has saved progress for this legacy task."),
      );
    append(root, legacy);
  }
  if (state.statistics)
    append(
      root,
      append(
        element("div", "charts-grid"),
        barChart(
          "Review outcomes",
          "Latest accessible tasks · saved outcomes",
          rowsFromCounts(state.statistics.outcome_counts),
        ),
        barChart(
          "Task status",
          "Latest accessible tasks · not all-time totals",
          rowsFromCounts(state.statistics.task_status_counts),
        ),
      ),
    );
  const completions = list(state.statistics?.completion_timeline);
  if (completions.length)
    append(
      root,
      barChart(
        "Completed over time",
        "Dates and counts from persisted completion events",
        completions.map((item) => ({
          key: item.date,
          label: item.date,
          value: countOf(item.count),
        })),
      ),
    );
  $("workspace").replaceChildren(root);
  for (const detail of $("workspace").querySelectorAll("details"))
    detail.open = openDetails.has(detail.dataset.key);
  if (focusedAction)
    [...$("workspace").querySelectorAll("button[data-action]")]
      .find((item) => item.dataset.action === focusedAction)
      ?.focus({ preventScroll: true });
}

async function copyPrompt(prompt) {
  try {
    if (!navigator.clipboard?.writeText)
      throw new Error("Clipboard unavailable");
    await navigator.clipboard.writeText(prompt);
    showMessage(
      "notice",
      "Request copied. In Fred chat, choose Document Review Coordinator and paste it.",
    );
  } catch {
    const details = $("workspace").querySelector("details[data-key='prompt']");
    if (details) details.open = true;
    showMessage(
      "notice",
      "Clipboard access is unavailable. Select and copy the request shown below.",
    );
  }
}
async function cancelRun(task, run) {
  if (
    !window.confirm(
      "Cancel this run? Completed decisions are preserved. An in-flight model call may finish before cancellation is observed.",
    )
  )
    return;
  const generation = bridge.generation;
  try {
    await bridge.request(`tasks/${encodeURIComponent(task.task_id)}/cancel`, {
      method: "POST",
      body: { run_id: run.run_id },
    });
    if (generation === bridge.generation) {
      showMessage(
        "notice",
        "Cancellation saved. Completed decisions remain available.",
      );
      await loadDetail();
      refresh();
    }
  } catch (error) {
    if (generation === bridge.generation) reportError(error);
  }
}

function selectOptions(select, items, valueKey, labelKey, placeholder) {
  const option = element("option", "", placeholder);
  option.value = "";
  select.replaceChildren(
    option,
    ...items.map((item) => {
      const row = element("option", "", item[labelKey]);
      row.value = item[valueKey];
      return row;
    }),
  );
}
async function openCreate() {
  $("create-form").reset();
  $("create-error").textContent = "";
  state.offset = 0;
  state.documentRequest += 1;
  $("folder").disabled = true;
  $("document").disabled = true;
  $("create").disabled = true;
  selectOptions($("folder"), [], "tag_id", "path", "Loading folders…");
  selectOptions(
    $("document"),
    [],
    "document_uid",
    "document_name",
    "Choose a folder first",
  );
  $("document-count").textContent = "";
  $("previous-documents").disabled = true;
  $("next-documents").disabled = true;
  $("create-dialog").showModal();
  const generation = bridge.generation,
    request = state.documentRequest;
  try {
    const result = await bridge.request("folders");
    if (generation !== bridge.generation || request !== state.documentRequest)
      return;
    const items = list(result?.items);
    selectOptions(
      $("folder"),
      items,
      "tag_id",
      "path",
      items.length ? "Choose a corpus folder" : "No accessible folders",
    );
    $("folder").disabled = !items.length;
  } catch (error) {
    if (generation === bridge.generation)
      $("create-error").textContent = error.message;
  }
}
async function loadDocuments(offset = 0) {
  const tag = $("folder").value;
  state.offset = offset;
  const request = ++state.documentRequest,
    generation = bridge.generation;
  $("document").disabled = true;
  $("create").disabled = true;
  $("previous-documents").disabled = true;
  $("next-documents").disabled = true;
  selectOptions(
    $("document"),
    [],
    "document_uid",
    "document_name",
    tag ? "Loading documents…" : "Choose a folder first",
  );
  if (!tag) return;
  try {
    const query = new URLSearchParams({
      tag_id: tag,
      offset: String(offset),
      limit: "50",
    });
    const data = await bridge.request(`documents?${query}`);
    if (generation !== bridge.generation || request !== state.documentRequest)
      return;
    state.documents = list(data?.items);
    selectOptions(
      $("document"),
      state.documents,
      "document_uid",
      "document_name",
      state.documents.length
        ? "Choose a document"
        : "No accessible documents here; try the next page",
    );
    $("document").disabled = !state.documents.length;
    const paging = documentPaging(data, offset);
    $("document-count").textContent = paging.label;
    $("previous-documents").disabled = !paging.previous;
    $("next-documents").disabled = !paging.next;
    $("create-error").textContent = "";
  } catch (error) {
    if (generation === bridge.generation && request === state.documentRequest)
      $("create-error").textContent = error.message;
  }
}
async function createTask(event) {
  event.preventDefault();
  const documentUid = $("document").value,
    tagId = $("folder").value,
    title = $("title").value.trim();
  if (!documentUid || !tagId || !title) return;
  $("create").disabled = true;
  const generation = bridge.generation;
  try {
    const task = await bridge.request("tasks", {
      method: "POST",
      body: { title, document: { tag_id: tagId, document_uid: documentUid } },
    });
    if (generation !== bridge.generation) return;
    $("create-dialog").close();
    showMessage(
      "notice",
      "Review created. Copy its request and ask the Document Review Coordinator in Fred chat to begin.",
    );
    await selectTask(task.task_id);
    refresh();
  } catch (error) {
    if (generation === bridge.generation)
      $("create-error").textContent = error.message;
  } finally {
    if (generation === bridge.generation)
      $("create").disabled = !$("document").value;
  }
}

window.addEventListener("pagehide", () => {
  clearTimeout(state.timer);
  bridge.dispose();
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refresh(true);
});
$("refresh").addEventListener("click", () => refresh(true));
$("new-task").addEventListener("click", openCreate);
$("close-dialog").addEventListener("click", () => $("create-dialog").close());
$("create-form").addEventListener("submit", createTask);
$("folder").addEventListener("change", () => loadDocuments());
$("document").addEventListener("change", () => {
  $("create").disabled = !$("document").value;
  if (!$("title").value)
    $("title").value =
      state.documents
        .find((item) => item.document_uid === $("document").value)
        ?.document_name?.slice(0, 200) ?? "";
});
$("previous-documents").addEventListener("click", () =>
  loadDocuments(Math.max(0, state.offset - 50)),
);
$("next-documents").addEventListener("click", () =>
  loadDocuments(state.offset + 50),
);
$("task-search").addEventListener("input", renderTasks);
renderMetrics(null);
// The client retries the announcement until the host answers, so a frame that
// renders before the host is listening still connects.
bridge.connect().catch((error) => {
  showMessage("error", `Could not reach Fred: ${error.message}`);
});
setTimeout(() => {
  if (!bridge.teamId)
    showMessage(
      "notice",
      "Waiting for Fred. Open Review Board from a collaborative team's Applications page; this page cannot connect directly to your backend.",
    );
}, 12000);
