// @ts-check
// Pop-up messages and the confirm-before-deleting dialog.
import {$, $dialog, button, el} from "./dom.js";

/**
 * A short message at the bottom of the screen. Errors stay up longer.
 * @param {string} msg
 * @param {"ok" | "err" | "info"} [kind]
 */
export function toast(msg, kind = "ok") {
  const t = el("div", "toast " + kind);
  t.setAttribute("role", kind === "err" ? "alert" : "status");
  t.append(el("span", null, msg));
  const x = button("x", "×");
  x.setAttribute("aria-label", "Dismiss");
  const close = () => t.remove();
  x.onclick = close;
  t.append(x);
  $("toasts").append(t);
  setTimeout(close, kind === "err" ? 9000 : 4500);
}

/**
 * Ask before deleting. Resolves true if the user pressed the OK button.
 * Resolves on the form's submit (fires at once) rather than only on "close", which a browser
 * may deliver late; "close" still covers Esc. Whichever comes first wins.
 * @param {{title: string, body?: Node | null, note?: string, okLabel: string}} opts
 * @returns {Promise<boolean>}
 */
export function confirmDialog({title, body, note, okLabel}) {
  const dlg = $dialog("dlg");
  $("dlg-title").textContent = title;
  $("dlg-body").replaceChildren(...(body ? [body] : []));
  $("dlg-note").textContent = note || "";
  $("dlg-ok").textContent = okLabel;
  dlg.returnValue = "";
  return new Promise(resolve => {
    const form = /** @type {HTMLFormElement} */ (dlg.querySelector("form"));
    let done = false;
    /** @param {boolean} ok */
    const finish = ok => {
      if (done) return;
      done = true;
      form.removeEventListener("submit", onSubmit);
      dlg.removeEventListener("close", onClose);
      resolve(ok);
    };
    /** @param {SubmitEvent} e */
    const onSubmit = e => finish(!!e.submitter && /** @type {HTMLButtonElement} */ (e.submitter).value === "ok");
    const onClose = () => finish(dlg.returnValue === "ok");
    form.addEventListener("submit", onSubmit);
    dlg.addEventListener("close", onClose);
    dlg.showModal();
    $("dlg-cancel").focus();   // safe default: Enter cancels
  });
}
