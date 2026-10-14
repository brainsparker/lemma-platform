#!/usr/bin/env python3
"""Deterministic ACP v1 agent used by the Rust integration suite."""

import json
import os
import pathlib
import sys

# ACP uses UTF-8 even when Windows gives redirected Python pipes a legacy codec.
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")

log_path = pathlib.Path(sys.argv[1])
# What this agent has forgotten. "forget-session" makes `session/load` fail the
# way a real provider's does when its rollout file has been pruned or its
# session deleted from disk -- the case Lemma cannot predict and withholds the
# conversation's history for.
MODE = sys.argv[2] if len(sys.argv) > 2 else "remembers"


def effort(current, levels):
    return {
        "id": "effort",
        "name": "Effort",
        "category": "thought_level",
        "type": "select",
        "currentValue": current,
        "options": [{"value": level, "name": level.title()} for level in levels],
    }


# "per-model-options": each model offers its own reasoning levels, as Claude's
# and Codex's adapters do, and one model's cannot be read at all.
PER_MODEL_EFFORT = {
    "fake-1": ("medium", ["low", "medium"]),
    "fake-2": ("high", ["low", "high", "max"]),
}
PER_MODEL_CATALOG = [
    {"value": "fake-1", "name": "Fake 1"},
    {"value": "fake-2", "name": "Fake 2"},
    {"value": "fake-broken", "name": "Fake Broken"},
]


def per_model_options(model):
    current, levels = PER_MODEL_EFFORT[model]
    return [
        {
            "id": "model",
            "name": "Model",
            "category": "model",
            "type": "select",
            "currentValue": model,
            "options": PER_MODEL_CATALOG,
        },
        effort(current, levels),
    ]


def emit(message):
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def result(request_id, value):
    emit({"jsonrpc": "2.0", "id": request_id, "result": value})


def record(message):
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(message, separators=(",", ":")) + "\n")


for raw_line in sys.stdin:
    message = json.loads(raw_line)
    record(message)
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize":
        # What the host set up for this agent beyond ACP, so a test can see
        # the switches a real adapter would read (see `acp::session_options`).
        record(
            {
                "environment": {
                    name: value
                    for name, value in os.environ.items()
                    if name
                    in {"CODEX_CONFIG", "CODEX_HOME", "PATH", "XDG_CONFIG_HOME"}
                    or name.startswith(("OPENCODE_", "CLAUDE_CODE_", "LEMMA_"))
                }
            }
        )
        result(
            request_id,
            {
                "protocolVersion": 1,
                "agentCapabilities": {
                    "loadSession": MODE == "forget-session",
                    "mcpCapabilities": {"http": True, "sse": True},
                    "promptCapabilities": {"image": False, "audio": False},
                },
                "authMethods": [],
                "agentInfo": {"name": "fake-acp", "version": "1.0.0"},
            },
        )
    elif method == "session/load" and MODE == "forget-session":
        emit(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32603, "message": "session not found"},
            }
        )
    elif method == "session/new" and MODE == "per-model-options":
        result(
            request_id,
            {"sessionId": "fake-session", "configOptions": per_model_options("fake-1")},
        )
    elif method == "session/set_config_option" and MODE == "per-model-options":
        value = message["params"]["value"]
        if value in PER_MODEL_EFFORT:
            result(request_id, {"configOptions": per_model_options(value)})
        else:
            emit(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32602, "message": f"cannot switch to {value}"},
                }
            )
    elif method in {"session/new", "session/load"}:
        result(
            request_id,
            {
                "sessionId": "fake-session",
                "configOptions": [
                    {
                        "id": "model",
                        "name": "Model",
                        "description": "Dynamic model catalog",
                        "category": "model",
                        "type": "select",
                        "currentValue": "fake-1",
                        "options": [
                            {"value": "fake-1", "name": "Fake 1"},
                            {"value": "fake-2", "name": "Fake 2"},
                        ],
                    }
                ],
            },
        )
    elif method == "session/set_config_option":
        result(
            request_id,
            {
                "configOptions": [
                    {
                        "id": "model",
                        "name": "Model",
                        "category": "model",
                        "type": "select",
                        "currentValue": message["params"]["value"],
                        "options": [{"value": "fake-2", "name": "Fake 2"}],
                    }
                ]
            },
        )
    elif method == "session/prompt":
        emit(
            {
                "jsonrpc": "2.0",
                "id": 900,
                "method": "session/request_permission",
                "params": {
                    "sessionId": "fake-session",
                    "toolCall": {
                        "toolCallId": "native-shell",
                        "status": "pending",
                        "title": "Run native shell",
                    },
                    "options": [
                        {
                            "optionId": "allow_once",
                            "name": "Allow once",
                            "kind": "allow_once",
                        }
                    ],
                },
            }
        )
        permission_response = json.loads(sys.stdin.readline())
        record(permission_response)
        emit(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": "fake-session",
                    "update": {
                        "sessionUpdate": "agent_thought_chunk",
                        "content": {"type": "text", "text": "thinking"},
                    },
                },
            }
        )
        emit(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": "fake-session",
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "hello from fake"},
                    },
                },
            }
        )
        result(request_id, {"stopReason": "end_turn"})
    else:
        result(
            request_id,
            {
                "error": {
                    "code": -32601,
                    "message": f"unsupported method {method}",
                }
            },
        )
