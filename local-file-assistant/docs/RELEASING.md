# Releasing (Ollama build)

This is the Ollama build (`main` / `fix/answer-quality`). Its installers are published to
**github.com/tarun81-ops/local-agentic**; the OpenVINO build publishes to `local-agentic-openvino`. Each installed
app only ever looks at its own repo (`build.publish` in `ui\package.json`), so an Ollama install never updates to
the OpenVINO build or the other way round. Never publish one build into the other's repo.

## 1. Build

On the Windows PC that builds releases, once: `scripts\setup.ps1` run. Ollama is not bundled: the setup
wizard sends new users to ollama.com/download and pulls the models through it, so the installer stays small.

```powershell
cd ui
# bump "version" in ui\package.json first (0.2.0 -> 0.3.0): the updater compares versions
npm test           # the once-a-day update rule
npm run build      # web UI, frozen backend + smoke test, NSIS installer
```

`ui\release\` then holds `LocalFileAssistant-Setup-<version>.exe`, its `.blockmap`, and `latest.yml`
(the version and SHA-512 the installed apps check before installing).

## 2. Sign (only if you have a code-signing certificate)

Unsigned installers work, but Windows SmartScreen warns on the first runs. With a certificate (`.pfx`):

```powershell
$env:CSC_LINK = "C:\path\to\certificate.pfx"
$env:CSC_KEY_PASSWORD = "<its password>"   # type it in this window only; never put it in a file in the repo
npm run build
```

electron-builder signs the app and the installer. Check: right-click the `.exe` > Properties > Digital Signatures.

## 3. Publish

Needs a GitHub token with `repo` scope for `local-agentic`, set only in this PowerShell window:

```powershell
$env:GH_TOKEN = "<token>"
npx electron-builder --win nsis --publish always
```

This uploads the installer, `.blockmap` and `latest.yml` to a **draft** release named after the version. Open the
repo's Releases page, add notes, and click Publish. Installed apps see it at their next daily check.

To test an update before users get it, keep the release a draft and follow "Update from the previous version" in
docs/TESTING-ON-WINDOWS.md with a build that points at a test repo.
