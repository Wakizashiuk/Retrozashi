"""Windows UI Automation: operate emulator dialogs by control name instead of by pixel.

PCSX2, DuckStation and Eden are Qt apps and Dolphin is Qt too; Qt exposes its widgets to UIA,
so "Internal Resolution" combo box or "Enable VSync" check box can be driven directly:
  - exact (no OCR or coordinate guessing), immune to DPI, theme and window position;
  - no mouse movement for Invoke/Toggle/Select/Value patterns, so it works in the background;
  - every action returns the control's new state, so verification is built in.

Scope guard: only windows owned by a *registered emulator's process* are visible to the agent.
The controller cannot list or touch any other application.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..core.safety import KillSwitch
from ..emulators.registry import EmulatorRegistry

# Control types worth showing Claude. Static labels are opt-in (they're noisy but help with context).
INTERACTIVE = {"Button", "CheckBox", "ComboBox", "Edit", "RadioButton", "TabItem", "ListItem",
               "MenuItem", "Slider", "Spinner", "TreeItem", "Hyperlink", "SplitButton", "DataItem"}
LABELS = {"Text", "Group"}
MAX_ELEMENTS = 200

ACTIONS = ("click", "toggle_on", "toggle_off", "select", "set_text", "expand", "collapse", "focus", "read")


@dataclass
class UiNode:
    """Backend-neutral view of one UIA element."""
    name: str
    control_type: str
    automation_id: str = ""
    enabled: bool = True
    depth: int = 0
    raw: Any = field(default=None, repr=False)


class UiaBackend(Protocol):
    def windows(self, pids: set[int]) -> list[UiNode]: ...
    def descendants(self, window: UiNode) -> list[UiNode]: ...
    def perform(self, node: UiNode, action: str, value: str) -> None: ...
    def state(self, node: UiNode) -> dict[str, Any]: ...


class UiaError(Exception):
    pass


class UiaController:
    def __init__(self, registry: EmulatorRegistry, kill: KillSwitch, backend: UiaBackend | None = None) -> None:
        self.registry = registry
        self.kill = kill
        self.backend = backend or PywinautoBackend()
        self._handles: dict[str, UiNode] = {}

    def list_controls(self, emulator: str, title_contains: str = "", include_labels: bool = False) -> dict[str, Any]:
        """Snapshot the emulator's windows. IDs (w0, e12, ...) are valid until the next listing."""
        adapter = self.registry.get(emulator)
        pids = {p.pid for p in adapter.running_processes()}
        if not pids:
            raise UiaError(f"{adapter.display_name} is not running. Launch it first.")
        self._handles.clear()
        wanted = INTERACTIVE | (LABELS if include_labels else set())
        out: list[dict[str, Any]] = []
        count = 0
        for wi, win in enumerate(self.backend.windows(pids)):
            if title_contains and title_contains.lower() not in win.name.lower():
                continue
            wid = f"w{wi}"
            self._handles[wid] = win
            controls = []
            for node in self.backend.descendants(win):
                if node.control_type not in wanted or (not node.name and not node.automation_id):
                    continue
                if count >= MAX_ELEMENTS:
                    controls.append({"truncated": f"more than {MAX_ELEMENTS} controls; filter by window title"})
                    break
                eid = f"e{count}"
                count += 1
                self._handles[eid] = node
                entry = {"id": eid, "type": node.control_type, "name": node.name}
                if node.automation_id:
                    entry["automation_id"] = node.automation_id
                if not node.enabled:
                    entry["enabled"] = False
                if node.depth:
                    entry["depth"] = node.depth
                controls.append(entry)
            out.append({"id": wid, "title": win.name, "controls": controls})
        if not out:
            raise UiaError(f"No visible {adapter.display_name} window" + (f" matching {title_contains!r}" if title_contains else ""))
        return {"windows": out, "hint": "Act with ui_act(element_id, action, value). Re-list after dialogs open or close."}

    def act(self, element_id: str, action: str, value: str = "") -> dict[str, Any]:
        if action not in ACTIONS:
            raise UiaError(f"Unknown action {action!r}; expected one of {', '.join(ACTIONS)}")
        node = self._handles.get(element_id)
        if node is None:
            raise UiaError(f"Unknown element {element_id!r}. Call ui_list_controls first (IDs reset on each listing).")
        if not node.enabled and action not in ("read", "focus"):
            raise UiaError(f"{node.control_type} {node.name!r} is disabled")
        self.kill.check()
        if action != "read":
            try:
                self.backend.perform(node, action, value)
            except UiaError:
                raise
            except Exception as e:  # stale element, pattern unsupported, COM error ...
                raise UiaError(f"{action} on {node.control_type} {node.name!r} failed: {e}. "
                               "Re-list controls, or fall back to computer actions.") from e
        return {"element": element_id, "name": node.name, "type": node.control_type,
                "state": self.backend.state(node)}


class PywinautoBackend:
    """pywinauto's UIA backend (pip install retrozashi[uia]). Prefers UIA patterns, which move no mouse;
    falls back to a real click only for controls that implement none."""

    def __init__(self) -> None:
        from pywinauto import Desktop  # import here so the module loads on any OS

        self._desktop = Desktop(backend="uia")

    def windows(self, pids: set[int]) -> list[UiNode]:
        return [UiNode(w.window_text(), w.element_info.control_type, w.element_info.automation_id or "",
                       w.is_enabled(), 0, w)
                for w in self._desktop.windows(visible_only=True) if w.process_id() in pids]

    def descendants(self, window: UiNode) -> list[UiNode]:
        out: list[UiNode] = []

        def walk(wrapper, depth: int) -> None:
            for child in wrapper.children():
                info = child.element_info
                if not info.visible:
                    continue
                out.append(UiNode(info.name or "", info.control_type or "", info.automation_id or "",
                                  info.enabled, depth, child))
                if len(out) < MAX_ELEMENTS * 5:
                    walk(child, depth + 1)

        walk(window.raw, 0)
        return out

    def perform(self, node: UiNode, action: str, value: str) -> None:
        w = node.raw
        if action == "focus":
            w.set_focus()
        elif action == "click":
            for method in ("invoke", "select", "toggle"):
                if self._try(w, method):
                    return
            w.click_input()  # last resort: real mouse click on the control's centre
        elif action in ("toggle_on", "toggle_off"):
            want = 1 if action == "toggle_on" else 0
            if w.get_toggle_state() != want:
                w.toggle()
        elif action == "select":
            if node.control_type == "ComboBox":
                w.select(value)
            else:
                w.select()
        elif action == "set_text":
            if node.control_type in ("Edit", "Spinner", "ComboBox") and hasattr(w, "set_edit_text"):
                w.set_edit_text(value)
            else:
                w.iface_value.SetValue(value)
        elif action == "expand":
            w.expand()
        elif action == "collapse":
            w.collapse()

    @staticmethod
    def _try(w, method: str) -> bool:
        fn = getattr(w, method, None)
        if fn is None:
            return False
        try:
            fn()
            return True
        except Exception:
            return False

    def state(self, node: UiNode) -> dict[str, Any]:
        w = node.raw
        s: dict[str, Any] = {}
        for key, getter in (("toggled", "get_toggle_state"), ("selected", "is_selected"),
                            ("text", "selected_text" if node.control_type == "ComboBox" else "get_value"),
                            ("expanded", "is_expanded")):
            fn = getattr(w, getter, None)
            if fn is None:
                continue
            try:
                s[key] = fn()
            except Exception:
                pass
        try:
            s["enabled"] = w.is_enabled()
        except Exception:
            pass
        return s


def uia_available() -> bool:
    try:
        import pywinauto  # noqa: F401
        return True
    except Exception:  # ImportError, or pywinauto's COM init failing off Windows
        return False
