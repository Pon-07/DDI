from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["ML severity demo"])

_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DDI Severity Advisory</title>
  <style>
    :root { color-scheme: light; font: 16px/1.5 system-ui, sans-serif; }
    body { max-width: 760px; margin: 3rem auto; padding: 0 1rem; color: #172033; }
    main { border: 1px solid #d7deea; border-radius: 14px; padding: 1.5rem; }
    label { display: block; margin: 1rem 0 .3rem; font-weight: 650; }
    input { box-sizing: border-box; width: 100%; padding: .7rem; border: 1px solid #aab5c5; border-radius: 7px; }
    button { margin-top: 1rem; padding: .7rem 1rem; border: 0; border-radius: 7px; background: #1459a6; color: white; font-weight: 700; cursor: pointer; }
    pre { overflow: auto; padding: 1rem; background: #f3f6fa; border-radius: 8px; white-space: pre-wrap; }
    .notice { padding: .8rem; border-left: 4px solid #d08a00; background: #fff8e8; }
  </style>
</head>
<body>
  <main>
    <h1>DDI severity advisory</h1>
    <p>This local demo estimates severity patterns for a drug pair. It does not determine whether an interaction exists and is not clinically validated.</p>
    <form id="pair-form">
      <label for="drug-a">Drug A</label><input id="drug-a" required maxlength="255">
      <label for="drug-b">Drug B</label><input id="drug-b" required maxlength="255">
      <label><input id="documented" type="checkbox" style="width:auto"> An interaction is documented by a validated source</label>
      <button type="submit">Get advisory</button>
    </form>
    <p class="notice">Use only for local evaluation. Do not enter patient identifiers or protected health information. Follow the WHO licensing terms for derived ATC mappings before redistribution.</p>
    <pre id="result" aria-live="polite">Loading model status…</pre>
  </main>
  <script>
    const output = document.getElementById('result');
    fetch('/ml/status').then(r => r.json()).then(status => {
      output.textContent = status.enabled ? `Model: ${status.model}\nHeld-out macro-F1: ${JSON.stringify(status.held_out_macro_f1, null, 2)}\nClinical validation: ${status.clinically_validated}` : `Model unavailable: ${status.reason}`;
    }).catch(() => { output.textContent = 'Could not load model status.'; });
    document.getElementById('pair-form').addEventListener('submit', async event => {
      event.preventDefault();
      output.textContent = 'Calculating advisory…';
      const response = await fetch('/ml/severity-advisory', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          drug_a: document.getElementById('drug-a').value,
          drug_b: document.getElementById('drug-b').value,
          interaction_documented: document.getElementById('documented').checked
        })
      });
      const body = await response.json();
      output.textContent = JSON.stringify(body, null, 2);
    });
  </script>
</body>
</html>"""


@router.get("/", response_class=HTMLResponse, summary="Standalone ML severity demo")
@router.get("/app", response_class=HTMLResponse, summary="Standalone ML severity demo")
def ml_demo() -> HTMLResponse:
    return HTMLResponse(_PAGE)
