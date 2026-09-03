# HELIS on Windows Task Scheduler

These scripts register two bounded, current-user tasks without administrator elevation:

- `HELIS Discovery` invokes every 15 minutes and retains HELIS's one-hour discovery due gate;
- `HELIS Scheduler` invokes every five minutes and retains HELIS's 15-minute portfolio due gate.

The task actions contain repository and environment-file paths, never environment values. The
trusted environment loader parses `NAME=VALUE` lines literally and never evaluates the environment
file as PowerShell. Run it directly to import the same configuration into the current PowerShell
process before interactive HELIS commands.

Run the controlled-pilot script before registering recurring tasks. It stops on the first blocked
inventory, smoke test, readiness check or pilot failure. The required confirmation switch permits
only configured public market reads and calls to the credential-free local model; the underlying
pilot still uses zero cash and receives no external-write gateways. The script never installs
software, downloads a model or registers scheduled tasks.

From Windows PowerShell in the repository:

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.config\helis" | Out-Null
Copy-Item deploy\helis.env.example "$env:USERPROFILE\.config\helis\helis.env"
.\deploy\windows\Start-HelisControlledPilot.ps1 -ConfirmPublicNetworkReads
.\deploy\windows\Start-HelisLive.ps1 -ConfirmLiveOperations
helis-live doctor --probe-model
```

Install a current-user desktop shortcut for the local read-only owner dashboard:

```powershell
.\deploy\windows\Install-HelisDashboardShortcut.ps1
```

The shortcut is created in the actual Windows desktop directory returned by the operating system
(including a redirected OneDrive desktop). It stores only the repository path and localhost port,
never credentials. Double-clicking it runs `Start-HelisDashboard.ps1`, opens
`http://127.0.0.1:8765` and keeps a console window available for status and `Ctrl+C`. An existing
shortcut is preserved unless `-Replace` is supplied explicitly.

The default paths are `%USERPROFILE%\Helis` and
`%USERPROFILE%\.config\helis\helis.env`. Pass `-RepoRoot` or `-EnvFile` to the launcher or
registration script when using another layout. Existing tasks are preserved unless `-Replace` or
the launcher's `-ReplaceTasks` is supplied explicitly.

`Start-HelisLive.ps1` is the strict live path after external validation and all five downstream live
adapter slots have been configured. It performs bootstrap, local-model inventory and smoke checks,
an external-write-disabled controlled pilot, configuration-only live activation, both health
checks, and task registration. Tasks are registered disabled and enabled only after a second
activation check confirms both schedule entries. Any failure in between leaves both tasks disabled.
Use `-ReplaceTasks` only to intentionally replace an existing pair.

The activation check does not call Vercel, Brave, Resend or Stripe and therefore cannot prove that
a provider has not revoked a credential. It checks configuration shape, safe destinations and the
Vercel CLI executable; actual provider failures are recorded by the bounded wake logs. The launcher
does not approve publication, contact or checkout work.

Pause live operation without deleting tasks or venture state:

```powershell
Disable-ScheduledTask -TaskName "HELIS Discovery", "HELIS Scheduler"
```

The tasks use the current interactive user at the limited run level, so they do not store a Windows
password and do not run while that user is logged out. Logs are written to `.helis\discovery.log`
and `.helis\scheduler.log`.
