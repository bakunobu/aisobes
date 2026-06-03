/**
 * Countdown Timer — extracted from smart_wish_tracker/countdown.html
 * Adapted for live_interview: DB-only (no localStorage), Project→Task→Subtask hierarchy,
 * light theme, integrates with /api/timer/* and /api/tasks endpoints.
 */
(function () {
  "use strict";

  // ── Constants ───────────────────────────────────────────────────────────
  const CIRCUMFERENCE = 879.2; // 2πr where r=140 (matches 300×300 SVG)
  const DEFAULT_SECONDS = 900; // 15 minutes
  const REFRESH_INTERVAL = 30; // seconds between daily-stats refreshes

  // ── DOM refs (populated when home.html loads timer-block) ───────────────
  let clockEl, startBtn, pauseBtn, stopBtn, timerLabel, taskSelect;
  let progressArc, progressPoint;
  let dailyMood, dailyTasks, dailyTime, dailyCompletion, dailyBar;

  // ── State ───────────────────────────────────────────────────────────────
  let totalSeconds = DEFAULT_SECONDS;
  let plannedSeconds = DEFAULT_SECONDS;
  let timerInterval = null;
  let isRunning = false;
  let isPaused = false;
  let currentTaskId = null;
  let currentSubtaskId = null;
  let currentTaskName = "";
  let currentSessionId = null;
  let dailyStatsTimer = null;

  // ── Helpers ─────────────────────────────────────────────────────────────

  function fmt(secs) {
    const h = Math.floor(secs / 3600);
    const m = Math.floor((secs % 3600) / 60);
    const s = secs % 60;
    return (
      String(h).padStart(2, "0") +
      ":" +
      String(m).padStart(2, "0") +
      ":" +
      String(s).padStart(2, "0")
    );
  }

  function elapsed() {
    return plannedSeconds - totalSeconds;
  }

  // ── Progress Ring ───────────────────────────────────────────────────────

  function updateProgressRing() {
    if (!progressArc || !progressPoint) return;
    const ratio = plannedSeconds > 0 ? elapsed() / plannedSeconds : 0;
    const clamped = Math.max(0, Math.min(1, ratio));
    const offset = CIRCUMFERENCE * (1 - clamped);
    progressArc.style.strokeDashoffset = offset;

    const angle = clamped * 360;
    const rad = (angle - 90) * (Math.PI / 180);
    const cx = 150;
    const cy = 150;
    const r = 140;
    const px = cx + r * Math.cos(rad);
    const py = cy + r * Math.sin(rad);
    progressPoint.setAttribute("cx", px);
    progressPoint.setAttribute("cy", py);
  }

  function resetProgressRing() {
    if (!progressArc || !progressPoint) return;
    progressArc.style.strokeDashoffset = CIRCUMFERENCE;
    progressPoint.setAttribute("cx", "150");
    progressPoint.setAttribute("cy", "10");
  }

  // ── Clock Display ───────────────────────────────────────────────────────

  function updateClock() {
    if (clockEl) clockEl.textContent = fmt(totalSeconds);
    updateProgressRing();
  }

  // ── Timer Loop ──────────────────────────────────────────────────────────

  function tick() {
    if (totalSeconds <= 0) {
      completeTimer();
      return;
    }
    totalSeconds--;
    updateClock();
  }

  // ── API Calls ───────────────────────────────────────────────────────────

  async function apiStart() {
    const resp = await fetch("/api/timer/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        task_id: currentTaskId,
        subtask_id: currentSubtaskId,
        planned_duration: plannedSeconds,
      }),
    });
    const data = await resp.json();
    if (data.success) {
      currentSessionId = data.session_id;
    }
    return data;
  }

  async function apiPause() {
    const resp = await fetch("/api/timer/pause", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: currentSessionId }),
    });
    return resp.json();
  }

  async function apiStop() {
    const resp = await fetch("/api/timer/stop", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: currentSessionId }),
    });
    return resp.json();
  }

  async function apiComplete() {
    const resp = await fetch("/api/timer/complete", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: currentSessionId }),
    });
    return resp.json();
  }

  async function loadTasks() {
    const resp = await fetch("/api/tasks");
    return resp.json();
  }

  async function loadDailyStats() {
    const resp = await fetch("/api/daily-stats");
    const data = await resp.json();
    if (data.success) {
      renderDailyStats(data.stats);
    }
  }

  // ── Timer Controls ─────────────────────────────────────────────────────

  async function startTimer() {
    if (isRunning) return;

    // Require a task selection
    if (!currentTaskId && !currentSubtaskId) {
      alert("Please select a task first.");
      return;
    }

    // If paused, resume
    if (isPaused) {
      // Start a new session (resume)
      await apiStart();
      isPaused = false;
    } else {
      // Fresh start
      if (totalSeconds <= 0) totalSeconds = plannedSeconds;
      await apiStart();
    }

    isRunning = true;
    timerInterval = setInterval(tick, 1000);

    if (startBtn) startBtn.disabled = true;
    if (pauseBtn) pauseBtn.disabled = false;
    if (stopBtn) stopBtn.disabled = false;

    updateLabel();
  }

  async function pauseTimer() {
    if (!isRunning) return;

    clearInterval(timerInterval);
    timerInterval = null;
    isRunning = false;
    isPaused = true;

    await apiPause();

    if (startBtn) {
      startBtn.disabled = false;
      startBtn.textContent = "▶ Resume";
    }
    if (pauseBtn) pauseBtn.disabled = true;

    updateLabel();
  }

  async function stopTimer() {
    if (timerInterval) {
      clearInterval(timerInterval);
      timerInterval = null;
    }

    if (currentSessionId && (isRunning || isPaused)) {
      await apiStop();
    }

    isRunning = false;
    isPaused = false;
    currentSessionId = null;
    totalSeconds = plannedSeconds;
    updateClock();
    resetProgressRing();

    if (startBtn) {
      startBtn.disabled = false;
      startBtn.textContent = "▶ Start";
    }
    if (pauseBtn) pauseBtn.disabled = true;
    if (stopBtn) stopBtn.disabled = true;

    updateLabel();
  }

  async function completeTimer() {
    clearInterval(timerInterval);
    timerInterval = null;
    isRunning = false;
    isPaused = false;

    if (currentSessionId) {
      await apiComplete();
    }

    totalSeconds = 0;
    updateClock();
    updateProgressRing();
    currentSessionId = null;

    if (startBtn) {
      startBtn.disabled = false;
      startBtn.textContent = "▶ Start";
    }
    if (pauseBtn) pauseBtn.disabled = true;
    if (stopBtn) stopBtn.disabled = true;

    updateLabel();
    loadDailyStats();

    // Reset to default after a short delay so user sees 00:00:00
    setTimeout(() => {
      if (!isRunning) {
        totalSeconds = plannedSeconds;
        updateClock();
        resetProgressRing();
      }
    }, 2000);
  }

  function updateLabel() {
    if (!timerLabel) return;
    if (isRunning) {
      timerLabel.textContent = `▶ ${currentTaskName}`;
    } else if (isPaused) {
      timerLabel.textContent = `⏸ ${currentTaskName}`;
    } else if (currentTaskName) {
      timerLabel.textContent = currentTaskName;
    } else {
      timerLabel.textContent = "No task selected";
    }
  }

  // ── Task Selector ──────────────────────────────────────────────────────

  async function populateTaskSelect() {
    if (!taskSelect) return;

    try {
      const data = await loadTasks();
      if (!data.success) return;

      taskSelect.innerHTML = '<option value="">— Select a task —</option>';

      data.tasks.forEach((task) => {
        const opt = document.createElement("option");
        opt.value = `task:${task.id}`;
        opt.textContent = `${task.project_name} > Task #${task.id}`;
        opt.dataset.estimated = task.estimated_time || 0;
        taskSelect.appendChild(opt);

        (task.subtasks || []).forEach((sub) => {
          const subOpt = document.createElement("option");
          subOpt.value = `subtask:${sub.id}`;
          subOpt.textContent = `  └─ Subtask #${sub.id}`;
          subOpt.dataset.estimated = sub.estimated_time || 0;
          taskSelect.appendChild(subOpt);
        });
      });
    } catch (e) {
      console.error("Failed to load tasks:", e);
    }
  }

  function onTaskSelectChange() {
    if (!taskSelect) return;
    const val = taskSelect.value;
    if (!val) {
      currentTaskId = null;
      currentSubtaskId = null;
      currentTaskName = "";
      plannedSeconds = DEFAULT_SECONDS;
    } else if (val.startsWith("task:")) {
      currentTaskId = parseInt(val.slice(5));
      currentSubtaskId = null;
      currentTaskName =
        taskSelect.selectedOptions[0].textContent;
      const est = parseInt(taskSelect.selectedOptions[0].dataset.estimated) || 0;
      plannedSeconds = est > 0 ? est : DEFAULT_SECONDS;
    } else if (val.startsWith("subtask:")) {
      currentSubtaskId = parseInt(val.slice(8));
      currentTaskId = null;
      currentTaskName =
        taskSelect.selectedOptions[0].textContent.trim();
      const est = parseInt(taskSelect.selectedOptions[0].dataset.estimated) || 0;
      plannedSeconds = est > 0 ? est : DEFAULT_SECONDS;
    }

    totalSeconds = plannedSeconds;
    updateClock();
    resetProgressRing();
    updateLabel();
  }

  // ── Daily Stats Rendering ───────────────────────────────────────────────

  function renderDailyStats(stats) {
    if (dailyMood) dailyMood.textContent = stats.mood_emoji;
    if (dailyTasks) dailyTasks.textContent = `${stats.finished_tasks}/${stats.total_sessions}`;
    if (dailyTime) dailyTime.textContent = stats.total_time_formatted;
    if (dailyCompletion) {
      dailyCompletion.textContent = `${stats.completion_percentage}% completed`;
      if (dailyBar) {
        dailyBar.className = "completion-bar";
        if (stats.completion_percentage >= 75) {
          dailyBar.classList.add("high-completion");
        } else if (stats.completion_percentage >= 50) {
          dailyBar.classList.add("medium-completion");
        } else if (stats.completion_percentage > 0) {
          dailyBar.classList.add("low-completion");
        }
      }
    }
  }

  // ── Click-to-edit Clock ─────────────────────────────────────────────────

  function onClockClick() {
    if (isRunning || isPaused) return;
    const input = prompt("Enter time (minutes):", Math.floor(plannedSeconds / 60));
    if (input === null) return;
    const mins = parseInt(input);
    if (isNaN(mins) || mins <= 0 || mins > 480) {
      alert("Enter a number between 1 and 480 minutes.");
      return;
    }
    plannedSeconds = mins * 60;
    totalSeconds = plannedSeconds;
    updateClock();
    resetProgressRing();
  }

  // ── Initialization ──────────────────────────────────────────────────────

  function bindDom() {
    clockEl = document.getElementById("timer-clock");
    startBtn = document.getElementById("timer-start");
    pauseBtn = document.getElementById("timer-pause");
    stopBtn = document.getElementById("timer-stop");
    timerLabel = document.getElementById("timer-label");
    taskSelect = document.getElementById("timer-task-select");

    // Progress ring SVG elements
    progressArc = document.querySelector(".progress-arc");
    progressPoint = document.querySelector(".progress-point");

    // Daily stats DOM
    dailyMood = document.getElementById("daily-mood");
    dailyTasks = document.getElementById("daily-tasks-count");
    dailyTime = document.getElementById("daily-time-spent");
    dailyCompletion = document.getElementById("daily-completion-pct");
    dailyBar = document.getElementById("daily-completion-bar");
  }

  function bindEvents() {
    if (startBtn) startBtn.addEventListener("click", startTimer);
    if (pauseBtn) pauseBtn.addEventListener("click", pauseTimer);
    if (stopBtn) stopBtn.addEventListener("click", stopTimer);
    if (clockEl) clockEl.addEventListener("click", onClockClick);
    if (taskSelect) taskSelect.addEventListener("change", onTaskSelectChange);
  }

  function init() {
    bindDom();

    // If the timer block doesn't exist on this page, bail out
    if (!clockEl) return;

    bindEvents();
    updateClock();
    resetProgressRing();
    populateTaskSelect();
    loadDailyStats();

    // Periodic daily-stats refresh
    dailyStatsTimer = setInterval(loadDailyStats, REFRESH_INTERVAL * 1000);
  }

  // ── Public API (for project.html inline buttons) ────────────────────────

  window.Timer = {
    selectTask: function (taskId, subtaskId, taskName, estimatedSeconds) {
      currentTaskId = taskId || null;
      currentSubtaskId = subtaskId || null;
      currentTaskName = taskName;
      plannedSeconds = estimatedSeconds > 0 ? estimatedSeconds : DEFAULT_SECONDS;
      totalSeconds = plannedSeconds;
      updateClock();
      resetProgressRing();
      updateLabel();
      // Also update the dropdown if present
      if (taskSelect) {
        const prefix = subtaskId ? `subtask:${subtaskId}` : `task:${taskId}`;
        if (taskSelect.value !== prefix) {
          taskSelect.value = prefix;
        }
      }
    },
    get isRunning() {
      return isRunning;
    },
    get currentTaskName() {
      return currentTaskName;
    },
    selectAndStart: function(taskId, subtaskId, taskName, estimatedSeconds) {
      Timer.selectTask(taskId, subtaskId, taskName, estimatedSeconds);
      setTimeout(function() {
        if (startBtn && !startBtn.disabled) {
          startBtn.click();
        }
      }, 100);
    },
  };

  // Boot when DOM is ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
