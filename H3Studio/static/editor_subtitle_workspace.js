(function (root) {
  "use strict";
  // Move the existing controls, never clone an input or keep a second subtitle draft.
  function mount(doc) {
    const $ = id => doc.getElementById(id), inspector = $("inspectorPanel");
    const moves = [["overlayTextEditor", "subtitleTextSlot"], ["overlayTimingFields", "subtitleTimingSlot"]].map(([id, slot]) => {
      const node = $(id), marker = doc.createComment(`subtitle-home:${id}`);
      node.parentNode.insertBefore(marker, node);
      return { node, marker, slot: $(slot) };
    });
    function render(view) {
      const open = view.open === true, selected = view.selected === true, tab = ["content", "style", "create"].includes(view.tab) ? view.tab : "content";
      for (const { node, marker, slot } of moves) {
        if (open && node.parentNode !== slot) slot.appendChild(node);
        if (!open && node.parentNode !== marker.parentNode) marker.parentNode.insertBefore(node, marker.nextSibling);
      }
      inspector.classList.toggle("subtitle-workspace-open", open);
      inspector.classList.toggle("subtitle-has-selection", selected);
      $("editorShell").classList.toggle("subtitle-workspace-open", open);
      inspector.dataset.subtitleTab = tab;
      inspector.setAttribute("aria-label", open ? "字幕編輯" : "片段設定");
      $("inspectorTitle").textContent = open ? "字幕" : "片段設定";
      $("subtitlePanel").classList.toggle("hidden", !open);
      $("closeSubtitleWorkspace").classList.toggle("hidden", !open);
      $("openSubtitleWorkspace").setAttribute("aria-expanded", String(open));
      $("showPositionAnimation").classList.toggle("hidden", open && (tab !== "style" || !selected));
      for (const name of ["content", "style", "create"]) {
        const button = $(`subtitleTab-${name}`);
        button.setAttribute("aria-selected", String(name === tab));
        button.tabIndex = name === tab ? 0 : -1;
        $(`subtitlePane-${name}`).classList.toggle("hidden", name !== tab);
      }
      const index = view.index ?? -1, count = view.count || 0;
      $("subtitleCueEditor").classList.toggle("hidden", !selected);
      $("subtitleCueEmpty").classList.toggle("hidden", selected);
      $("subtitleCueFields").disabled = !selected || view.blocked || !view.ready;
      $("selectedSubtitleLabel").textContent = selected ? `第 ${index + 1} / ${count} 句` : "選取字幕";
      $("previousSubtitle").disabled = view.blocked || !selected || index <= 0;
      $("nextSubtitle").disabled = view.blocked || !selected || index >= count - 1;
      $("subtitleStyleEmpty").classList.toggle("hidden", selected);
    }
    return { render };
  }
  const api = { mount };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.H3EditorSubtitleWorkspace = api;
})(typeof window !== "undefined" ? window : globalThis);
