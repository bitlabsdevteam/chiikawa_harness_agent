# Chiikawa enterprise security requirements

Enterprise Chiikawa is a lightweight coding agent for dedicated developer laptops
and development machines. Company IT administrators are the super admins. The
requirements below include the deployment decisions confirmed by the owner; they
are requirements, not a claim that every control is already implemented.

## 1. Lightweight, coding-focused operation

Keep the core agent small and limit its purpose to software implementation,
review, debugging, testing, and supporting engineering work. Standard-profile
usage must not acquire enterprise identity or infrastructure dependencies.

## 2. Jail and Sandbox execution

Jail is the default, including Enterprise. Developers can use Jail or Sandbox
within the IT administrator's policy. Both enterprise runtimes must enforce the
same identity, device, provider, data, network, and quota restrictions. A mode
switch, new conversation, resumed session, or child agent must not bypass them.

The current standard-profile Jail runs shell commands with host permissions.
It does not satisfy enterprise containment. Enterprise Jail needs an enforced OS
boundary before host commands can be exposed under these requirements. Do not
silently substitute unrestricted host execution if enforcement is unavailable.

## 3. Super admins, developers, and Microsoft Entra ID

Authenticate developers with Microsoft Entra ID for the configured company
tenant. Use stable tenant/object identifiers, not email addresses or a role
claimed in a prompt. IT controls enrollment, disable/revoke operations, approved
providers, network destinations, installation, and token limits. Developers
cannot grant themselves administrative authority or rewrite managed policy.

## 4. Project instructions and the core policy

Developers may create AGENTS.MD for project guidance. Supported filename variants
remain agents.md, AGENTS.md, and AGENTS.MD. Project guidance is subordinate to the
installed core policy and IT-managed runtime controls. It cannot authorize data
export, elevate roles, change device assignment, or increase quotas.

## 5. Company information, providers, and network access

Company code and private information may leave the local execution environment
only through destinations explicitly approved by IT. OpenAI, Anthropic Claude,
Google, and other model providers are permitted with super-admin approval.
Provider approval alone does not enable network access to an arbitrary endpoint.

Network access defaults to offline: no external internet access. IT may approve
specific URLs or IP addresses. Enforce destination restrictions on model traffic
and tool traffic; do not rely on the model to honor them. Do not implicitly allow
redirect targets, arbitrary proxies, wildcard domains, or all public internet
access. Authentication/bootstrap also needs explicit IT-approved connectivity.

Credentials and managed security state must be inaccessible to project tools.
Approval of a destination permits transmission to that destination under the
coding task; it is not a promise that no data crosses the machine boundary.

## 6. One assigned machine per developer

Bind each enrolled developer to one assigned machine. IT authorizes enrollment,
replacement, and revocation. Check the assignment before protected execution;
reject missing, mismatched, or revoked assignments. Machine/motherboard identifiers
can support enrollment but are not secret authentication credentials. Managed OS
protection and device credentials are required against identifier spoofing.

## 7. IT-admin-only installation and removal

Only company IT administrators may install, update, configure, or uninstall the
managed enterprise deployment. Protect installation, policy, enrollment, provider
credentials, and accounting with OS permissions. Do not run model-generated
commands as the IT administrator. Application roles alone cannot prevent an OS
administrator from replacing software; the OS administrator is the trust anchor.

## 8. Administrator-controlled token limits

Usage is unlimited by default. IT may set, change, or remove a developer's token
limit. Developers cannot override it with CLI flags, project files, another
conversation, child agents, concurrent requests, or local accounting resets.

Account for ordinary model replies, compaction, and delegated-agent requests.
A context-compaction threshold is not a token quota. Any configured quota must
have explicit accounting/reset semantics and fail closed when enforcement cannot
establish that a request fits the remaining allowance.

## Implementation tracking

See [enterprise requirements review](docs/enterprise-requirements-review.md) for
current evidence, gaps, and verification gates. Passing existing Sandbox tests
does not certify identity, device enrollment, network allowlisting, or quotas.
