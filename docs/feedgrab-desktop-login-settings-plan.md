# Feedgrab Desktop Login And Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or direct TDD execution task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the approved desktop login/status/import flow and a schema-driven settings page for feedgrab Desktop.

**Architecture:** Keep CLI behavior and session file formats unchanged. Platform settings are persisted to a desktop JSON file and projected into worker environment variables. In packaged builds, the default `FEEDGRAB_DATA_DIR` points to installer-root `sessions`; users can override it from the settings page.

**Tech Stack:** Electron, Vite, React, TypeScript, Python sidecar worker, pytest, Vitest.

---

### Task 1: Login Status And Session Import

**Files:**
- Modify: `feedgrab/service/login.py`
- Modify: `feedgrab/worker.py`
- Test: `tests/test_service_desktop.py`
- Test: `tests/test_worker_protocol.py`

- [ ] Add tests for platform-aware status, optional/no-login platforms, X multi-cookie files, and installer session import candidates.
- [ ] Implement platform metadata and session candidate detection without changing existing cookie/session formats.
- [ ] Add worker methods for login status refresh payload and session import.
- [ ] Run targeted pytest.

### Task 2: Electron Login Actions

**Files:**
- Modify: `desktop/electron/ipc-types.ts`
- Modify: `desktop/electron/preload.cts`
- Modify: `desktop/electron/main.ts`
- Modify: `desktop/electron/python-worker.ts`
- Modify: `desktop/electron/runtime.ts`
- Test: `desktop/tests/runtime.test.ts`
- Test: `desktop/tests/python-worker.test.ts`

- [ ] Add tests for install-session directory env and login/import IPC mapping.
- [ ] Add typed IPC methods for status refresh, import, and single-platform login.
- [ ] Run login through a separate child process to avoid JSONL stdout pollution.
- [ ] Run desktop unit tests/typecheck.

### Task 3: Platform Settings Schema

**Files:**
- Create: `feedgrab/service/platform_settings.py`
- Modify: `feedgrab/service/settings.py`
- Modify: `feedgrab/worker.py`
- Test: `tests/test_service_desktop.py`
- Test: `tests/test_worker_protocol.py`

- [ ] Add schema/snapshot/update tests for toggle, number, select, path, and secret fields.
- [ ] Persist desktop settings to `FEEDGRAB_SETTINGS_PATH`.
- [ ] Apply settings to `os.environ` while keeping secrets redacted in snapshots.
- [ ] Run targeted pytest.

### Task 4: React Login And Settings UI

**Files:**
- Modify: `desktop/renderer/src/App.tsx`
- Modify: `desktop/renderer/src/styles.css`
- Modify: `desktop/renderer/src/state/appReducer.ts`
- Modify: `desktop/electron/ipc-types.ts`
- Test: `desktop/tests/App.test.tsx`

- [ ] Add tests for login refresh/import buttons and settings tab rendering.
- [ ] Implement login page buttons and per-platform status copy.
- [ ] Implement settings `基础设置 / 平台设置` tabs using schema controls.
- [ ] Run Vitest and build/typecheck.

### Task 5: Verification And Docs

**Files:**
- Modify: `feedgrab-desktop-readme.md`
- Modify: `docs/feedgrab-desktop-packaging.md`
- Modify: `DEVLOG.md` if the implementation is committed as a release-worthy change.

- [ ] Run Python targeted tests.
- [ ] Run desktop tests, lint, and build.
- [ ] Start or smoke-check the desktop app where feasible.
- [ ] Update user-facing docs for session directory, installer `sessions` import, and platform settings.
