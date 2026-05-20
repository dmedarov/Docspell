# macOS LaunchAgents for Docspell

Three scheduled tasks that automate routine maintenance:

| Plist | When | What |
| --- | --- | --- |
| `net.medarov.docspell.verify.plist`       | Daily 09:00      | `verify_docspell.py --brief` → health-log |
| `net.medarov.docspell.auto-confirm.plist` | Daily 10:00 + 22:00 | `auto_confirm.py --apply` drains inbox |
| `net.medarov.docspell.backup.plist`       | Sunday 03:00     | `dsc export` to `~/Backups/docspell/<date>/` (keeps last 12 snapshots) |

## Install (one-time)

```bash
cd ~/CODING/Docspell

# Backup needs dsc — install once
brew install dsc

# Ensure Docspell + Gmail passwords are stored in keychain (run if not done)
security add-generic-password -s docspell        -a library/dmedarov         -w
security add-generic-password -s docspell-gmail  -a damian.medarov@gmail.com -w

# Copy all three plists into ~/Library/LaunchAgents
cp launchd/*.plist ~/Library/LaunchAgents/

# Tell launchd about them (one bootstrap per plist)
for f in ~/Library/LaunchAgents/net.medarov.docspell.*.plist; do
  launchctl bootstrap gui/$(id -u) "$f"
done

# Verify they're loaded
launchctl list | grep medarov.docspell
```

## Run immediately (without waiting for schedule)

```bash
launchctl kickstart -k gui/$(id -u)/net.medarov.docspell.verify
launchctl kickstart -k gui/$(id -u)/net.medarov.docspell.auto-confirm
launchctl kickstart -k gui/$(id -u)/net.medarov.docspell.backup
```

## Watch the logs

```bash
tail -f ~/Library/Logs/docspell-verify.log
tail -f ~/Library/Logs/docspell-auto-confirm.log
tail -f ~/Library/Logs/docspell-backup.log
```

## Disable / uninstall

```bash
for f in ~/Library/LaunchAgents/net.medarov.docspell.*.plist; do
  launchctl bootout gui/$(id -u) "$f"
  rm "$f"
done
```

## Notes

- All three plists read the Docspell password from keychain at runtime —
  no secrets are stored in the .plist files themselves.
- `RunAtLoad = false` everywhere — agents fire only on their schedule,
  not when the Mac wakes from sleep.
- If the Mac is asleep at the scheduled time, launchd runs the job at
  the next wake event.
- Python path is `/opt/homebrew/bin/python3` (Apple Silicon homebrew).
  If you use a different python (system, pyenv, …), edit each plist's
  `ProgramArguments` accordingly.
- The backup retention keeps the 12 most recent dated subdirectories
  (≈ 3 months of weekly snapshots). Adjust the `tail -n +13` to taste.
