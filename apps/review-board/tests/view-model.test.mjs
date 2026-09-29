import test from "node:test";
import assert from "node:assert/strict";
import {
  latestRun,
  percentOf,
  secondsBetween,
  durationLabel,
  rowsFromCounts,
  totalCounts,
  stageRows,
  promptFor,
  matchesTask,
  statusOf,
  dateLabel,
  documentPaging,
  completedStageDurations,
  pollDelay,
  detailAction,
  runStatisticsKey,
  LIVE_POLL_MS,
  IDLE_POLL_MS,
} from "../ui/view-model.mjs";

// Fixtures are intentionally confined to tests. The UI ships no generated data.
const task = {
  task_id: "task-0123456789abcdef0123456789abcdef",
  title: "Procurement review",
  document: { document_name: "contract.pdf" },
  progress_percent: 25,
  review: {
    active_run_id: "run-one",
    runs: [
      {
        run_id: "run-one",
        stages: [
          {
            stage_id: "analyst",
            status: "completed",
            result: { decision: "Recorded decision" },
          },
        ],
      },
      { run_id: "run-two", stages: [] },
    ],
  },
};

test("missing progress is unknown, never a fabricated percentage", () => {
  assert.equal(percentOf(undefined), null);
  assert.equal(percentOf("50"), null);
  assert.equal(percentOf(NaN), null);
  assert.equal(percentOf(25), 25);
  assert.equal(percentOf(-3), 0);
  assert.equal(percentOf(200), 100);
});
test("active run selection preserves fixed queued stages", () => {
  assert.equal(latestRun(task).run_id, "run-one");
  const stages = stageRows(task);
  assert.equal(stages.length, 4);
  assert.equal(stages[0].status, "completed");
  assert.equal(stages[0].result.decision, "Recorded decision");
  assert.deepEqual(
    stages.slice(1).map((stage) => stage.status),
    ["queued", "queued", "queued"],
  );
  assert.equal(
    latestRun({ review: { runs: task.review.runs, active_run_id: null } })
      .run_id,
    "run-two",
  );
  assert.equal(latestRun({}), null);
});
test("durations require two recorded timestamps and never estimate live work", () => {
  assert.equal(secondsBetween("2026-01-01T10:00:00Z", null), null);
  assert.equal(secondsBetween("bad", "2026-01-01T10:00:05Z"), null);
  assert.equal(
    secondsBetween("2026-01-01T10:00:05Z", "2026-01-01T10:00:00Z"),
    null,
  );
  assert.equal(
    secondsBetween("2026-01-01T10:00:00Z", "2026-01-01T10:00:05Z"),
    5,
  );
  assert.equal(durationLabel(null), "Not recorded");
  assert.equal(durationLabel(90), "1m 30s");
  assert.equal(dateLabel("invalid"), "Not recorded");
});
test("chart helpers use returned counts without inventing categories or events", () => {
  assert.deepEqual(rowsFromCounts({ completed: 2, failed: 1 }), [
    { key: "completed", label: "completed", value: 2 },
    { key: "failed", label: "failed", value: 1 },
  ]);
  assert.equal(totalCounts({ high: 2, info: 3, invalid: -1 }), 5);
  assert.deepEqual(rowsFromCounts(null), []);
});
test("copy requests keep the exact task reference and explicit resume/restart", () => {
  assert.match(promptFor(task), new RegExp(`^Review task ${task.task_id}\\.`));
  assert.match(promptFor(task, "resume"), /^Resume task /);
  assert.match(promptFor(task, "restart"), /^Restart task /);
  assert.doesNotMatch(promptFor(task), /contract\.pdf/);
});
test("task search and unknown status handle legacy records safely", () => {
  assert.equal(matchesTask(task, "CONTRACT"), true);
  assert.equal(matchesTask({ title: "Old task", task_id: "old" }, "old"), true);
  assert.equal(matchesTask(task, "unrelated"), false);
  assert.equal(statusOf("<script>"), "unknown");
});
test("filtered corpus pages never imply the end of an unknown total", () => {
  assert.equal(documentPaging({ items: [], total: null }, 0).next, true);
  assert.equal(documentPaging({ items: [{}], total: null }, 50).next, true);
  assert.equal(documentPaging({ items: [{}], total: 51 }, 50).next, false);
  assert.equal(documentPaging({ items: [], total: null }, 100000).next, false);
  assert.equal(documentPaging({ items: [], total: null }, 50).previous, true);
});
test("completed-duration chart excludes failed and running steps", () => {
  const timing = {
    started_at: "2026-01-01T10:00:00Z",
    finished_at: "2026-01-01T10:00:05Z",
  };
  assert.deepEqual(
    completedStageDurations([
      { ...timing, id: "analyst", short: "Analysis", status: "completed" },
      {
        ...timing,
        id: "risk_reviewer",
        short: "Risk review",
        status: "failed",
      },
      {
        ...timing,
        id: "action_planner",
        short: "Actions",
        status: "running",
        finished_at: null,
      },
    ]),
    [{ key: "analyst", label: "Analysis", value: 5 }],
  );
});

const listed = (status, progress_percent = 0) => ({
  task_id: "task-1",
  status,
  progress_percent,
});
const reviewDetail = (entry) => ({
  ...entry,
  review: { runs: [] },
  progress: [],
});
const reporterDetail = (...statuses) => ({
  ...listed("queued"),
  review: null,
  progress: statuses.map((status, index) => ({
    agent_label: `worker-${index}`,
    status,
  })),
});
test("polling is fast only while a listed task or the selected detail runs", () => {
  const idle = [
    listed("completed", 100),
    { ...listed("queued"), task_id: "b" },
  ];
  assert.equal(pollDelay(idle, null), IDLE_POLL_MS);
  assert.equal(pollDelay([...idle, listed("running", 25)], null), LIVE_POLL_MS);
  // A generic task's list entry reads queued while its reporters work.
  assert.equal(pollDelay(idle, reporterDetail("running")), LIVE_POLL_MS);
  assert.equal(pollDelay(idle, reporterDetail("completed")), IDLE_POLL_MS);
  assert.equal(pollDelay([], reviewDetail(listed("running"))), LIVE_POLL_MS);
});
test("a finished review detail is kept until its list entry changes", () => {
  const entry = listed("completed", 100);
  assert.equal(detailAction([entry], "task-1", reviewDetail(entry)), "keep");
  assert.equal(
    detailAction([listed("running", 0)], "task-1", reviewDetail(entry)),
    "reload",
  );
  assert.equal(
    detailAction([listed("completed", 75)], "task-1", reviewDetail(entry)),
    "reload",
  );
  assert.equal(detailAction([entry], "task-1", null), "reload");
  assert.equal(
    detailAction([entry], "task-1", { ...reviewDetail(entry), task_id: "b" }),
    "reload",
  );
});
test("a running review detail is reloaded on every poll", () => {
  const entry = listed("running", 25);
  assert.equal(detailAction([entry], "task-1", reviewDetail(entry)), "reload");
});
test("a finished review detail is reloaded when the list names a newer run", () => {
  const shown = { ...listed("completed", 100), run_id: "run-1" };
  const entry = { ...shown, run_id: "run-2" };
  assert.equal(detailAction([entry], "task-1", reviewDetail(shown)), "reload");
});
test("a generic detail is reloaded on every poll", () => {
  const entry = listed("queued");
  assert.equal(detailAction([entry], "task-1", reporterDetail()), "reload");
  assert.equal(
    detailAction([entry], "task-1", reporterDetail("running")),
    "reload",
  );
  // A reporter can join or report again after every earlier one has finished.
  assert.equal(
    detailAction([entry], "task-1", reporterDetail("completed", "failed")),
    "reload",
  );
});
test("a selection missing from the fresh list is cleared", () => {
  const detail = reviewDetail(listed("completed", 100));
  assert.equal(
    detailAction([{ ...listed("completed"), task_id: "b" }], "task-1", detail),
    "clear",
  );
  assert.equal(detailAction([], "task-1", detail), "clear");
  assert.equal(detailAction([listed("queued")], null, null), "none");
});
test("run statistics are keyed by run, status and completed stage count", () => {
  const run = {
    run_id: "run-1",
    status: "running",
    stages: [{ status: "completed" }, { status: "running" }],
  };
  assert.equal(runStatisticsKey(run), "run-1:running:1");
  assert.notEqual(
    runStatisticsKey({
      ...run,
      stages: [{ status: "completed" }, { status: "completed" }],
    }),
    runStatisticsKey(run),
  );
  assert.notEqual(
    runStatisticsKey({ ...run, status: "cancelled" }),
    runStatisticsKey(run),
  );
  assert.notEqual(
    runStatisticsKey({ ...run, run_id: "run-2" }),
    runStatisticsKey(run),
  );
  // A heartbeat or new event changes nothing the numbers depend on.
  assert.equal(
    runStatisticsKey({ ...run, updated_at: "later", events: [{}] }),
    runStatisticsKey(run),
  );
});
