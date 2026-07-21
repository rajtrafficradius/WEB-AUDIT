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
        apply(data.status || "unavailable", data.label || "SEMrush", data.message || "");
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
          setText("[data-progress-percent]", data.percent + "%");
          setText("[data-progress-pages]", data.pages);
          setText("[data-progress-findings]", data.findings);
          setText("[data-progress-recommendations]", data.recommendations);
          if (track) {
            track.style.setProperty("--progress", data.percent + "%");
            track.setAttribute("aria-valuenow", String(data.percent));
          }
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
