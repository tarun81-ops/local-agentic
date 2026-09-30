export function render(container) {
  container.innerHTML = `
    <h1 class="page-title">INDEX &amp; SETTINGS</h1>

    <div style="margin-top:26px">
      <div class="page-subtitle"><span style="color:var(--red)">01 —</span> INDEXED FOLDERS</div>
      <div class="empty-state">
        <div class="heading">NOT AVAILABLE YET</div>
        <div class="body">Backend <code>/files/status</code> isn't implemented yet, so folder and scan status can't be shown here for real.</div>
      </div>
    </div>

    <div style="margin-top:26px">
      <div class="page-subtitle"><span style="color:var(--red)">02 —</span> MODEL &amp; BACKEND</div>
      <div class="empty-state">
        <div class="body" style="display:flex;flex-direction:column;gap:8px">
          <div>BASE URL &nbsp; <code>http://localhost:11434/v1</code></div>
          <div>Configured via <code>backend/.env</code> — set <code>OLLAMA_MODEL</code> to choose a model.</div>
        </div>
      </div>
    </div>

    <div style="margin-top:26px;display:flex;gap:22px;flex-wrap:wrap">
      <div style="flex:1;min-width:260px">
        <div class="page-subtitle"><span style="color:var(--red)">03 —</span> GLOBAL SHORTCUT</div>
        <div class="empty-state" style="max-width:none">
          <div style="display:flex;align-items:center;justify-content:space-between">
            <div style="font-size:13px">SUMMON OVERLAY</div>
            <div style="font-family:var(--mono);font-size:11.5px;font-weight:700">CTRL + SHIFT + SPACE</div>
          </div>
        </div>
      </div>
      <div style="flex:1;min-width:260px">
        <div class="page-subtitle"><span style="color:var(--red)">04 —</span> PRIVACY</div>
        <div class="empty-state" style="max-width:none;border-color:var(--blue);background:var(--blue-tint)">
          <div class="heading">PRIVATE BY DESIGN</div>
          <div class="body">Nothing you index or ask ever leaves this device. No cloud calls, no telemetry.</div>
        </div>
      </div>
    </div>
  `;
}
