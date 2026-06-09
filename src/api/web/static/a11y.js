/* Shared accessibility helpers (WCAG 2.1 AA).
   - Accessible modal: focus trap, focus restore, Escape, background scroll lock.
   - Keyboard-operable clickable table rows (role=button + Enter/Space).
   Templates call window.PERSO_modal.open(id) / .close(id). */
(function () {
  "use strict";

  var FOCUSABLE =
    'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])';
  var opener = null;

  function focusables(dialog) {
    return Array.prototype.filter.call(dialog.querySelectorAll(FOCUSABLE), function (el) {
      return el.offsetParent !== null || el === document.activeElement;
    });
  }

  function onKeydown(overlay, dialog, id) {
    return function (e) {
      if (e.key === "Escape") {
        e.preventDefault();
        close(id);
        return;
      }
      if (e.key !== "Tab") return;
      var f = focusables(dialog);
      if (!f.length) return;
      var first = f[0];
      var last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
  }

  function open(id, trigger) {
    var overlay = document.getElementById(id);
    if (!overlay) return;
    opener = trigger || document.activeElement;
    overlay.classList.add("is-open");
    document.body.classList.add("modal-open");
    var dialog = overlay.querySelector(".modal") || overlay;
    overlay.__a11yKeydown = onKeydown(overlay, dialog, id);
    overlay.addEventListener("keydown", overlay.__a11yKeydown);
    var f = focusables(dialog);
    if (f.length) {
      f[0].focus();
    } else {
      dialog.setAttribute("tabindex", "-1");
      dialog.focus();
    }
  }

  function close(id) {
    var overlay = document.getElementById(id);
    if (!overlay) return;
    overlay.classList.remove("is-open");
    document.body.classList.remove("modal-open");
    if (overlay.__a11yKeydown) {
      overlay.removeEventListener("keydown", overlay.__a11yKeydown);
      overlay.__a11yKeydown = null;
    }
    if (opener && typeof opener.focus === "function") opener.focus();
    opener = null;
  }

  window.PERSO_modal = { open: open, close: close };

  // Make clickable table rows keyboard-operable (2.1.1 / 4.1.2).
  document.addEventListener("DOMContentLoaded", function () {
    var rows = document.querySelectorAll("tr.is-clickable");
    Array.prototype.forEach.call(rows, function (row) {
      if (!row.hasAttribute("tabindex")) row.setAttribute("tabindex", "0");
      if (!row.hasAttribute("role")) row.setAttribute("role", "button");
      row.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          row.click();
        }
      });
    });
  });
})();
