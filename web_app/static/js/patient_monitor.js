/* FlapGuard AI v4.0 Clinical Frontend Engine (Zero External Dependencies):
- HiDPI Native-Scale Multisensor Perfusion Chart (SpO2, Delta-T, Doppler, AI Risk)
- Adaptive Day/Night Canvas Theme Sync + Shaded Clinical Threshold Bands
- Guided CIE-LAB Flap ROI Scanner & Live Deterioration Simulator
*/

(function () {
  function getCsrfToken() {
    const m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute("content") || "" : "";
  }

  function cssVar(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name);
    return (v && v.trim()) || fallback;
  }

  const METRIC_CFG = {
    spo2: {
      caption: "Tissue SpO₂ (%) · Shaded safe (>92%) & critical (<90%) clinical bands",
      colorVar: "--moderate",
      threshold: 90,
      fmtVal: (v) => v.toFixed(1) + "%",
      fmtTick: (v) => Math.round(v) + "%",
      bounds: (vals) => {
        const lo = vals.length ? Math.min(...vals) : 90;
        const minY = lo < 50 ? Math.max(0, lo - 10) : 50;
        return { minY, maxY: 100, ticks: [minY, (minY + 90) / 2, 90, 100], safe: [92, 100], danger: [minY, 90] };
      },
    },
    temp_delta: {
      caption: "Thermal Drop ΔT (°C) · Shaded safe (<1.5°C) & critical (≥1.8°C) bands",
      colorVar: "--high",
      threshold: 1.8,
      fmtVal: (v) => v.toFixed(1) + "°C",
      fmtTick: (v) => v.toFixed(1) + "°",
      bounds: (vals) => {
        const hi = vals.length ? Math.max(...vals) : 0;
        const maxY = Math.max(4.0, hi * 1.15);
        return { minY: 0, maxY, ticks: [0, 1.8, maxY], safe: [0, 1.5], danger: [1.8, maxY] };
      },
    },
    doppler: {
      caption: "Doppler Flow Velocity (cm/s) · Shaded safe (>15 cm/s) & critical (<12 cm/s) bands",
      colorVar: "--accent",
      threshold: 12,
      fmtVal: (v) => v.toFixed(1) + " cm/s",
      fmtTick: (v) => Math.round(v) + "",
      bounds: (vals) => {
        const hi = vals.length ? Math.max(...vals) : 0;
        const maxY = Math.max(30, hi * 1.15);
        return { minY: 0, maxY, ticks: [0, 12, maxY], safe: [15, maxY], danger: [0, 12] };
      },
    },
    risk: {
      caption: "AI Compromise Risk (%) · Shaded low (<30%) & critical (≥70%) clinical bands",
      colorVar: "--critical",
      threshold: 70,
      fmtVal: (v) => v.toFixed(1) + "%",
      fmtTick: (v) => Math.round(v) + "%",
      bounds: () => ({ minY: 0, maxY: 100, ticks: [0, 35, 70, 100], safe: [0, 30], danger: [70, 100] }),
    },
  };

  let activeMetric = "spo2";

  function drawTrendChart(canvasId, points, metricKey) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const mode = METRIC_CFG[metricKey] ? metricKey : "spo2";
    const cfg = METRIC_CFG[mode];

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const cssW = canvas.clientWidth || 680;
    const cssH = 250;
    canvas.width = Math.round(cssW * dpr);
    canvas.height = Math.round(cssH * dpr);

    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const colSurface = cssVar("--surface", "#ffffff");
    const colGrid = cssVar("--chart-grid", "#e2e8f0");
    const colSubtle = cssVar("--subtle", "#64748b");
    const colDanger = cssVar("--critical", "#dc2626");
    const colSafeBand = cssVar("--chart-safe-band", "rgba(5, 150, 105, 0.08)");
    const colDangerBand = cssVar("--chart-danger-band", "rgba(220, 38, 38, 0.08)");
    const colLine = cssVar(cfg.colorVar, "#2563eb");

    ctx.fillStyle = colSurface;
    ctx.fillRect(0, 0, cssW, cssH);

    const pad = { top: 28, right: 20, bottom: 30, left: 48 };
    const plotW = cssW - pad.left - pad.right;
    const plotH = cssH - pad.top - pad.bottom;

    const vals = (points || []).map((pt) => Number(pt[mode] || 0));
    const b = cfg.bounds(vals);

    function yFor(v) {
      const clamped = Math.max(b.minY, Math.min(b.maxY, v));
      const frac = b.maxY > b.minY ? (clamped - b.minY) / (b.maxY - b.minY) : 0;
      return pad.top + plotH - frac * plotH;
    }

    // 1. Shaded safe & danger clinical threshold bands
    ctx.fillStyle = colSafeBand;
    ctx.fillRect(pad.left, yFor(b.safe[1]), plotW, yFor(b.safe[0]) - yFor(b.safe[1]));
    ctx.fillStyle = colDangerBand;
    ctx.fillRect(pad.left, yFor(b.danger[1]), plotW, yFor(b.danger[0]) - yFor(b.danger[1]));

    // 2. Horizontal grid lines & Y-axis labels
    ctx.strokeStyle = colGrid;
    ctx.lineWidth = 1;
    ctx.fillStyle = colSubtle;
    ctx.font = "11px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif";
    ctx.textAlign = "right";
    b.ticks.forEach((tk) => {
      const y = yFor(tk);
      ctx.beginPath();
      ctx.moveTo(pad.left, y);
      ctx.lineTo(cssW - pad.right, y);
      ctx.stroke();
      ctx.fillText(cfg.fmtTick(tk), pad.left - 8, y + 4);
    });

    // 3. Dashed critical threshold line
    const thY = yFor(cfg.threshold);
    ctx.save();
    ctx.strokeStyle = colDanger;
    ctx.lineWidth = 1.25;
    ctx.setLineDash([5, 4]);
    ctx.beginPath();
    ctx.moveTo(pad.left, thY);
    ctx.lineTo(cssW - pad.right, thY);
    ctx.stroke();
    ctx.restore();

    if (!points || points.length === 0) {
      ctx.textAlign = "center";
      ctx.fillStyle = colSubtle;
      ctx.font = "13px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif";
      ctx.fillText("No readings yet — log a bedside assessment or start the simulator.", pad.left + plotW / 2, pad.top + plotH / 2);
      return;
    }

    // 4. Native-scale line + subtle area gradient
    const coords = vals.map((v, idx) => ({
      x: points.length === 1 ? pad.left + plotW / 2 : pad.left + (idx / (points.length - 1)) * plotW,
      y: yFor(v),
    }));

    if (coords.length > 1) {
      const grad = ctx.createLinearGradient(0, pad.top, 0, pad.top + plotH);
      grad.addColorStop(0, colLine + "33");
      grad.addColorStop(1, colLine + "04");
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.moveTo(coords[0].x, pad.top + plotH);
      coords.forEach((c) => ctx.lineTo(c.x, c.y));
      ctx.lineTo(coords[coords.length - 1].x, pad.top + plotH);
      ctx.closePath();
      // Only fill hex colors cleanly (fallback safe opacity otherwise)
      if (/^#[0-9a-fA-F]{6}$/.test(colLine)) {
        ctx.fill();
      }

      ctx.strokeStyle = colLine;
      ctx.lineWidth = 2.6;
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      ctx.beginPath();
      coords.forEach((c, idx) => (idx === 0 ? ctx.moveTo(c.x, c.y) : ctx.lineTo(c.x, c.y)));
      ctx.stroke();
    }

    // 5. Data point dots with clean ring halo
    coords.forEach((c, idx) => {
      const isLast = idx === coords.length - 1;
      const r = isLast ? 4.8 : 3.2;
      ctx.beginPath();
      ctx.arc(c.x, c.y, r, 0, Math.PI * 2);
      ctx.fillStyle = colLine;
      ctx.fill();
      ctx.lineWidth = 1.8;
      ctx.strokeStyle = colSurface;
      ctx.stroke();
    });

    // 6. Top-right latest value readout
    ctx.textAlign = "right";
    ctx.fillStyle = colLine;
    ctx.font = "700 12px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif";
    ctx.fillText("Latest: " + cfg.fmtVal(vals[vals.length - 1]), cssW - pad.right, 16);

    // 7. Chronological X-axis labels (Start, Mid, Latest)
    ctx.fillStyle = colSubtle;
    ctx.font = "11px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif";
    ctx.textAlign = "left";
    ctx.fillText(points[0].label || "Start", pad.left, cssH - 9);
    if (points.length > 2) {
      ctx.textAlign = "center";
      ctx.fillText(points[Math.floor(points.length / 2)].label || "", pad.left + plotW / 2, cssH - 9);
    }
    if (points.length > 1) {
      ctx.textAlign = "right";
      ctx.fillText(points[points.length - 1].label || "Latest", cssW - pad.right, cssH - 9);
    }
  }

  function applyVisionResult(data) {
    const statusBox = document.getElementById("camera-result-box");
    const redInput = document.getElementById("visual-redness-input");
    const cyaInput = document.getElementById("visual-cyanosis-input");
    const palInput = document.getElementById("visual-pallor-input");
    const modeInput = document.getElementById("source-mode-input");

    if (redInput) redInput.value = data.visual_redness_index;
    if (cyaInput) cyaInput.value = data.visual_cyanosis_index;
    if (palInput) palInput.value = data.visual_pallor_index;
    if (modeInput) modeInput.value = "CIE-LAB ROI Camera + Sensor Fusion";

    if (statusBox) {
      const qStatus = data.quality_status || "Good";
      const warnings = Array.isArray(data.quality_warnings) ? data.quality_warnings : [];
      statusBox.style.display = "block";
      statusBox.className = "flash " + (qStatus === "Poor" ? "flash-danger" : qStatus === "Caution" ? "flash-warning" : "flash-info");

      let html =
        "<strong>Visual Triage:</strong> " +
        data.visual_triage_label +
        " &middot; <strong>Score:</strong> " +
        data.visual_perfusion_score +
        "/100 &middot; <strong>Quality:</strong> " +
        qStatus +
        "<br><span class='muted'>Redness: " +
        data.visual_redness_index +
        " | Cyanosis: " +
        data.visual_cyanosis_index +
        " | Pallor: " +
        data.visual_pallor_index +
        " (Auto-filled into sensor form)</span>";

      if (warnings.length > 0) {
        html += "<br><span style='font-size:0.83rem;'><strong>Capture Note:</strong> " + warnings[0] + "</span>";
      }
      statusBox.innerHTML = html;
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    const payloadEl = document.getElementById("chart-data-json");
    const captionEl = document.getElementById("chart-threshold-caption");
    let chartPoints = [];

    function renderChart() {
      if (captionEl && METRIC_CFG[activeMetric]) {
        captionEl.textContent = METRIC_CFG[activeMetric].caption;
      }
      drawTrendChart("sensor-trend-chart", chartPoints, activeMetric);
    }

    if (payloadEl) {
      try {
        chartPoints = JSON.parse(payloadEl.textContent || "[]");
      } catch (e) {
        chartPoints = [];
      }
      renderChart();
      window.addEventListener("resize", renderChart);
      window.addEventListener("flapguard:themechange", renderChart);

      document.querySelectorAll("#chart-metric-tabs [data-metric]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          activeMetric = btn.dataset.metric || "spo2";
          document.querySelectorAll("#chart-metric-tabs [data-metric]").forEach(function (b) {
            b.classList.toggle("active", b === btn);
          });
          renderChart();
        });
      });
    }

    const videoEl = document.getElementById("webcam-video");
    const previewImg = document.getElementById("uploaded-preview");
    const placeholder = document.getElementById("camera-placeholder");
    const startCamBtn = document.getElementById("start-camera-btn");
    const captureCamBtn = document.getElementById("capture-camera-btn");
    const stopCamBtn = document.getElementById("stop-camera-btn");
    const statusBox = document.getElementById("camera-result-box");
    let activeStream = null;

    if (startCamBtn && videoEl) {
      startCamBtn.addEventListener("click", async function () {
        try {
          activeStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
          videoEl.srcObject = activeStream;
          videoEl.style.display = "block";
          if (previewImg) previewImg.style.display = "none";
          if (placeholder) placeholder.style.display = "none";
          if (captureCamBtn) captureCamBtn.disabled = false;
          if (stopCamBtn) stopCamBtn.disabled = false;
        } catch (err) {
          if (statusBox) {
            statusBox.style.display = "block";
            statusBox.className = "flash flash-warning";
            statusBox.textContent = "Camera access unavailable in this browser. Upload a flap photo below instead.";
          }
        }
      });
    }

    if (captureCamBtn && videoEl) {
      captureCamBtn.addEventListener("click", async function () {
        const snap = document.createElement("canvas");
        snap.width = videoEl.videoWidth || 640;
        snap.height = videoEl.videoHeight || 480;
        snap.getContext("2d").drawImage(videoEl, 0, 0, snap.width, snap.height);
        const dataUrl = snap.toDataURL("image/jpeg", 0.9);

        const resp = await fetch("/analyze-image", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": getCsrfToken() },
          body: JSON.stringify({ image_base64: dataUrl }),
        });
        const data = await resp.json();
        if (resp.ok) {
          applyVisionResult(data);
        } else if (statusBox) {
          statusBox.style.display = "block";
          statusBox.className = "flash flash-danger";
          statusBox.textContent = data.error || "Image analysis failed.";
        }
      });
    }

    if (stopCamBtn) {
      stopCamBtn.addEventListener("click", function () {
        if (activeStream) {
          activeStream.getTracks().forEach((t) => t.stop());
          activeStream = null;
        }
        if (videoEl) videoEl.style.display = "none";
        if (placeholder && (!previewImg || previewImg.style.display === "none")) {
          placeholder.style.display = "flex";
        }
        if (captureCamBtn) captureCamBtn.disabled = true;
        stopCamBtn.disabled = true;
      });
    }

    const uploadInput = document.getElementById("flap-image-upload");
    const analyzeUploadBtn = document.getElementById("analyze-upload-btn");
    if (analyzeUploadBtn && uploadInput) {
      analyzeUploadBtn.addEventListener("click", async function () {
        if (!uploadInput.files || !uploadInput.files[0]) {
          if (statusBox) {
            statusBox.style.display = "block";
            statusBox.className = "flash flash-warning";
            statusBox.textContent = "Choose a flap image file first.";
          }
          return;
        }
        const file = uploadInput.files[0];
        if (previewImg) {
          previewImg.src = URL.createObjectURL(file);
          previewImg.style.display = "block";
        }
        if (videoEl) videoEl.style.display = "none";
        if (placeholder) placeholder.style.display = "none";

        const formData = new FormData();
        formData.append("image", file);
        const ctrlInput = document.getElementById("control-image-upload");
        if (ctrlInput && ctrlInput.files && ctrlInput.files[0]) {
          formData.append("control_image", ctrlInput.files[0]);
        }
        const resp = await fetch("/analyze-image", { method: "POST", headers: { "X-CSRF-Token": getCsrfToken() }, body: formData });
        const data = await resp.json();
        if (resp.ok) {
          applyVisionResult(data);
        } else if (statusBox) {
          statusBox.style.display = "block";
          statusBox.className = "flash flash-danger";
          statusBox.textContent = data.error || "Could not analyze image.";
        }
      });
    }

    const simStartBtn = document.getElementById("sim-start-btn");
    const simStopBtn = document.getElementById("sim-stop-btn");
    const simScenarioEl = document.getElementById("sim-scenario");
    const simStatusEl = document.getElementById("sim-live-status");
    const riskBadgeEl = document.getElementById("current-risk-badge");
    let simTimer = null;
    let simStep = 0;

    if (simStartBtn && simScenarioEl) {
      const patientId = simStartBtn.dataset.patientId;

      async function runSimTick() {
        const scenario = simScenarioEl.value;
        const resp = await fetch(`/patients/${patientId}/simulate`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRF-Token": getCsrfToken(),
          },
          body: JSON.stringify({ scenario: scenario, step: simStep }),
        });
        if (!resp.ok) return;
        const payload = await resp.json();
        const r = payload.reading;

        if (simStatusEl) {
          simStatusEl.style.display = "block";
          simStatusEl.className = "flash " + (r.alert_required ? "flash-danger" : "flash-info");
          simStatusEl.textContent =
            `Step ${simStep} | ΔT: ${r.temp_delta_c}°C | SpO2: ${r.spo2_pct}% | Doppler: ${r.doppler_flow_cm_s} cm/s -> ` +
            `${r.predicted_class} [${r.risk_tier} - ${r.risk_score_pct}%]`;
        }

        if (riskBadgeEl) {
          riskBadgeEl.className = "badge badge-" + String(r.risk_tier || "unassessed").toLowerCase();
          riskBadgeEl.textContent = `${r.risk_tier} Risk — ${r.predicted_class} (${r.risk_score_pct}%)`;
        }

        chartPoints.push({
          label: "Sim-" + simStep,
          temp_delta: r.temp_delta_c,
          spo2: r.spo2_pct,
          doppler: r.doppler_flow_cm_s,
          risk: r.risk_score_pct,
        });
        if (chartPoints.length > 30) chartPoints.shift();
        renderChart();

        simStep += 1;
        if (simStep > 8) {
          clearInterval(simTimer);
          simTimer = null;
          simStartBtn.disabled = false;
          if (simStopBtn) simStopBtn.disabled = true;
        }
      }

      simStartBtn.addEventListener("click", function () {
        simStep = 0;
        simStartBtn.disabled = true;
        if (simStopBtn) simStopBtn.disabled = false;
        runSimTick();
        simTimer = setInterval(runSimTick, 2200);
      });

      if (simStopBtn) {
        simStopBtn.addEventListener("click", function () {
          if (simTimer) clearInterval(simTimer);
          simTimer = null;
          simStartBtn.disabled = false;
          simStopBtn.disabled = true;
        });
      }
    }
  });
})();
