// Pure presentation helpers. They format persisted data; they never simulate work.
export const STAGES = [
  {
    id: "analyst",
    name: "Document analyst",
    short: "Analysis",
    specialty: "Purpose, facts & evidence",
    symbol: "01",
  },
  {
    id: "risk_reviewer",
    name: "Risk reviewer",
    short: "Risk review",
    specialty: "Gaps, ambiguity & risk",
    symbol: "02",
  },
  {
    id: "action_planner",
    name: "Action planner",
    short: "Action plan",
    specialty: "Priorities & next steps",
    symbol: "03",
  },
  {
    id: "summary",
    name: "Coordinator",
    short: "Synthesis",
    specialty: "Overall decision & limitations",
    symbol: "04",
  },
];
export const SEVERITIES = ["critical", "high", "medium", "low", "info"];
const STATUSES = new Set([
  "queued",
  "running",
  "interrupted",
  "completed",
  "failed",
  "cancelled",
  "blocked",
]);
export const list = (value) => (Array.isArray(value) ? value : []);
export const statusOf = (value) => (STATUSES.has(value) ? value : "unknown");
export const labelOf = (value) =>
  String(value ?? "Unknown").replaceAll("_", " ");
export function percentOf(value) {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.max(0, Math.min(100, value))
    : null;
}
export function countOf(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : 0;
}
export function documentPaging(data, offset, pageSize = 50) {
  const count = list(data?.items).length;
  return {
    label: `Page ${Math.floor(offset / pageSize) + 1} · ${count} accessible document${count === 1 ? "" : "s"}`,
    previous: offset > 0,
    // Upstream authorization can filter every row in a nonterminal page.
    next:
      offset < 100000 && (data?.total == null || offset + count < data.total),
  };
}
export function latestRun(task) {
  const runs = list(task?.review?.runs);
  return (
    runs.find((run) => run.run_id === task.review.active_run_id) ??
    runs.at(-1) ??
    null
  );
}
export function secondsBetween(start, end) {
  if (!start || !end) return null;
  const seconds = (Date.parse(end) - Date.parse(start)) / 1000;
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : null;
}
export function durationLabel(seconds) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0)
    return "Not recorded";
  if (seconds < 1) return "<1s";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${Math.round(seconds % 60)}s`;
}
export function dateLabel(value, timeOnly = false) {
  if (!value) return "Not recorded";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "Not recorded";
  return new Intl.DateTimeFormat(
    undefined,
    timeOnly
      ? { hour: "2-digit", minute: "2-digit", second: "2-digit" }
      : { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" },
  ).format(date);
}
export function promptFor(task, mode = "start") {
  const action =
    mode === "resume" ? "Resume" : mode === "restart" ? "Restart" : "Review";
  return `${action} task ${task.task_id}. Read the task's selected corpus document through the Review Board MCP server, run the document analyst, risk reviewer and action planner in order, then save your final summary. Use the existing saved decisions when resuming.`;
}
export function rowsFromCounts(counts, order = Object.keys(counts ?? {})) {
  return order.map((key) => ({
    key,
    label: labelOf(key),
    value: countOf(counts?.[key]),
  }));
}
export function totalCounts(counts) {
  return Object.values(counts ?? {}).reduce(
    (sum, value) => sum + countOf(value),
    0,
  );
}
export function matchesTask(task, query) {
  const haystack = [task.title, task.task_id, task.document?.document_name]
    .filter(Boolean)
    .join(" ")
    .toLocaleLowerCase();
  return haystack.includes(query.trim().toLocaleLowerCase());
}
export function stageRows(task) {
  const run = latestRun(task);
  return STAGES.map((definition) => ({
    ...definition,
    ...(list(run?.stages).find((stage) => stage.stage_id === definition.id) ?? {
      status: "queued",
    }),
  }));
}
export function completedStageDurations(stages) {
  return list(stages)
    .filter(
      (stage) =>
        stage.status === "completed" &&
        secondsBetween(stage.started_at, stage.finished_at) !== null,
    )
    .map((stage) => ({
      key: stage.id,
      label: stage.short,
      value: secondsBetween(stage.started_at, stage.finished_at),
    }));
}
export const LIVE_POLL_MS = 6000;
export const IDLE_POLL_MS = 30000;
// A generic task's list entry always reads queued; its reporters carry the live state.
export function isRunning(detail) {
  return (
    detail?.status === "running" ||
    list(detail?.progress).some((report) => report.status === "running")
  );
}
export function pollDelay(tasks, detail) {
  return list(tasks).some((task) => task.status === "running") ||
    isRunning(detail)
    ? LIVE_POLL_MS
    : IDLE_POLL_MS;
}
function detailIsSettled(entry, detail) {
  if (
    detail?.task_id !== entry.task_id ||
    detail.status !== entry.status ||
    detail.progress_percent !== entry.progress_percent ||
    detail.run_id !== entry.run_id ||
    isRunning(detail)
  )
    return false;
  // The list entry summarises a review's latest run, not a generic task's reporters.
  return detail.review != null;
}
// What a fresh task list means for the selected detail: drop, reload or keep it.
export function detailAction(tasks, selected, detail) {
  if (!selected) return "none";
  const entry = list(tasks).find((task) => task.task_id === selected);
  if (!entry) return "clear";
  return detailIsSettled(entry, detail) ? "keep" : "reload";
}
export function runStatisticsKey(run) {
  const completed = list(run?.stages).filter(
    (stage) => stage.status === "completed",
  ).length;
  return `${run?.run_id}:${run?.status}:${completed}`;
}
