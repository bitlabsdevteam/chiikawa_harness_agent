# Enterprise requirements review

Source: [SECURITY.md](../SECURITY.md). This is an implementation gap review, not
a compliance certification. The original requirements remain authoritative;
an existing feature is not evidence that the entire requirement is satisfied.

## Requirement coverage

| Requirement | Current evidence | Remaining implementation and acceptance evidence |
| --- | --- | --- |
| Coding purposes only | The installed core policy identifies Chiikawa as a coding agent and now explicitly bounds tasks to software engineering. | Prompt instructions are behavioral guidance, not a deterministic task classifier. Runtime permissions must apply regardless of how a task is described. |
| 1. Lightweight | `pyproject.toml` declares no third-party runtime dependencies; Docker uses the standard-library subprocess interface. | Keep management controls separate from model/framework dependencies. Measure release size and startup costs when those controls are added. |
| 2. Jail and Sandbox, Jail by default | Standard CLI defaults to Jail. Enterprise requires Sandbox; `Harness._validate_profile` rejects Jail. Jail shell execution has host permissions. | Implement the confirmed enterprise Jail default with strict containment in requirement 5. Do not relabel the existing host shell as isolated. An enterprise Jail option needs an enforced OS boundary before it can provide containment. |
| 3. Super admin and developer profiles | `profile=enterprise` selects execution behavior, not an authenticated user role. No identity registry, role authorization, or developer revocation exists. | Bind developers to authenticated OS/service identities; implement admin-only enrollment, role changes, disable/revoke operations, and reject developer self-promotion. Verify denial using separate principals. |
| 4. Developer AGENTS.MD | Core policy directs tool-based discovery of `agents.md`, `AGENTS.md`, and `AGENTS.MD`. These files cannot replace the core policy. | Preserve developer project guidance under administrator policy. Test ordinary coding guidance alongside forged admin/permission instructions. |
| 5. No leakage of company code/private information | Sandbox denies tool networking by default and masks selected paths. Foundry/OpenRouter requests run on the host and include task/context/tool output. Standard Jail allows host networking. | Enforce the confirmed IT-approved external provider boundary; restrict all model traffic, redirects, proxies, telemetry, tool networking, and local exports accordingly. A deny-network container alone cannot prove this requirement. Use controlled allowed/denied endpoints and test actual payload routing. OS/service egress enforcement is necessary for hostile host users. |
| 6. Assigned machines only | No hardware enrollment or device binding exists. | Enroll an administrator-approved device identity for each developer; reject unknown/revoked identities before provider or tool use. Hardware identifiers are not secrets or proof of possession and can change or be spoofed. Strong enforcement needs a managed device credential/attestation and a documented replacement process. |
| 7. Super-admin install/uninstall only | `scripts/install.py` deliberately installs into the current user's directory without admin access. Enterprise only checks that its installed code is outside the project. | Provide a managed installation path and lifecycle controls backed by OS permissions or service administration. Deny non-admin replacement/removal; distinguish managed deployment from ordinary community installations and copied source. |
| 8. Admin-set developer token limits | `max_output_tokens` is caller-controlled and per response. `budget_tokens` triggers context compaction; it is not a usage quota. | Define the accounting window and token categories. Enforce admin-owned limits across all requests, compaction, children, concurrent processes, resume, and new sessions. Reserve quota atomically before requests; specify failure, retry, missing-usage, and crash behavior. Test exhaustion and concurrent attempts, not just a displayed counter. |

## Confirmed deployment decisions

The owner confirmed: dedicated developer laptops/development machines; providers
including OpenAI, Claude, Google, and others only with IT approval; Jail default;
Microsoft Entra ID; one machine per developer; unlimited tokens unless IT sets a
limit; offline network default with IT-approved URLs/IPs; and company IT as the
super admin. These decisions supersede the former Sandbox-only enterprise default.

Implementation must preserve the approved Jail default while adding its missing
OS boundary. It must not relabel existing unrestricted host execution as secure.
A local privileged management service can hold enrollment, quotas, and credentials
while running project tools without administrator privileges. IT-owned service
state is necessary because a developer-writable JSON file cannot enforce a quota
or authenticate a role. Shared organization enrollment is needed to revoke an
old assignment when provisioning its replacement on a different machine.

Quota configuration will state its accounting period explicitly. Unlimited is the
default; a finite grant can be consumed until IT resets/replaces the grant. Daily
or monthly replenishment must never be silently inferred from a numeric limit.

The actual Entra tenant/app registration, IT-approved provider destinations, and
managed OS deployment must be configured by the organization. Synthetic test
identities and endpoints do not constitute production enrollment or permission.

## Verification gates

Before declaring the requirements implemented, verify identity/role decisions,
device enrollment and revocation, installation lifecycle authorization, network
and provider routing, quota accounting/concurrency, and inheritance under the
chosen deployment model. Run existing regression and package checks after code
changes. Use synthetic data and local controlled services for leakage tests;
never send company data to prove that a forbidden path is blocked.

Existing Sandbox, prompt separation, and release tests establish their stated
boundaries only. They do not establish super-admin management, device binding,
organization-wide data containment, or developer usage quotas.
