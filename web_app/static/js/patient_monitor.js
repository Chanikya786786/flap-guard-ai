/* Pure Offline JavaScript for FlapGuard AI:
   - HTML5 Canvas Multisensor Trend Chart (Zero external chart libraries)
   - Live Webcam Flap Scanner & Image Upload Analyzer
   - Live Virtual Sensor Stream Simulator
*/

(function () {
  function drawTrendChart(canvasId, points) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const w = (canvas.width = canvas.clientWidth || 680);
    const h = (canvas.height = 240);

    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, w, h);

    if (!points || points.length === 0) {
      ctx.fillStyle = "#64748b";
      ctx.font = "14px sans-serif";
      ctx.fillText("No sensor readings recorded yet. Submit a reading or start simulation.", 20, h / 2);
      return;
    }

    const pad = { top: 28, right: 20, bottom: 32, left: 42 };
    const plotW = w - pad.left - pad.right;
    const plotH = h - pad.top - pad.bottom;

    // Horizontal grid lines (0..100 scale)
    ctx.strokeStyle = "#e2e8f0";
    ctx.lineWidth = 1;
    ctx.fillStyle = "#64748b";
    ctx.font = "11px sans-serif";
    for (let yVal = 0; yVal <= 100; yVal += 25) {
      const y = pad.top + plotH - (yVal / 100) * plotH;
      ctx.beginPath();
      ctx.moveTo(pad.left, y);
      ctx.lineTo(w - pad.right, y);
      ctx.stroke();
      ctx.fillText(String(yVal), 8, y + 4);
    }

    const series = [
      { key: "spo2", label: "SpO2 (%)", color: "#2563eb", scale: (v) => Math.max(0, Math.min(100, v)) },
      { key: "risk", label: "AI Risk (%)", color: "#dc2626", scale: (v) => Math.max(0, Math.min(100, v)) },
      { key: "doppler", label: "Doppler (x3)", color: "#15803d", scale: (v) => Math.max(0, Math.min(100, v * 3.0)) },
      { key: "temp_delta", label: "Delta-T (x20)", color: "#d97706", scale: (v) => Math.max(0, Math.min(100, v * 20.0)) },
    ];

    series.forEach((s) => {
      ctx.strokeStyle = s.color;
      ctx.lineWidth = 2.2;
      ctx.beginPath();
      points.forEach((pt, idx) => {
        const x = points.length === 1 ? pad.left + plotW / 2 : pad.left + (idx / (points.length - 1)) * plotW;
        const y = pad.top + plotH - (s.scale(Number(pt[s.key] || 0)) / 100) * plotH;
        if (idx === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();

      // Dots
      ctx.fillStyle = s.color;
      points.forEach((pt, idx) => {
        const x = points.length === 1 ? pad.left + plotW / 2 : pad.left + (idx / (points.length - 1)) * plotW;
        const y = pad.top + plotH - (s.scale(Number(pt[s.key] || 0)) / 100) * plotH;
        ctx.beginPath();
        ctx.arc(x, y, 3, 0, Math.PI * 2);
        ctx.fill();
      });
    });

    // Legend
    let lx = pad.left;
    series.forEach((s) => {
      ctx.fillStyle = s.color;
      ctx.fillRect(lx, 8, 10, 10);
      ctx.fillStyle = "#0f172a";
      ctx.font = "11px sans-serif";
      ctx.fillText(s.label, lx + 14, 17);
      lx += 118;
    });

    // X-axis first/last timestamps
    ctx.fillStyle = "#475569";
    ctx.fillText(points[0].label || "Start", pad.left, h - 10);
    if (points.length > 1) {
      const lastLabel = points[points.length - 1].label || "Latest";
      ctx.fillText(lastLabel, w - pad.right - 52, h - 10);
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
    if (modeInput) modeInput.value = "Camera Scan + Multisensor Fusion";

    if (statusBox) {
      statusBox.style.display = "block";
      statusBox.innerHTML =
        "<strong>Camera Visual Triage:</strong> " +
        data.visual_triage_label +
        " | <strong>Visual Perfusion Score:</strong> " +
        data.visual_perfusion_score +
        "/100<br>" +
        "<span class='muted'>Redness: " +
        data.visual_redness_index +
        " | Cyanosis: " +
        data.visual_cyanosis_index +
        " | Pallor: " +
        data.visual_pallor_index +
        " (Auto-filled into sensor form below)</span>";
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    // 1. Render initial trend chart
    const payloadEl = document.getElementById("chart-data-json");
    let chartPoints = [];
    if (payloadEl) {
      try {
        chartPoints = JSON.parse(payloadEl.textContent || "[]");
      } catch (e) {
        chartPoints = [];
      }
      drawTrendChart("sensor-trend-chart", chartPoints);
      window.addEventListener("resize", () => drawTrendChart("sensor-trend-chart", chartPoints));
    }

    // 2. Live Webcam Flap Scanner
    const videoEl = document.getElementById("webcam-video");
    const previewImg = document.getElementById("uploaded-preview");
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
          if (captureCamBtn) captureCamBtn.disabled = false;
          if (stopCamBtn) stopCamBtn.disabled = false;
        } catch (err) {
          if (statusBox) {
            statusBox.style.display = "block";
            statusBox.textContent = "Camera access unavailable in this browser. Use Upload Flap Image below.";
          }
        }
      });
    }

    if (captureCamBtn && videoEl) {
      captureCamBtn.addEventListener("click", async function () {
        const snap = document.createElement("canvas");
        snap.width = videoEl.videoWidth || 320;
        snap.height = videoEl.videoHeight || 240;
        snap.getContext("2d").drawImage(videoEl, 0, 0, snap.width, snap.height);
        const dataUrl = snap.toDataURL("image/jpeg", 0.9);

        const resp = await fetch("/analyze-image", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ image_base64: dataUrl }),
        });
        const data = await resp.json();
        if (resp.ok) {
          applyVisionResult(data);
        } else if (statusBox) {
          statusBox.style.display = "block";
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
        if (captureCamBtn) captureCamBtn.disabled = true;
        stopCamBtn.disabled = true;
      });
    }

    // 3. Upload Flap Image Analyzer
    const uploadInput = document.getElementById("flap-image-upload");
    const analyzeUploadBtn = document.getElementById("analyze-upload-btn");
    if (analyzeUploadBtn && uploadInput) {
      analyzeUploadBtn.addEventListener("click", async function () {
        if (!uploadInput.files || !uploadInput.files[0]) {
          if (statusBox) {
            statusBox.style.display = "block";
            statusBox.textContent = "Please choose an image file first.";
          }
          return;
        }
        const file = uploadInput.files[0];
        if (previewImg) {
          previewImg.src = URL.createObjectURL(file);
          previewImg.style.display = "block";
        }
        if (videoEl) videoEl.style.display = "none";

        const formData = new FormData();
        formData.append("image", file);
        const resp = await fetch("/analyze-image", { method: "POST", body: formData });
        const data = await resp.json();
        if (resp.ok) {
          applyVisionResult(data);
        } else if (statusBox) {
          statusBox.style.display = "block";
          statusBox.textContent = data.error || "Could not analyze image.";
        }
      });
    }

    // 4. Live Virtual Sensor Stream Simulator
    const simStartBtn = document.getElementById("sim-start-btn");
    const simStopBtn = document.getElementById("sim-stop-btn");
    const simScenarioEl = document.getElementById("sim-scenario");
    const simStatusEl = document.getElementById("sim-live-status");
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
            `Step ${simStep} | Delta-T: ${r.temp_delta_c} C | SpO2: ${r.spo2_pct}% | Doppler: ${r.doppler_flow_cm_s} cm/s -> ` +
            `${r.predicted_class} [${r.risk_tier} - ${r.risk_score_pct}%]`;
        }

        chartPoints.push({
          label: "Sim-" + simStep,
          temp_delta: r.temp_delta_c,
          spo2: r.spo2_pct,
          doppler: r.doppler_flow_cm_s,
          risk: r.risk_score_pct,
        });
        if (chartPoints.length > 30) chartPoints.shift();
        drawTrendChart("sensor-trend-chart", chartPoints);

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