"""Bridge Droid CLI 0.244+ Script MCP confirmations into droid-sdk 0.5.0.

CLI asks permission with confirmationType=script. SDK 0.5.0 only knows mcp_tool /
exec / … so validation fails, the handler never runs, and tools cancel. Rewrite
script toolUses into nested mcp_tool confirmations before the SDK parses them.
"""

from __future__ import annotations

from typing import Any, Mapping


_PATCHED = False


def _rewrite_script_permissions(raw: Mapping[str, Any]) -> dict[str, Any]:
    tool_uses = raw.get("toolUses") or raw.get("tool_uses") or ()
    rewritten: list[dict[str, Any]] = []
    for item in tool_uses:
        if not isinstance(item, Mapping):
            rewritten.append(item)  # type: ignore[arg-type]
            continue
        conf = item.get("confirmationType") or item.get("confirmation_type")
        details = item.get("details") or {}
        if conf != "script" or not isinstance(details, Mapping):
            rewritten.append(dict(item))
            continue
        calls = details.get("calls") or ()
        if not calls:
            # Unknown script with no MCP calls — keep cancel-safe shape.
            rewritten.append(dict(item))
            continue
        for call in calls:
            if not isinstance(call, Mapping):
                continue
            nested = call.get("confirmation") or {}
            if not isinstance(nested, Mapping) or nested.get("type") != "mcp_tool":
                continue
            rewritten.append(
                {
                    "toolUse": item.get("toolUse") or item.get("tool_use"),
                    "confirmationType": "mcp_tool",
                    "details": {
                        "type": "mcp_tool",
                        "toolName": nested.get("toolName")
                        or call.get("toolName")
                        or "",
                        "impactLevel": nested.get("impactLevel")
                        or details.get("impactLevel")
                        or "high",
                        "serverName": nested.get("serverName") or "",
                        "actualToolName": nested.get("actualToolName") or "",
                    },
                }
            )
    out = dict(raw)
    out["toolUses"] = rewritten or list(tool_uses)
    return out


def patch_script_permissions() -> None:
    """Idempotent monkeypatch of the SDK permission wire decoder."""
    global _PATCHED
    if _PATCHED:
        return
    from droid_sdk._high_level import interaction_adapter as ia

    original = ia.permission_request_from_wire

    def permission_request_from_wire(params):
        if isinstance(params, Mapping):
            params = _rewrite_script_permissions(params)
        return original(params)

    ia.permission_request_from_wire = permission_request_from_wire
    _PATCHED = True
