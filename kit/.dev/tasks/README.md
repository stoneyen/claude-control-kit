# .dev/tasks/ — file-backed task ledger for multi-step work

One YAML file per task: `<plan-slug>/T<NN>-<slug>.yaml` with `status`
(pending/in_progress/done/blocked) and a `results[]` array of execution
snapshots (timestamp / files / commits / tests / notes). Survives session
boundaries, compaction, and crashes — unlike in-memory task lists.

Ad-hoc multi-step debugging without a parent plan lives under
`_ad-hoc/<YYYY-MM-DD>-<slug>.yaml`. Use it to answer "where did we leave off?"
across sessions.
