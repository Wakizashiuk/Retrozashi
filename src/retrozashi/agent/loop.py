"""The agent loop: user intent -> Claude -> (native tools | computer actions) -> results -> Claude ...

    ┌────────────┐  messages + tools   ┌──────────────┐
    │ AgentLoop  │ ──────────────────▶ │ Claude API   │
    │            │ ◀────────────────── │ (streamed)   │
    └─────┬──────┘  tool_use blocks    └──────────────┘
          │ toolset_name == "computer"      │ otherwise
          ▼                                 ▼
    ActionExecutor (SendInput, mss)    NativeTools (config files, processes, UI Automation)
          │                                 │
          └────────── tool_result blocks ───┘  (all in ONE user message)

Verification is part of the loop: Claude requests a screenshot (or re-reads a config) after
acting, sees the outcome, and either continues or finishes.
"""
from __future__ import annotations

from typing import Any

import anthropic

from ..config import AgentSettings
from ..core.events import EventBus, EventKind
from ..core.safety import AgentHalted, KillSwitch
from ..desktop.executor import ActionError, ActionExecutor
from .prompts import SYSTEM_PROMPT
from .tools import NativeTools

COMPUTER_TOOLSET = {"type": "computer_toolset_20260801"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
HALT_TEXT = "Not executed: the user pressed the kill switch."
BATCH_ABORT_TEXT = "Not executed: an earlier computer action in this turn failed."


class AgentLoop:
    def __init__(self, settings: AgentSettings, bus: EventBus, kill: KillSwitch,
                 executor: ActionExecutor, native: NativeTools,
                 client: anthropic.Anthropic | None = None) -> None:
        self.settings = settings
        self.bus = bus
        self.kill = kill
        self.executor = executor
        self.native = native
        self._client = client  # created lazily, so a missing key doesn't stop the app from starting
        self.messages: list[dict[str, Any]] = []  # append-only: never edit earlier turns
        self._use_fallback = settings.refusal_fallback

    # ---- public -------------------------------------------------------------
    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            from ..credentials import get_api_key

            # None lets the SDK fall back to its own lookup (e.g. an `ant auth login` profile).
            self._client = anthropic.Anthropic(api_key=get_api_key())
        return self._client

    def reset_client(self) -> None:
        """Call after the API key changes."""
        self._client = None

    def reset(self) -> None:
        self.messages = []

    def run(self, user_text: str) -> None:
        """Run one user request to completion, halt, or the iteration cap."""
        self.kill.reset()
        self.messages.append({"role": "user", "content": user_text})
        self.bus.publish(EventKind.USER, user_text)
        try:
            for _ in range(self.settings.max_iterations):
                self.bus.publish(EventKind.STATUS, "thinking")
                response = self._call_claude()
                self.messages.append({"role": "assistant", "content": _blocks(response.content)})

                for block in response.content:
                    if block.type == "text" and block.text.strip():
                        self.bus.publish(EventKind.ASSISTANT, block.text)

                if response.stop_reason == "refusal":
                    self.bus.publish(EventKind.ERROR, "Claude declined this request.")
                    return
                if response.stop_reason == "max_tokens":
                    self.bus.publish(EventKind.ERROR, "Response hit max_tokens; try a narrower request.")
                    return

                tool_uses = [b for b in response.content if b.type == "tool_use"]
                if not tool_uses:
                    return  # end_turn: Claude is done
                self.bus.publish(EventKind.STATUS, "acting")
                results = self._run_tools(tool_uses)
                self.messages.append({"role": "user", "content": results})
                self.kill.check()
            self.bus.publish(EventKind.ERROR, f"Stopped after {self.settings.max_iterations} steps.")
        except AgentHalted:
            pass  # status is published below; the conversation stays valid so "continue" works
        except anthropic.APIConnectionError as e:
            self.bus.publish(EventKind.ERROR, f"Network error: {e}")
        except anthropic.RateLimitError:
            self.bus.publish(EventKind.ERROR, "Rate limited. Wait a moment and retry.")
        except anthropic.AuthenticationError:
            self.bus.publish(EventKind.ERROR, "Your API key was rejected. Click 🔑 API key to replace it.")
            self.reset_client()
        except anthropic.APIStatusError as e:
            self.bus.publish(EventKind.ERROR, f"API error {e.status_code}: {e.message}")
        except anthropic.AnthropicError as e:  # e.g. no credentials found at all
            self.bus.publish(EventKind.ERROR, f"{e}. Click 🔑 API key to add one, or \"Use in Claude app\" to chat in the Claude desktop app instead (no API key).")
            self.reset_client()
        finally:
            self.bus.publish(EventKind.STATUS, "halted" if self.kill.halted else "idle")

    # ---- Claude call --------------------------------------------------------
    def _request(self) -> dict[str, Any]:
        params: dict[str, Any] = dict(
            model=self.settings.model,
            max_tokens=self.settings.max_tokens,
            system=SYSTEM_PROMPT,
            tools=[COMPUTER_TOOLSET, *self.native.definitions],
            messages=self.messages,
            thinking={"type": "adaptive"},
            output_config={"effort": self.settings.effort},
            cache_control={"type": "ephemeral"},  # system + tools + history prefix is reused every step
        )
        if self._use_fallback:
            # Server-side refusal fallback: a policy decline re-runs on a fallback model in the same call.
            params["extra_headers"] = {"anthropic-beta": FALLBACK_BETA}
            params["extra_body"] = {"fallbacks": "default"}
        return params

    def _call_claude(self):
        try:
            return self._stream(self._request())
        except anthropic.BadRequestError as e:
            if self._use_fallback and "fallback" in str(e).lower():
                self._use_fallback = False  # not available for this account/model: carry on without it
                return self._stream(self._request())
            raise

    def _stream(self, params: dict[str, Any]):
        # Streaming lets the kill switch abort mid-response: leaving the `with` closes the connection.
        with self.client.messages.stream(**params) as stream:
            for _event in stream:
                if self.kill.halted:
                    raise AgentHalted()
            return stream.get_final_message()

    # ---- tool execution -----------------------------------------------------
    def _run_tools(self, tool_uses: list[Any]) -> list[dict[str, Any]]:
        """Run every tool_use in order and return exactly one tool_result per block. Even when
        halted we answer every block, so the conversation stays valid for a follow-up."""
        results: list[dict[str, Any]] = []
        batch_failed = False
        for block in tool_uses:
            is_computer = getattr(block, "toolset_name", None) == "computer"
            result: dict[str, Any] = {"type": "tool_result", "tool_use_id": block.id}
            if is_computer:
                result["toolset_name"] = "computer"  # required on every computer result

            if self.kill.halted:
                result.update(is_error=True, content=HALT_TEXT)
            elif is_computer and batch_failed:
                result.update(is_error=True, content=BATCH_ABORT_TEXT)
            elif is_computer:
                try:
                    action = self.executor.execute(block.name, dict(block.input))
                    self.bus.publish(EventKind.ACTION, action.summary, member=block.name, input=block.input)
                    if block.name in ("screenshot", "zoom"):
                        self.bus.publish(EventKind.SCREENSHOT, action.summary, content=action.content)
                    result["content"] = action.content
                except AgentHalted:
                    result.update(is_error=True, content=HALT_TEXT)
                except (ActionError, ValueError, KeyError, OSError) as e:
                    batch_failed = True
                    self.bus.publish(EventKind.ERROR, f"{block.name} failed: {e}")
                    result.update(is_error=True, content=f"{type(e).__name__}: {e}")
            else:
                text, is_error = self.native.run(block.name, dict(block.input))
                self.bus.publish(EventKind.TOOL, f"{block.name}({_brief(block.input)})", ok=not is_error)
                result.update(content=text, is_error=is_error)
            results.append(result)
        return results


def _blocks(content: list[Any]) -> list[dict[str, Any]]:
    """Echo Claude's blocks back unchanged (thinking blocks included: preserved thinking
    rejects edited history). model_dump keeps extra fields such as toolset_name."""
    return [b.model_dump(mode="json", exclude_none=True) for b in content]


def _brief(inp: Any) -> str:
    s = ", ".join(f"{k}={v!r}" for k, v in dict(inp).items())
    return s if len(s) < 80 else s[:77] + "..."
