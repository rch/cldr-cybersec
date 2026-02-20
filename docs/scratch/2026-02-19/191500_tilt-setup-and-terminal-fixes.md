# Tilt Setup + Terminal Rendering Fixes

## Tilt for RKE2 Iteration

### Files Created
- `Tiltfile` — single `custom_build` with `live_update` + `fall_back_on`
- `tilt/panel-viz-dev.yaml` — deployment overlay with `--autoreload`
- `tilt/engine-dev.yaml` — deployment overlay
- `tilt/build-and-push.sh` — podman build + push to Zarf registry

### Key Discoveries

**Live update works with shared images.** The original exit code 2 was a timing issue
(pod mid-rollout), not caused by the otel-navigator and pty-proxy sidecars sharing
the same image. The `fall_back_on()` directive ensures Dockerfile/requirements changes
trigger full rebuilds.

**`watchfiles` required for Panel autoreload.** Added to `requirements-airgap.txt`.
Without it, `--autoreload` doesn't detect file changes.

**`kubectl cp` doesn't fire inotify reliably.** Tilt's file sync uses `tar` extraction
which doesn't always trigger `watchfiles`' inotify watcher. Fix: `run('touch ...')`
step after `sync()` in live_update.

**Panel autoreload only affects connected sessions.** When no browser is connected,
the file change is detected but nothing visible happens (no process restart).

### Usage
```bash
KUBECONFIG=~/.kube/rke2.yaml tilt up
# Open http://localhost:5006/otel-navigator in browser
# Edit zarf/images/otel-navigator.py → auto-syncs + reloads in ~5s
```

## Terminal Rendering Fixes (GhosttyTerminal)

### Changes to `zarf/images/otel-navigator.py`

1. **Clear screen before connecting WebSocket** — `term.write('\x1b[2J\x1b[H')` after
   `fitTerminal()` but before `connect()` eliminates canvas artifacts from pre-layout
   rendering (the garbled `WWWWW` on line 0).

2. **Double-RAF timing** — `requestAnimationFrame` chained twice ensures the browser
   has completed layout + paint after `term.open()` before reading cell dimensions.

3. **Cursor blink deferred** — `cursorBlink: false` on init, enabled on `ws.onopen`,
   prevents cursor flicker during initialization.

4. **Font simplified** — Removed `JetBrains Mono` (may not be available), using
   `Menlo, Monaco, "Courier New", monospace` for reliable rendering.

5. **Container CSS** — Added `overflow:hidden; position:relative` to `terminal_container`,
   `.xterm-screen` width 100%.

6. **ResizeObserver debounced** — 50ms debounce prevents excessive resize calls.

7. **Reconnect clears screen** — `term.write('\x1b[2J\x1b[H')` before reconnecting
   ensures clean state for new welcome banner.
