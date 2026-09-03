// The `{ }` button every Python-backed panel carries: it
// shows the exact `gdsx.api` call behind the current view, copyable. No
// panel formats this text itself -- they hand over the string `DesignClient`
// already produced alongside the data, so what's shown is never a guess at
// what ran.

export function attachPythonCallButton(
  host: HTMLElement,
  getCall: () => string | null,
): void {
  const btn = document.createElement("button");
  btn.className = "py-call-btn";
  btn.type = "button";
  btn.textContent = "{ }";
  btn.title = "show the Python call behind this view";

  const popover = document.createElement("div");
  popover.className = "py-call-popover";
  popover.hidden = true;

  const code = document.createElement("pre");
  const copy = document.createElement("button");
  copy.type = "button";
  copy.className = "py-call-copy";
  copy.textContent = "copy";
  popover.append(code, copy);

  copy.addEventListener("click", () => {
    navigator.clipboard?.writeText(code.textContent ?? "").catch(() => {});
    copy.textContent = "copied";
    setTimeout(() => (copy.textContent = "copy"), 1000);
  });

  btn.addEventListener("click", () => {
    popover.hidden = !popover.hidden;
    if (!popover.hidden) code.textContent = getCall() ?? "-- nothing to show yet --";
  });

  host.append(btn, popover);
}