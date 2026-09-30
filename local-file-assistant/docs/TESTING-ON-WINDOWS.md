# Checking the app on Windows

The backend suite and the built UI are tested automatically (and in CI on Windows once the
code is on GitHub). These steps cover what only a real Windows desktop can show. Tick them
off after big changes.

## 1. Tests and model quality (5 min)

```powershell
cd backend
.venv\Scripts\python.exe -m pytest                                   # 69 tests, no Ollama needed
.venv\Scripts\python.exe -m pytest tests\test_live_ollama.py -s      # real model, Ollama running
.venv\Scripts\python.exe eval\run_eval.py                            # answer quality + speed + RAM
```

Keep the `eval\results\*.json` file: it's the baseline to compare the next model or prompt with.

## 2. Electron in dev mode (`cd ui; npm run dev`)

- [ ] Window opens, "STARTING THE LOCAL BACKEND…" turns into the Ask page within ~10 s.
- [ ] Index > + ADD FOLDER > pick `test_corpus`: shows SCANNING with files/min, then UP TO DATE.
- [ ] Ask "What is the invoice total for Acme Corp?": answer streams, source card says VERIFIED, OPEN opens the PDF.
- [ ] Ctrl+Shift+Space anywhere opens the overlay; type "invoice", ↓ moves, Enter opens, Esc closes.
- [ ] Close the window: app stays in the tray. Tray > Quit: Task Manager shows no `python.exe` left behind.
- [ ] Kill the backend `python.exe` in Task Manager: within a few seconds the app works again (auto-restart).
- [ ] Settings shows FREE MEMORY; after 10 idle minutes `ollama ps` no longer lists the chat model.

## 3. Installer (`cd ui; npm run build`)

`build-backend.ps1` refuses to ship `.env` or any index data, and starts the frozen backend
once (health + token check) before electron-builder runs.

- [ ] Install `ui\release\Local File Assistant Setup *.exe` on a Windows account with no Python.
- [ ] Repeat the checks in section 2 in the installed app.
- [ ] Settings > LAUNCH AT STARTUP on, sign out and in: app starts in the tray.
- [ ] Logs exist: `%APPDATA%\LocalFileAssistant\logs\backend.log` and `%APPDATA%\Local File Assistant\logs\main.log`.

## 4. Upgrading an old index

Start the new version once with an index made by the old one: Index shows the folders being
rebuilt on their own (QUEUED, then SCANNING), and search works when they finish.
