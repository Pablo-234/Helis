# HELIS operations

This guide turns the bounded scheduler into a host-managed autonomous process. HELIS itself does **not** run an unbounded resident loop. The host wakes it periodically; HELIS decides whether work is due, acquires a crash-safe lease, reconciles portfolio state and advances only bounded eligible work.

## Recommended Linux layout

The reference service assumes:

```text
~/Helis/                         repository + virtualenv
~/Helis/helis.db                durable SQLite state
~/Helis/.helis/workspaces/      generated venture workspaces
~/.config/helis/helis.env       model/gateway configuration
~/.config/systemd/user/         user systemd units
```

If the repository lives elsewhere, edit `WorkingDirectory`, `ExecStart` and `ReadWritePaths` in the service file.

## 1. Install HELIS

```bash
git clone https://github.com/Pablo-234/Helis.git ~/Helis
cd ~/Helis
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
mkdir -p ~/.config/helis .helis/workspaces
cp deploy/helis.env.example ~/.config/helis/helis.env
chmod 600 ~/.config/helis/helis.env
```

Edit `~/.config/helis/helis.env`. The default LLM target is the local OpenAI-compatible endpoint `http://localhost:11434/v1` using `qwen3.5:9b`. External validation, prospect and contact gateways are optional and remain separately operator-configured.

Run the zero-side-effect preflight:

```bash
cd ~/Helis
source .venv/bin/activate
set -a
. ~/.config/helis/helis.env
set +a
helis-scheduler health
```

`health` does not call a model, gateway, customer, or payment system.

## 2. Recommended: systemd user timer

Install the reference units:

```bash
mkdir -p ~/.config/systemd/user
cp ~/Helis/deploy/systemd/helis-scheduler.service ~/.config/systemd/user/
cp ~/Helis/deploy/systemd/helis-scheduler.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now helis-scheduler.timer
```

The timer invokes the host service roughly every five minutes. The service itself calls:

```text
helis-scheduler wake
  --minimum-interval-seconds 900
  --lease-seconds 600
  --max-advances 2
```

So frequent host wakeups do **not** imply frequent work. HELIS enforces its own 15-minute minimum attempt interval, singleton lease, per-tick advance cap, venture resource envelopes, approval gates and adaptive GTM backoff.

Useful checks:

```bash
systemctl --user status helis-scheduler.timer
systemctl --user list-timers helis-scheduler.timer
journalctl --user -u helis-scheduler.service -n 100 --no-pager
helis-scheduler wake-status
helis-scheduler status
helis-scheduler health
```

To keep a user timer running while the user is logged out, Linux distributions using systemd-logind can enable lingering:

```bash
loginctl enable-linger "$USER"
```

This changes host session behavior, so enable it deliberately rather than hiding it inside an install script.

## 3. Cron fallback

If user systemd is unavailable, use the reference line from `deploy/cron/helis-scheduler.cron.example`.

```bash
crontab -e
```

The cron example explicitly sources `~/.config/helis/helis.env`, creates the log directory and invokes `helis-scheduler wake` every five minutes. HELIS still enforces its internal wake interval and lease, so an overlapping cron invocation cannot legitimately become a second scheduler worker.

## 4. Restart and crash behavior

The scheduler is intentionally restart-safe:

- durable venture, GTM, portfolio, envelope, wake and backoff state live in SQLite;
- a scheduler lease expires after its TTL if a process dies;
- duplicate external dispatches use persisted idempotency keys;
- open cash commitments block unsafe envelope rollover;
- reviewed preview bytes remain hash-locked;
- GTM outcomes are refreshed before capital reallocation;
- adaptive no-op cooldowns are fingerprint-bound and reset immediately after relevant state changes.

A machine reboot therefore requires no in-memory agent process to survive. The next timer/cron invocation reconstructs the control loop from durable state.

## 5. Logs and state

Systemd logs go to the user journal. Cron fallback logs to `~/Helis/.helis/scheduler.log`.

The SQLite database is the authoritative operational state. Back it up while no write is in progress, or use a SQLite-aware backup procedure. Do not copy only workspace files and assume HELIS can reconstruct approvals, cash reservations, revenue attribution or idempotency state from them.

## 6. Security boundaries that remain in force

Running HELIS from a timer does not increase its authority:

- external contact still requires a persisted run-scoped approval by default;
- publication still requires its existing approval/hash boundary;
- cash reservations authorize capacity but do not themselves perform payment;
- the model cannot choose gateway destinations or credentials;
- killed/paused ventures do not receive fresh portfolio allocation;
- scheduler cooldowns cannot manufacture approval or resource capacity;
- self-modification is still prohibited from silently changing the live process.

The supplied systemd unit also uses a restrictive umask, `NoNewPrivileges`, a private `/tmp`, a read-only home view and a single explicit writable HELIS repository path.

## 7. Updating HELIS

Stop automatic wakes before changing the installed code:

```bash
systemctl --user stop helis-scheduler.timer
cd ~/Helis
git pull
source .venv/bin/activate
pip install -e .
helis-scheduler health
systemctl --user start helis-scheduler.timer
```

For development, keep using branches + CI rather than editing the live checkout underneath an executing scheduler wake.
