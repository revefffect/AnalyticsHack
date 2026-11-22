/*! Native form controls. See frontend/ui/components/form-controls.jsx for source and attribution. */

// components/form-controls.jsx
var selector = 'select, input[type="checkbox"], option, optgroup';
var installations = /* @__PURE__ */ new WeakMap();
var svgNamespace = "http://www.w3.org/2000/svg";
function icon(document2, className, paths) {
  const graphic = document2.createElementNS(svgNamespace, "svg");
  graphic.setAttribute("class", className);
  graphic.setAttribute("viewBox", "0 0 16 16");
  graphic.setAttribute("width", "16");
  graphic.setAttribute("height", "16");
  graphic.setAttribute("aria-hidden", "true");
  graphic.setAttribute("focusable", "false");
  for (const [className2, d] of paths) {
    const path = document2.createElementNS(svgNamespace, "path");
    path.setAttribute("class", className2);
    path.setAttribute("d", d);
    graphic.append(path);
  }
  return graphic;
}
function decorate(control) {
  if (!control.parentNode) return;
  if (control.matches("option, optgroup")) {
    if (!control.dataset.slot) control.dataset.slot = "native-select-" + control.localName;
    return;
  }
  const isSelect = control.localName === "select";
  const isSwitch = !isSelect && (control.getAttribute("role") === "switch" || control.hasAttribute("data-ui-switch"));
  const slot = isSelect ? "native-select" : isSwitch ? "switch" : "checkbox";
  if (control.dataset.slot && !control.parentElement?.hasAttribute("data-native-control-wrap")) return;
  if (control.matches('input[aria-hidden="true"], input[tabindex="-1"][hidden]')) return;
  const document2 = control.ownerDocument;
  let wrapper = control.parentElement;
  if (!wrapper?.hasAttribute("data-native-control-wrap")) {
    const refocus = document2.activeElement === control;
    wrapper = document2.createElement("span");
    wrapper.setAttribute("data-native-control-wrap", "");
    control.before(wrapper);
    wrapper.append(control);
    if (refocus) control.focus({ preventScroll: true });
  }
  if (wrapper.dataset.slot === slot + "-wrapper") return;
  for (const child of [...wrapper.children]) if (child !== control) child.remove();
  wrapper.className = "ui-" + slot + "-wrap";
  wrapper.dataset.slot = slot + "-wrapper";
  control.dataset.slot = slot;
  if (isSelect) {
    wrapper.append(icon(document2, "ui-select-indicator", [["ui-chevron", "m4 6 4 4 4-4"]]));
  } else if (isSwitch) {
    control.setAttribute("role", "switch");
    const thumb = document2.createElement("span");
    thumb.className = "ui-switch-thumb";
    thumb.dataset.slot = "switch-thumb";
    thumb.setAttribute("aria-hidden", "true");
    wrapper.append(thumb);
  } else {
    const check = icon(document2, "ui-checkbox-indicator", [
      ["ui-check", "m3.2 8 3.2 3.2L12.8 5"],
      ["ui-mixed", "M3.5 8h9"]
    ]);
    check.dataset.slot = "checkbox-indicator";
    wrapper.append(check);
  }
}
function installFormControls(root = document.body) {
  if (installations.has(root)) return installations.get(root);
  function enhance(node) {
    if (node.nodeType !== 1 || !root.contains(node)) return;
    if (node.matches(selector)) decorate(node);
    node.querySelectorAll(selector).forEach(decorate);
  }
  root.querySelectorAll(selector).forEach(decorate);
  const observer = new MutationObserver((records) => {
    for (const record of records) {
      if (record.type === "childList") {
        record.addedNodes.forEach(enhance);
        continue;
      }
      const control = record.target;
      if (control.matches(selector)) {
        decorate(control);
      } else if (control.parentElement?.hasAttribute("data-native-control-wrap")) {
        const wrapper = control.parentElement;
        wrapper.replaceWith(control);
        delete control.dataset.slot;
      }
    }
  });
  observer.observe(root, { subtree: true, childList: true, attributes: true, attributeFilter: ["type", "role", "data-ui-switch"] });
  const dispose = () => {
    observer.disconnect();
    installations.delete(root);
  };
  installations.set(root, dispose);
  return dispose;
}
export {
  installFormControls
};
