# HELIS operations

This guide turns HELIS into a host-managed autonomous process without an unbounded resident agent loop. The host periodically invokes two independent bounded control loops:

1. `helis-discovery wake` — scans configured market sources and advances the resumable business-brain cycle;
2. `helis-scheduler wake` — reconciles the funded portfolio and advances bounded eligible venture work.

Each loop has its own durable due interval and expiring singleton lease. A stuck market source therefore cannot become the portfolio scheduler's lock, and a busy portfolio tick cannot prevent fresh market discovery.

## Recommended layouts

### Windows

```text
%USERPROFILE%\Helis\                         repository + virtualenv
%USERPROFILE%\Helis\helis.db                durable SQLite state
%USERPROFILE%\Helis\helis.toml              market source configuration
%USERPROFILE%\Helis\.helis\workspaces\      generated venture workspaces
%USERPROFILE%\.config\helis\helis.env       model/gateway configuration
Windows Task Scheduler                       current-user wake tasks
```

### Linux

```text
~/Helis/                         repository + virtualenv
~/Helis/helis.db                durable SQLite state
~/Helis/helis.toml              market source configuration
~/Helis/.helis/workspaces/      generated venture workspaces
~/Helis/.helis/self-improvement/ isolated self-improvement candidates
~/.config/helis/helis.env       model/gateway configuration
~/.config/systemd/user/         user systemd units
```

If the repository lives elsewhere, edit `WorkingDirectory`, `ExecStart` and `ReadWritePaths` in the service files.

## 1. Install HELIS

### Windows PowerShell

```powershell
git clone https://github.com/Pablo-234/Helis.git "$env:USERPROFILE\Helis"
Set-Location "$env:USERPROFILE\Helis"
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
New-Item -ItemType Directory -Force "$env:USERPROFILE\.config\helis" | Out-Null
Copy-Item deploy\helis.env.example "$env:USERPROFILE\.config\helis\helis.env"
helis-live bootstrap
.\deploy\windows\Import-HelisEnv.ps1
helis-discovery health
helis-scheduler health
```

HELIS requires Python 3.11 or newer. Edit the copied environment file before enabling optional
gateways. The default local model settings already match the zero-spend pilot commands, so the
initial preflight does not require secrets or importing the file into the interactive shell.

### Linux

```bash
git clone https://github.com/Pablo-234/Helis.git ~/Helis
cd ~/Helis
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
mkdir -p ~/.config/helis .helis/workspaces .helis/self-improvement
cp deploy/helis.env.example ~/.config/helis/helis.env
chmod 600 ~/.config/helis/helis.env
```

Edit `~/.config/helis/helis.env`. The default LLM target is the local OpenAI-compatible endpoint `http://localhost:11434/v1` using `qwen3.5:9b`. External gateways remain separately operator-configured.

Run both zero-side-effect preflights:

```bash
cd ~/Helis
source .venv/bin/activate
set -a
. ~/.config/helis/helis.env
set +a
helis-discovery health
helis-scheduler health
```

Neither health command scans the network, calls the model, contacts a customer, publishes, spends money or mutates a venture.

### First controlled pilot

Before enabling timers or external gateways, prepare and inspect one safe local run:

```bash
helis-live bootstrap
helis-live model-status
helis-live model-smoke
helis-live doctor --probe-model
helis-live pilot
helis-live pilot-status
helis-operator inbox
```

`model-status`, `model-smoke` and `doctor` return a nonzero process exit code whenever their
required check is blocked. They can therefore gate scripts without parsing human-readable output.

`bootstrap` creates missing directories, the SQLite schema and a conservative Hacker News source configuration. Existing configuration and database files are preserved. Every invocation is audited.

`doctor` checks the source file, writable state paths, local model configuration, optional Docker sandbox, reference timers and gateway configuration. By default it performs no network calls. `--probe-model` makes one uncredentialed `GET /models` request to localhost and never asks for a completion.

`model-status` performs the same metadata-only inventory check in a focused form. It distinguishes:

- `endpoint_down` — start the installed runtime with `ollama serve`, or install Ollama from [the official download page](https://ollama.com/download);
- `incompatible` — the endpoint answered but did not expose the expected OpenAI-compatible model inventory;
- `model_missing` — fetch the exact configured model with `ollama pull <HELIS_LLM_MODEL>`;
- `ready` — the exact model appears in the OpenAI-compatible `/models` inventory.

The default `qwen3.5:9b` tag and its normal `ollama run qwen3.5:9b` command are documented in the [official Ollama model library](https://ollama.com/library/qwen3.5:9b). HELIS never installs Ollama, starts a daemon or downloads a multi-gigabyte model implicitly. Those machine-level actions remain explicit operator commands.

Once inventory is ready, `model-smoke` sends exactly one local chat-completion request with `max_tokens=96`, no credential and no configured token price. It requires the model to return `{"status":"ok"}` as valid JSON. This catches a model that is installed but incompatible with HELIS before a longer pilot consumes time.

The controlled pilot fails closed unless all of the following remain true:

- the model endpoint is localhost;
- no model API credential is configured;
- configured input/output token prices and pilot cash are zero;
- the market configuration parses and has an enabled source;
- database and workspace paths are writable;
- external validation, contact, publication, checkout and deployment gateways are not passed into the pilot runtime.

The pilot may read the configured public sources, call the local model and persist normal HELIS lifecycle state. It runs the real bounded online-venture operator, not a simulated business result. Its durable report includes discovery counts, funded ventures, stop reason, blockers and the current operator inbox. The report can be recovered after a terminal disconnect with `helis-live pilot-status`.

## 2. Recommended on Windows: Task Scheduler

From Windows PowerShell in the installed repository:

```powershell
.\.venv\Scripts\Activate.ps1
.\deploy\windows\Start-HelisControlledPilot.ps1 -ConfirmPublicNetworkReads
.\deploy\windows\Register-HelisTasks.ps1
helis-live doctor --probe-model
Get-ScheduledTask -TaskName "HELIS Discovery", "HELIS Scheduler"
```

Run the controlled pilot before registration. It performs the fail-closed sequence `bootstrap` →
`model-status` → `model-smoke` → `doctor --probe-model` → `pilot` → `pilot-status` → operator
inbox and stops at the first nonzero exit code. `-ConfirmPublicNetworkReads` explicitly authorizes
the pilot's configured public-source reads and local-model calls; it does not enable contact,
publication, payment, deployment or scheduled-task creation.

The registration script creates `HELIS Discovery` at a 15-minute host cadence and
`HELIS Scheduler` at a five-minute host cadence. Their fixed wake commands preserve the same internal
one-hour discovery and 15-minute portfolio due gates used on Linux. Overlapping invocations are
ignored and each process keeps a bounded execution limit.

Both tasks run as the current interactive user with `Limited` privileges. No administrator
elevation, Windows password, model credential or gateway token is placed in the task definition.
The action contains only the trusted script path, mode, repository path and environment-file path;
`Import-HelisEnv.ps1` parses values literally without evaluating the environment file as
PowerShell. The wake script and interactive setup use that same parser. The tasks therefore run
only while that user is logged on. Windows battery and sleep policy still applies.

### Live adapter configuration

The recurring scheduler uses the same provider selection as the manual online-venture autopilot.
When configured, it can route approved work through:

- Vercel CLI for an immutable reviewed preview;
- Brave Search for read-only public prospect discovery;
- Resend for an approved exact-recipient email and observed replies;
- Stripe for an approved exact-price Payment Link and read-only paid-session polling.

Copy only the credentials you actually intend to enable into `helis.env`, then import the file and
inspect adapter selection without contacting any provider:

```powershell
.\deploy\windows\Import-HelisEnv.ps1
helis-live selected
helis-scheduler health
```

Merely configuring an adapter does not approve its side effect. Publication, checkout creation and
first contact remain separate persisted approvals, and every task wake uses those existing gates.
The scheduler may autonomously perform public research and poll the result of a previously approved
action, but it cannot manufacture approval from an API key.

Registration preserves any task with the same name. Use `-Replace` only when intentionally
updating existing HELIS tasks:

```powershell
.\deploy\windows\Register-HelisTasks.ps1 -Replace
```

Logs are written to `.helis\discovery.log` and `.helis\scheduler.log`. `helis-live doctor` queries
task presence read-only through `schtasks.exe`; it does not register, start, stop or modify a task.

## 3. Recommended on Linux: systemd user timers

Install both reference timer pairs:

```bash
mkdir -p ~/.config/systemd/user
cp ~/Helis/deploy/systemd/helis-discovery.service ~/.config/systemd/user/
cp ~/Helis/deploy/systemd/helis-discovery.timer ~/.config/systemd/user/
cp ~/Helis/deploy/systemd/helis-scheduler.service ~/.config/systemd/user/
cp ~/Helis/deploy/systemd/helis-scheduler.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now helis-discovery.timer helis-scheduler.timer
```

### Market discovery cadence

The discovery timer invokes the oneshot roughly every 15 minutes, while HELIS itself enforces:

```text
helis-discovery wake
  --minimum-interval-seconds 3600
  --lease-seconds 900
  --observation-limit 100
  --candidate-limit 5
  --max-model-calls 8
  --max-tokens 40000
  --max-cost-cents 25
```

So a normal full market scan occurs at most once per hour. More frequent host invocations provide crash recovery after an expired lease without multiplying normal source traffic. Source adapters are isolated individually; one failing feed is recorded while healthy sources can still contribute observations.

After scanning, HELIS always invokes one bounded resumable `HelisCycle`. This matters after a crash: existing unprocessed observations or a pending discovered/evaluated venture can continue even when the latest source scan contains nothing new. When there is genuinely no work, the cycle makes zero model calls.

### Portfolio execution cadence

The scheduler timer invokes roughly every five minutes, while HELIS itself enforces:

```text
helis-scheduler wake
  --minimum-interval-seconds 900
  --lease-seconds 600
  --max-advances 2
```

Frequent host wakeups therefore do not imply frequent work. The scheduler still enforces venture resource envelopes, approvals, cash commitments and adaptive GTM backoff.

Useful checks:

```bash
systemctl --user status helis-discovery.timer helis-scheduler.timer
systemctl --user list-timers 'helis-*'
journalctl --user -u helis-discovery.service -n 100 --no-pager
journalctl --user -u helis-scheduler.service -n 100 --no-pager
helis-discovery health
helis-scheduler wake-status
helis-scheduler status
helis-scheduler health
```

## Operator inbox

The unified inbox is a read-only view over every unresolved validation, preview publication, checkout, first-contact outreach, self-improvement and venture capability request:

```bash
helis-operator inbox
helis-operator inbox --json
helis-operator show <KEY>
```

`show` displays the exact consequence, immutable binding, current snapshot token and next command. Decide only the current snapshot:

```bash
helis-operator approve <KEY> --confirm <TOKEN>
helis-operator reject <KEY> --confirm <TOKEN> --reason "<audited reason>"
```

Approval never calls an external gateway. It delegates to the existing domain-specific approval gate and leaves execution to the scheduler. Rejection cancels the exact waiting run and persists the reason. A changed request gets a different token, so a stale approval or rejection command fails closed. Human, deterministic and external-service capability results appear as `input` items and point to the existing `helis-agent supply-capability-result` command; they cannot be mistaken for approvals.

To keep user timers running while logged out on systems using systemd-logind:

```bash
loginctl enable-linger "$USER"
```

Enable lingering deliberately; it changes host session behavior and is not hidden inside an install script.

## 4. Cron fallback

If user systemd is unavailable, install the reference lines from:

- `deploy/cron/helis-discovery.cron.example`
- `deploy/cron/helis-scheduler.cron.example`

```bash
crontab -e
```

Both examples explicitly source `~/.config/helis/helis.env` because cron does not understand systemd `EnvironmentFile=`. The discovery line invokes every 15 minutes but keeps its internal one-hour scan gate; the scheduler line invokes every five minutes but keeps its internal 15-minute work gate.

## 5. Restart and crash behavior

HELIS is intentionally restart-safe:

- durable observations, ventures, GTM, portfolio, envelope, wake and backoff state live in SQLite;
- discovery and portfolio scheduling use separate expiring leases;
- unprocessed observations survive a model-budget exhaustion or process crash;
- duplicate source observations are idempotent through deterministic observation IDs/store inserts;
- duplicate external dispatches use persisted idempotency keys;
- open cash commitments block unsafe envelope rollover;
- reviewed preview bytes remain hash-locked;
- GTM outcomes refresh before capital reallocation;
- adaptive no-op cooldowns are fingerprint-bound and reset after relevant state changes;
- controlled self-improvement candidates remain hash-bound across evaluation, review branch, CI and final merge gates.

A reboot requires no in-memory agent process to survive. The next timer/cron invocation reconstructs each control loop from durable state.

## 6. Logs and state

Systemd logs go to the user journal. Cron fallback logs to:

```text
~/Helis/.helis/discovery.log
~/Helis/.helis/scheduler.log
```

Windows Task Scheduler uses the same two repository-local log filenames under
`%USERPROFILE%\Helis\.helis\`.

SQLite is the authoritative operational state. Back it up while no write is in progress, or use a SQLite-aware backup procedure. Workspace files alone cannot reconstruct approvals, cash reservations, revenue attribution, wake leases or idempotency state.

## 7. Security boundaries that remain in force

Running HELIS from timers does not increase its authority:

- market scanning is `NETWORK_READ` and is still policy-evaluated per configured source;
- external contact still requires persisted run-scoped approval by default;
- publication keeps its approval/hash boundary;
- cash reservations authorize capacity but do not themselves perform payment;
- the model cannot choose gateway destinations or credentials;
- killed/paused ventures receive no fresh portfolio allocation;
- scheduler/discovery leases cannot manufacture approval or resource capacity;
- self-improvement requires isolated candidate evaluation, explicit review-branch approval, green exact-branch CI, a second merge approval and fresh pre-merge attestation.

The supplied systemd services also use restrictive umasks, `NoNewPrivileges`, private `/tmp`, a read-only home view and one explicit writable HELIS repository path.

## 8. Updating HELIS

Stop both automatic wake sources before changing installed code:

```bash
systemctl --user stop helis-discovery.timer helis-scheduler.timer
cd ~/Helis
git pull
source .venv/bin/activate
pip install -e .
helis-discovery health
helis-scheduler health
systemctl --user start helis-discovery.timer helis-scheduler.timer
```

For development, keep using branches + CI rather than editing the live checkout underneath an executing wake.

On Windows, disable both tasks in Task Scheduler before updating the live checkout, reinstall the
editable package, run both health commands, then enable them again. Re-run the registration script
with `-Replace` only when the reference task definitions changed.
