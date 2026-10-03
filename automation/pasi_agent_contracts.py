from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from automation.orchestrator import bridge
from automation.orchestrator.config import CONFIG
from automation.orchestrator.state import StateCorruptionError, StateManager


SCHEMA_VERSION = "pasi-agent-v1"
SERVER_NAME = "pasi-agent"
SERVER_VERSION = "1.0.0"
MAX_ERROR_MESSAGE = 2000
MAX_RESPONSE_TEXT = 120_000
DEFAULT_STALE_MS = 30_000

_COMMON_ERROR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code", "message", "retryable", "source", "details"],
    "properties": {
        "code": {"type": "string"},
        "message": {"type": "string", "minLength": 1, "maxLength": MAX_ERROR_MESSAGE},
        "retryable": {"type": "boolean"},
        "source": {"type": "string"},
        "field": {"type": ["string", "null"]},
        "details": {"type": "object"},
    },
}

_OBSERVATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["observed_at", "source", "age_ms"],
    "properties": {
        "observed_at": {"type": ["string", "null"], "format": "date-time"},
        "source": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "age_ms": {"type": "integer", "minimum": 0},
    },
}

_RUNNER_ERROR_SCHEMA = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "properties": {
        "code": {"type": "string"},
        "message": {"type": "string"},
        "retryable": {"type": "boolean"},
    },
}

_RUNNER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "profile",
        "status",
        "execution_mode",
        "ready",
        "process_alive",
        "phase",
        "progress",
        "observation",
    ],
    "properties": {
        "profile": {"type": "string", "enum": ["m1", "168h"]},
        "status": {
            "type": "string",
            "enum": [
                "starting",
                "running",
                "stopping",
                "paused",
                "completed",
                "failed",
                "cancelled",
                "unavailable",
            ],
        },
        "execution_mode": {"type": ["string", "null"]},
        "ready": {"type": "boolean"},
        "process_alive": {"type": "boolean"},
        "phase": {"type": ["string", "null"]},
        "run_id": {"type": ["string", "null"]},
        "runner_pid": {"type": ["integer", "null"], "minimum": 2},
        "started_at": {"type": ["string", "null"], "format": "date-time"},
        "last_updated_at": {"type": ["string", "null"], "format": "date-time"},
        "completed_at": {"type": ["string", "null"], "format": "date-time"},
        "failed_at": {"type": ["string", "null"], "format": "date-time"},
        "paused_at": {"type": ["string", "null"], "format": "date-time"},
        "progress": {
            "type": "object",
            "additionalProperties": False,
            "required": ["completed", "target"],
            "properties": {
                "completed": {"type": "integer", "minimum": 0},
                "target": {"type": "integer", "minimum": 0},
                "current_index": {"type": ["integer", "null"], "minimum": 1},
                "current_operation_id": {"type": ["string", "null"]},
                "current_operation_status": {"type": ["string", "null"]},
                "last_operation_id": {"type": ["string", "null"]},
            },
        },
        "error": _RUNNER_ERROR_SCHEMA,
        "observation": _OBSERVATION_SCHEMA,
    },
}

_PROCESS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["pid", "role", "alive", "workspace", "recognized", "observation"],
    "properties": {
        "pid": {"type": "integer", "minimum": 2},
        "parent_pid": {"type": ["integer", "null"], "minimum": 1},
        "process_group_id": {"type": ["integer", "null"], "minimum": 1},
        "role": {"type": "string"},
        "profile": {"type": ["string", "null"], "enum": ["m1", "168h", "legacy", "bridge", None]},
        "alive": {"type": "boolean"},
        "workspace": {"type": "boolean"},
        "recognized": {"type": "boolean"},
        "script": {"type": ["string", "null"]},
        "command_line": {"type": ["string", "null"]},
        "command_line_redacted": {"type": ["string", "null"]},
        "observation": _OBSERVATION_SCHEMA,
    },
}

_BROWSER_DATA_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "active_tab": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "properties": {
                "tab_id": {"type": ["integer", "null"], "minimum": 1},
                "url": {"type": ["string", "null"]},
                "host": {"type": ["string", "null"]},
                "is_chatgpt": {"type": "boolean"},
                "conversation_id": {"type": ["string", "null"]},
            },
        },
        "browser": {
            "type": "object",
            "additionalProperties": False,
            "required": ["connected"],
            "properties": {
                "connected": {"type": "boolean"},
                "browser_type": {"type": ["string", "null"]},
                "controller": {"type": ["string", "null"]},
            },
        },
        "cdp": {
            "type": "object",
            "additionalProperties": False,
            "required": ["available"],
            "properties": {
                "available": {"type": "boolean"},
                "native_controller": {"type": ["boolean", "null"]},
                "debugger_attached": {"type": ["boolean", "null"]},
                "network_authority": {"type": ["boolean", "null"]},
                "controller_state": {"type": ["string", "null"]},
            },
        },
        "network": {
            "type": "object",
            "additionalProperties": False,
            "required": ["interceptor", "state"],
            "properties": {
                "interceptor": {"type": ["string", "null"]},
                "state": {"type": "string"},
                "active_operation_id": {"type": ["string", "null"]},
                "request_id": {"type": ["string", "null"]},
                "stream_complete": {"type": ["boolean", "null"]},
            },
        },
        "observation": _OBSERVATION_SCHEMA,
    },
}

_EXTENSION_DATA_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["extension", "service_worker", "controller", "tabs", "observation"],
    "properties": {
        "extension": {
            "type": "object",
            "additionalProperties": False,
            "required": ["installed"],
            "properties": {
                "installed": {"type": "boolean"},
                "extension_id": {"type": ["string", "null"]},
                "version": {"type": ["string", "null"]},
                "manifest_version": {"type": ["integer", "null"]},
            },
        },
        "service_worker": {
            "type": "object",
            "additionalProperties": False,
            "required": ["state"],
            "properties": {
                "state": {"type": "string", "enum": ["observed", "stale", "unknown"]},
                "last_heartbeat_at": {"type": ["string", "null"], "format": "date-time"},
                "age_ms": {"type": ["integer", "null"], "minimum": 0},
            },
        },
        "controller": {
            "type": "object",
            "additionalProperties": False,
            "required": ["state"],
            "properties": {
                "state": {"type": "string"},
                "controller_id": {"type": ["string", "null"]},
                "network_controller": {"type": ["string", "null"]},
                "dispatcher_waiter": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["active"],
                    "properties": {
                        "active": {"type": "boolean"},
                        "tab_id": {"type": ["integer", "null"], "minimum": 1},
                    },
                },
            },
        },
        "tabs": {"type": "array", "items": {"type": "object"}},
        "observation": _OBSERVATION_SCHEMA,
    },
}

_OPERATION_DATA_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["operation"],
    "properties": {
        "operation": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "operation_id": {"type": "string"},
                "run_id": {"type": ["string", "null"]},
                "profile": {"type": ["string", "null"], "enum": ["m1", "168h", None]},
                "operation_type": {"type": ["string", "null"]},
                "status": {"type": ["string", "null"]},
                "controller_id": {"type": ["string", "null"]},
                "current_tab_id": {"type": ["integer", "null"], "minimum": 1},
                "chat_url": {"type": ["string", "null"]},
                "prompt": {"type": "object"},
                "response": {"type": "object"},
                "dispatch": {"type": "object"},
                "network": {"type": "object"},
                "timing": {"type": "object"},
                "error": {"type": ["object", "null"]},
            },
        },
    },
}

_ENVIRONMENT_DATA_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["configured", "active_environment", "candidate", "safe_version", "promotion"],
    "properties": {
        "configured": {"type": "boolean"},
        "active_environment": {"type": ["string", "null"], "enum": ["blue", "green", None]},
        "candidate": {"type": ["object", "null"]},
        "safe_version": {"type": ["object", "null"]},
        "promotion": {"type": "object"},
    },
}

_ACCEPTANCE_DATA_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["available", "acceptances"],
    "properties": {
        "available": {"type": "boolean"},
        "acceptances": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "acceptance_id",
                    "profile",
                    "version_id",
                    "status",
                    "gates",
                    "summary",
                ],
                "properties": {
                    "acceptance_id": {"type": "string"},
                    "profile": {"type": "string", "enum": ["m1", "168h"]},
                    "version_id": {"type": ["string", "null"]},
                    "status": {"type": "string"},
                    "started_at": {"type": ["string", "null"], "format": "date-time"},
                    "completed_at": {"type": ["string", "null"], "format": "date-time"},
                    "gates": {"type": "array", "items": {"type": "object"}},
                    "summary": {"type": "object"},
                },
            },
        },
    },
}


def _envelope(data_schema: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "request_id", "ok", "observed_at", "data", "error"],
        "properties": {
            "schema_version": {"const": SCHEMA_VERSION},
            "request_id": {"type": "string", "minLength": 1},
            "ok": {"type": "boolean"},
            "observed_at": {"type": "string", "format": "date-time"},
            "data": {"anyOf": [dict(data_schema), {"type": "null"}]},
            "error": {"anyOf": [_COMMON_ERROR_SCHEMA, {"type": "null"}]},
        },
        "allOf": [
            {
                "if": {"properties": {"ok": {"const": True}}},
                "then": {
                    "properties": {"error": {"const": None}},
                },
            },
            {
                "if": {"properties": {"ok": {"const": False}}},
                "then": {
                    "properties": {"data": {"const": None}},
                },
            },
        ],
    }


TOOL_INPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "pasi.get_runner_state": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "profile": {"type": ["string", "null"], "enum": ["m1", "168h", None]},
            "include_terminal": {"type": "boolean", "default": True},
        },
    },
    "pasi.get_process_state": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "profile": {"type": ["string", "null"], "enum": ["m1", "168h", None]},
            "include_bridge": {"type": "boolean", "default": True},
        },
    },
    "pasi.get_browser_state": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "tab_id": {"type": ["integer", "null"], "minimum": 1},
            "include_network": {"type": "boolean", "default": True},
        },
    },
    "pasi.get_extension_state": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "include_tabs": {"type": "boolean", "default": True},
            "include_permissions": {"type": "boolean", "default": False},
        },
    },
    "pasi.get_operation_state": {
        "type": "object",
        "additionalProperties": False,
        "required": ["operation_id"],
        "properties": {
            "operation_id": {"type": "string", "minLength": 1, "maxLength": 200},
            "include_response_text": {"type": "boolean", "default": False},
        },
    },
    "pasi.get_environment_state": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "include_previous_safe": {"type": "boolean", "default": True},
        },
    },
    "pasi.get_acceptance_evidence": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "acceptance_id": {"type": ["string", "null"]},
            "profile": {"type": ["string", "null"], "enum": ["m1", "168h", None]},
            "version_id": {"type": ["string", "null"]},
            "include_evidence": {"type": "boolean", "default": True},
        },
    },
}

TOOL_OUTPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "pasi.get_runner_state": _envelope({
        "type": "object",
        "additionalProperties": False,
        "required": ["active_profile", "profiles"],
        "properties": {
            "active_profile": {"type": ["string", "null"], "enum": ["m1", "168h", None]},
            "profiles": {
                "type": "object",
                "additionalProperties": False,
                "required": ["m1", "168h"],
                "properties": {"m1": _RUNNER_SCHEMA, "168h": _RUNNER_SCHEMA},
            },
        },
    }),
    "pasi.get_process_state": _envelope({
        "type": "object",
        "additionalProperties": False,
        "required": ["processes"],
        "properties": {
            "processes": {"type": "array", "items": _PROCESS_SCHEMA},
        },
    }),
    "pasi.get_browser_state": _envelope(_BROWSER_DATA_SCHEMA),
    "pasi.get_extension_state": _envelope(_EXTENSION_DATA_SCHEMA),
    "pasi.get_operation_state": _envelope(_OPERATION_DATA_SCHEMA),
    "pasi.get_environment_state": _envelope(_ENVIRONMENT_DATA_SCHEMA),
    "pasi.get_acceptance_evidence": _envelope(_ACCEPTANCE_DATA_SCHEMA),
}

TOOL_DESCRIPTIONS: dict[str, str] = {
    "pasi.get_runner_state": "Read authoritative PASI runner lifecycle and progress state. Observation only.",
    "pasi.get_process_state": "Inspect actual live PASI processes from the host process table. Observation only.",
    "pasi.get_browser_state": "Read the latest persisted PASI browser/CDP health and network-authority observation. Observation only.",
    "pasi.get_extension_state": "Read the latest PASI extension/service-worker health observation and dispatcher state. Observation only.",
    "pasi.get_operation_state": "Read one PASI operation without mutating queue state. Response bodies are excluded unless explicitly requested.",
    "pasi.get_environment_state": "Read staged blue-green environment, candidate, safe-version, and promotion state. Observation only.",
    "pasi.get_acceptance_evidence": "Read persisted acceptance evidence for M1 or 168h. Observation only.",
}

TOOL_NAMES = tuple(TOOL_INPUT_SCHEMAS)


class PasiAgentInputError(ValueError):
    pass


class PasiAgentObservationService:
    """Read-only observation facade for the PASI runtime."""

    def __init__(
        self,
        *,
        state_manager: StateManager | None = None,
        stale_ms: int = DEFAULT_STALE_MS,
        environment_state_path: Path | None = None,
        acceptance_evidence_path: Path | None = None,
    ) -> None:
        self.state_manager = state_manager or StateManager(CONFIG.ai_dir)
        self.stale_ms = stale_ms
        self.environment_state_path = (
            environment_state_path
            or Path(os.environ.get(
                "PASI_ENVIRONMENT_STATE_PATH",
                str(Path.home() / ".pasi" / "environment" / "state.json"),
            )).expanduser()
        )
        self.acceptance_evidence_path = (
            acceptance_evidence_path
            or Path(os.environ.get(
                "PASI_ACCEPTANCE_EVIDENCE_PATH",
                str(Path.home() / ".pasi" / "acceptance" / "evidence.json"),
            )).expanduser()
        )

    def observe(self, tool_name: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        request_id = str((arguments or {}).get("_request_id") or "").strip()
        if not request_id:
            request_id = "agent-" + hashlib.sha256(
                f"{tool_name}:{utc_now()}".encode("utf-8")
            ).hexdigest()[:16]

        try:
            self._validate_arguments(tool_name, arguments or {})
            args = dict(arguments or {})
            args.pop("_request_id", None)
            if tool_name == "pasi.get_runner_state":
                data = self.get_runner_state(**args)
            elif tool_name == "pasi.get_process_state":
                data = self.get_process_state(**args)
            elif tool_name == "pasi.get_browser_state":
                data = self.get_browser_state(**args)
            elif tool_name == "pasi.get_extension_state":
                data = self.get_extension_state(**args)
            elif tool_name == "pasi.get_operation_state":
                data = self.get_operation_state(**args)
            elif tool_name == "pasi.get_environment_state":
                data = self.get_environment_state(**args)
            elif tool_name == "pasi.get_acceptance_evidence":
                data = self.get_acceptance_evidence(**args)
            else:
                raise PasiAgentInputError(f"unknown tool: {tool_name}")
            return _success(request_id, data)
        except PasiAgentInputError as exc:
            return _failure(
                request_id,
                code="PASI_INVALID_ARGUMENT",
                message=str(exc),
                retryable=False,
                source="agent_interface",
                details={},
            )
        except _ObservationNotFound as exc:
            return _failure(
                request_id,
                code=exc.code,
                message=str(exc),
                retryable=False,
                source=exc.source,
                details=exc.details,
            )
        except StateCorruptionError as exc:
            return _failure(
                request_id,
                code="PASI_INTERNAL",
                message=str(exc),
                retryable=False,
                source="operation_store",
                details={},
            )
        except (OSError, ValueError, TypeError) as exc:
            return _failure(
                request_id,
                code="PASI_NOT_AVAILABLE",
                message=str(exc),
                retryable=True,
                source="agent_interface",
                details={},
            )
        except Exception as exc:
            return _failure(
                request_id,
                code="PASI_INTERNAL",
                message=f"{type(exc).__name__}: {str(exc)}"[:MAX_ERROR_MESSAGE],
                retryable=False,
                source="agent_interface",
                details={},
            )

    def get_runner_state(
        self,
        profile: str | None = None,
        include_terminal: bool = True,
    ) -> dict[str, Any]:
        if profile not in {None, "m1", "168h"}:
            raise PasiAgentInputError("profile must be m1, 168h, or null")
        if profile:
            state = bridge.load_runner_state(profile)
            profiles = {"m1": bridge.load_runner_state("m1"), "168h": bridge.load_runner_state("168h")}
            active_profile = state.get("active_profile") if state else None
        else:
            state = bridge.load_runner_state()
            profiles = state.get("profiles", {}) if isinstance(state, Mapping) else {}
            active_profile = state.get("active_profile") if isinstance(state, Mapping) else None

        normalized = {
            current_profile: self._runner_record(
                current_profile,
                profiles.get(current_profile, {}),
                include_terminal=include_terminal,
            )
            for current_profile in ("m1", "168h")
        }
        return {
            "active_profile": active_profile if active_profile in {"m1", "168h"} else None,
            "profiles": normalized,
        }

    def get_process_state(
        self,
        profile: str | None = None,
        include_bridge: bool = True,
    ) -> dict[str, Any]:
        if profile not in {None, "m1", "168h"}:
            raise PasiAgentInputError("profile must be m1, 168h, or null")
        raw = bridge.runner_processes()
        records: list[dict[str, Any]] = []
        for item in raw:
            current_profile = item.get("profile")
            if profile and current_profile != profile:
                continue
            role = {
                "m1": "runner_m1",
                "168h": "runner_168h",
                "legacy": "legacy_runner",
            }.get(current_profile, "unknown")
            if current_profile == "legacy":
                role = "legacy_runner"
            records.append(self._process_record(item, role))
        if include_bridge:
            records.insert(0, self._process_record(
                {
                    "pid": os.getpid(),
                    "profile": "bridge",
                    "cmdline": " ".join(str(part) for part in os.sys.argv),
                    "workspace": True,
                    "recognized": True,
                },
                "bridge",
            ))
        return {"processes": records}

    def get_browser_state(
        self,
        tab_id: int | None = None,
        include_network: bool = True,
    ) -> dict[str, Any]:
        if tab_id is not None and (not isinstance(tab_id, int) or tab_id < 1):
            raise PasiAgentInputError("tab_id must be a positive integer or null")

        health = self.state_manager.load_browser_health()
        state = self.state_manager.load_browser_state()
        observation = self.state_manager.load_browser_results()
        health_data = health.get("data") if isinstance(health, Mapping) else {}
        state_data = state.get("data") if isinstance(state, Mapping) else {}
        latest_data = observation.get("data") if isinstance(observation, Mapping) else {}
        if not isinstance(health_data, Mapping):
            health_data = {}
        if not isinstance(state_data, Mapping):
            state_data = {}
        if not isinstance(latest_data, Mapping):
            latest_data = {}

        selected_tab = health_data.get("tab_id") or state_data.get("tab_id")
        if tab_id is not None and selected_tab not in {None, tab_id}:
            selected_tab = None

        chat_url = _first_string(
            health_data.get("chat_url"),
            state_data.get("chat_url"),
            latest_data.get("request_url"),
        )
        host = None
        conversation_id = None
        if chat_url:
            parsed = _safe_url(chat_url)
            host = parsed[0]
            conversation_id = parsed[1]

        network_kind = str(latest_data.get("kind") or "")
        network_active = _first_string(
            latest_data.get("active_operation_id"),
            health_data.get("active_operation_id"),
            state_data.get("active_operation_id"),
        )
        network_state = (
            "active"
            if network_active
            else "completed"
            if network_kind in {"chatgpt_network_response", "chatgpt_response"}
            else "idle"
        )
        if not include_network:
            network_state = "omitted"

        observed_at = _latest_timestamp(health, state, observation)
        age_ms = _age_ms(observed_at)

        return {
            "active_tab": {
                "tab_id": selected_tab,
                "url": chat_url,
                "host": host,
                "is_chatgpt": bool(host in {"chatgpt.com", "www.chatgpt.com"}),
                "conversation_id": conversation_id,
            } if chat_url or selected_tab is not None else None,
            "browser": {
                "connected": bool(health),
                "browser_type": "chromium",
                "controller": health_data.get("controller_version"),
            },
            "cdp": {
                "available": bool(health),
                "native_controller": _optional_bool(health_data.get("native_controller")),
                "debugger_attached": _optional_bool(
                    state_data.get("debugger_attached", health_data.get("native_controller"))
                ),
                "network_authority": _optional_bool(health_data.get("network_authority")),
                "controller_state": _first_string(
                    state_data.get("controller_state"),
                    "ready" if health else "unknown",
                ),
            },
            "network": {
                "interceptor": "cdp_fetch" if network_kind.startswith("chatgpt_network") else None,
                "state": network_state,
                "active_operation_id": network_active,
                "request_id": _first_string(latest_data.get("request_id")),
                "stream_complete": latest_data.get("stream_complete")
                if isinstance(latest_data.get("stream_complete"), bool)
                else None,
            },
            "observation": {
                "observed_at": observed_at,
                "source": ["browser_health", "browser_state", "browser_observation"],
                "age_ms": age_ms,
            },
        }

    def get_extension_state(
        self,
        include_tabs: bool = True,
        include_permissions: bool = False,
    ) -> dict[str, Any]:
        health = self.state_manager.load_browser_health()
        health_data = health.get("data") if isinstance(health, Mapping) else {}
        if not isinstance(health_data, Mapping):
            health_data = {}

        observed_at = _latest_timestamp(health)
        age_ms = _age_ms(observed_at)
        worker_state = (
            "observed"
            if health and age_ms <= self.stale_ms
            else "stale"
            if health
            else "unknown"
        )
        waiter_active = health_data.get("dispatcher_waiter_active")
        waiter_tab_id = health_data.get("dispatcher_waiter_tab_id")
        if not isinstance(waiter_active, bool):
            waiter_active = None

        tabs = []
        if include_tabs and health_data.get("chat_url"):
            tabs.append({
                "tab_id": health_data.get("tab_id"),
                "url": health_data.get("chat_url"),
                "supported": True,
                "authorized": True,
                "active": health_data.get("page_visible") is True,
            })

        extension = {
            "installed": bool(health),
            "extension_id": None,
            "version": _first_string(health_data.get("extension_version")),
            "manifest_version": 3,
        }
        if include_permissions:
            extension["permissions"] = {"reported": False}

        controller_id = _first_string(
            health_data.get("controller_id"),
            health_data.get("active_controller_id"),
        )
        return {
            "extension": extension,
            "service_worker": {
                "state": worker_state,
                "last_heartbeat_at": observed_at,
                "age_ms": age_ms,
            },
            "controller": {
                "state": _first_string(
                    health_data.get("controller_state"),
                    "ready" if health else "unknown",
                ),
                "controller_id": controller_id,
                "network_controller": _first_string(
                    health_data.get("network_controller"),
                    health_data.get("controller_version"),
                ),
                "dispatcher_waiter": {
                    "active": waiter_active if waiter_active is not None else False,
                    "tab_id": waiter_tab_id if isinstance(waiter_tab_id, int) else None,
                },
            },
            "tabs": tabs,
            "observation": {
                "observed_at": observed_at,
                "source": ["extension_service_worker", "browser_health"] if health else ["browser_health"],
                "age_ms": age_ms,
            },
        }

    def get_operation_state(
        self,
        operation_id: str,
        include_response_text: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise PasiAgentInputError("operation_id is required")
        if len(operation_id) > 200:
            raise PasiAgentInputError("operation_id exceeds 200 characters")

        read_bridge = bridge.BridgeState(self.state_manager)
        operation = read_bridge.get_operation(operation_id, repair_response=False)
        if operation is None:
            raise _ObservationNotFound(
                "PASI_NOT_FOUND",
                f"Operation {operation_id} was not found.",
                "operation_store",
                {"operation_id": operation_id},
            )

        prompt = operation.get("prompt")
        prompt_data = {
            "present": isinstance(prompt, str) and bool(prompt),
            "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest() if isinstance(prompt, str) else None,
            "content_included": False,
        }
        response_text = operation.get("response_text")
        response_data = {
            "available": bool(operation.get("response_text_available") is True and isinstance(response_text, str)),
            "source": operation.get("response_source"),
            "network_authoritative": operation.get("network_response_authoritative") is True,
            "processing_required": operation.get("response_processing_required") is True,
            "processing_complete": operation.get("response_processing_complete") is True,
        }
        if include_response_text and isinstance(response_text, str):
            response_data["text"] = response_text[:MAX_RESPONSE_TEXT]

        return {
            "operation": {
                "operation_id": operation_id,
                "run_id": operation.get("run_id"),
                "profile": operation.get("runner_profile") or operation.get("profile"),
                "operation_type": operation.get("operation_type"),
                "status": operation.get("status"),
                "controller_id": operation.get("controller_id"),
                "current_tab_id": operation.get("tab_id"),
                "chat_url": operation.get("chat_url"),
                "prompt": prompt_data,
                "response": response_data,
                "dispatch": {
                    "claimed": operation.get("status") in {"claimed", "generating", "completed"},
                    "claimed_at": operation.get("claimed_at"),
                    "submitted": operation.get("submitted") is True
                    or operation.get("status") in {"generating", "completed"},
                    "submitted_at": operation.get("submitted_at"),
                },
                "network": {
                    "request_id": operation.get("network_request_id"),
                    "state": (
                        "completed"
                        if operation.get("network_terminal")
                        or operation.get("network_response_authoritative") is True and response_data["available"]
                        else "active"
                        if operation.get("status") == "generating"
                        else "idle"
                    ),
                    "stream_complete": operation.get("network_stream_complete"),
                },
                "timing": operation.get("timing") if isinstance(operation.get("timing"), Mapping) else {},
                "error": _operation_error(operation),
            },
        }

    def get_environment_state(
        self,
        include_previous_safe: bool = True,
    ) -> dict[str, Any]:
        payload = self._read_optional_json(self.environment_state_path)
        if not payload:
            return {
                "configured": False,
                "active_environment": None,
                "candidate": None,
                "safe_version": None,
                "promotion": {
                    "state": "unconfigured",
                    "active_environment": None,
                    "candidate_environment": None,
                    "rollback_available": False,
                },
            }

        active = payload.get("active_environment")
        candidate = payload.get("candidate")
        safe = payload.get("safe_version") if include_previous_safe else None
        promotion = payload.get("promotion")
        if not isinstance(promotion, Mapping):
            promotion = {
                "state": "unknown",
                "active_environment": active if active in {"blue", "green"} else None,
                "candidate_environment": (
                    candidate.get("environment")
                    if isinstance(candidate, Mapping)
                    else None
                ),
                "rollback_available": bool(safe),
            }
        return {
            "configured": True,
            "active_environment": active if active in {"blue", "green"} else None,
            "candidate": dict(candidate) if isinstance(candidate, Mapping) else None,
            "safe_version": dict(safe) if isinstance(safe, Mapping) else None,
            "promotion": dict(promotion),
        }

    def _derive_acceptance_evidence(self, profile: str | None) -> list[dict[str, Any]]:
        profiles = [profile] if profile else ["m1", "168h"]
        records: list[dict[str, Any]] = []
        for current_profile in profiles:
            runner = bridge.load_runner_state(current_profile)
            if not runner.get("available"):
                continue
            version_id = str(runner.get("branch") or runner.get("repo") or "unversioned")
            status = str(runner.get("status") or "unavailable")
            completed = int(runner.get("completed_operations") or 0)
            target = int(runner.get("target_operations") or 0)
            gates: list[dict[str, Any]] = [
                {
                    "gate_id": current_profile + "-process",
                    "name": current_profile.upper() + " runner process observed",
                    "status": "passed" if runner.get("process_alive") or status == "completed" else "failed" if status in {"failed", "cancelled"} else "pending",
                    "observed_at": runner.get("last_updated_at") or runner.get("started_at"),
                    "evidence_refs": [f"process:{runner.get('runner_pid')}"] if runner.get("runner_pid") else [],
                    "details": {},
                }
            ]
            if current_profile == "m1":
                gates.extend([
                    {
                        "gate_id": "m1-browser",
                        "name": "ChatGPT browser/CDP authority available",
                        "status": "passed" if runner.get("chat_url") and runner.get("process_alive") else "failed" if status == "failed" else "pending",
                        "observed_at": runner.get("last_updated_at") or runner.get("started_at"),
                        "evidence_refs": ["browser:chatgpt_health"],
                        "details": {"chat_url": runner.get("chat_url")},
                    },
                    {
                        "gate_id": "m1-chain",
                        "name": "20-operation sequential chain completed",
                        "status": "passed" if completed == target == 20 and status == "completed" else "failed" if status == "failed" else "pending",
                        "observed_at": runner.get("completed_at") or runner.get("failed_at") or runner.get("last_updated_at"),
                        "evidence_refs": [f"operations:{completed}/{target}"],
                        "details": {"completed": completed, "target": target},
                    },
                    {
                        "gate_id": "m1-response-processing",
                        "name": "Response processing reached the durable completion boundary",
                        "status": "passed" if completed == 20 and status == "completed" else "failed" if status == "failed" else "pending",
                        "observed_at": runner.get("completed_at") or runner.get("last_updated_at"),
                        "evidence_refs": [f"last-operation:{runner.get('last_operation_id')}"] if runner.get("last_operation_id") else [],
                        "details": {"request_ids": runner.get("request_ids")},
                    },
                ])
            else:
                gates.extend([
                    {
                        "gate_id": "168h-initialization",
                        "name": "168h acceptance initialization completed",
                        "status": "failed" if status == "failed" and runner.get("phase") in {"initialization", "initializing_worktree", "loading_schedule"} else "passed" if runner.get("phase") not in {"initializing_worktree", "loading_schedule"} else "pending",
                        "observed_at": runner.get("last_updated_at") or runner.get("started_at"),
                        "evidence_refs": [f"worktree:{runner.get('worktree')}"] if runner.get("worktree") else [],
                        "details": {"branch": runner.get("branch")},
                    },
                    {
                        "gate_id": "168h-run",
                        "name": "168h acceptance execution remains operational",
                        "status": "passed" if status in {"running", "roadmap_complete", "deadline_reached"} else "failed" if status == "failed" else "pending",
                        "observed_at": runner.get("completed_at") or runner.get("failed_at") or runner.get("last_updated_at"),
                        "evidence_refs": [f"run:{runner.get('run_id')}"] if runner.get("run_id") else [],
                        "details": {"current_task": runner.get("current_task"), "completed_tasks": runner.get("completed_tasks")},
                    },
                ])
            passed = sum(1 for gate in gates if gate["status"] == "passed")
            failed = sum(1 for gate in gates if gate["status"] == "failed")
            pending = sum(1 for gate in gates if gate["status"] in {"pending", "running"})
            records.append({
                "acceptance_id": f"derived-{current_profile}-{runner.get('run_id') or 'unknown'}",
                "profile": current_profile,
                "version_id": version_id,
                "started_at": runner.get("started_at"),
                "completed_at": runner.get("completed_at"),
                "status": "passed" if failed == 0 and pending == 0 else "failed" if failed else "running" if pending else "pending",
                "gates": gates,
                "summary": {"passed": passed, "failed": failed, "pending": pending},
            })
        return records

    def get_acceptance_evidence(
        self,
        acceptance_id: str | None = None,
        profile: str | None = None,
        version_id: str | None = None,
        include_evidence: bool = True,
    ) -> dict[str, Any]:
        if profile not in {None, "m1", "168h"}:
            raise PasiAgentInputError("profile must be m1, 168h, or null")
        payload = self._read_optional_json(self.acceptance_evidence_path)
        records: list[dict[str, Any]]
        if isinstance(payload, Mapping):
            candidate = payload.get("acceptances")
            records = [dict(item) for item in candidate] if isinstance(candidate, list) else []
        elif isinstance(payload, list):
            records = [dict(item) for item in payload if isinstance(item, Mapping)]
        else:
            records = []
        if not records:
            records = self._derive_acceptance_evidence(profile)

        filtered = []
        for record in records:
            if acceptance_id and record.get("acceptance_id") != acceptance_id:
                continue
            if profile and record.get("profile") != profile:
                continue
            if version_id and record.get("version_id") != version_id:
                continue
            if not include_evidence:
                stripped = dict(record)
                stripped["gates"] = [
                    {
                        "gate_id": gate.get("gate_id"),
                        "name": gate.get("name"),
                        "status": gate.get("status"),
                        "observed_at": gate.get("observed_at"),
                    }
                    for gate in record.get("gates", [])
                    if isinstance(gate, Mapping)
                ]
                record = stripped
            filtered.append(record)

        return {
            "available": bool(records),
            "acceptances": filtered,
        }

    def _read_optional_json(self, path: Path) -> Any:
        try:
            if not path.is_file() or path.stat().st_size > 2_000_000:
                return None
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _validate_arguments(self, tool_name: str, arguments: Mapping[str, Any]) -> None:
        if tool_name not in TOOL_INPUT_SCHEMAS:
            raise PasiAgentInputError(f"unknown tool: {tool_name}")
        if not isinstance(arguments, Mapping):
            raise PasiAgentInputError("arguments must be an object")
        if "_request_id" in arguments and not isinstance(arguments["_request_id"], str):
            raise PasiAgentInputError("_request_id must be a string")

    @staticmethod
    def _runner_record(profile: str, state: Mapping[str, Any], *, include_terminal: bool) -> dict[str, Any]:
        if not isinstance(state, Mapping) or not state:
            return {
                "profile": profile,
                "status": "unavailable",
                "execution_mode": None,
                "ready": False,
                "process_alive": False,
                "phase": None,
                "run_id": None,
                "runner_pid": None,
                "started_at": None,
                "last_updated_at": None,
                "completed_at": None,
                "failed_at": None,
                "paused_at": None,
                "progress": {
                    "completed": 0,
                    "target": 0,
                    "current_index": None,
                    "current_operation_id": None,
                    "current_operation_status": None,
                    "last_operation_id": None,
                },
                "error": None,
                "observation": {
                    "observed_at": utc_now(),
                    "source": ["runner_state_file"],
                    "age_ms": 0,
                },
            }
        status = str(state.get("status") or "unavailable")
        if not include_terminal and status in {"completed", "failed", "cancelled", "paused"}:
            status = "unavailable"
        completed = state.get("completed_operations")
        target = state.get("target_operations")
        return {
            "profile": profile,
            "status": status,
            "execution_mode": state.get("execution_mode"),
            "ready": state.get("ready") is True,
            "process_alive": state.get("process_alive") is True,
            "phase": state.get("phase"),
            "run_id": state.get("run_id"),
            "runner_pid": state.get("runner_pid"),
            "started_at": state.get("started_at"),
            "last_updated_at": state.get("last_updated_at"),
            "completed_at": state.get("completed_at"),
            "failed_at": state.get("failed_at"),
            "paused_at": state.get("paused_at"),
            "progress": {
                "completed": int(completed or 0),
                "target": int(target or 0),
                "current_index": state.get("current_operation_index"),
                "current_operation_id": state.get("current_operation_id"),
                "current_operation_status": state.get("current_operation_status"),
                "last_operation_id": state.get("last_operation_id"),
            },
            "error": _runner_error(state),
            "observation": {
                "observed_at": _first_string(
                    state.get("last_updated_at"),
                    state.get("completed_at"),
                    state.get("failed_at"),
                    state.get("started_at"),
                ) or utc_now(),
                "source": ["runner_state_file", "process"] if state.get("process_alive") is not None else ["runner_state_file"],
                "age_ms": _age_ms(_first_string(state.get("last_updated_at"), state.get("started_at"))),
            },
        }

    @staticmethod
    def _process_record(item: Mapping[str, Any], role: str) -> dict[str, Any]:
        cmdline = str(item.get("cmdline") or "")
        pid = int(item.get("pid") or 0)
        return {
            "pid": pid,
            "parent_pid": None,
            "process_group_id": None,
            "role": role,
            "profile": item.get("profile") if item.get("profile") in {"m1", "168h", "legacy"} else "bridge" if role == "bridge" else None,
            "alive": True,
            "workspace": item.get("workspace") is True,
            "recognized": item.get("recognized") is True,
            "script": item.get("script"),
            "command_line": cmdline or None,
            "command_line_redacted": _redact_command(cmdline) if cmdline else None,
            "observation": {
                "observed_at": utc_now(),
                "source": ["proc"],
                "age_ms": 0,
            },
        }


class _ObservationNotFound(RuntimeError):
    def __init__(self, code: str, message: str, source: str, details: dict[str, Any]) -> None:
        super().__init__(message)
        self.code = code
        self.source = source
        self.details = details


def _success(request_id: str, data: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id,
        "ok": True,
        "observed_at": utc_now(),
        "data": dict(data),
        "error": None,
    }


def _failure(
    request_id: str,
    *,
    code: str,
    message: str,
    retryable: bool,
    source: str,
    details: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id,
        "ok": False,
        "observed_at": utc_now(),
        "data": None,
        "error": {
            "code": code,
            "message": message[:MAX_ERROR_MESSAGE],
            "retryable": retryable,
            "source": source,
            "field": None,
            "details": dict(details),
        },
    }


def _runner_error(state: Mapping[str, Any]) -> dict[str, Any] | None:
    error = state.get("error")
    if not isinstance(error, str) or not error.strip():
        return None
    message = error.strip()[:MAX_ERROR_MESSAGE]
    retryable = not message.lower().startswith(("runtimeerror: acceptance", "acceptance"))
    return {
        "code": _classify_runner_error(message),
        "message": message,
        "retryable": retryable,
    }


def _classify_runner_error(message: str) -> str:
    lowered = message.casefold()
    if "worktree is not clean" in lowered:
        return "ACCEPTANCE_WORKTREE_DIRTY"
    if "process is no longer alive" in lowered:
        return "RUNNER_PROCESS_DEAD"
    if "exited before initialization" in lowered:
        return "RUNNER_STARTUP_EXITED"
    if "bridge" in lowered and "health" in lowered:
        return "BRIDGE_HEALTH_FAILED"
    return "RUNNER_FAILED"


def _operation_error(operation: Mapping[str, Any]) -> dict[str, Any] | None:
    value = operation.get("error")
    if not isinstance(value, str) or not value.strip():
        return None
    return {
        "code": "OPERATION_FAILED",
        "message": value[:MAX_ERROR_MESSAGE],
        "retryable": False,
    }


def _redact_command(value: str) -> str:
    return re.sub(
        r"(?i)(authorization|token|password|secret|api[_-]?key)=\S+",
        r"\1=<redacted>",
        value,
    )


def _first_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _safe_url(value: str) -> tuple[str | None, str | None]:
    try:
        from urllib.parse import urlsplit
        parsed = urlsplit(value)
        host = parsed.hostname.casefold() if parsed.hostname else None
        path = parsed.path.rstrip("/")
        conversation_id = path.split("/c/", 1)[1] if "/c/" in path else None
        if conversation_id:
            conversation_id = conversation_id.split("/", 1)[0]
        return host, conversation_id
    except ValueError:
        return None, None


def _latest_timestamp(*records: Any) -> str | None:
    timestamps = []
    for record in records:
        if isinstance(record, Mapping):
            for key in ("captured_at", "last_updated_at", "observed_at"):
                value = record.get(key)
                if isinstance(value, str):
                    timestamps.append(value)
            data = record.get("data")
            if isinstance(data, Mapping):
                value = data.get("captured_at")
                if isinstance(value, str):
                    timestamps.append(value)
    if not timestamps:
        return None
    parsed: list[tuple[datetime, str]] = []
    for value in timestamps:
        try:
            normalized = value.replace("Z", "+00:00")
            parsed.append((datetime.fromisoformat(normalized).astimezone(timezone.utc), value))
        except ValueError:
            continue
    return max(parsed, key=lambda item: item[0])[1] if parsed else max(timestamps)


def _age_ms(timestamp: str | None) -> int:
    if not timestamp:
        return 0
    try:
        normalized = timestamp.replace("Z", "+00:00")
        captured = datetime.fromisoformat(normalized).astimezone(timezone.utc)
    except ValueError:
        return 0
    return max(0, int((datetime.now(timezone.utc) - captured).total_seconds() * 1000))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
