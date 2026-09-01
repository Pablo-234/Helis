# HELIS on Windows Task Scheduler

These scripts register two bounded, current-user tasks without administrator elevation:

- `HELIS Discovery` invokes every 15 minutes and retains HELIS's one-hour discovery due gate;
- `HELIS Scheduler` invokes every five minutes and retains HELIS's 15-minute portfolio due gate.

The task actions contain repository and environment-file paths, never environment values. The
trusted environment loader parses `NAME=VALUE` lines literally and never evaluates the environment
file as PowerShell. Run it directly to import the same configuration into the current PowerShell
process before interactive HELIS commands.

From Windows PowerShell in the repository:

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.config\helis" | Out-Null
Copy-Item deploy\helis.env.example "$env:USERPROFILE\.config\helis\helis.env"
helis-live bootstrap
.\deploy\windows\Import-HelisEnv.ps1
helis-live model-status
.\deploy\windows\Register-HelisTasks.ps1
helis-live doctor --probe-model
```

The default paths are `%USERPROFILE%\Helis` and
`%USERPROFILE%\.config\helis\helis.env`. Pass `-RepoRoot` or `-EnvFile` to the registration script
when using another layout. Existing tasks are preserved unless `-Replace` is supplied explicitly.

The tasks use the current interactive user at the limited run level, so they do not store a Windows
password and do not run while that user is logged out. Logs are written to `.helis\discovery.log`
and `.helis\scheduler.log`.
