/* Traffic Radius SEO Studio: dependency-free progressive enhancement. */
(function () {
  "use strict";

  var navToggle = document.querySelector("[data-nav-toggle]");
  var nav = document.querySelector("[data-primary-nav]");

  if (navToggle && nav) {
    navToggle.addEventListener("click", function () {
      var open = navToggle.getAttribute("aria-expanded") === "true";
      navToggle.setAttribute("aria-expanded", String(!open));
      nav.toggleAttribute("data-open", !open);
    });
  }

  document.querySelectorAll("[data-dismiss-notice]").forEach(function (button) {
    button.addEventListener("click", function () {
      var notice = button.closest(".notice");
      if (notice) notice.remove();
    });
  });

  document.querySelectorAll("[data-filter-input]").forEach(function (input) {
    var selector = input.getAttribute("data-filter-target");
    var target = selector ? document.querySelector(selector) : null;
    if (!target) return;

    input.addEventListener("input", function () {
      var query = input.value.trim().toLocaleLowerCase();
      target.querySelectorAll("[data-filter-row]").forEach(function (row) {
        var matches = !query || row.textContent.toLocaleLowerCase().indexOf(query) !== -1;
        row.hidden = !matches;
      });
    });
  });

  document.querySelectorAll("[data-disclosure-button]").forEach(function (button) {
    var controlledId = button.getAttribute("aria-controls");
    var controlled = controlledId ? document.getElementById(controlledId) : null;
    if (!controlled) return;

    button.addEventListener("click", function () {
      var expanded = button.getAttribute("aria-expanded") === "true";
      button.setAttribute("aria-expanded", String(!expanded));
      controlled.hidden = expanded;
    });
  });

  document.querySelectorAll("[data-confirm-message]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      var message = form.getAttribute("data-confirm-message");
      if (message && !window.confirm(message)) event.preventDefault();
    });
  });

  function csrfToken() {
    var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  document.querySelectorAll("[data-semrush-status]").forEach(function (chip) {
    var url = chip.getAttribute("data-status-url");
    if (!url) return;
    var dot = chip.querySelector("[data-status-dot]");
    var text = chip.querySelector("[data-status-text]");
    function apply(state, label, title) {
      chip.setAttribute("data-state", state);
      if (title) chip.title = title;
      if (dot) dot.setAttribute("data-state", state);
      if (text) text.textContent = label;
    }
    apply("checking", "Checking SEMrush…", "");
    window.fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (response) { return response.ok ? response.json() : Promise.reject(); })
      .then(function (data) {
        var state = data.status || "unavailable";
        if (state === "working" && data.demo) state = "demo";
        apply(state, data.label || "SEMrush", data.message || "");
      })
      .catch(function () { apply("unavailable", "SEMrush status unknown", ""); });
  });

  document.querySelectorAll("[data-reveal-toggle]").forEach(function (button) {
    var targetId = button.getAttribute("aria-controls");
    var target = targetId ? document.getElementById(targetId) : null;
    var url = button.getAttribute("data-reveal-url");
    if (!target || !url) return;
    var revealed = false;
    var masked = target.textContent;
    button.addEventListener("click", function () {
      if (revealed) {
        target.textContent = masked;
        revealed = false;
        button.setAttribute("aria-pressed", "false");
        button.setAttribute("aria-label", "Reveal key");
        return;
      }
      button.disabled = true;
      window.fetch(url, {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-CSRFToken": csrfToken(), Accept: "application/json" },
      })
        .then(function (response) { return response.ok ? response.json() : Promise.reject(); })
        .then(function (data) {
          if (data.api_key) {
            target.textContent = data.api_key;
            revealed = true;
            button.setAttribute("aria-pressed", "true");
            button.setAttribute("aria-label", "Hide key");
          }
        })
        .catch(function () { target.textContent = "Could not reveal the key."; })
        .finally(function () { button.disabled = false; });
    });
  });

  document.querySelectorAll("[data-audit-progress]").forEach(function (card) {
    if (card.getAttribute("data-active") !== "true") return;
    var url = card.getAttribute("data-progress-url");
    var track = card.querySelector('[role="progressbar"]');
    var priorState = "";
    // The server reports stage milestones (2/25/72/80/90/100). Between polls
    // the bar creeps smoothly toward — but never past — the next milestone,
    // so long stages read as steady motion instead of a stuck bar.
    var MILESTONES = [2, 25, 72, 80, 90, 100];
    var serverPercent = 0;
    var serverState = "";
    var shownPercent = 0;
    var creepTimer = null;
    function creepCeiling(value) {
      // A queued run has done no work yet: keep the bar honest near zero
      // instead of creeping to the edge of the collecting milestone.
      if (serverState === "draft") return 10;
      for (var i = 0; i < MILESTONES.length; i += 1) {
        if (MILESTONES[i] > value + 0.5) return MILESTONES[i] - 1;
      }
      return 99;
    }
    function renderPercent(value) {
      var rounded = Math.round(value);
      setText("[data-progress-percent]", rounded + "%");
      if (track) {
        track.style.setProperty("--progress", value.toFixed(1) + "%");
        track.setAttribute("aria-valuenow", String(rounded));
      }
    }
    function startCreep() {
      if (creepTimer) return;
      creepTimer = window.setInterval(function () {
        var ceiling = creepCeiling(serverPercent);
        if (shownPercent < ceiling) {
          shownPercent = Math.min(ceiling, shownPercent + Math.max(0.12, (ceiling - shownPercent) * 0.035));
          renderPercent(shownPercent);
        }
      }, 900);
    }
    function setText(selector, value) {
      var node = card.querySelector(selector);
      if (node) node.textContent = value;
    }
    function poll() {
      window.fetch(url, { credentials: "same-origin", headers: { "Accept": "application/json" } })
        .then(function (response) {
          if (!response.ok) throw new Error("Progress request failed");
          return response.json();
        })
        .then(function (data) {
          setText("[data-progress-label]", data.label);
          setText("[data-progress-message]", data.message || "The audit uses approved-domain evidence only.");
          setText("[data-progress-pages]", data.pages);
          setText("[data-progress-findings]", data.findings);
          setText("[data-progress-recommendations]", data.recommendations);
          serverPercent = Number(data.percent) || 0;
          serverState = String(data.state || "");
          shownPercent = Math.max(shownPercent, serverPercent);
          renderPercent(shownPercent);
          if (data.active) startCreep();
          else if (creepTimer) { window.clearInterval(creepTimer); creepTimer = null; }
          var autoDownloaded = false;
          if (data.package_ready && data.package_url && data.package_artifact_id) {
            var downloadKey = "tr-auto-download:" + data.package_artifact_id;
            var alreadyDownloaded = null;
            try { alreadyDownloaded = window.sessionStorage.getItem(downloadKey); } catch (err) { alreadyDownloaded = "unsupported"; }
            if (!alreadyDownloaded) {
              try { window.sessionStorage.setItem(downloadKey, "1"); } catch (err) { /* private mode */ }
              autoDownloaded = true;
              window.location.assign(data.package_url);
            }
          }
          if (!data.active) {
            window.setTimeout(function () { window.location.reload(); }, autoDownloaded ? 1800 : 700);
            return;
          }
          priorState = data.state;
          window.setTimeout(poll, 2200);
        })
        .catch(function () {
          setText("[data-progress-message]", "Still working. Reconnecting to the progress service…");
          window.setTimeout(poll, 5000);
        });
    }
    poll();
  });})();
