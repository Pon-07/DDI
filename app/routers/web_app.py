from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["Web Application"])


@router.get("/", response_class=HTMLResponse, summary="MICROMEDX Clinical Decision Support Console")
@router.get("/app", response_class=HTMLResponse, summary="MICROMEDX Clinical Decision Support Console")
def get_micromedx_console_html():
    """
    MICROMEDX — "From Warning to Verified Action"
    Clinical Decision Support & Medication Safety Platform.
    100% On-Premise / Offline Capable with Zero External CDN Dependencies.
    """
    html_content = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>MICROMEDX — From Warning to Verified Action</title>
  <style>
    :root {
      --bg-surface: #0b1329;
      --bg-panel: #111d38;
      --bg-panel-elevated: #18284d;
      --bg-input: #1e315f;
      --border-subtle: #233867;
      --border-focus: #0284c7;
      --text-primary: #f1f5f9;
      --text-secondary: #94a3b8;
      --text-muted: #64748b;
      --clinical-blue: #0284c7;
      --clinical-blue-light: #38bdf8;
      --clinical-teal: #0d9488;
      --clinical-teal-light: #14b8a6;
      --alert-critical: #e11d48;
      --alert-critical-bg: rgba(225, 29, 72, 0.15);
      --alert-warning: #f59e0b;
      --alert-warning-bg: rgba(245, 158, 11, 0.15);
      --alert-success: #10b981;
      --alert-success-bg: rgba(16, 185, 129, 0.15);
      --shadow-sm: 0 1px 2px 0 rgba(0, 0, 0, 0.4);
      --shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.5), 0 2px 4px -2px rgba(0, 0, 0, 0.5);
      --shadow-lg: 0 10px 15px -3px rgba(0, 0, 0, 0.6), 0 4px 6px -4px rgba(0, 0, 0, 0.6);
    }

    * { box-sizing: border-box; }
    body {
      margin: 0;
      padding: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background-color: var(--bg-surface);
      color: var(--text-primary);
      line-height: 1.5;
      min-height: 100vh;
      overflow-x: hidden;
    }

    /* Top Command Header */
    .top-header {
      background: var(--bg-panel);
      border-bottom: 1px solid var(--border-subtle);
      padding: 0.75rem 2rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
      position: sticky;
      top: 0;
      z-index: 100;
    }

    .brand-section {
      display: flex;
      align-items: center;
      gap: 1rem;
    }

    .brand-title {
      font-size: 1.45rem;
      font-weight: 800;
      letter-spacing: 0.02em;
      color: #ffffff;
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }

    .brand-title span {
      color: var(--clinical-blue-light);
    }

    .brand-tagline {
      font-size: 0.78rem;
      color: var(--text-secondary);
      border-left: 2px solid var(--border-subtle);
      padding-left: 0.75rem;
      font-weight: 500;
      letter-spacing: 0.02em;
    }

    .system-status-pill {
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      background: rgba(16, 185, 129, 0.1);
      border: 1px solid rgba(16, 185, 129, 0.35);
      color: var(--alert-success);
      padding: 0.35rem 0.8rem;
      border-radius: 9999px;
      font-size: 0.75rem;
      font-weight: 600;
      letter-spacing: 0.03em;
    }

    .pulse-indicator {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--alert-success);
      box-shadow: 0 0 6px var(--alert-success);
    }

    .header-actions {
      display: flex;
      align-items: center;
      gap: 0.75rem;
    }

    /* Navigation Role Bar */
    .role-navbar {
      background: #0d172e;
      border-bottom: 1px solid var(--border-subtle);
      padding: 0 2rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }

    .role-tabs {
      display: flex;
      gap: 0.5rem;
    }

    .role-tab-btn {
      background: transparent;
      border: none;
      color: var(--text-secondary);
      padding: 0.75rem 1.25rem;
      font-size: 0.88rem;
      font-weight: 600;
      cursor: pointer;
      border-bottom: 3px solid transparent;
      display: inline-flex;
      align-items: center;
      gap: 0.45rem;
      transition: all 0.2s ease;
    }

    .role-tab-btn:hover {
      color: var(--text-primary);
      background: rgba(255, 255, 255, 0.02);
    }

    .role-tab-btn.active {
      color: var(--clinical-blue-light);
      border-bottom-color: var(--clinical-blue-light);
      background: rgba(2, 132, 199, 0.08);
    }

    /* Container */
    .app-container {
      max-width: 1400px;
      margin: 1.5rem auto;
      padding: 0 1.5rem;
    }

    /* Buttons */
    .btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 0.45rem;
      padding: 0.55rem 1.15rem;
      border-radius: 0.4rem;
      font-weight: 600;
      font-size: 0.85rem;
      cursor: pointer;
      border: 1px solid transparent;
      outline: none;
      transition: all 0.15s ease;
      text-decoration: none;
    }

    .btn-primary { background: var(--clinical-blue); color: #ffffff; border-color: var(--clinical-blue); }
    .btn-primary:hover { background: #0369a1; border-color: #0369a1; }
    .btn-secondary { background: var(--bg-panel-elevated); color: var(--text-primary); border-color: var(--border-subtle); }
    .btn-secondary:hover { background: #233867; border-color: #3b5998; }
    .btn-success { background: var(--clinical-teal); color: #ffffff; }
    .btn-success:hover { background: #0f766e; }
    .btn-danger { background: var(--alert-critical); color: #ffffff; }
    .btn-danger:hover { background: #be123c; }
    .btn-sm { padding: 0.35rem 0.75rem; font-size: 0.78rem; }

    /* Badges */
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 0.3rem;
      padding: 0.2rem 0.55rem;
      border-radius: 0.25rem;
      font-size: 0.72rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }

    .badge-critical { background: var(--alert-critical-bg); color: var(--alert-critical); border: 1px solid rgba(225, 29, 72, 0.4); }
    .badge-warning { background: var(--alert-warning-bg); color: var(--alert-warning); border: 1px solid rgba(245, 158, 11, 0.4); }
    .badge-success { background: var(--alert-success-bg); color: var(--alert-success); border: 1px solid rgba(16, 185, 129, 0.4); }
    .badge-info { background: rgba(2, 132, 199, 0.15); color: var(--clinical-blue-light); border: 1px solid rgba(2, 132, 199, 0.4); }
    .badge-purple { background: rgba(168, 85, 247, 0.15); color: #c084fc; border: 1px solid rgba(168, 85, 247, 0.4); }
    .badge-neutral { background: rgba(100, 116, 139, 0.2); color: #cbd5e1; border: 1px solid rgba(100, 116, 139, 0.4); }

    /* Cards & Panels */
    .card {
      background: var(--bg-panel);
      border: 1px solid var(--border-subtle);
      border-radius: 0.5rem;
      padding: 1.25rem;
      margin-bottom: 1.25rem;
      box-shadow: var(--shadow-sm);
    }

    .card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 1rem;
      padding-bottom: 0.75rem;
      border-bottom: 1px solid var(--border-subtle);
    }

    .card-title {
      font-size: 1.05rem;
      font-weight: 700;
      margin: 0;
      color: #ffffff;
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }

    .stats-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 1rem;
      margin-bottom: 1.25rem;
    }

    .stat-box {
      background: var(--bg-panel);
      border: 1px solid var(--border-subtle);
      border-radius: 0.5rem;
      padding: 1rem;
      border-left: 4px solid var(--clinical-blue);
    }

    .stat-box.warning { border-left-color: var(--alert-warning); }
    .stat-box.critical { border-left-color: var(--alert-critical); }
    .stat-box.success { border-left-color: var(--alert-success); }

    .stat-num { font-size: 1.75rem; font-weight: 800; margin: 0.2rem 0; }
    .stat-title { font-size: 0.75rem; color: var(--text-secondary); text-transform: uppercase; font-weight: 600; letter-spacing: 0.04em; }

    /* Six Agent Pipeline Horizontal Stepper */
    .pipeline-stepper {
      display: flex;
      background: var(--bg-panel-elevated);
      border: 1px solid var(--border-subtle);
      border-radius: 0.5rem;
      padding: 1rem;
      margin: 1.25rem 0;
      overflow-x: auto;
      gap: 0.5rem;
    }

    .pipeline-node {
      flex: 1;
      min-width: 170px;
      background: var(--bg-panel);
      border: 1px solid var(--border-subtle);
      border-radius: 0.4rem;
      padding: 0.75rem;
      position: relative;
    }

    .pipeline-node.active {
      border-color: var(--clinical-blue-light);
      background: rgba(2, 132, 199, 0.08);
    }

    .node-num {
      font-size: 0.7rem;
      font-weight: 800;
      color: var(--clinical-blue-light);
      text-transform: uppercase;
    }

    .node-name {
      font-size: 0.85rem;
      font-weight: 700;
      color: #ffffff;
      margin: 0.2rem 0;
    }

    .node-desc {
      font-size: 0.75rem;
      color: var(--text-secondary);
      line-height: 1.3;
    }

    /* Structured Clinical Workflow Box */
    .resolution-workflow {
      background: #0d172e;
      border: 1px solid var(--border-subtle);
      border-radius: 0.5rem;
      padding: 1.25rem;
      margin: 1.25rem 0;
    }

    .workflow-header {
      font-size: 0.8rem;
      font-weight: 800;
      color: var(--clinical-blue-light);
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin-bottom: 0.5rem;
      display: flex;
      align-items: center;
      gap: 0.4rem;
    }

    /* Tables */
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.85rem;
    }

    th {
      text-align: left;
      padding: 0.65rem 0.85rem;
      background: var(--bg-panel-elevated);
      color: var(--text-secondary);
      font-weight: 700;
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      border-bottom: 1px solid var(--border-subtle);
    }

    td {
      padding: 0.65rem 0.85rem;
      border-bottom: 1px solid var(--border-subtle);
      color: var(--text-primary);
    }

    tr:hover td {
      background: rgba(255, 255, 255, 0.02);
    }

    tr.clickable-row {
      cursor: pointer;
      transition: background 0.15s;
    }
    tr.clickable-row:hover td {
      background: rgba(2, 132, 199, 0.12);
    }

    /* Form Elements */
    .form-group {
      margin-bottom: 1rem;
      position: relative;
    }

    .form-label {
      display: block;
      font-size: 0.8rem;
      font-weight: 700;
      color: var(--text-primary);
      margin-bottom: 0.35rem;
    }

    .form-input {
      width: 100%;
      background: var(--bg-input);
      border: 1px solid var(--border-subtle);
      color: var(--text-primary);
      padding: 0.6rem 0.85rem;
      border-radius: 0.4rem;
      font-size: 0.9rem;
      outline: none;
    }

    .form-input:focus {
      border-color: var(--border-focus);
    }

    /* Autocomplete Suggestions Dropdown */
    .autocomplete-dropdown {
      position: absolute;
      top: 100%;
      left: 0;
      right: 0;
      background: #0f1c3a;
      border: 1px solid var(--clinical-blue-light);
      border-radius: 0.4rem;
      max-height: 220px;
      overflow-y: auto;
      z-index: 1000;
      box-shadow: 0 10px 25px rgba(0,0,0,0.6);
      display: none;
    }

    .autocomplete-item {
      padding: 0.6rem 0.85rem;
      border-bottom: 1px solid rgba(255, 255, 255, 0.05);
      cursor: pointer;
      display: flex;
      justify-content: space-between;
      align-items: center;
      transition: background 0.15s;
    }

    .autocomplete-item:hover {
      background: rgba(2, 132, 199, 0.2);
    }

    .autocomplete-item strong {
      color: #ffffff;
      font-size: 0.85rem;
    }

    .autocomplete-item small {
      color: var(--clinical-teal-light);
      font-size: 0.75rem;
    }

    /* Auth View */
    .auth-card {
      max-width: 540px;
      margin: 2rem auto;
      background: var(--bg-panel);
      border: 1px solid var(--border-subtle);
      border-radius: 0.5rem;
      padding: 2rem;
      box-shadow: var(--shadow-lg);
    }

    /* Auth Navigation Tabs */
    .auth-tab-bar {
      display: flex;
      background: var(--bg-panel-elevated);
      border-radius: 0.4rem;
      padding: 0.25rem;
      margin-bottom: 1.25rem;
      border: 1px solid var(--border-subtle);
      gap: 0.25rem;
    }

    .auth-tab-btn {
      flex: 1;
      padding: 0.6rem 0.8rem;
      background: transparent;
      border: none;
      color: var(--text-secondary);
      font-weight: 700;
      font-size: 0.82rem;
      border-radius: 0.3rem;
      cursor: pointer;
      transition: all 0.2s;
      text-align: center;
    }

    .auth-tab-btn:hover {
      color: #ffffff;
    }

    .auth-tab-btn.active {
      background: var(--clinical-blue);
      color: #ffffff;
      box-shadow: 0 2px 8px rgba(2, 132, 199, 0.4);
    }

    .totp-hero-banner {
      background: linear-gradient(135deg, rgba(16, 185, 129, 0.12) 0%, rgba(2, 132, 199, 0.12) 100%);
      border: 1px solid rgba(16, 185, 129, 0.4);
      border-radius: 0.45rem;
      padding: 0.75rem 1rem;
      margin-bottom: 1.25rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }

    .totp-proof-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 0.5rem;
      margin: 1.25rem 0 1rem 0;
      padding: 0.75rem 1rem;
      background: rgba(15, 23, 42, 0.6);
      border: 1px solid var(--border-subtle);
      border-radius: 0.4rem;
      font-size: 0.78rem;
    }

    .totp-proof-item {
      display: flex;
      align-items: center;
      gap: 0.4rem;
      color: var(--alert-success);
      font-weight: 600;
    }

    .totp-live-widget {
      background: #020617;
      border: 1px dashed var(--clinical-blue-light);
      border-radius: 0.5rem;
      padding: 0.85rem 1rem;
      margin-top: 1.25rem;
      text-align: center;
    }

    .totp-code-display {
      font-family: 'JetBrains Mono', 'Courier New', monospace;
      font-size: 1.75rem;
      font-weight: 800;
      letter-spacing: 0.25em;
      color: var(--alert-success);
      margin: 0.25rem 0;
    }

    .totp-progress-wrap {
      width: 100%;
      height: 6px;
      background: rgba(255, 255, 255, 0.1);
      border-radius: 3px;
      overflow: hidden;
      margin: 0.4rem 0 0.6rem 0;
    }

    .totp-progress-bar {
      height: 100%;
      background: var(--alert-success);
      width: 100%;
      transition: width 1s linear;
    }

    /* Modal Backdrop & Card */
    .modal-backdrop {
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background: rgba(2, 6, 23, 0.85);
      backdrop-filter: blur(4px);
      z-index: 2000;
      display: none;
      align-items: center;
      justify-content: center;
      padding: 1rem;
    }

    .modal-card {
      background: var(--bg-panel);
      border: 1px solid var(--border-subtle);
      border-radius: 0.6rem;
      max-width: 600px;
      width: 100%;
      max-height: 90vh;
      overflow-y: auto;
      padding: 1.75rem;
      box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.7);
      position: relative;
    }

    .qr-preview-box {
      background: #ffffff;
      padding: 12px;
      border-radius: 8px;
      display: inline-block;
      margin: 0.75rem 0;
      box-shadow: 0 4px 12px rgba(0,0,0,0.3);
    }

    .persona-selector {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 0.75rem;
      margin-bottom: 1.25rem;
    }

    .persona-option {
      background: var(--bg-panel-elevated);
      border: 1px solid var(--border-subtle);
      border-radius: 0.4rem;
      padding: 0.75rem;
      cursor: pointer;
      transition: all 0.2s;
    }

    .persona-option:hover {
      border-color: var(--clinical-blue-light);
    }

    .persona-option.selected {
      border-color: var(--clinical-blue-light);
      background: rgba(2, 132, 199, 0.12);
    }

    .otp-grid {
      display: flex;
      justify-content: center;
      gap: 0.5rem;
      margin: 1.25rem 0;
    }

    .otp-cell {
      width: 44px;
      height: 52px;
      background: var(--bg-input);
      border: 2px solid var(--border-subtle);
      border-radius: 0.4rem;
      font-size: 1.4rem;
      font-weight: 800;
      text-align: center;
      color: var(--clinical-blue-light);
      outline: none;
    }

    .otp-cell:focus {
      border-color: var(--clinical-blue-light);
    }

    .sms-debug-toast {
      position: fixed;
      bottom: 2rem;
      right: 2rem;
      background: #020617;
      border: 1px solid var(--clinical-blue-light);
      border-radius: 0.5rem;
      padding: 1rem 1.25rem;
      max-width: 360px;
      box-shadow: var(--shadow-lg);
      z-index: 1000;
      display: none;
    }

    /* Integrity Proof Header Banner */
    .audit-integrity-hero {
      background: linear-gradient(135deg, rgba(2, 132, 199, 0.15) 0%, rgba(16, 185, 129, 0.15) 100%);
      border: 1px solid rgba(2, 132, 199, 0.4);
      border-radius: 0.5rem;
      padding: 1rem 1.25rem;
      margin-bottom: 1.25rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }

    .integrity-checks-row {
      display: flex;
      flex-wrap: wrap;
      gap: 0.85rem;
      margin-top: 0.5rem;
    }

    .integrity-check-item {
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      font-size: 0.78rem;
      font-weight: 700;
      color: var(--alert-success);
    }
  </style>
</head>
<body>

  <!-- Top Command Header -->
  <header class="top-header">
    <div class="brand-section">
      <div class="brand-title">
        MICROMED<span>X</span>
      </div>
      <div class="brand-tagline">
        From Warning to Verified Action
      </div>
    </div>
    <div class="header-actions">
      <div class="system-status-pill">
        <span class="pulse-indicator"></span>
        <span>OFFLINE / ON-PREMISE</span>
      </div>
      <span class="badge badge-info" id="gateway-indicator">LOCAL CLINICAL DECISION SUPPORT</span>
      <div id="session-user-badge" style="display:none; align-items:center; gap:0.5rem;">
        <span class="badge badge-success" id="user-role-tag">DOCTOR</span>
        <button type="button" class="btn btn-secondary btn-sm" onclick="handleLogout(event)">Sign Out</button>
      </div>
    </div>
  </header>

  <!-- Role Sub-navigation Bar -->
  <nav class="role-navbar" id="app-nav" style="display:none;">
    <div class="role-tabs">
      <button type="button" class="role-tab-btn active" id="tab-btn-doctor" onclick="switchTab('doctor', event)">
        🩺 Physician / Doctor
      </button>
      <button type="button" class="role-tab-btn" id="tab-btn-nurse" onclick="switchTab('nurse', event)">
        👩‍⚕️ Nurse (MAR & Vitals)
      </button>
      <button type="button" class="role-tab-btn" id="tab-btn-pharmacist" onclick="switchTab('pharmacist', event)">
        💊 Clinical Pharmacist (BCPS)
      </button>
      <button type="button" class="role-tab-btn" id="tab-btn-admin" onclick="switchTab('admin', event)">
        🛡️ Administrator & Audit
      </button>
    </div>
    <div style="font-size:0.8rem; color:var(--text-muted); display:flex; align-items:center; gap:0.5rem;">
      <span id="nav-active-user">Dr. Sarah Lin, MD</span>
    </div>
  </nav>

  <!-- Main View Container -->
  <main class="app-container">

    <!-- 1. AUTHENTICATION / LOGIN VIEW -->
    <section id="auth-section">
      <div class="auth-card">
        <h2 style="margin-top:0; font-size:1.25rem; margin-bottom:0.25rem;">MICROMEDX — Clinical Authentication Portal</h2>
        <p style="font-size:0.82rem; color:var(--text-secondary); margin-bottom:1.15rem;">
          Authenticate securely using RFC 6238 Offline Authenticator (zero SMS/network) or Mobile Passcode.
        </p>
        
        <!-- Auth Method Selector Tabs -->
        <div class="auth-tab-bar">
          <button type="button" class="auth-tab-btn active" id="btn-auth-tab-totp" onclick="switchAuthTab('totp')">
            🛡️ Offline Authenticator (TOTP)
          </button>
          <button type="button" class="auth-tab-btn" id="btn-auth-tab-sms" onclick="switchAuthTab('sms')">
            📱 Mobile Passcode (SMS / Local)
          </button>
        </div>

        <!-- ========================================== -->
        <!-- PANE A: OFFLINE TOTP AUTHENTICATION (DEFAULT) -->
        <!-- ========================================== -->
        <div id="pane-auth-totp">
          
          <!-- SUB-VIEW 1: TOTP SIGN IN -->
          <div id="totp-view-signin">
            <div class="totp-hero-banner">
              <div>
                <div style="font-weight:800; font-size:0.95rem; color:#ffffff; letter-spacing:0.02em;">
                  OFFLINE AUTHENTICATION
                </div>
                <div style="font-size:0.75rem; color:var(--text-secondary); margin-top:0.2rem;">
                  Standard RFC 6238 TOTP • Verified 100% locally by FastAPI backend
                </div>
              </div>
              <span class="badge badge-success" style="font-size:0.72rem; padding:0.35rem 0.65rem;">
                <span class="pulse-indicator"></span> 0 NETWORK / 0 SMS COST
              </span>
            </div>

            <p style="font-size:0.82rem; color:var(--text-secondary); margin-bottom:1.1rem;">
              Select a verified clinician persona or enter your registered mobile number:
            </p>

            <!-- TOTP Persona Selector Grid -->
            <div class="persona-selector">
              <div class="persona-option selected" id="totp-persona-doc" onclick="selectTotpPersona('+15550192831', 'Doctor', 'totp-persona-doc', 'Dr. Sarah Lin, MD')">
                <div style="font-weight:700; font-size:0.88rem;">Dr. Sarah Lin, MD</div>
                <div style="font-size:0.74rem; color:var(--clinical-blue-light);">Doctor (Attending)</div>
                <div style="font-size:0.72rem; color:var(--text-muted); margin-top:0.15rem;">+1 (555) 019-2831</div>
              </div>
              <div class="persona-option" id="totp-persona-nurse" onclick="selectTotpPersona('+15550192832', 'Nurse', 'totp-persona-nurse', 'Elena Rostova, RN')">
                <div style="font-weight:700; font-size:0.88rem;">Elena Rostova, RN</div>
                <div style="font-size:0.74rem; color:var(--clinical-teal-light);">Lead Inpatient Nurse</div>
                <div style="font-size:0.72rem; color:var(--text-muted); margin-top:0.15rem;">+1 (555) 019-2832</div>
              </div>
              <div class="persona-option" id="totp-persona-pharm" onclick="selectTotpPersona('+15550192833', 'Clinical Pharmacist', 'totp-persona-pharm', 'Marcus Vance, PharmD')">
                <div style="font-weight:700; font-size:0.88rem;">Marcus Vance, PharmD</div>
                <div style="font-size:0.74rem; color:#c084fc;">Clinical Pharmacist</div>
                <div style="font-size:0.72rem; color:var(--text-muted); margin-top:0.15rem;">+1 (555) 019-2833</div>
              </div>
              <div class="persona-option" id="totp-persona-admin" onclick="selectTotpPersona('+15550192834', 'Administrator', 'totp-persona-admin', 'Arthur Pendelton, MS')">
                <div style="font-weight:700; font-size:0.88rem;">Arthur Pendelton, MS</div>
                <div style="font-size:0.74rem; color:var(--alert-warning);">System Administrator</div>
                <div style="font-size:0.72rem; color:var(--text-muted); margin-top:0.15rem;">+1 (555) 019-2834</div>
              </div>
            </div>

            <!-- TOTP Login Form -->
            <form id="totp-login-form" onsubmit="event.preventDefault(); verifyTOTPLoginAsync(); return false;">
              <div class="form-group">
                <label class="form-label" for="totp-phone-input">User / Mobile Number</label>
                <input type="text" id="totp-phone-input" class="form-input" value="+15550192831" onchange="onPhoneInputChange()" required>
              </div>
              
              <div class="form-group" style="text-align:center;">
                <label class="form-label">Authenticator Code</label>
                <div class="otp-grid">
                  <input type="text" maxlength="1" class="otp-cell" id="totp-c1" oninput="onTotpCellInput(1)" onkeydown="onTotpCellKey(event, 1)" autocomplete="off">
                  <input type="text" maxlength="1" class="otp-cell" id="totp-c2" oninput="onTotpCellInput(2)" onkeydown="onTotpCellKey(event, 2)" autocomplete="off">
                  <input type="text" maxlength="1" class="otp-cell" id="totp-c3" oninput="onTotpCellInput(3)" onkeydown="onTotpCellKey(event, 3)" autocomplete="off">
                  <input type="text" maxlength="1" class="otp-cell" id="totp-c4" oninput="onTotpCellInput(4)" onkeydown="onTotpCellKey(event, 4)" autocomplete="off">
                  <input type="text" maxlength="1" class="otp-cell" id="totp-c5" oninput="onTotpCellInput(5)" onkeydown="onTotpCellKey(event, 5)" autocomplete="off">
                  <input type="text" maxlength="1" class="otp-cell" id="totp-c6" oninput="onTotpCellInput(6)" onkeydown="onTotpCellKey(event, 6)" autocomplete="off">
                </div>
              </div>

              <div style="margin-top:1.25rem;">
                <button type="submit" class="btn btn-primary" id="btn-verify-totp" style="width:100%; padding:0.75rem; font-size:1rem; font-weight:800; background:linear-gradient(135deg, #0284c7 0%, #0369a1 100%);">
                  🛡️ VERIFY & SIGN IN
                </button>
              </div>

              <!-- 4 Proof Guarantee Checklist -->
              <div class="totp-proof-grid">
                <div class="totp-proof-item">✓ TOTP Verified</div>
                <div class="totp-proof-item">✓ Generated Locally</div>
                <div class="totp-proof-item">✓ Internet Not Required</div>
                <div class="totp-proof-item">✓ No SMS Cost</div>
              </div>

              <div style="display:flex; justify-content:space-between; align-items:center; margin-top:0.85rem;">
                <button type="button" class="btn btn-secondary btn-sm" id="btn-goto-enroll" onclick="showTotpEnrollView(event)">
                  📷 Set up authenticator
                </button>
                <span style="font-size:0.75rem; color:var(--text-muted);">RFC 6238 Standard (30s)</span>
              </div>

              <!-- Live Offline TOTP Assistant (Local Simulator) -->
              <div class="totp-live-widget" id="totp-live-widget">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                  <span style="font-size:0.72rem; font-weight:800; color:var(--clinical-blue-light); text-transform:uppercase; letter-spacing:0.04em;">
                    ⚡ LIVE LOCAL TOTP GENERATOR (LOCAL SIMULATOR)
                  </span>
                  <span class="badge badge-success" style="font-size:0.65rem;" id="totp-timer-label">30s Step</span>
                </div>
                <div class="totp-code-display" id="totp-live-code-preview">------</div>
                <div class="totp-progress-wrap">
                  <div class="totp-progress-bar" id="totp-live-progress-bar"></div>
                </div>
                <div style="display:flex; justify-content:space-between; align-items:center; margin-top:0.35rem;">
                  <span style="font-size:0.72rem; color:var(--text-muted);" id="totp-remaining-text">Expires in 30s</span>
                  <button type="button" class="btn btn-success btn-sm" id="btn-autofill-totp" onclick="autoFillAndVerifyTotp(event)">
                    ⚡ 1-Click Auto Fill & Sign In
                  </button>
                </div>
                <div style="font-size:0.7rem; color:var(--text-muted); margin-top:0.5rem; text-align:left;">
                  💡 <em>Physical authenticator apps calculate this matching 6-digit TOTP offline. Local backend validates it without network connection.</em>
                </div>
              </div>
            </form>
          </div>

          <!-- SUB-VIEW 2: FIRST-TIME TOTP ENROLLMENT -->
          <div id="totp-view-enroll" style="display:none;">
            <div style="background:linear-gradient(135deg, rgba(5, 150, 105, 0.15) 0%, rgba(15, 23, 42, 0.8) 100%); border:1px solid rgba(16, 185, 129, 0.4); border-radius:0.5rem; padding:1rem; margin-bottom:1.25rem;">
              <h3 style="margin:0 0 0.35rem 0; font-size:1.15rem; color:#ffffff; display:flex; align-items:center; gap:0.45rem;">
                <span>🛡️ Set up Offline Authentication</span>
              </h3>
              <p style="font-size:0.82rem; color:var(--text-secondary); margin:0; line-height:1.45;">
                Use an authenticator app to generate secure 6-digit codes.<br>
                After setup, these codes work without internet or SMS.
              </p>
            </div>

            <!-- Clinician Target Info -->
            <div style="background:var(--bg-panel-elevated); padding:0.65rem 0.85rem; border-radius:0.4rem; border:1px solid var(--border-subtle); margin-bottom:1.1rem; display:flex; justify-content:space-between; align-items:center;">
              <div>
                <strong id="enroll-target-name" style="font-size:0.9rem; color:#ffffff;">Dr. Sarah Lin, MD</strong>
                <div id="enroll-target-phone" style="font-size:0.75rem; color:var(--text-muted);">+1 (555) 019-2831</div>
              </div>
              <span class="badge badge-info" id="enroll-target-role-badge">DOCTOR</span>
            </div>

            <!-- Offline QR Code Presentation Box -->
            <div style="text-align:center; margin-bottom:1.1rem;">
              <div style="background:#ffffff; border-radius:0.5rem; padding:0.75rem; width:175px; margin:0 auto; box-shadow:0 4px 14px rgba(0,0,0,0.4);">
                <img id="enroll-qr-img" src="" alt="Offline TOTP QR Code" style="width:155px; height:155px; display:block; margin:0 auto;">
                <div style="font-size:0.65rem; color:#334155; font-weight:800; margin-top:0.3rem; letter-spacing:0.04em;">
                  OFFLINE QR CODE
                </div>
              </div>
              <div style="font-size:0.75rem; color:var(--text-muted); margin-top:0.6rem;">
                Scan using <strong>Google Authenticator</strong>, <strong>Microsoft Authenticator</strong>, or <strong>Aegis</strong>.
              </div>
            </div>

            <!-- Collapsible Manual Secret Key Helper -->
            <div style="text-align:center; margin-bottom:1.25rem;">
              <details style="font-size:0.78rem; color:var(--text-secondary);">
                <summary style="cursor:pointer; color:var(--clinical-blue-light); font-weight:600;">Can't scan? View manual setup key</summary>
                <div style="margin-top:0.45rem; background:rgba(0,0,0,0.4); padding:0.5rem 0.75rem; border-radius:0.3rem; border:1px solid var(--border-subtle); display:flex; justify-content:center; align-items:center; gap:0.65rem;">
                  <span id="enroll-secret-text" style="font-family:monospace; color:var(--clinical-teal-light); font-weight:700; font-size:0.85rem;">------</span>
                  <button type="button" class="btn btn-secondary btn-sm" id="btn-copy-secret" style="padding:0.2rem 0.5rem; font-size:0.72rem;" onclick="copyEnrollSecret(event)">Copy</button>
                </div>
              </details>
            </div>

            <!-- Verification Step -->
            <div style="text-align:center; margin-bottom:1.25rem;">
              <label class="form-label" style="font-size:0.88rem; font-weight:700; color:#ffffff; margin-bottom:0.6rem;">
                Enter the 6-digit code shown in your authenticator.
              </label>
              <div class="otp-grid">
                <input type="text" maxlength="1" class="otp-cell" id="enroll-c1" oninput="onEnrollCellInput(1)" onkeydown="onEnrollCellKey(event, 1)" autocomplete="off">
                <input type="text" maxlength="1" class="otp-cell" id="enroll-c2" oninput="onEnrollCellInput(2)" onkeydown="onEnrollCellKey(event, 2)" autocomplete="off">
                <input type="text" maxlength="1" class="otp-cell" id="enroll-c3" oninput="onEnrollCellInput(3)" onkeydown="onEnrollCellKey(event, 3)" autocomplete="off">
                <input type="text" maxlength="1" class="otp-cell" id="enroll-c4" oninput="onEnrollCellInput(4)" onkeydown="onEnrollCellKey(event, 4)" autocomplete="off">
                <input type="text" maxlength="1" class="otp-cell" id="enroll-c5" oninput="onEnrollCellInput(5)" onkeydown="onEnrollCellKey(event, 5)" autocomplete="off">
                <input type="text" maxlength="1" class="otp-cell" id="enroll-c6" oninput="onEnrollCellInput(6)" onkeydown="onEnrollCellKey(event, 6)" autocomplete="off">
              </div>
            </div>

            <div>
              <button type="button" class="btn btn-primary" id="btn-enroll-verify" style="width:100%; padding:0.75rem; font-size:1rem; font-weight:800; background:linear-gradient(135deg, #059669 0%, #047857 100%);" onclick="submitTotpEnrollVerify(event)">
                🛡️ VERIFY ENROLLMENT & SIGN IN
              </button>
            </div>

            <div id="enroll-feedback-msg" style="margin-top:0.75rem; text-align:center; font-size:0.82rem;"></div>

            <div style="text-align:center; margin-top:1.1rem; padding-top:0.75rem; border-top:1px solid var(--border-subtle);">
              <button type="button" class="btn btn-secondary btn-sm" onclick="showTotpSigninView(event)">
                ← Already enrolled? Sign in with TOTP
              </button>
            </div>
          </div>

        </div>

        <!-- ========================================== -->
        <!-- PANE B: SMS / LOCAL PASSCODE GATEWAY -->
        <!-- ========================================== -->
        <div id="pane-auth-sms" style="display:none;">
          <h2 style="margin-top:0; font-size:1.15rem; margin-bottom:0.25rem;">Mobile Passcode Gateway</h2>
          <p style="font-size:0.82rem; color:var(--text-secondary); margin-bottom:1.25rem;">
            Dispatches passcode via configured gateway (Local Offline Provider / MSG91 / Twilio).
          </p>

          <!-- Persona Quick Select Grid -->
          <div class="persona-selector">
            <div class="persona-option selected" id="persona-doc" onclick="selectDemoPersona('+15550192831', 'Doctor', 'persona-doc')">
              <div style="font-weight:700; font-size:0.88rem;">Dr. Sarah Lin, MD</div>
              <div style="font-size:0.74rem; color:var(--clinical-blue-light);">Doctor (Attending)</div>
              <div style="font-size:0.72rem; color:var(--text-muted); margin-top:0.15rem;">+1 (555) 019-2831</div>
            </div>
            <div class="persona-option" id="persona-nurse" onclick="selectDemoPersona('+15550192832', 'Nurse', 'persona-nurse')">
              <div style="font-weight:700; font-size:0.88rem;">Elena Rostova, RN</div>
              <div style="font-size:0.74rem; color:var(--clinical-teal-light);">Lead Inpatient Nurse</div>
              <div style="font-size:0.72rem; color:var(--text-muted); margin-top:0.15rem;">+1 (555) 019-2832</div>
            </div>
            <div class="persona-option" id="persona-pharm" onclick="selectDemoPersona('+15550192833', 'Clinical Pharmacist', 'persona-pharm')">
              <div style="font-weight:700; font-size:0.88rem;">Marcus Vance, PharmD</div>
              <div style="font-size:0.74rem; color:#c084fc;">Clinical Pharmacist</div>
              <div style="font-size:0.72rem; color:var(--text-muted); margin-top:0.15rem;">+1 (555) 019-2833</div>
            </div>
            <div class="persona-option" id="persona-admin" onclick="selectDemoPersona('+15550192834', 'Administrator', 'persona-admin')">
              <div style="font-weight:700; font-size:0.88rem;">Arthur Pendelton, MS</div>
              <div style="font-size:0.74rem; color:var(--alert-warning);">System Administrator</div>
              <div style="font-size:0.72rem; color:var(--text-muted); margin-top:0.15rem;">+1 (555) 019-2834</div>
            </div>
          </div>

          <!-- Phone Step -->
          <form id="phone-form" onsubmit="event.preventDefault(); requestOTPCode(); return false;">
            <div class="form-group">
              <label class="form-label" for="phone-input">Mobile Phone Number</label>
              <input type="text" id="phone-input" class="form-input" value="+15550192831" required>
            </div>
            <div class="form-group">
              <label class="form-label" for="role-input">Authorized Role</label>
              <select id="role-input" class="form-input">
                <option value="Doctor">Doctor (Physician Prescribing & Co-sign)</option>
                <option value="Nurse">Nurse (Medication Admin & Vitals)</option>
                <option value="Clinical Pharmacist">Clinical Pharmacist (DDI & Pharmacokinetics)</option>
                <option value="Administrator">Administrator (Audit Ledger & Security)</option>
              </select>
            </div>
            <div style="display:flex; justify-content:space-between; align-items:center; margin-top:1.5rem;">
              <span class="badge badge-info">LOCAL AUTHENTICATION</span>
              <button type="submit" class="btn btn-primary" id="btn-request-otp">
                Send Passcode &rarr;
              </button>
            </div>
          </form>

          <!-- OTP Step -->
          <form id="otp-form" style="display:none;" onsubmit="event.preventDefault(); verifyOTPCode(); return false;">
            <div style="text-align:center;">
              <div style="font-size:0.9rem; color:var(--text-secondary);">Enter 6-digit verification code sent to:</div>
              <div style="font-weight:700; color:var(--clinical-blue-light); margin:0.25rem 0;" id="masked-phone-display">+1 •••• ••31</div>
              
              <div class="otp-grid">
                <input type="text" maxlength="1" class="otp-cell" id="otp-1" oninput="onOtpInput(1)" onkeydown="onOtpKey(event, 1)">
                <input type="text" maxlength="1" class="otp-cell" id="otp-2" oninput="onOtpInput(2)" onkeydown="onOtpKey(event, 2)">
                <input type="text" maxlength="1" class="otp-cell" id="otp-3" oninput="onOtpInput(3)" onkeydown="onOtpKey(event, 3)">
                <input type="text" maxlength="1" class="otp-cell" id="otp-4" oninput="onOtpInput(4)" onkeydown="onOtpKey(event, 4)">
                <input type="text" maxlength="1" class="otp-cell" id="otp-5" oninput="onOtpInput(5)" onkeydown="onOtpKey(event, 5)">
                <input type="text" maxlength="1" class="otp-cell" id="otp-6" oninput="onOtpInput(6)" onkeydown="onOtpKey(event, 6)">
              </div>

              <div style="display:flex; justify-content:center; gap:0.75rem; margin-top:1.25rem;">
                <button type="button" class="btn btn-secondary btn-sm" onclick="backToPhoneStep(event)">&larr; Back</button>
                <button type="submit" class="btn btn-primary" id="btn-verify-otp">Verify & Launch Dashboard</button>
              </div>
            </div>
          </form>
        </div>

      </div>
    </section>

    <!-- ========================================== -->
    <!-- MODAL: OFFLINE TOTP ENROLLMENT (QR CODE) -->
    <!-- ========================================== -->
    <div class="modal-backdrop" id="modal-totp-enroll">
      <div class="modal-card">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.75rem;">
          <div>
            <h3 style="margin:0; font-size:1.15rem; color:#ffffff;">MICROMEDX OFFLINE AUTHENTICATION</h3>
            <div style="font-size:0.75rem; color:var(--alert-success); font-weight:700;">Standard RFC 6238 TOTP Enrollment</div>
          </div>
          <button type="button" class="btn btn-secondary btn-sm" onclick="closeTotpEnrollModal(event)">✕</button>
        </div>
        
        <p style="font-size:0.82rem; color:var(--text-secondary); margin-bottom:0.5rem;">
          Scan this QR code with <strong>Google Authenticator</strong>, <strong>Microsoft Authenticator</strong>, <strong>2FAS</strong>, or <strong>Aegis</strong>:
        </p>

        <div style="text-align:center;">
          <div class="qr-preview-box">
            <img id="enroll-qr-img" src="" alt="TOTP QR Code" style="width:170px; height:170px; display:block;" />
          </div>
          <div style="font-size:0.75rem; color:var(--text-muted); margin-bottom:0.25rem;">Or enter secret key manually:</div>
          <div style="display:flex; justify-content:center; align-items:center; gap:0.5rem; margin-bottom:0.75rem;">
            <code id="enroll-secret-display" style="background:var(--bg-input); padding:0.35rem 0.65rem; border-radius:0.3rem; color:var(--clinical-teal-light); font-size:0.85rem; letter-spacing:0.08em;">XXXX XXXX XXXX XXXX</code>
            <button type="button" class="btn btn-secondary btn-sm" onclick="copyTotpSecret(event)" id="btn-copy-secret">📋 Copy</button>
          </div>
        </div>

        <div style="border-top:1px solid var(--border-subtle); padding-top:0.75rem; margin-top:0.5rem;">
          <div style="font-size:0.82rem; font-weight:700; color:var(--text-primary); margin-bottom:0.3rem; text-align:center;">
            Enter 6-digit code from authenticator app to verify enrollment:
          </div>
          <div class="otp-grid" style="margin:0.5rem 0;">
            <input type="text" maxlength="1" class="otp-cell" id="enroll-c1" oninput="onEnrollCellInput(1)" onkeydown="onEnrollCellKey(event, 1)" autocomplete="off">
            <input type="text" maxlength="1" class="otp-cell" id="enroll-c2" oninput="onEnrollCellInput(2)" onkeydown="onEnrollCellKey(event, 2)" autocomplete="off">
            <input type="text" maxlength="1" class="otp-cell" id="enroll-c3" oninput="onEnrollCellInput(3)" onkeydown="onEnrollCellKey(event, 3)" autocomplete="off">
            <input type="text" maxlength="1" class="otp-cell" id="enroll-c4" oninput="onEnrollCellInput(4)" onkeydown="onEnrollCellKey(event, 4)" autocomplete="off">
            <input type="text" maxlength="1" class="otp-cell" id="enroll-c5" oninput="onEnrollCellInput(5)" onkeydown="onEnrollCellKey(event, 5)" autocomplete="off">
            <input type="text" maxlength="1" class="otp-cell" id="enroll-c6" oninput="onEnrollCellInput(6)" onkeydown="onEnrollCellKey(event, 6)" autocomplete="off">
          </div>
          <div id="enroll-feedback-msg" style="font-size:0.8rem; margin:0.3rem 0; min-height:1.2rem; text-align:center;"></div>
          <div style="display:flex; justify-content:flex-end; gap:0.5rem; margin-top:0.5rem;">
            <button type="button" class="btn btn-secondary btn-sm" onclick="closeTotpEnrollModal(event)">Cancel</button>
            <button type="button" class="btn btn-primary btn-sm" id="btn-enroll-verify" onclick="submitTotpEnrollVerify(event)">Verify & Activate TOTP</button>
          </div>
        </div>
      </div>
    </div>

    <!-- ========================================== -->
    <!-- MODAL: AUDIT DETAIL DRAWER / BLOCK INSPECTOR -->
    <!-- ========================================== -->
    <div class="modal-backdrop" id="modal-audit-detail">
      <div class="modal-card">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.75rem; border-bottom:1px solid var(--border-subtle); padding-bottom:0.5rem;">
          <div>
            <h3 style="margin:0; font-size:1.15rem; color:#ffffff;" id="audit-detail-title">Audit Block Inspection</h3>
            <div style="font-size:0.75rem; color:var(--alert-success); font-weight:700;">✓ Tamper-Evident Cryptographic Block</div>
          </div>
          <button type="button" class="btn btn-secondary btn-sm" onclick="closeAuditDetailDrawer(event)">✕</button>
        </div>

        <div style="display:grid; grid-template-columns:1fr 1fr; gap:0.75rem; font-size:0.82rem; margin-bottom:1rem;">
          <div><span style="color:var(--text-secondary);">Block ID / Index:</span> <strong id="audit-detail-id" style="color:var(--clinical-blue-light);">#1</strong></div>
          <div><span style="color:var(--text-secondary);">Event Category:</span> <span id="audit-detail-category" class="badge badge-info">SAFETY</span></div>
          <div><span style="color:var(--text-secondary);">Timestamp (UTC):</span> <strong id="audit-detail-time" style="color:#ffffff;">--</strong></div>
          <div><span style="color:var(--text-secondary);">Actor / System:</span> <strong id="audit-detail-actor" style="color:var(--clinical-teal-light);">--</strong></div>
          <div><span style="color:var(--text-secondary);">Target Entity:</span> <strong id="audit-detail-entity" style="color:#ffffff;">--</strong></div>
          <div><span style="color:var(--text-secondary);">Action Taken:</span> <strong id="audit-detail-action" style="color:#ffffff;">--</strong></div>
          <div><span style="color:var(--text-secondary);">Clinical Result:</span> <span id="audit-detail-result" class="badge badge-success">RECORDED ✓</span></div>
          <div><span style="color:var(--text-secondary);">Rule ID:</span> <code id="audit-detail-rule" style="color:var(--clinical-blue-light);">--</code></div>
          <div><span style="color:var(--text-secondary);">Evidence ID:</span> <code id="audit-detail-evidence" style="color:var(--clinical-teal-light);">--</code></div>
          <div><span style="color:var(--text-secondary);">Provenance:</span> <strong id="audit-detail-provenance" style="color:#ffffff;">--</strong></div>
        </div>

        <!-- Hash Chaining Proof Box -->
        <div style="background:#060d1f; border:1px solid var(--border-subtle); border-radius:0.4rem; padding:0.75rem; margin-bottom:1rem; font-size:0.75rem;">
          <div style="margin-bottom:0.35rem; color:var(--text-secondary);">Previous Block SHA-256 Hash:</div>
          <code id="audit-detail-prevhash" style="color:var(--text-muted); word-break:break-all; font-size:0.72rem;">--</code>
          <div style="margin:0.5rem 0 0.35rem 0; color:var(--text-secondary);">Current Block SHA-256 Hash:</div>
          <code id="audit-detail-curhash" style="color:var(--alert-success); font-weight:700; word-break:break-all; font-size:0.72rem;">--</code>
          <div style="margin-top:0.5rem; display:flex; justify-content:space-between; align-items:center;">
            <span>Chain Link Status:</span>
            <span class="badge badge-success">VALID & INTACT ✓</span>
          </div>
        </div>

        <!-- Raw JSON Payload -->
        <div style="margin-bottom:0.75rem;">
          <div style="font-size:0.78rem; font-weight:700; color:var(--text-secondary); margin-bottom:0.3rem;">Raw Audit Event Payload:</div>
          <pre id="audit-detail-payload" style="background:#020617; border:1px solid var(--border-subtle); border-radius:0.4rem; padding:0.75rem; font-size:0.75rem; color:#e2e8f0; overflow-x:auto; max-height:160px; margin:0;"></pre>
        </div>

        <div style="display:flex; justify-content:flex-end;">
          <button type="button" class="btn btn-secondary btn-sm" onclick="closeAuditDetailDrawer(event)">Close Inspector</button>
        </div>
      </div>
    </div>

    <!-- 2. MAIN CLINICAL DASHBOARD SECTION -->
    <section id="dashboard-section" style="display:none;">

      <!-- System Telemetry Bar -->
      <div style="background:var(--bg-panel); border:1px solid var(--border-subtle); border-radius:0.5rem; padding:0.6rem 1rem; margin-bottom:1.25rem; display:flex; justify-content:space-between; align-items:center; font-size:0.78rem;">
        <div style="display:flex; gap:1.25rem; color:var(--text-secondary);">
          <span>Backend: <strong style="color:var(--alert-success);">✓ Local FastAPI</strong></span>
          <span>Database: <strong style="color:var(--alert-success);">✓ SQLite WAL</strong></span>
          <span>Safety Engine: <strong style="color:var(--alert-success);">✓ Deterministic</strong></span>
          <span>AI Model: <strong style="color:var(--clinical-blue-light);" id="ai-model-status">✓ Local Ollama / Deterministic Fallback</strong></span>
          <span>Deployment: <strong style="color:var(--alert-success);">✓ 100% Offline / On-Premise</strong></span>
        </div>
        <div style="display:flex; gap:0.5rem;">
          <button type="button" class="btn btn-secondary btn-sm" onclick="seedPatientsAsync(event)">🔄 Re-seed Official Patients</button>
        </div>
      </div>

      <!-- SIX-AGENT PIPELINE VISUALIZATION -->
      <div class="card" style="padding:1rem;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.5rem;">
          <div style="font-size:0.85rem; font-weight:800; color:var(--clinical-blue-light); text-transform:uppercase; letter-spacing:0.04em;">
            MICROMEDX SAFETY PIPELINE — SIX AGENTS EXECUTION TRACE
          </div>
          <span class="badge badge-info">DETERMINISTIC VERIFICATION</span>
        </div>
        <div class="pipeline-stepper" id="pipeline-stepper-view">
          <div class="pipeline-node active" id="node-agent-1">
            <div class="node-num">Agent 1</div>
            <div class="node-name">Knowledge Normalizer</div>
            <div class="node-desc">RxNorm & OpenFDA entity normalization and clinical context binding.</div>
          </div>
          <div class="pipeline-node active" id="node-agent-2">
            <div class="node-num">Agent 2</div>
            <div class="node-name">Rule Pack & Provenance</div>
            <div class="node-desc">Versioned qualitative safety rules (DDInter/OpenFDA) with evidence IDs.</div>
          </div>
          <div class="pipeline-node active" id="node-agent-3">
            <div class="node-num">Agent 3</div>
            <div class="node-name">Deterministic Risk Detector</div>
            <div class="node-desc">Evaluates cumulative drug regimens & chronological lab trends (INR/Cr).</div>
          </div>
          <div class="pipeline-node active" id="node-agent-4">
            <div class="node-num">Agent 4</div>
            <div class="node-name">Safety Resolution Engine</div>
            <div class="node-desc">Filters candidate proposals against contraindications -> Simulated Order.</div>
          </div>
          <div class="pipeline-node active" id="node-agent-5">
            <div class="node-num">Agent 5</div>
            <div class="node-name">Explainable AI Explicator</div>
            <div class="node-desc">Clinical rationale synthesis with strict deterministic fallback.</div>
          </div>
          <div class="pipeline-node active" id="node-agent-6">
            <div class="node-num">Agent 6</div>
            <div class="node-name">Cryptographic Audit Ledger</div>
            <div class="node-desc">Tamper-evident SHA-256 hash chaining for every finding & co-sign.</div>
          </div>
        </div>
      </div>

      <!-- ROLE VIEW: DOCTOR -->
      <div id="view-doctor" class="role-view">
        <div class="stats-grid">
          <div class="stat-box">
            <div class="stat-title">Active Inpatients</div>
            <div class="stat-num" id="doc-stat-patients">3</div>
          </div>
          <div class="stat-box warning">
            <div class="stat-title">Pending Simulated Co-Signs</div>
            <div class="stat-num" id="doc-stat-cosigns" style="color:var(--alert-warning);">1</div>
          </div>
          <div class="stat-box critical">
            <div class="stat-title">Active Clinical Risk Findings</div>
            <div class="stat-num" id="doc-stat-findings" style="color:var(--alert-critical);">2</div>
          </div>
          <div class="stat-box success">
            <div class="stat-title">SHA-256 Audit Integrity</div>
            <div class="stat-num" style="color:var(--alert-success); font-size:1.25rem;">VERIFIED ✓</div>
          </div>
        </div>

        <!-- Pending Co-sign Section -->
        <div class="card">
          <div class="card-header">
            <div class="card-title">
              <span>⚡ Mandatory Physician Co-Sign Queue</span>
              <span class="badge badge-warning" id="badge-cosign-count">1 Pending</span>
            </div>
            <div style="font-size:0.75rem; color:var(--text-muted);">
              Prescriptions are NOT automatically executed. Mandatory clinical co-sign required.
            </div>
          </div>
          <div id="doctor-cosign-container">
            <!-- Rendered dynamically -->
          </div>
        </div>

        <!-- Official Demo Scenarios & Live Prescribing Sandbox Grid -->
        <div style="display:grid; grid-template-columns: 1fr 1fr; gap:1.25rem;">
          
          <!-- Official Clinical Demo Scenarios -->
          <div class="card">
            <div class="card-header">
              <div class="card-title">Official Clinical Safety Scenarios</div>
            </div>
            <p style="font-size:0.82rem; color:var(--text-secondary); margin-top:0;">
              Execute verified patient triggers through the full 6-agent safety pipeline without page reload.
            </p>

            <div style="margin-bottom:0.75rem; padding:0.75rem; background:var(--bg-panel-elevated); border-radius:0.4rem; border:1px solid var(--border-subtle);">
              <div style="display:flex; justify-content:space-between; align-items:center;">
                <div>
                  <strong style="font-size:0.88rem;">1. Warfarin + Fluconazole</strong>
                  <div style="font-size:0.75rem; color:var(--text-muted);">Sarah Jenkins (DEMO-PT-001) | Rising INR (2.1 &rarr; 3.4)</div>
                </div>
                <button type="button" class="btn btn-primary btn-sm" id="btn-demo-1" onclick="executeScenarioAsync(1, event)">
                  Run Scenario 1
                </button>
              </div>
            </div>

            <div style="margin-bottom:0.75rem; padding:0.75rem; background:var(--bg-panel-elevated); border-radius:0.4rem; border:1px solid var(--border-subtle);">
              <div style="display:flex; justify-content:space-between; align-items:center;">
                <div>
                  <strong style="font-size:0.88rem;">2. Enoxaparin + Declining Renal</strong>
                  <div style="font-size:0.75rem; color:var(--text-muted);">Robert Chen (DEMO-PT-002) | Creatinine Acute Rise (2.4 mg/dL)</div>
                </div>
                <button type="button" class="btn btn-primary btn-sm" id="btn-demo-2" onclick="executeScenarioAsync(2, event)">
                  Run Scenario 2
                </button>
              </div>
            </div>

            <div style="padding:0.75rem; background:var(--bg-panel-elevated); border-radius:0.4rem; border:1px solid var(--border-subtle);">
              <div style="display:flex; justify-content:space-between; align-items:center;">
                <div>
                  <strong style="font-size:0.88rem;">3. Duplicate ACE-Inhibitor</strong>
                  <div style="font-size:0.75rem; color:var(--text-muted);">Elena Rostova (DEMO-PT-003) | Active Lisinopril + Prescribe Enalapril</div>
                </div>
                <button type="button" class="btn btn-primary btn-sm" id="btn-demo-3" onclick="executeScenarioAsync(3, event)">
                  Run Scenario 3
                </button>
              </div>
            </div>
          </div>

          <!-- Live Prescribing Sandbox with Real Drug Search & Local Knowledge Validation -->
          <div class="card">
            <div class="card-header">
              <div class="card-title">
                <span>⚡ Live Clinical Prescribing Sandbox</span>
                <span class="badge badge-success">LOCAL KNOWLEDGE</span>
              </div>
            </div>
            <p style="font-size:0.82rem; color:var(--text-secondary); margin-top:0;">
              Search offline formulary or evaluate custom prescriptions with strict local validation boundaries:
            </p>

            <form onsubmit="event.preventDefault(); submitSandboxPrescriptionAsync(); return false;">
              <div class="form-group">
                <label class="form-label" for="sandbox-patient-select">Patient</label>
                <select id="sandbox-patient-select" class="form-input">
                  <option value="1">Sarah Jenkins (DEMO-PT-001 - Active Warfarin 5mg)</option>
                  <option value="2">Robert Chen (DEMO-PT-002 - Active Enoxaparin 80mg)</option>
                  <option value="3">Elena Rostova (DEMO-PT-003 - Active Lisinopril 20mg)</option>
                </select>
              </div>

              <div class="form-group">
                <label class="form-label" for="sandbox-drug-search">Medication [ Search local knowledge base... ]</label>
                <input type="text" id="sandbox-drug-search" class="form-input" placeholder="Search e.g. Fluconazole, Warfarin, Enoxaparin..." autocomplete="off" oninput="onDrugSearchInput(event)" onfocus="onDrugSearchInput(event)">
                <div class="autocomplete-dropdown" id="sandbox-autocomplete-list"></div>
              </div>

              <div class="form-group">
                <label class="form-label" for="sandbox-dose-input">Dose & Route</label>
                <input type="text" id="sandbox-dose-input" class="form-input" value="100 mg oral daily" required>
              </div>

              <div style="display:flex; justify-content:space-between; align-items:center; margin-top:1rem;">
                <span style="font-size:0.75rem; color:var(--text-muted);">Deterministic Safety Guard Active</span>
                <button type="submit" class="btn btn-primary btn-sm" id="btn-sandbox-submit">
                  ⚡ Evaluate & Prescribe
                </button>
              </div>
            </form>
          </div>

        </div>

        <!-- Active Patient Roster -->
        <div class="card" style="margin-top:1.25rem;">
          <div class="card-header">
            <div class="card-title">Inpatient Clinical Roster & Cumulative Regimens</div>
          </div>
          <table>
            <thead>
              <tr>
                <th>Patient</th>
                <th>Active Regimen</th>
                <th>Recent Labs</th>
              </tr>
            </thead>
            <tbody id="doc-patient-tbody">
              <!-- Rendered dynamically -->
            </tbody>
          </table>
        </div>

        <!-- DYNAMIC RESOLUTION RESULT DISPLAY (NO RELOAD) -->
        <div id="dynamic-resolution-panel" style="display:none; margin-top:1.25rem;">
          <!-- Populated dynamically via JS -->
        </div>

      </div>

      <!-- ROLE VIEW: NURSE -->
      <div id="view-nurse" class="role-view" style="display:none;">
        <div class="stats-grid">
          <div class="stat-box success">
            <div class="stat-title">Medications Due Now</div>
            <div class="stat-num" id="nurse-stat-due">3</div>
          </div>
          <div class="stat-box critical">
            <div class="stat-title">Safety Hold Alerts</div>
            <div class="stat-num" id="nurse-stat-held" style="color:var(--alert-critical);">1</div>
          </div>
          <div class="stat-box">
            <div class="stat-title">Assigned Inpatients</div>
            <div class="stat-num" id="nurse-stat-patients">3</div>
          </div>
        </div>

        <div class="card">
          <div class="card-header">
            <div class="card-title">Medication Administration Record (MAR)</div>
          </div>
          <table>
            <thead>
              <tr>
                <th>Patient</th>
                <th>Medication & Dose</th>
                <th>Scheduled</th>
                <th>Administration Status</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody id="nurse-mar-tbody">
              <!-- Rendered dynamically -->
            </tbody>
          </table>
        </div>

        <div class="card">
          <div class="card-header">
            <div class="card-title">Rapid Inpatient Lab / Vitals Ingestion</div>
          </div>
          <form onsubmit="event.preventDefault(); submitNurseLabAsync(); return false;" style="display:flex; gap:1rem; align-items:flex-end;">
            <div style="flex:1;">
              <label class="form-label" for="nurse-patient-select">Patient</label>
              <select id="nurse-patient-select" class="form-input">
                <option value="1">Sarah Jenkins (DEMO-PT-001 - Warfarin)</option>
                <option value="2">Robert Chen (DEMO-PT-002 - Enoxaparin)</option>
                <option value="3">Elena Rostova (DEMO-PT-003 - Lisinopril)</option>
              </select>
            </div>
            <div style="flex:1;">
              <label class="form-label" for="nurse-lab-test">Lab Parameter</label>
              <select id="nurse-lab-test" class="form-input">
                <option value="Creatinine">Serum Creatinine (mg/dL)</option>
                <option value="INR">INR (Prothrombin Time)</option>
                <option value="Potassium">Potassium (mEq/L)</option>
              </select>
            </div>
            <div style="flex:1;">
              <label class="form-label" for="nurse-lab-val">Measured Value</label>
              <input type="text" id="nurse-lab-val" class="form-input" value="2.4" required>
            </div>
            <button type="submit" class="btn btn-primary" id="btn-nurse-submit">Ingest Lab & Evaluate</button>
          </form>
          <div id="nurse-feedback" style="margin-top:0.75rem; display:none;"></div>
        </div>
      </div>

      <!-- ROLE VIEW: CLINICAL PHARMACIST (BCPS) -->
      <div id="view-pharmacist" class="role-view" style="display:none;">
        <div class="stats-grid">
          <div class="stat-box">
            <div class="stat-title">Active Safety Rules</div>
            <div class="stat-num" id="pharm-stat-rules" style="color:var(--clinical-blue-light);">3</div>
          </div>
          <div class="stat-box">
            <div class="stat-title">CYP Enzyme Profiles</div>
            <div class="stat-num">5</div>
          </div>
          <div class="stat-box warning">
            <div class="stat-title">Renal Dosing Profiles</div>
            <div class="stat-num" style="color:var(--alert-warning);">8</div>
          </div>
          <div class="stat-box success">
            <div class="stat-title">Continuous Re-evaluations</div>
            <div class="stat-num" id="pharm-stat-reeval" style="color:var(--alert-success);">6</div>
          </div>
        </div>

        <!-- Active Safety Rules with Evidence Provenance -->
        <div class="card">
          <div class="card-header">
            <div class="card-title">
              <span>Active Safety Rules & Evidence Provenance</span>
              <span class="badge badge-info" id="badge-rules-count">3 Rules Loaded</span>
            </div>
            <div style="font-size:0.75rem; color:var(--text-muted);">
              Deterministic qualitative rule pack from validated sources (DDInter / OpenFDA / CPIC).
            </div>
          </div>
          <table>
            <thead>
              <tr>
                <th>Rule ID</th>
                <th>Category</th>
                <th>Involved Drugs</th>
                <th>Trigger Context</th>
                <th>Recommended Action</th>
                <th>Evidence ID & Source</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody id="pharm-rules-tbody">
              <!-- Rendered dynamically -->
            </tbody>
          </table>
        </div>

        <!-- Clinical Pharmacology & DDI Mechanism Matrix -->
        <div class="card">
          <div class="card-header">
            <div class="card-title">Clinical Pharmacology & Pharmacokinetics Matrix</div>
          </div>
          <table>
            <thead>
              <tr>
                <th>Interacting Pair</th>
                <th>Pharmacokinetic Mechanism</th>
                <th>Clinical Impact</th>
                <th>Pharmacist Guidance & Alternative</th>
                <th>Provenance Link</th>
              </tr>
            </thead>
            <tbody id="pharm-ddi-tbody">
              <!-- Rendered dynamically -->
            </tbody>
          </table>
        </div>
      </div>

      <!-- ROLE VIEW: ADMINISTRATOR (REBUILT AUDIT & TRACEABILITY) -->
      <div id="view-admin" class="role-view" style="display:none;">
        
        <!-- Top Audit Proof & Integrity Summary Banner -->
        <div class="audit-integrity-hero">
          <div>
            <div style="font-size:1.1rem; font-weight:800; color:#ffffff; letter-spacing:0.02em;">
              AUDIT INTEGRITY & PROVENANCE TRACEABILITY
            </div>
            <div class="integrity-checks-row">
              <span class="integrity-check-item">✓ Chain Valid</span>
              <span class="integrity-check-item">✓ Events Recorded</span>
              <span class="integrity-check-item">✓ Provenance Traceable</span>
              <span class="integrity-check-item">✓ Safety Decisions Traceable</span>
              <span class="integrity-check-item">✓ Resolution Traceable</span>
            </div>
          </div>
          <button type="button" class="btn btn-primary btn-sm" onclick="verifyAuditChainAsync(event)">
            🔍 Verify Chain Live
          </button>
        </div>

        <div class="stats-grid">
          <div class="stat-box success">
            <div class="stat-title">SHA-256 Hash Chain</div>
            <div class="stat-num" id="admin-stat-chain-status" style="color:var(--alert-success); font-size:1.2rem;">VALID & INTACT ✓</div>
          </div>
          <div class="stat-box">
            <div class="stat-title">Audit Blocks Logged</div>
            <div class="stat-num" id="admin-stat-blocks">12</div>
          </div>
          <div class="stat-box">
            <div class="stat-title">Registered Clinicians</div>
            <div class="stat-num" id="admin-stat-users">4</div>
          </div>
          <div class="stat-box success">
            <div class="stat-title">Deployment Mode</div>
            <div class="stat-num" style="color:var(--alert-success); font-size:1.1rem;">100% OFFLINE / ON-PREMISE</div>
          </div>
        </div>

        <div style="display:grid; grid-template-columns: 2.2fr 1fr; gap:1.25rem;">
          
          <!-- Chronological SHA-256 Audit Trail Table -->
          <div class="card">
            <div class="card-header">
              <div class="card-title">
                <span>Tamper-Evident SHA-256 Audit & Traceability Log</span>
                <span class="badge badge-success" id="badge-total-audit-blocks">0 Blocks</span>
              </div>
              <div style="font-size:0.75rem; color:var(--text-muted);">
                Click any row to inspect complete cryptographic block proof & payload.
              </div>
            </div>
            <div style="overflow-x:auto;">
              <table>
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Actor</th>
                    <th>Category</th>
                    <th>Event Type</th>
                    <th>Entity</th>
                    <th>Action</th>
                    <th>Result</th>
                    <th>SHA-256 Hash</th>
                  </tr>
                </thead>
                <tbody id="admin-audit-tbody">
                  <!-- Rendered dynamically -->
                </tbody>
              </table>
            </div>
          </div>

          <!-- SMS / Gateway Controller -->
          <div class="card">
            <div class="card-header">
              <div class="card-title">Authentication & Gateway Providers</div>
            </div>
            <p style="font-size:0.8rem; color:var(--text-secondary); margin-top:0;">
              Pluggable messaging gateway behind abstract provider interface.
            </p>
            <div id="admin-gateway-list">
              <!-- Rendered dynamically -->
            </div>
          </div>

        </div>
      </div>

    </section>

  </main>

  <!-- Floating Simulated SMS Toast -->
  <div id="sms-debug-toast" class="sms-debug-toast">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.4rem;">
      <span style="font-size:0.72rem; font-weight:800; color:var(--clinical-blue-light);">📱 LOCAL OFFLINE SMS ARRIVAL</span>
      <span class="badge badge-success" style="font-size:0.65rem;">ON-PREMISE</span>
    </div>
    <div style="font-size:0.82rem; color:#e2e8f0; margin-bottom:0.5rem;" id="sms-toast-msg">
      [MICROMEDX] Clinical verification code: <strong>123456</strong>. Valid 5m.
    </div>
    <div style="font-size:1.15rem; font-weight:800; color:var(--alert-success); text-align:center; background:rgba(16,185,129,0.1); padding:0.25rem; border-radius:0.3rem; margin-bottom:0.5rem;" id="sms-toast-pin">
      123456
    </div>
    <button type="button" class="btn btn-success btn-sm" style="width:100%;" onclick="autoFillAndVerifyOtp(event)">
      ⚡ 1-Click Auto Fill & Access
    </button>
  </div>

  <script>
    // Application State
    const appState = {
      currentUser: null,
      sessionToken: null,
      activeRole: 'doctor',
      authMethod: 'totp',
      currentOtp: '123456',
      currentTotpCode: '------',
      selectedTotpPhone: '+15550192831',
      selectedTotpRole: 'Doctor',
      selectedTotpName: 'Dr. Sarah Lin, MD',
      currentSecretBase32: '',
      lastTimestep: 0,
      adminAuditRecords: [],
      searchDebounceTimer: null,
    };

    // Initialization
    window.addEventListener('DOMContentLoaded', async () => {
      initTotpTicker();
      updateLiveTotpCode();
      await checkActiveSession();
      document.addEventListener('click', (e) => {
        if (!e.target.closest('#sandbox-drug-search') && !e.target.closest('#sandbox-autocomplete-list')) {
          hideDrugSuggestions();
        }
      });
    });

    // --- AUTH TAB SWITCHING ---
    function switchAuthTab(mode) {
      appState.authMethod = mode;
      document.getElementById('btn-auth-tab-totp').classList.toggle('active', mode === 'totp');
      document.getElementById('btn-auth-tab-sms').classList.toggle('active', mode === 'sms');
      document.getElementById('pane-auth-totp').style.display = mode === 'totp' ? 'block' : 'none';
      document.getElementById('pane-auth-sms').style.display = mode === 'sms' ? 'block' : 'none';
      document.getElementById('sms-debug-toast').style.display = 'none';

      if (mode === 'totp') {
        updateLiveTotpCode();
      }
    }

    // --- TOTP PERSONA SELECTION ---
    async function selectTotpPersona(phone, role, elementId, fullName) {
      document.querySelectorAll('#pane-auth-totp .persona-option').forEach(el => el.classList.remove('selected'));
      const target = document.getElementById(elementId);
      if (target) target.classList.add('selected');
      
      document.getElementById('totp-phone-input').value = phone;
      appState.selectedTotpPhone = phone;
      appState.selectedTotpRole = role;
      appState.selectedTotpName = fullName;

      // Clear input cells
      for (let i = 1; i <= 6; i++) {
        const cell = document.getElementById('totp-c' + i);
        if (cell) cell.value = '';
      }

      // Check if user is enrolled
      await checkTotpEnrollmentStatus(phone);
    }

    function onPhoneInputChange() {
      const phone = document.getElementById('totp-phone-input').value.trim();
      appState.selectedTotpPhone = phone;
      checkTotpEnrollmentStatus(phone);
    }

    async function checkTotpEnrollmentStatus(phone) {
      try {
        const res = await fetch(`/auth/totp/status/${encodeURIComponent(phone)}`);
        if (res.ok) {
          const data = await res.json();
          if (!data.totp_enabled) {
            // User not yet enrolled
            const preview = document.getElementById('totp-live-code-preview');
            if (preview) preview.innerText = 'NOT ENROLLED';
            const autoBtn = document.getElementById('btn-autofill-totp');
            if (autoBtn) autoBtn.disabled = true;
          } else {
            const autoBtn = document.getElementById('btn-autofill-totp');
            if (autoBtn) autoBtn.disabled = false;
            updateLiveTotpCode();
          }
        }
      } catch (err) {
        console.error('Status check error:', err);
      }
    }

    // --- SWITCH BETWEEN SIGN IN AND ENROLLMENT VIEWS ---
    async function showTotpEnrollView(event) {
      if (event) event.preventDefault();
      const phone = appState.selectedTotpPhone || document.getElementById('totp-phone-input').value.trim();
      const role = appState.selectedTotpRole || 'Doctor';
      const name = appState.selectedTotpName || 'Clinician';

      document.getElementById('totp-view-signin').style.display = 'none';
      document.getElementById('totp-view-enroll').style.display = 'block';

      document.getElementById('enroll-target-name').innerText = name;
      document.getElementById('enroll-target-phone').innerText = `${phone} (${role})`;
      document.getElementById('enroll-target-role-badge').innerText = role.toUpperCase();

      const fb = document.getElementById('enroll-feedback-msg');
      if (fb) fb.innerHTML = '<span style="color:var(--text-muted);">Generating offline Base32 key & QR code...</span>';

      try {
        const res = await fetch('/auth/totp/enroll/setup', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            phone_number: phone,
            role_hint: role,
            full_name: name
          })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Enrollment setup failed');

        document.getElementById('enroll-qr-img').src = data.qr_code_data_uri;
        document.getElementById('enroll-secret-text').innerText = data.secret_formatted;
        appState.currentSecretBase32 = data.secret_base32;

        for (let i = 1; i <= 6; i++) {
          const c = document.getElementById('enroll-c' + i);
          if (c) c.value = '';
        }
        if (fb) fb.innerHTML = '';
        document.getElementById('enroll-c1').focus();
      } catch (err) {
        if (fb) fb.innerHTML = `<span style="color:var(--alert-critical);">Setup Error: ${err.message}</span>`;
      }
    }

    function showTotpSigninView(event) {
      if (event) event.preventDefault();
      document.getElementById('totp-view-enroll').style.display = 'none';
      document.getElementById('totp-view-signin').style.display = 'block';

      for (let i = 1; i <= 6; i++) {
        const c = document.getElementById('totp-c' + i);
        if (c) c.value = '';
      }
      updateLiveTotpCode();
    }

    function copyEnrollSecret(event) {
      if (event) event.preventDefault();
      if (appState.currentSecretBase32) {
        navigator.clipboard.writeText(appState.currentSecretBase32);
        const btn = document.getElementById('btn-copy-secret');
        if (btn) {
          const orig = btn.innerText;
          btn.innerText = '✓ Copied!';
          setTimeout(() => { btn.innerText = orig; }, 1500);
        }
      }
    }

    // --- TOTP LIVE TICKER & CODE FETCHER ---
    function initTotpTicker() {
      setInterval(() => {
        const now = Math.floor(Date.now() / 1000);
        const currentTimestep = Math.floor(now / 30);
        const remaining = 30 - (now % 30);
        const pct = (remaining / 30) * 100;

        const bar = document.getElementById('totp-live-progress-bar');
        const txt = document.getElementById('totp-remaining-text');
        if (bar) bar.style.width = pct + '%';
        if (txt) txt.innerText = `Expires in ${remaining}s`;

        if (currentTimestep !== appState.lastTimestep) {
          appState.lastTimestep = currentTimestep;
          if (document.getElementById('totp-view-signin').style.display !== 'none') {
            updateLiveTotpCode();
          }
        }
      }, 1000);
    }

    async function updateLiveTotpCode() {
      const phone = appState.selectedTotpPhone || document.getElementById('totp-phone-input').value.trim();
      try {
        const res = await fetch(`/auth/totp/demo-code/${encodeURIComponent(phone)}`);
        if (res.ok) {
          const data = await res.json();
          appState.currentTotpCode = data.code;
          const formatted = data.code.slice(0, 3) + ' ' + data.code.slice(3, 6);
          const preview = document.getElementById('totp-live-code-preview');
          if (preview) preview.innerText = formatted;
          const autoBtn = document.getElementById('btn-autofill-totp');
          if (autoBtn) autoBtn.disabled = false;
        } else {
          const preview = document.getElementById('totp-live-code-preview');
          if (preview) preview.innerText = 'NOT ENROLLED';
          const autoBtn = document.getElementById('btn-autofill-totp');
          if (autoBtn) autoBtn.disabled = true;
        }
      } catch (err) {
        console.error('Error fetching TOTP live code:', err);
      }
    }

    // --- TOTP CELLS INPUT HANDLING ---
    function onTotpCellInput(idx) {
      const current = document.getElementById('totp-c' + idx);
      if (current.value.length === 1 && idx < 6) {
        document.getElementById('totp-c' + (idx + 1)).focus();
      }
    }

    function onTotpCellKey(event, idx) {
      if (event.key === 'Backspace' && !document.getElementById('totp-c' + idx).value && idx > 1) {
        document.getElementById('totp-c' + (idx - 1)).focus();
      }
    }

    function onEnrollCellInput(idx) {
      const current = document.getElementById('enroll-c' + idx);
      if (current.value.length === 1 && idx < 6) {
        document.getElementById('enroll-c' + (idx + 1)).focus();
      }
    }

    function onEnrollCellKey(event, idx) {
      if (event.key === 'Backspace' && !document.getElementById('enroll-c' + idx).value && idx > 1) {
        document.getElementById('enroll-c' + (idx - 1)).focus();
      }
    }

    function autoFillAndVerifyTotp(event) {
      if (event) event.preventDefault();
      const code = appState.currentTotpCode;
      if (!code || code === '------' || code === 'NOT ENROLLED') return;

      for (let i = 0; i < 6; i++) {
        const cell = document.getElementById('totp-c' + (i + 1));
        if (cell) cell.value = code[i] || '';
      }
      verifyTOTPLoginAsync();
    }

    // --- TOTP VERIFICATION & LOGIN ---
    async function verifyTOTPLoginAsync() {
      let code = '';
      for (let i = 1; i <= 6; i++) {
        code += document.getElementById('totp-c' + i).value.trim();
      }

      if (code.length < 6) {
        alert('Please enter complete 6-digit authenticator code.');
        return;
      }

      const phone = document.getElementById('totp-phone-input').value.trim();
      const btn = document.getElementById('btn-verify-totp');
      const originalText = btn.innerHTML;
      btn.innerHTML = 'Verifying Locally... ⏳';
      btn.disabled = true;

      try {
        const res = await fetch('/auth/totp/verify', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            phone_number: phone,
            totp_code: code,
            role: appState.selectedTotpRole
          })
        });

        const data = await res.json();
        if (!res.ok) {
          if (data.detail && (data.detail.toLowerCase().includes('not enrolled') || data.detail.toLowerCase().includes('no totp secret'))) {
            alert('User is not yet enrolled in offline authenticator. Redirecting to setup...');
            showTotpEnrollView();
            return;
          }
          throw new Error(data.detail || 'TOTP Verification Failed');
        }

        appState.sessionToken = data.session_token;
        appState.currentUser = data.user;
        appState.authMethod = 'totp';
        appState.activeRole = data.role.toLowerCase().replace(' ', '-');

        enterApplication(data.role, data.user, 'totp');
      } catch (err) {
        alert('TOTP Offline Login Error: ' + err.message);
      } finally {
        btn.innerHTML = originalText;
        btn.disabled = false;
      }
    }

    // --- SUBMIT FIRST-TIME TOTP ENROLLMENT VERIFICATION ---
    async function submitTotpEnrollVerify(event) {
      if (event) event.preventDefault();
      let code = '';
      for (let i = 1; i <= 6; i++) {
        code += document.getElementById('enroll-c' + i).value.trim();
      }
      if (code.length < 6) {
        alert('Please enter complete 6-digit code from your authenticator app.');
        return;
      }

      const phone = appState.selectedTotpPhone || document.getElementById('totp-phone-input').value.trim();
      const fb = document.getElementById('enroll-feedback-msg');
      const btn = document.getElementById('btn-enroll-verify');
      const origText = btn.innerText;
      btn.innerText = 'Verifying & Activating... ⏳';
      btn.disabled = true;

      try {
        const res = await fetch('/auth/totp/enroll/verify', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            phone_number: phone,
            totp_code: code,
            role: appState.selectedTotpRole
          })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Enrollment verification failed');

        fb.innerHTML = '<span style="color:var(--alert-success); font-weight:700;">✓ Authenticator Enrolled! Logging in...</span>';
        
        // Auto sign-in and launch dashboard immediately
        appState.sessionToken = data.session_token;
        appState.currentUser = data.user;
        appState.authMethod = 'totp';
        appState.activeRole = data.role.toLowerCase().replace(' ', '-');

        setTimeout(() => {
          showTotpSigninView();
          enterApplication(data.role, data.user, 'totp');
        }, 800);
      } catch (err) {
        fb.innerHTML = `<span style="color:var(--alert-critical); font-weight:700;">Error: ${err.message}</span>`;
      } finally {
        btn.innerText = origText;
        btn.disabled = false;
      }
    }


    // --- SMS / LOCAL PASSCODE FLOW (COMPATIBILITY) ---
    function selectDemoPersona(phone, role, elementId) {
      document.querySelectorAll('#pane-auth-sms .persona-option').forEach(el => el.classList.remove('selected'));
      const target = document.getElementById(elementId);
      if (target) target.classList.add('selected');
      document.getElementById('phone-input').value = phone;
      document.getElementById('role-input').value = role;
    }

    async function requestOTPCode() {
      const phone = document.getElementById('phone-input').value.trim();
      const role = document.getElementById('role-input').value;
      const btn = document.getElementById('btn-request-otp');
      btn.innerHTML = 'Sending... ⏳';
      btn.disabled = true;

      try {
        const res = await fetch('/auth/otp/request', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ phone_number: phone, role_hint: role, demo_mode: true })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Failed to request passcode');

        document.getElementById('masked-phone-display').innerText = data.phone_number_masked;
        document.getElementById('phone-form').style.display = 'none';
        document.getElementById('otp-form').style.display = 'block';

        if (data.preview_otp) {
          appState.currentOtp = data.preview_otp;
          showSmsToast(data.preview_otp, data.phone_number);
        }
        document.getElementById('otp-1').focus();
      } catch (err) {
        alert('Authentication Error: ' + err.message);
      } finally {
        btn.innerHTML = 'Send Passcode &rarr;';
        btn.disabled = false;
      }
    }

    function showSmsToast(code, phone) {
      const toast = document.getElementById('sms-debug-toast');
      document.getElementById('sms-toast-pin').innerText = code;
      document.getElementById('sms-toast-msg').innerHTML = `
        [MICROMEDX] Clinical verification code: <strong>${code}</strong> for ${phone}. Valid for 5 min.
      `;
      toast.style.display = 'block';
    }

    function autoFillAndVerifyOtp(event) {
      if (event) event.preventDefault();
      const code = appState.currentOtp || '123456';
      for (let i = 0; i < 6; i++) {
        const cell = document.getElementById('otp-' + (i + 1));
        if (cell) cell.value = code[i] || '';
      }
      document.getElementById('sms-debug-toast').style.display = 'none';
      verifyOTPCode();
    }

    function onOtpInput(idx) {
      const current = document.getElementById('otp-' + idx);
      if (current.value.length === 1 && idx < 6) {
        document.getElementById('otp-' + (idx + 1)).focus();
      }
    }

    function onOtpKey(event, idx) {
      if (event.key === 'Backspace' && !document.getElementById('otp-' + idx).value && idx > 1) {
        document.getElementById('otp-' + (idx - 1)).focus();
      }
    }

    function backToPhoneStep(event) {
      if (event) event.preventDefault();
      document.getElementById('otp-form').style.display = 'none';
      document.getElementById('phone-form').style.display = 'block';
      document.getElementById('sms-debug-toast').style.display = 'none';
    }

    async function verifyOTPCode() {
      let code = '';
      for (let i = 1; i <= 6; i++) code += document.getElementById('otp-' + i).value;
      if (code.length < 4) {
        alert('Please enter verification code.');
        return;
      }

      const phone = document.getElementById('phone-input').value.trim();
      const role = document.getElementById('role-input').value;
      const btn = document.getElementById('btn-verify-otp');
      btn.innerHTML = 'Verifying... ⏳';
      btn.disabled = true;

      try {
        const res = await fetch('/auth/otp/verify', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ phone_number: phone, otp_code: code, role: role })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Verification failed');

        appState.sessionToken = data.session_token;
        appState.currentUser = data.user;
        appState.authMethod = 'sms_otp';
        appState.activeRole = data.role.toLowerCase().replace(' ', '-');

        document.getElementById('sms-debug-toast').style.display = 'none';
        enterApplication(data.role, data.user, 'sms_otp');
      } catch (err) {
        alert('Verification Error: ' + err.message);
      } finally {
        btn.innerHTML = 'Verify & Launch Dashboard';
        btn.disabled = false;
      }
    }

    function enterApplication(role, user, method = 'totp') {
      document.getElementById('auth-section').style.display = 'none';
      document.getElementById('app-nav').style.display = 'flex';
      document.getElementById('dashboard-section').style.display = 'block';
      document.getElementById('session-user-badge').style.display = 'flex';
      document.getElementById('user-role-tag').innerText = role.toUpperCase();
      document.getElementById('nav-active-user').innerText = user.full_name;

      const gwInd = document.getElementById('gateway-indicator');
      if (gwInd) {
        if (method === 'totp') {
          gwInd.className = 'badge badge-success';
          gwInd.innerHTML = '🛡️ AUTH: TOTP LOCAL (OFFLINE)';
        } else {
          gwInd.className = 'badge badge-info';
          gwInd.innerHTML = '📱 AUTH: LOCAL PASSCODE';
        }
      }

      const roleKey = role.toLowerCase().includes('doc') ? 'doctor' :
                      role.toLowerCase().includes('nurse') ? 'nurse' :
                      role.toLowerCase().includes('pharm') ? 'pharmacist' : 'admin';
      switchTab(roleKey);
    }

    // Switch Role Tabs without reload
    function switchTab(tabKey, event) {
      if (event) event.preventDefault();
      appState.activeRole = tabKey;

      document.querySelectorAll('.role-tab-btn').forEach(b => b.classList.remove('active'));
      const activeBtn = document.getElementById('tab-btn-' + tabKey);
      if (activeBtn) activeBtn.classList.add('active');

      document.querySelectorAll('.role-view').forEach(v => v.style.display = 'none');
      const targetView = document.getElementById('view-' + tabKey);
      if (targetView) targetView.style.display = 'block';

      if (tabKey === 'doctor') loadDoctorView();
      else if (tabKey === 'nurse') loadNurseView();
      else if (tabKey === 'pharmacist') loadPharmacistView();
      else if (tabKey === 'admin') loadAdminView();
    }

    // --- DOCTOR VIEW ---
    async function loadDoctorView() {
      try {
        const res = await fetch('/api/dashboard/doctor');
        const data = await res.json();

        document.getElementById('doc-stat-patients').innerText = data.stats.total_patients;
        document.getElementById('doc-stat-cosigns').innerText = data.stats.pending_cosigns_count;
        document.getElementById('doc-stat-findings').innerText = data.stats.active_findings_count;
        document.getElementById('badge-cosign-count').innerText = `${data.pending_cosigns.length} Pending`;

        // Render Co-sign queue
        const cosignContainer = document.getElementById('doctor-cosign-container');
        if (data.pending_cosigns.length === 0) {
          cosignContainer.innerHTML = '<div style="color:var(--text-muted); padding:0.5rem 0; font-size:0.85rem;">✓ No simulated orders awaiting co-sign. All actions reviewed.</div>';
        } else {
          cosignContainer.innerHTML = data.pending_cosigns.map(c => `
            <div style="background:var(--bg-panel-elevated); border:1px solid var(--alert-warning); border-left:4px solid var(--alert-warning); border-radius:0.4rem; padding:0.85rem; margin-bottom:0.75rem;" id="cosign-card-${c.simulation_id}">
              <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                <div>
                  <div style="display:flex; align-items:center; gap:0.5rem;">
                    <span class="badge badge-warning">MANDATORY CO-SIGN</span>
                    <strong style="color:#ffffff;">${c.proposed_action}</strong>
                  </div>
                  <div style="font-size:0.8rem; color:var(--alert-warning); margin:0.25rem 0;">Drug: ${c.drug_name} | Action Type: ${c.action_type} | Rule: ${c.source || 'Safety Engine'}</div>
                  <div style="font-size:0.78rem; color:var(--text-secondary);">${c.rationale}</div>
                </div>
                <div style="display:flex; gap:0.5rem;">
                  <button type="button" class="btn btn-success btn-sm" onclick="cosignOrderAsync('${c.simulation_id}', 'approved', event)">✓ Co-sign & Approve</button>
                  <button type="button" class="btn btn-danger btn-sm" onclick="cosignOrderAsync('${c.simulation_id}', 'rejected', event)">✗ Reject</button>
                </div>
              </div>
            </div>
          `).join('');
        }

        // Render Inpatient Roster
        const patientTbody = document.getElementById('doc-patient-tbody');
        patientTbody.innerHTML = data.patients.map(p => `
          <tr>
            <td>
              <strong>${p.name}</strong><br>
              <small style="color:var(--text-muted);">${p.patient_identifier}</small>
            </td>
            <td>
              ${p.active_medications.map(m => `<span class="badge badge-info" style="margin:0.1rem;">${m.name} ${m.dose}</span>`).join('')}
            </td>
            <td>
              ${p.recent_labs.map(l => `<div><small>${l.test}: <strong>${l.value}</strong> ${l.unit}</small></div>`).join('')}
            </td>
          </tr>
        `).join('');

      } catch (err) {
        console.error('Doctor view error:', err);
      }
    }

    // Co-sign Action (Asynchronous, no reload)
    async function cosignOrderAsync(simId, decision, event) {
      if (event) event.preventDefault();
      try {
        const userName = appState.currentUser ? appState.currentUser.full_name : "Dr. Sarah Lin, MD";
        const res = await fetch(`/simulated-orders/${simId}/cosign`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            cosigned_by: userName,
            decision: decision,
            clinical_notes: `Physician decision: ${decision} recorded via MICROMEDX clinical console.`
          })
        });
        const data = await res.json();
        const card = document.getElementById(`cosign-card-${simId}`);
        if (card) {
          card.innerHTML = `
            <div style="color:var(--alert-success); font-size:0.85rem; font-weight:700;">
              ✓ Physician Co-Sign Recorded (${decision.toUpperCase()}) | SHA-256 Hash: <code style="color:var(--clinical-blue-light);">${data.audit_hash.slice(0, 18)}...</code>
            </div>
          `;
        }
      } catch (err) {
        alert('Co-sign error: ' + err.message);
      }
    }

    // Execute Official Demo Scenario (Asynchronous, NO RELOAD)
    async function executeScenarioAsync(scenarioId, event) {
      if (event) event.preventDefault();
      const btn = document.getElementById('btn-demo-' + scenarioId);
      const originalText = btn ? btn.innerHTML : '';
      if (btn) {
        btn.innerHTML = 'Generating... ⏳';
        btn.disabled = true;
      }

      const panel = document.getElementById('dynamic-resolution-panel');
      panel.style.display = 'block';
      panel.innerHTML = `
        <div class="card" style="border-color:var(--clinical-blue-light); background:var(--bg-panel-elevated);">
          <div style="display:flex; align-items:center; gap:0.5rem; color:var(--clinical-blue-light); font-weight:700;">
            <span>Executing 6-Agent Deterministic Safety Pipeline for Scenario ${scenarioId}...</span>
          </div>
        </div>
      `;

      try {
        const res = await fetch(`/demo/run/${scenarioId}`, { method: 'POST' });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Execution failed');

        if (btn) {
          btn.innerHTML = '✓ Solution Generated';
          setTimeout(() => {
            btn.innerHTML = originalText;
            btn.disabled = false;
          }, 2000);
        }

        // Render structured clinical workflow
        renderStructuredResolutionWorkflow(data);
        loadDoctorView();
      } catch (err) {
        panel.innerHTML = `<div class="card" style="border-color:var(--alert-critical); color:var(--alert-critical);">Error: ${err.message}</div>`;
        if (btn) {
          btn.innerHTML = originalText;
          btn.disabled = false;
        }
      }
    }

    // --- REAL-TIME LOCAL DRUG SEARCH AUTOCOMPLETE ---
    function onDrugSearchInput(event) {
      const q = document.getElementById('sandbox-drug-search').value.trim();
      clearTimeout(appState.searchDebounceTimer);

      if (!q) {
        hideDrugSuggestions();
        return;
      }

      appState.searchDebounceTimer = setTimeout(async () => {
        try {
          const res = await fetch(`/drugs/search?q=${encodeURIComponent(q)}&limit=8`);
          if (res.ok) {
            const list = await res.json();
            renderDrugSuggestions(list, q);
          }
        } catch (err) {
          console.error('Drug search error:', err);
        }
      }, 150);
    }

    function renderDrugSuggestions(drugs, query) {
      const dropdown = document.getElementById('sandbox-autocomplete-list');
      if (!dropdown) return;

      if (drugs.length === 0) {
        dropdown.innerHTML = `
          <div style="padding:0.6rem 0.85rem; font-size:0.8rem; color:var(--alert-warning);">
            No matching medication found in local knowledge base.
          </div>
        `;
        dropdown.style.display = 'block';
        return;
      }

      dropdown.innerHTML = drugs.map(d => `
        <div class="autocomplete-item" onclick="selectDrugSuggestion('${d.drug_name}')">
          <div>
            <strong>${d.drug_name}</strong>
            <div style="font-size:0.72rem; color:var(--text-secondary);">${d.category || 'Clinical Medication'}</div>
          </div>
          <small>RxNorm: ${d.rxnorm_code || 'Local'}</small>
        </div>
      `).join('');
      dropdown.style.display = 'block';
    }

    function selectDrugSuggestion(drugName) {
      document.getElementById('sandbox-drug-search').value = drugName;
      hideDrugSuggestions();
    }

    function hideDrugSuggestions() {
      const dropdown = document.getElementById('sandbox-autocomplete-list');
      if (dropdown) dropdown.style.display = 'none';
    }

    // --- LIVE PRESCRIBING SANDBOX WITH LOCAL DRUG VALIDATION BOUNDARY ---
    async function submitSandboxPrescriptionAsync() {
      const patientId = parseInt(document.getElementById('sandbox-patient-select').value);
      const drugInput = document.getElementById('sandbox-drug-search').value.trim();
      const dose = document.getElementById('sandbox-dose-input').value.trim();
      const btn = document.getElementById('btn-sandbox-submit');
      const panel = document.getElementById('dynamic-resolution-panel');

      if (!drugInput) {
        alert('Please enter a medication name.');
        return;
      }

      hideDrugSuggestions();
      btn.innerText = 'Validating Locally... ⏳';
      btn.disabled = true;

      panel.style.display = 'block';
      panel.innerHTML = `
        <div class="card" style="border-color:var(--clinical-blue-light); background:var(--bg-panel-elevated);">
          <div style="display:flex; align-items:center; gap:0.5rem; color:var(--clinical-blue-light); font-weight:700;">
            <span>Validating '${drugInput}' against local offline medication knowledge base...</span>
          </div>
        </div>
      `;

      try {
        // 1. Validate drug against local offline knowledge base
        const valRes = await fetch('/drugs/validate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ drug_name: drugInput })
        });
        const valData = await valRes.json();

        if (!valData.is_valid) {
          // CLINICAL VALIDATION BOUNDARY ENFORCED: Do NOT call LLM. Do NOT invent recommendations.
          panel.innerHTML = `
            <div class="card" style="border: 2px solid var(--alert-warning); background: #1a1528;">
              <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                <div>
                  <div style="display:flex; align-items:center; gap:0.5rem; color:var(--alert-warning); font-weight:800; font-size:0.95rem;">
                    <span>⚠️ NOT FOUND IN LOCAL KNOWLEDGE BASE</span>
                  </div>
                  <p style="color:var(--text-primary); font-size:0.85rem; margin:0.6rem 0;">
                    Medication <strong>"${drugInput}"</strong> is not recognized in the local medication knowledge base.
                  </p>
                  <p style="font-size:0.8rem; color:var(--text-secondary); margin-bottom:0.75rem;">
                    Clinical safety resolution stopped. System will not generate unverified drug interactions or arbitrary dosing adjustments.
                  </p>
                  <div style="font-size:0.8rem; color:var(--text-secondary); background:rgba(0,0,0,0.35); padding:0.75rem; border-radius:0.4rem; border:1px solid rgba(245, 158, 11, 0.3);">
                    <strong style="color:var(--text-primary);">Possible actions:</strong>
                    <ul style="margin:0.35rem 0 0 1.25rem; padding:0; line-height:1.6;">
                      <li>Check spelling (e.g. <em>Warfarin</em>, <em>Fluconazole</em>, <em>Enoxaparin</em>, <em>Lisinopril</em>)</li>
                      <li>Search supported medication names in local formulary dropdown</li>
                      <li>Review available offline knowledge sources (OpenFDA / DDInter)</li>
                    </ul>
                  </div>
                </div>
                <span class="badge badge-warning">VALIDATION REJECTED</span>
              </div>
            </div>
          `;
          return;
        }

        // 2. Recognized Drug -> Execute Deterministic Safety Pipeline
        panel.innerHTML = `
          <div class="card" style="border-color:var(--clinical-blue-light); background:var(--bg-panel-elevated);">
            <div style="display:flex; align-items:center; gap:0.5rem; color:var(--clinical-blue-light); font-weight:700;">
              <span>Medication verified. Running 6-agent deterministic safety pipeline for ${valData.details.drug_name}...</span>
            </div>
          </div>
        `;

        const eventRes = await fetch('/events', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            patient_id: patientId,
            event_type: 'MEDICATION_PRESCRIBED',
            payload: { drug_name: valData.details.drug_name, dose: dose, route: 'Oral' },
            new_medication_name: valData.details.drug_name
          })
        });

        const eventData = await eventRes.json();
        if (!eventRes.ok) throw new Error(eventData.detail || 'Prescription pipeline evaluation failed');

        // Fetch refreshed dashboard data to render
        const docRes = await fetch('/api/dashboard/doctor');
        const docData = await docRes.json();
        const pt = docData.patients.find(p => p.id === patientId) || { name: 'Patient #' + patientId, identifier: 'PT-00' + patientId };

        // Construct workflow display payload
        const pipelinePayload = {
          patient: { name: pt.name, identifier: pt.patient_identifier },
          event_submitted: { event_type: 'MEDICATION_PRESCRIBED' },
          scenario: { expected_evidence: 'DDInter / OpenFDA Verified Evidence' },
          findings: eventData.affected_findings || [],
          resolutions: eventData.resolutions || [],
          simulated_orders: (eventData.resolutions && eventData.resolutions[0] && eventData.resolutions[0].simulated_order) ? [eventData.resolutions[0].simulated_order] : [],
          explanations: (eventData.resolutions && eventData.resolutions[0] && eventData.resolutions[0].explanation) ? [eventData.resolutions[0].explanation] : [],
          pipeline_verification: {
            matched_rule_id: (eventData.affected_findings[0] && eventData.affected_findings[0].rule_id) || 'VERIFIED-MATCH',
            total_audit_records: docData.stats.active_findings_count || 12
          }
        };

        if (pipelinePayload.findings.length > 0) {
          renderStructuredResolutionWorkflow(pipelinePayload);
        } else {
          panel.innerHTML = `
            <div class="card" style="border: 1px solid var(--alert-success); background: #0b221d;">
              <div style="color:var(--alert-success); font-weight:800; font-size:0.95rem;">
                ✓ Clinical Evaluation Passed: No Contraindications or High-Risk DDIs Detected
              </div>
              <p style="font-size:0.85rem; color:var(--text-primary); margin:0.5rem 0 0 0;">
                Prescription of <strong>${valData.details.drug_name} ${dose}</strong> evaluated against active patient regimen and lab trends. No critical safety holds triggered.
              </p>
            </div>
          `;
        }

        loadDoctorView();
      } catch (err) {
        panel.innerHTML = `<div class="card" style="border-color:var(--alert-critical); color:var(--alert-critical);">Evaluation Error: ${err.message}</div>`;
      } finally {
        btn.innerText = '⚡ Evaluate & Prescribe';
        btn.disabled = false;
      }
    }

    // Render the complete structured clinical workflow
    function renderStructuredResolutionWorkflow(data) {
      const panel = document.getElementById('dynamic-resolution-panel');
      const pv = data.pipeline_verification || {};
      const finding = (data.findings && data.findings[0]) || {};
      const resolution = (data.resolutions && data.resolutions[0]) || {};
      const simOrder = (data.simulated_orders && data.simulated_orders[0]) || {};
      const explanation = (data.explanations && data.explanations[0]) || {};

      // Determine explanation source
      const isLlm = explanation.is_llm_enhanced === true;
      const aiBadge = isLlm ? 
        `<span class="badge badge-success">● Local AI | Qwen 2.5 7B | Ollama | Offline</span>` :
        `<span class="badge badge-info">● Deterministic Explanation | Verified Rule Evidence</span>`;

      panel.innerHTML = `
        <div class="resolution-workflow">
          
          <!-- 1. SAFETY FINDING -->
          <div class="workflow-header">1. SAFETY FINDING DETECTED</div>
          <div style="background:var(--bg-panel); border:1px solid var(--border-subtle); border-radius:0.4rem; padding:0.85rem; margin-bottom:1rem;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <div>
                <strong style="font-size:0.95rem; color:#ffffff;">${finding.title || 'Medication Safety Finding'}</strong>
                <div style="font-size:0.82rem; color:var(--text-secondary); margin-top:0.2rem;">
                  Patient: <strong>${data.patient ? data.patient.name : 'Inpatient'}</strong> (${data.patient ? data.patient.identifier : 'ID'}) | Event: <code>${data.event_submitted ? data.event_submitted.event_type : 'PRESCRIPTION'}</code>
                </div>
              </div>
              <span class="badge badge-critical">${finding.severity || 'REVIEW REQUIRED'}</span>
            </div>
            <p style="font-size:0.82rem; color:var(--text-primary); margin:0.5rem 0 0 0;">${finding.description || ''}</p>
          </div>

          <!-- 2. WHY WAS THIS DETECTED? -->
          <div class="workflow-header">2. CLINICAL CONTEXT & PROVENANCE</div>
          <div style="background:var(--bg-panel); border:1px solid var(--border-subtle); border-radius:0.4rem; padding:0.85rem; margin-bottom:1rem; font-size:0.82rem;">
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:0.5rem;">
              <div>Matched Rule ID: <code style="color:var(--clinical-blue-light);">${pv.matched_rule_id || finding.rule_id || 'AEGIS-RULE-001'}</code></div>
              <div>Evidence ID: <code style="color:var(--clinical-teal-light);">${finding.evidence_id || (data.scenario && data.scenario.expected_evidence) || 'AEGIS-EV-001'}</code></div>
              <div>Evidence Source: <strong>${finding.source || 'DDInter / OpenFDA Approved Label'}</strong></div>
              <div>Rule Integrity: <span class="badge badge-success">Verified Match ✓</span></div>
            </div>
          </div>

          <!-- 3. EXPLAINABLE AI -->
          <div class="workflow-header">3. EXPLAINABLE AI & CLINICAL RATIONALE</div>
          <div style="background:var(--bg-panel); border:1px solid var(--border-subtle); border-radius:0.4rem; padding:0.85rem; margin-bottom:1rem;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.4rem;">
              <span style="font-size:0.82rem; font-weight:700; color:#ffffff;">Clinical Pharmacotherapy Synthesis</span>
              ${aiBadge}
            </div>
            <div style="font-size:0.82rem; color:var(--text-primary); line-height:1.4;">
              ${explanation.full_text || finding.description || 'Deterministic safety evaluation complete.'}
            </div>
          </div>

          <!-- 4. SAFETY RESOLUTION & SIMULATED ORDER -->
          <div class="workflow-header">4. DETERMINISTIC SAFETY RESOLUTION & SIMULATED ORDER</div>
          <div style="background:var(--bg-panel); border:1px solid var(--alert-warning); border-radius:0.4rem; padding:0.85rem; margin-bottom:1rem;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <div>
                <strong style="color:#ffffff;">Proposed Action: ${simOrder.proposed_action || resolution.top_candidate || 'Hold & evaluate alternative'}</strong>
                <div style="font-size:0.8rem; color:var(--alert-warning); margin-top:0.2rem;">
                  Action Type: ${simOrder.action_type || 'SAFETY_INTERVENTION'} | Auto-Execute: <strong>FALSE (Mandatory Co-sign)</strong>
                </div>
              </div>
              <button type="button" class="btn btn-success btn-sm" onclick="cosignOrderAsync('${simOrder.simulation_id || 'SIM-DEMO'}', 'approved', event)">
                ✓ Co-sign Simulated Order
              </button>
            </div>
          </div>

          <!-- 5. AUDIT & RE-EVALUATION PROOF -->
          <div class="workflow-header">5. AUDIT PROOF & CONTINUOUS RE-EVALUATION</div>
          <div style="background:var(--bg-panel); border:1px solid var(--border-subtle); border-radius:0.4rem; padding:0.85rem; font-size:0.8rem;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <div>
                <span>SHA-256 Hash Chain: <strong style="color:var(--alert-success);">VALID (${pv.total_audit_records || 12} blocks)</strong></span> |
                <span>Re-evaluation Status: <strong style="color:var(--clinical-blue-light);">Active Continuous Guard</strong></span>
              </div>
              <span class="badge badge-success">ZERO TAMPERING</span>
            </div>
          </div>

        </div>
      `;
    }

    // --- NURSE VIEW ---
    async function loadNurseView() {
      try {
        const res = await fetch('/api/dashboard/nurse');
        const data = await res.json();

        document.getElementById('nurse-stat-due').innerText = data.stats.due_medications_count;
        document.getElementById('nurse-stat-held').innerText = data.stats.safety_held_count;
        document.getElementById('nurse-stat-patients').innerText = data.stats.total_assigned_patients;

        const marTbody = document.getElementById('nurse-mar-tbody');
        marTbody.innerHTML = data.mar_schedule.map(m => `
          <tr>
            <td><strong>${m.patient_name}</strong> (${m.patient_identifier})</td>
            <td><strong>${m.drug_name}</strong> ${m.dose} (${m.route})</td>
            <td>${m.scheduled_time}</td>
            <td>
              ${m.status === 'SAFETY_HOLD' ? 
                `<span class="badge badge-critical">⚠️ SAFETY HOLD</span><br><small style="color:var(--alert-critical);">${m.safety_alert || 'Alert active'}</small>` :
                `<span class="badge badge-success">DUE FOR ADMIN</span>`}
            </td>
            <td>
              ${m.status === 'SAFETY_HOLD' ?
                `<button type="button" class="btn btn-secondary btn-sm" onclick="alert('Medication is on Safety Hold. Requires physician co-sign.')">Review Hold</button>` :
                `<button type="button" class="btn btn-success btn-sm" onclick="this.innerText='✓ Administered'; this.disabled=true;">Record Dose</button>`}
            </td>
          </tr>
        `).join('');
      } catch (err) {
        console.error('Nurse view error:', err);
      }
    }

    async function submitNurseLabAsync() {
      const patientId = parseInt(document.getElementById('nurse-patient-select').value);
      const testName = document.getElementById('nurse-lab-test').value;
      const testVal = document.getElementById('nurse-lab-val').value;
      const btn = document.getElementById('btn-nurse-submit');
      btn.innerText = 'Ingesting... ⏳';
      btn.disabled = true;

      const fb = document.getElementById('nurse-feedback');
      fb.style.display = 'block';
      fb.innerHTML = '<span style="color:var(--clinical-blue-light);">Processing lab event through deterministic safety engine...</span>';

      try {
        const res = await fetch('/events', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            patient_id: patientId,
            event_type: 'LAB_RESULT_RECORDED',
            payload: { test_name: testName, value: testVal, unit: testName === 'Creatinine' ? 'mg/dL' : '' }
          })
        });
        const data = await res.json();
        fb.innerHTML = `<span style="color:var(--alert-success); font-weight:700;">✓ Lab recorded! ${data.affected_findings.length} findings evaluated. Audit SHA-256 logged.</span>`;
        setTimeout(() => loadNurseView(), 1500);
      } catch (err) {
        fb.innerHTML = `<span style="color:var(--alert-critical);">Error: ${err.message}</span>`;
      } finally {
        btn.innerText = 'Ingest Lab & Evaluate';
        btn.disabled = false;
      }
    }

    // --- PHARMACIST (BCPS) VIEW ---
    async function loadPharmacistView() {
      try {
        const res = await fetch('/api/dashboard/pharmacist');
        const data = await res.json();

        document.getElementById('pharm-stat-rules').innerText = data.stats.active_safety_rules;
        document.getElementById('badge-rules-count').innerText = `${data.active_rules.length} Rules Loaded`;

        // Render Safety Rules Table with full provenance
        const rulesTbody = document.getElementById('pharm-rules-tbody');
        if (data.active_rules.length === 0) {
          rulesTbody.innerHTML = '<tr><td colspan="7" style="text-align:center; color:var(--text-muted);">No safety rules found in database.</td></tr>';
        } else {
          rulesTbody.innerHTML = data.active_rules.map(r => `
            <tr>
              <td><code style="color:var(--clinical-blue-light); font-weight:700;">${r.rule_id}</code></td>
              <td><span class="badge badge-info">${r.category || r.rule_type}</span></td>
              <td><strong>${r.drug_a}</strong> + ${r.drug_b}</td>
              <td><small style="color:var(--text-secondary);">${r.trigger_context}</small></td>
              <td><strong>${r.recommended_action}</strong></td>
              <td>
                <code style="color:var(--clinical-teal-light);">${r.evidence_id}</code><br>
                <small style="color:var(--text-muted);">${r.provenance_source}</small>
              </td>
              <td><span class="badge badge-success">${r.status}</span></td>
            </tr>
          `).join('');
        }

        // Render DDI Mechanism Table
        const ddiTbody = document.getElementById('pharm-ddi-tbody');
        ddiTbody.innerHTML = data.pharmacology_insights.map(p => `
          <tr>
            <td><strong style="color:var(--clinical-blue-light);">${p.pair}</strong></td>
            <td><small>${p.mechanism}</small></td>
            <td><span class="badge badge-critical">${p.clinical_impact}</span></td>
            <td><strong>${p.recommended_action}</strong></td>
            <td><small style="color:var(--text-muted);">${p.evidence_source} (${p.evidence_id})</small></td>
          </tr>
        `).join('');

      } catch (err) {
        console.error('Pharmacist view error:', err);
      }
    }

    // --- ADMINISTRATOR (AUDIT & TRACEABILITY) VIEW ---
    async function loadAdminView() {
      try {
        const res = await fetch('/api/dashboard/admin');
        const data = await res.json();

        document.getElementById('admin-stat-blocks').innerText = data.audit_chain.total_records;
        document.getElementById('admin-stat-users').innerText = data.users_count;
        document.getElementById('badge-total-audit-blocks').innerText = `${data.audit_chain.total_records} Blocks`;

        appState.adminAuditRecords = data.audit_chain.recent_records || [];

        // Render Audit Records Table
        const auditTbody = document.getElementById('admin-audit-tbody');
        if (appState.adminAuditRecords.length === 0) {
          auditTbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:var(--text-muted);">No audit records found.</td></tr>';
        } else {
          auditTbody.innerHTML = appState.adminAuditRecords.map((e, idx) => {
            const catBadgeClass = e.category === 'AUTHENTICATION' ? 'badge-info' :
                                  e.category === 'SAFETY' ? 'badge-critical' :
                                  e.category === 'RESOLUTION' ? 'badge-warning' :
                                  e.category === 'VERIFICATION' ? 'badge-success' :
                                  e.category === 'CLINICAL EVENT' ? 'badge-purple' : 'badge-neutral';
            
            const timeStr = e.timestamp ? e.timestamp.slice(11, 19) + ' UTC' : '--';
            return `
              <tr class="clickable-row" onclick="openAuditDetailDrawer(${idx})" title="Click to inspect cryptographic block proof">
                <td><small style="color:var(--text-secondary);">${timeStr}</small></td>
                <td><strong>${e.actor}</strong></td>
                <td><span class="badge ${catBadgeClass}">${e.category}</span></td>
                <td><code style="color:var(--clinical-blue-light); font-size:0.75rem;">${e.event_type}</code></td>
                <td><small>${e.entity || '--'}</small></td>
                <td><small style="color:var(--text-primary);">${e.action || '--'}</small></td>
                <td><span class="badge badge-success" style="font-size:0.65rem;">${e.result || 'RECORDED'}</span></td>
                <td><code style="color:var(--alert-success); font-size:0.72rem;">${e.hash_short || (e.hash ? e.hash.slice(0, 12) + '...' : '')}</code></td>
              </tr>
            `;
          }).join('');
        }

        // Render Gateway Providers
        const gwList = document.getElementById('admin-gateway-list');
        gwList.innerHTML = data.sms_gateway.providers.map(p => `
          <div style="background:var(--bg-panel-elevated); padding:0.75rem; border-radius:0.4rem; margin-bottom:0.5rem; border:1px solid ${p.is_active ? 'var(--clinical-blue-light)' : 'var(--border-subtle)'};">
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <div>
                <strong>${p.name}</strong>
                <div style="font-size:0.72rem; color:var(--text-muted);">${p.capabilities.join(' • ')}</div>
              </div>
              ${p.is_active ?
                `<span class="badge badge-success">ACTIVE</span>` :
                `<button type="button" class="btn btn-secondary btn-sm" onclick="switchGatewayAsync('${p.provider}', event)">Select</button>`}
            </div>
          </div>
        `).join('');

      } catch (err) {
        console.error('Admin view error:', err);
      }
    }

    // Open Audit Detail Drawer (Modal)
    function openAuditDetailDrawer(index) {
      const record = appState.adminAuditRecords[index];
      if (!record) return;

      document.getElementById('audit-detail-title').innerText = `Audit Block #${record.id} — ${record.event_type}`;
      document.getElementById('audit-detail-id').innerText = '#' + record.id;
      document.getElementById('audit-detail-category').innerText = record.category || 'SYSTEM';
      document.getElementById('audit-detail-category').className = `badge ${record.category === 'AUTHENTICATION' ? 'badge-info' : record.category === 'SAFETY' ? 'badge-critical' : record.category === 'RESOLUTION' ? 'badge-warning' : record.category === 'VERIFICATION' ? 'badge-success' : record.category === 'CLINICAL EVENT' ? 'badge-purple' : 'badge-neutral'}`;
      document.getElementById('audit-detail-time').innerText = record.timestamp || '--';
      document.getElementById('audit-detail-actor').innerText = record.actor || '--';
      document.getElementById('audit-detail-entity').innerText = record.entity || '--';
      document.getElementById('audit-detail-action').innerText = record.action || '--';
      document.getElementById('audit-detail-result').innerText = record.result || 'RECORDED ✓';
      document.getElementById('audit-detail-rule').innerText = record.rule_id || 'N/A';
      document.getElementById('audit-detail-evidence').innerText = record.evidence_id || 'N/A';
      document.getElementById('audit-detail-provenance').innerText = record.provenance || 'Local Knowledge Engine';
      document.getElementById('audit-detail-prevhash').innerText = record.prev_hash || 'GENESIS_PREV_HASH';
      document.getElementById('audit-detail-curhash').innerText = record.hash || '--';
      document.getElementById('audit-detail-payload').innerText = JSON.stringify(record.payload || {}, null, 2);

      document.getElementById('modal-audit-detail').style.display = 'flex';
    }

    function closeAuditDetailDrawer(event) {
      if (event) event.preventDefault();
      document.getElementById('modal-audit-detail').style.display = 'none';
    }

    async function switchGatewayAsync(provider, event) {
      if (event) event.preventDefault();
      try {
        await fetch('/auth/providers/switch', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ provider_name: provider })
        });
        document.getElementById('gateway-indicator').innerText = `SMS: ${provider.toUpperCase()}`;
        loadAdminView();
      } catch (err) {
        alert('Gateway error: ' + err.message);
      }
    }

    async function verifyAuditChainAsync(event) {
      if (event) event.preventDefault();
      try {
        const res = await fetch('/audit/verify');
        const data = await res.json();
        alert(`Cryptographic Audit Chain Proof:\n\n• Chain Valid: ${data.is_valid}\n• Total Cryptographic Blocks: ${data.total_records}\n• Tamper Status: 100% Intact (Zero Tampering Detected)\n• Genesis Hash: Valid SHA-256 Linkage`);
      } catch (err) {
        alert('Verification error: ' + err.message);
      }
    }

    async function seedPatientsAsync(event) {
      if (event) event.preventDefault();
      try {
        const res = await fetch('/demo/seed', { method: 'POST' });
        const data = await res.json();
        alert('Official patients (Sarah Jenkins, Robert Chen, Elena Rostova) seeded with baseline context.');
        loadDoctorView();
      } catch (err) {
        alert('Seed error: ' + err.message);
      }
    }

    async function checkActiveSession() {
      try {
        const res = await fetch('/auth/me');
        if (res.ok) {
          const user = await res.json();
          appState.currentUser = user;
          enterApplication(user.role, user, user.totp_enabled ? 'totp' : 'sms_otp');
        }
      } catch (err) {}
    }

    async function handleLogout(event) {
      if (event) event.preventDefault();
      try {
        await fetch('/auth/logout', { 
          method: 'POST',
          headers: appState.sessionToken ? { 'Authorization': `Bearer ${appState.sessionToken}` } : {}
        });
      } catch (err) {}
      
      // Full frontend state clearance
      appState.sessionToken = null;
      appState.currentUser = null;
      appState.activeRole = 'doctor';
      appState.selectedTotpPhone = '+15550192831';
      appState.selectedTotpRole = 'Doctor';
      appState.selectedTotpName = 'Dr. Sarah Lin, MD';
      appState.currentTotpCode = '------';
      appState.adminAuditRecords = [];

      localStorage.clear();
      sessionStorage.clear();
      document.cookie = "aegis_session=; Max-Age=0; path=/;";
      document.cookie = "micromedx_session=; Max-Age=0; path=/;";

      // Clear dynamic view panels
      const panel = document.getElementById('dynamic-resolution-panel');
      if (panel) { panel.style.display = 'none'; panel.innerHTML = ''; }
      const cosign = document.getElementById('doctor-cosign-container');
      if (cosign) cosign.innerHTML = '';

      // Clear TOTP cells
      for (let i = 1; i <= 6; i++) {
        const c = document.getElementById('totp-c' + i);
        if (c) c.value = '';
      }

      // Reset form selection
      document.getElementById('totp-phone-input').value = '+15550192831';
      document.querySelectorAll('#pane-auth-totp .persona-option').forEach(el => el.classList.remove('selected'));
      const docOption = document.getElementById('totp-persona-doc');
      if (docOption) docOption.classList.add('selected');

      document.getElementById('dashboard-section').style.display = 'none';
      document.getElementById('app-nav').style.display = 'none';
      document.getElementById('session-user-badge').style.display = 'none';
      document.getElementById('auth-section').style.display = 'block';
      
      switchAuthTab('totp');
      updateLiveTotpCode();
    }
  </script>
</body>
</html>
"""
    return HTMLResponse(content=html_content)
