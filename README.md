# Task Trace

A small, local collector for **task-scoped Codex research workflows**. Keep the question, follow-up instructions, visible model actions, tool results, input files and changing outputs together, before they become scattered.

Python 3.9+ standard library; macOS/Linux. No service, telemetry or automatic upload. The collector itself is offline; `run` launches your authenticated Codex, which calls its configured model service.

The collector is **not a benchmark, expert labeler or secret scrubber**. A separate `harbor_export.py` converts reviewed archives into Harbor tasks; see [Harbor conversion](#harbor-conversion). It does not infer that a researcher's instruction means they accepted an answer. All included examples are synthetic; no research conversations or real datasets are distributed.

## Quick start: a two-turn synthetic study

Install and authenticate the official [Codex CLI](https://developers.openai.com/codex/cli/). Use a version supporting `exec --json`, `--ignore-user-config` and `exec resume`. Python needs no packages.

Run from this repository:

```sh
python3 examples/make_demo.py /tmp/task-trace-demo
python3 task_trace.py start \
  --workspace /tmp/task-trace-demo/workspace \
  --store /tmp/task-trace-demo/archive \
  --title "Synthetic study: aggregation and standardization"
python3 task_trace.py run \
  --workspace /tmp/task-trace-demo/workspace \
  --prompt-file /tmp/task-trace-demo/turn1.txt
```

The first turn analyzes synthetic counts, encounters a deliberately missing protocol, and creates code, numerical results and a report. `run` prints the captured `session_id`. Supply the missing material and continue **that same session**:

```sh
python3 task_trace.py attach \
  --workspace /tmp/task-trace-demo/workspace \
  /tmp/task-trace-demo/protocol.txt --label "Protocol supplied before turn 2"
cp /tmp/task-trace-demo/protocol.txt /tmp/task-trace-demo/workspace/protocol.txt
python3 task_trace.py run \
  --workspace /tmp/task-trace-demo/workspace \
  --resume SESSION_ID_FROM_FIRST_RUN \
  --prompt-file /tmp/task-trace-demo/turn2.txt
python3 task_trace.py finish --workspace /tmp/task-trace-demo/workspace
python3 task_trace.py check --workspace /tmp/task-trace-demo/workspace
```

Open `index.html` in the archive directory printed by `start`. It shows the questions, Codex event timeline, downloadable file versions and coverage report. `check` independently checks the event hash chain, referenced objects, known session/tool boundaries and declared gaps; it exits nonzero if coverage is incomplete or integrity fails. Neither check evaluates scientific correctness.

Use `--codex /absolute/path/to/codex` when the CLI is not on PATH, `--model MODEL_ID` to select a model, and `--timeout SECONDS` to change the 600-second run limit. The wrapper uses the `workspace-write` sandbox and ignores user config to reduce unrelated configuration dependencies. It **does not reproduce** all personal MCPs or project environments. Codex may still load applicable instructions and platform defaults. Authentication stays in your Codex installation; the collector never copies its auth file. No bypass of sandbox or hook trust is enabled.

## Capture a real task

Start **before its first question** in a dedicated workspace containing only the intended task inputs. Choose a private archive store outside that workspace (and preferably outside any Git repository). A `.task-trace.json` binding links the workspace to its archive. `run` saves exact prompt text, raw CLI stdout/stderr, visible events, the native session JSONL and workspace snapshots at observed item/turn boundaries. Resume only session IDs already captured in the same task. Multiple captured sessions can belong to one task.

Keep attachments in the workspace before they are needed, or use `attach` to preserve an explicitly selected external file. `attach` archives a copy; it does **not** give the model access to it. Copy the input into the workspace and mention it in the prompt when appropriate. Use `checkpoint --workspace PATH --label "reason"` after manual edits or before an important operation. Inputs added later keep their actual capture time; they are not relabeled as original inputs.

One workspace is bound to one task. Use a fresh workspace for another task. Do not run simultaneous `run` processes on the same workspace: their filesystem changes cannot be reliably attributed to separate turns. Concurrent hook writes within a session are serialized.

### Interactive Codex sessions: optional hook adapter

The preferred validated route is the CLI wrapper above. The repository also includes a **hook adapter**, tested with synthetic event fixtures. Live desktop hook compatibility must be verified on your installed Codex version before relying on it.

```sh
python3 task_trace.py install-hooks --workspace /absolute/path/to/workspace
```

This merges observer hooks into the workspace's `.codex/hooks.json`, preserves existing handlers and stores the previous configuration in the private archive. Review/trust the exact commands in Codex `/hooks` or Settings → Hooks, then start a **new** session in that workspace. The collector never marks hooks trusted for you. After a short turn, check that `events.jsonl` contains `hook` events including `SessionStart`, `UserPromptSubmit`, tool events and `Stop`. Missing required boundaries are reported as gaps. A hook installation is not proof that hooks ran.

After the last turn has stopped, run `finish` from a **separate terminal**, followed by `check`. Finishing inside an actively captured Codex turn is too early: subsequent hook activity removes the stale report and requires `finish` again. Capture hooks are observers; they do not block tools or inject research instructions. A collector failure may still produce a Codex hook error notice.

To make the thin Skill discoverable, symlink this repository's `skills/task-trace` directory into your Codex skills directory (commonly `~/.codex/skills/task-trace`). Keep the repository at a stable location. The Skill is an operating guide; it cannot replace the collector or recover earlier uncaptured files.

## What is in an archive?

| File | Purpose |
| --- | --- |
| `task.json` | Task identity, declared workspace, collector/Python version and size limit |
| `events.jsonl` | Timestamped, hash-chained capture events, questions, raw hook payloads and file manifests |
| `objects/<sha256>` | Exact captured bytes: inputs, outputs, attachments and native session snapshots |
| `raw/<run-id>/` | Original `codex exec --json` stream and stderr for each run |
| `report.json` | Integrity results, coverage gaps, explicit exclusions and limitations |
| `index.html` | Local, escaped HTML timeline; no remote assets or scripts |

A file manifest represents all observed files at that boundary. A path disappearing from the next manifest records a deletion; earlier versions remain in `objects/`. Native session snapshots preserve original bytes separately from the collector's interpretation. The JSON schema starts at version 1; Codex's native session schema is not guaranteed stable.

`closed_for_declared_scope` means the collector found no gaps in its supported checks **within the documented scope**. It does not certify every OS operation, hidden context, external dependency, model thought or research result. A task with failed tools can be captured correctly. An interrupted run, missing tool return, unavailable file or missing native transcript produces a gap rather than a success claim. Hashes detect local inconsistency; they are not signatures or proof of model identity.

## Boundaries and privacy

- Files larger than 50 MiB are reported as missing by default; choose `start --max-file-mb N` if appropriate. Symlinks and unreadable/changing files are reported as gaps. Common credentials, `.git`, `.venv`, `node_modules`, `.ssh`, `.aws` and archive directories are explicitly excluded. Exclusions remain visible in the report.
- This is **not automatic redaction**. Secrets can appear in prompts, tool output, arbitrary filenames or native logs. Keep archives private and review them separately before any sharing. A `.gitignore` is a convenience, not a security boundary.
- Only the selected filesystem scope and explicitly attached files are collected. URLs, remote databases, MCP server implementations, tool credentials and dependencies outside the workspace are not fetched. Harbor conversion still needs an explicit environment/dependency review and independent replay.
- File snapshots are taken at observed boundaries; transient files created and deleted inside one command may never be captured. Terminal output is limited to what Codex exposes; truncation is not reversed. Provider-hidden reasoning and unexposed system context are unavailable.
- The hook adapter follows [Codex hooks](https://learn.chatgpt.com/docs/hooks); hosted/specialized tools may bypass local hooks. The wrapper follows [non-interactive Codex](https://developers.openai.com/codex/noninteractive/). Native log parsing is conservative and reports unknown/incomplete conversation formats. Subagents launched through hooks require their own transcript; child trace coverage in wrapper-only runs is not guaranteed. Keep the first pilot task single-agent.
- Use a modest, dedicated task workspace. Each checkpoint walks it and hashes observed files. Large projects or high-frequency hooks need further performance work. Sudden termination can leave a partial run; `check` exposes it without repairing or deleting evidence.

## Validation

```sh
python3 -m unittest discover -s tests -v
```

A live two-turn synthetic run was checked on 2026-11-03 with `codex-cli 0.166.0-alpha.5.1` (requested model `gpt-6.1-sol`): 18 snapshots, 5 completed command records, both versions of code/results/report, and both questions in the original session. Archive-only reconstruction reproduced the final numerical results using Python 3.9.6. This validates a bounded, single-agent, local-file task; it does not validate desktop hooks, external MCP environments or all Codex versions. No live session logs are included in this repository.

After running the demo yourself, check its task-specific evidence:

```sh
python3 task_trace.py check --workspace /tmp/task-trace-demo/workspace
python3 examples/verify_demo.py /tmp/task-trace-demo/archive/TASK_ID
```

The tests exercise file changes/deletions, exact raw bytes, missing tool and subagent results, concurrency, idempotent hook installation, size/exclusion rules, HTML escaping, interrupted execution and integrity tampering. The fake CLI transport is explicitly a fixture, not evidence of live compatibility. `examples/make_demo.py` creates a separate live research exercise; it never reads existing conversations.

## Harbor conversion

`harbor_export.py` is an **independent CLI**. It reads the archive schema, without importing or calling the collector. Both tools share this repository to keep the interface documented together. Conversion does not run a model or execute captured code.

The initial target is **Harbor 0.25.0, task schema 1.4**, for archives from the collector’s `run` / `exec` route, with local-file tasks in a Python standard-library container. Hook-only archive conversion is not yet supported. It emits real multi-step tasks with stage-specific inputs, instructions, reference solutions, rubrics and verification. Arbitrary MCP servers, remote databases, scientific dependencies and original host environments are not inferred or recreated. Unsupported environment profiles fail explicitly. Image tags can change; choose a digest-qualified `environment.image` for repeatable deployment.

```sh
python3 harbor_export.py inspect --archive /private/path/to/ARCHIVE
python3 harbor_export.py init --archive /private/path/to/ARCHIVE --out recipe.json
# Review and complete recipe.json, then:
python3 harbor_export.py export --archive /private/path/to/ARCHIVE \
  --recipe recipe.json --out /private/path/to/export
HARBOR_TELEMETRY=off harbor run -p /private/path/to/export/task -a oracle
```

For the two-turn synthetic demo, `python3 examples/harbor/make_recipe.py --archive ARCHIVE --out recipe.json` supplies the proposed input mappings and rubrics. It intentionally leaves review fields and semantic assessments unfinished. Read the corresponding captured outputs before filling them. **The exporter refuses incomplete drafts and unassessed manual criteria on reference artifacts.**

A recipe selects, explicitly:

- `archive_head`: the exact closed capture to convert. Subsequent capture invalidates the recipe until reviewed again.
- `environment`: the supported profile, container image and a review of missing dependencies.
- `stages`: meaningful deliverable turns, in historical order. `source_run` is a `run_start` event sequence. Original prompt text is used for `instruction.mode = "historical"`; reconstructed instructions need `mode = "reconstructed"`, `text` and `reason`.
- `inputs`: a snapshot or attachment event, plus its destination under `/app`. For example: `{"destination":"data.csv","source":{"kind":"snapshot","seq":1,"path":"data.csv"}}`. The destination uses portable ASCII path segments; original source filenames can differ. A source captured after that turn cannot be used as its input. Later materials belong to later stages' `workdir/`.
- `outputs` and `reference_snapshot`: explicit deliverables from that turn's completed run, with a `reference_review` note. A future turn's final answer cannot silently replace an earlier answer. Historical outputs are not automatically gold answers.
- `rubric`: positive integer points per criterion, summing to `total_points`. Each criterion earns all its points or zero. `reward_strategy` selects Harbor's `mean` across stages or `final`; all stage scorecards are retained regardless. A phase mean is a reporting choice, not an extra measure of overall scientific quality.

Supported checks are `json_number` (absolute tolerance), `json_equal` (typed equality), `file_present` (nonempty delivery, **not** content correctness), and `manual` (an artifact-bound reviewed judgment). A criterion names an output file. Numerical checks additionally supply a JSON key/index `path`, an `expected` value and `abs_tol` for numbers. For example:

```json
{
  "id": "estimate",
  "points": 40,
  "description": "Standardized difference equals 41/1440 as a proportion.",
  "check": {
    "type": "json_number", "file": "results.json",
    "path": ["standardized_difference"],
    "expected": 0.02847222222222222, "abs_tol": 1e-10
  }
}
```

A `manual` criterion uses `{"type":"manual","file":"report.md"}`. Its trusted `rubric.assessments` entry must name `criterion_id`, the exact `artifact_sha256`, a Boolean `passed`, `reviewer`, `reviewer_role` (`curator` or `expert`), and an evidence-based `reason`. Identify model-assisted curation as such; do not label it expert acceptance. Multiple reviewed artifacts can be registered, with one verdict per criterion/hash. New artifact bytes require a new assessment: the verifier writes `scorecard.json` with `status: pending_review`, `score: null`, and emits **no numerical reward**. Harbor has no usable reward for that stage; this is not a score of zero. Harbor 0.25.0 can nevertheless display an empty aggregate as `mean: 0.0`. Use `python3 harbor_export.py scores --bundle EXPORT --trial HARBOR_TRIAL_DIRECTORY` to read the individual scorecards and preserve `pending_review`/`incomplete` as a null score. Do not treat a successful Harbor CLI exit or a bare aggregate mean as proof of a scored task. Truly missing required evidence receives zero for the corresponding criterion.

The bundle contains:

```text
export/
├── recipe.json              # Curated conversion decisions
├── provenance.json          # Source event/object hashes and generated file hashes
├── evidence/                # Optional private original capture, outside the task
└── task/
    ├── task.toml
    ├── environment/         # Python image + Docker network isolation
    ├── tests/grade.py       # Shared grader
    └── steps/<stage>/
        ├── instruction.md
        ├── workdir/         # Only the inputs introduced at this stage
        ├── solution/        # Reviewed reference artifacts + solve.sh
        └── tests/           # Rubric + verifier entrypoint
```

Add `--include-trace` to preserve a **private** evidence copy: original event log, task/report metadata and all verified referenced objects (including native sessions and CLI streams). Duplicate raw files and HTML are omitted; source object bytes remain unchanged. No original machine paths are followed to recover missing inputs. This flag does not redact or authorize sharing. Do not publish generated bundles automatically, even when the converter itself is public.

Harbor receives only `export/task`. Instructions, late-stage materials, references and grading files stay outside the agent image. Each stage gets its inputs when it starts; the filesystem persists. A fresh, separate verifier receives only declared output artifacts and trusted tests. The grader parses data and hashes files; it never imports or executes submitted code. Docker Compose enforces `network_mode: none` in this profile. `oracle` materializes references; it does not prove a new model can solve the task, nor does a presence check prove its code is correct.

The source head and generated-file manifest support auditing; they are not signatures. Input timestamp checks do not detect answers manually leaked in reconstructed prose: task boundaries and wording still need review. Export success means files were constructed, **not** that Harbor ran. Run the reference and negative controls before treating a bundle as runnable.

Harbor normally starts a fresh conversation for each stage while retaining files. Use `--resume-trajectory` only with an agent that supports it when conversational continuity is part of the evaluation. This offline profile does not configure model-service credentials or networking for in-container Codex. See the official [multi-step format](https://github.com/harbor-framework/harbor/blob/main/docs-mintlify/tasks/multi-step.mdx) and [separate verifier](https://github.com/harbor-framework/harbor/blob/main/docs-mintlify/tasks/separate-verifier.mdx).

The export tests cover source integrity, input timing, per-turn reference boundaries, answer/input separation, incomplete reviews, binary point totals, numerical edge cases, private trace separation and null-safe score summaries. The synthetic demo's code-presence item is deliberately limited to delivery; it does not award correctness for arbitrary submitted code. Semantic reference judgments in the validation exercise are model-assisted curator assessments, not expert labels.

Live Harbor 0.25.0 / Docker validation on 2026-11-03 checked the exact generated task-file hashes: reference artifacts scored 100/100 in each stage; deliberately wrong numerical outputs scored 80/100 and 60/100 (mean reward 0.7); empty submissions scored zero; a new unreviewed report produced a null score and stopped before stage 2. The `scores` command preserved that pending status despite Harbor's empty aggregate mean. All 34 local collector/converter tests passed on Python 3.9 and 3.13. These are synthetic packaging/scoring tests, not evidence of benchmark difficulty or general environment recovery.

MIT licensed. No vendor affiliation or endorsement is implied.
