# ONAY Web Control UI

Browser dashboard for the installation, served by the Web Server DAT inside `TOX/UI.tox`.
Shows camera connection status + live previews, monitor/window info, performance,
and lets you edit `table_settings` — all from `http://127.0.0.1:9980`.

Everything lives on disk, so the HTML/JS/CSS in `UI/web/` can be edited without
touching TouchDesigner (just refresh the browser).

## One-time hookup inside `TOX/UI.tox`

The callbacks live in `DAT/UIExt.py` (the file the callbacks DAT is already
synced to). Remaining steps:

1. **Callbacks DAT**: right-click → *Reload File* (picks up the new code)
2. **webserver1**:
   - `Port` = `9980`
   - `Callbacks DAT` → the synced text DAT
   - `Active` = On
3. On the **UI component itself** (the COMP that holds them):
   - Set **Global OP Shortcut** = `UI`
     (lets `StartupExt.OpenUI()` read the port; falls back to 9980 without it)
4. **Re-save `TOX/UI.tox`** (right-click the comp → *Save Component .tox*).

`DAT/StartupExt.py` now opens the browser automatically ~2s after the project
loads. Delete the `self.OpenUI()` line in `Startup()` if you don't want that.

## What the dashboard shows

- **Cameras** — every Video Device In TOP in the project is auto-discovered.
  Green dot = active + signal, amber = active but no frames, grey = inactive.
  Live JPEG preview (toggle off to save GPU), device name, resolution,
  and any TD errors on the operator. Active/Inactive button toggles the TOP.
- **Displays & Windows** — all physical monitors (resolution, position, Hz,
  primary) and every Window COMP with Open/Close buttons — handy for the
  two-display setup.
- **Settings** — the `table_settings` row for the current `NODE` (WF).
  Save writes the cells, saves the `.tsv` back to disk if the table is
  file-backed, and re-runs `SETTINGS.Startup()` to apply.
- **Header** — actual FPS vs target, and a Perform Mode toggle.

## API (for extending)

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/status` | GET | full JSON snapshot (cameras/monitors/windows/settings/perf) |
| `/api/cam?path=/path/to/top` | GET | JPEG snapshot of any TOP |
| `/api/action` | POST | `{action, ...}` — `cam_active`, `open_window`, `close_window`, `perform_mode`, `set_settings`, `reload_settings` |

To add a control: add a case in `_do_action()` in `DAT/UIExt.py`,
then call `act({action: '...'})` from `web/app.js`. The callbacks file is
synced, so saving it on disk updates TD immediately.

## Phone access

The server listens on all interfaces, so on the same network you can open
`http://<pc-ip>:9980` from a phone/tablet (allow TouchDesigner through the
Windows firewall when prompted).
