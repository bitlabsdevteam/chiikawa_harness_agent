"""Provider and HTTPS gateway: installed system policy, IT secrets, shared quota."""

import json
from pathlib import Path
import platform

from . import enterprise_models as models, system_policy
from .enterprise_policy import trusted_path
from .enterprise_transport import ApprovedHTTPS
from .tools import core_tools
from .subagent import subagent_tool


def tool_specs(names):
    """Only application-owned function declarations enter a provider request."""
    allowed = {item.name: item.spec for item in core_tools("/workspace")}
    for name, description, argument, detail in (
        ("remember", "Remember a trusted, non-secret project fact", "note", "Fact to append to project memory"),
        ("use_skill", "Load a project's skill instructions", "name", "Skill name from the catalog"),
        ("fetch_url", "Read an HTTPS URL approved by company IT", "url", "Approved HTTPS URL"),
    ):
        allowed[name] = {"schema": {"name": name, "description": description, "parameters": {
            "type": "object", "properties": {argument: {"type": "string", "description": detail}}, "required": [argument]}}}
    allowed["spawn_agent"] = subagent_tool(None).spec
    if (not isinstance(names, list) or any(not isinstance(name, str) or name not in allowed for name in names)
            or len(set(names)) != len(names)):
        raise PermissionError("Managed models accept only the installed tool declarations.")
    return [allowed[name] for name in names]


class ProviderGateway:
    def __init__(self, policy, ledger, state, *, transport=None):
        self.policy, self.ledger, self.state = policy, ledger, Path(state)
        self.transport = transport or ApprovedHTTPS(policy)
        self.core = system_policy.load_policy()

    def _headers(self, selected):
        path = trusted_path(self.state / "secrets" / selected.credential, private=True)
        value = path.read_text(encoding="utf-8").strip()
        if not value or len(value) > 16384 or any(c.isspace() for c in value):
            raise RuntimeError("IT must configure a valid provider credential.")
        headers = {"Authorization": "Bearer " + value} if selected.auth == "bearer" else {selected.auth: value}
        if selected.protocol == "anthropic":
            headers["anthropic-version"] = "2023-06-01"
        return headers

    def complete(self, developer, *, provider, model, messages, tools, max_output_tokens=None,
                 isolation="jail", workspace="/workspace", approval="safe", depth=0, purpose="response", on_delta=None):
        selected = self.policy.allow_model(provider, model)
        if on_delta is not None and selected.protocol != "responses":
            raise ValueError("Managed streaming requires a Responses API provider.")
        cap = selected.max_output_tokens if max_output_tokens is None else max_output_tokens
        if type(cap) is not int or not 1 <= cap <= selected.max_output_tokens:
            raise PermissionError("Requested output exceeds the IT-approved provider limit.")
        if isolation not in {"jail", "sandbox"} or approval not in {"ask", "safe", "read-only", "yolo"}:
            raise ValueError("Invalid runtime configuration.")
        if not isinstance(workspace, str) or type(depth) is not int or not 0 <= depth <= 2:
            raise ValueError("Invalid workspace or child depth.")
        if purpose not in {"response", "compaction"}:
            raise ValueError("Unknown managed request purpose.")
        declarations = tool_specs(tools)
        if depth >= 2 and "spawn_agent" in tools:
            declarations = [item for item in declarations if item["schema"]["name"] != "spawn_agent"]
        facts = {"profile": "enterprise", "isolation": isolation, "workspace": workspace,
                 "platform": "Linux" if isolation == "sandbox" else platform.system(),
                 "network": "offline worker; IT-approved HTTPS gateway only", "approval_policy": approval,
                 "child_depth": depth, "tools": [t["schema"]["name"] for t in declarations],
                 "policy_fingerprint": self.core.fingerprint}
        system = system_policy.compose_system(self.core, facts)
        if purpose == "compaction":
            from .context import SUMMARY_SYSTEM
            system += "\n\nCurrent authorized operation: " + SUMMARY_SYSTEM
            declarations = []
        body = models.build_request(selected.protocol, model, system, messages, declarations, cap)
        if developer.token_limit is not None:
            status = self.ledger.status(developer)
            if status["suspended"] or status["remaining"] <= 0:
                raise PermissionError("Developer quota is exhausted or suspended; contact company IT.")
            if not selected.count_endpoint:
                raise PermissionError("A finite quota requires an IT-approved input-token counting endpoint for this provider.")
        headers = self._headers(selected)
        if selected.count_endpoint:
            count = self.transport.json("POST", models.endpoint(selected.count_endpoint, model),
                                        models.input_request(selected.protocol, model, body), headers=headers)
            if not isinstance(count, dict):
                raise RuntimeError("Provider returned an invalid input token count response.")
            bound = count.get("totalTokens" if selected.protocol == "google" else "input_tokens")
            if type(bound) is not int or bound < 0:
                raise RuntimeError("Provider did not return a valid input token count; generation was not sent.")
        else:
            # Unlimited grants still record conservative charges for uncertain
            # requests. This estimate is never used to authorize a finite quota.
            bound = len(json.dumps(body, ensure_ascii=False).encode()) + 4096
        reservation = self.ledger.reserve(developer, bound, cap)
        if on_delta is not None:
            body["stream"] = True
            response = self.transport.stream_json(models.endpoint(selected.endpoint, model), body, on_delta, headers=headers)
        else:
            response = self.transport.json("POST", models.endpoint(selected.endpoint, model), body, headers=headers)
        result = models.parse_response(selected.protocol, response)
        usage = result["usage"]
        if all(type(usage.get(key)) is int and usage[key] >= 0 for key in ("input", "output")):
            self.ledger.settle(reservation, usage["input"], usage["output"])
        # Missing/malformed usage stays fully charged; no silent refund. Provider
        # failures also retain the reservation because billing may have occurred.
        return result

    def fetch(self, url):
        transport = ApprovedHTTPS(self.policy, max_response_bytes=200_000)
        response = transport.request("GET", url)
        try:
            return response.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("The managed fetch tool currently accepts UTF-8 text responses only.") from exc
