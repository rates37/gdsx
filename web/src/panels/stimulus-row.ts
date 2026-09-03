// A row of `port=0` / `port=1` toggle buttons: the stimulus you hold constant
// while decoding a register.
//
// Was written twice, once in the Register Inspector's orbit section and once
// in the Register Decoder's three sections, with the same markup and the same
// read-back. One copy now, so the two panels that merged into one cannot drift
// apart again.

export interface StimulusRow {
  row: HTMLElement;
  /** The current setting, as the `{port: 0|1}` map the API expects. */
  read: () => Record<string, number>;
}

export function stimulusRow(ports: string[]): StimulusRow {
  const row = document.createElement("div");
  row.className = "rd-toggle-row";
  const buttons = new Map<string, HTMLButtonElement>();
  for (const name of ports) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = `${name}=0`;
    btn.dataset.value = "0";
    btn.addEventListener("click", () => {
      const next = btn.dataset.value === "0" ? "1" : "0";
      btn.dataset.value = next;
      btn.textContent = `${name}=${next}`;
      btn.classList.toggle("active", next === "1");
    });
    buttons.set(name, btn);
    row.append(btn);
  }
  return {
    row,
    read: () => {
      const out: Record<string, number> = {};
      for (const [name, btn] of buttons) out[name] = Number(btn.dataset.value);
      return out;
    },
  };
}