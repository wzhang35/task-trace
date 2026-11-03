---
name: task-trace
description: Capture a task-scoped Codex research workflow with its questions, tool records, input files and changing outputs in a private local archive. Use when the user asks to preserve an ongoing task for later reconstruction or dataset preparation.
---

Use the Task Trace collector bundled with this repository. Resolve this SKILL.md's real path (including symlinks); `task_trace.py` is two directories above its containing directory. Read that repository's README for current commands and coverage limits. If the skill was copied alone, locate the user's installed collector; do not invent a replacement script.

Before capture, identify a dedicated task workspace and a private archive store outside it. Reuse an existing `.task-trace.json` binding rather than starting a duplicate task. Keep originals intact; preserve external inputs with `attach` and record when they were supplied. Do not silently relabel reconstructed questions or model-generated feedback as historical human supervision.

For a future interactive Codex session, use `start` and `install-hooks`, then tell the user to review/trust the commands in Codex and start a new session. Installing hooks mid-task does not recover earlier states. Verify that actual hook events arrive. Do not alter trust settings or promise desktop coverage based only on configuration being written.

For a separately requested non-interactive task, use `run` and resume its captured session ID for subsequent questions. Do not launch a nested Codex task simply to capture the current conversation. The wrapper's user config is ignored; report missing MCPs or environment dependencies explicitly.

Use `checkpoint` after manual changes or before a meaningful boundary; attach only task-relevant materials. At the end of an interactive turn, give the exact `finish` and `check` commands for a separate terminal **after the turn has stopped**. Finishing from inside the captured session cannot close its own later tool/Stop events. For a completed wrapper run, finish normally.

Report the archive path, coverage gaps and exclusions. Capture status is not scientific correctness, expert acceptance, model provenance certification or Harbor replay readiness. Raw archives may contain sensitive material: keep them local unless sharing of those exact contents has been authorized. Publishing the neutral collector does not authorize publishing task data.
