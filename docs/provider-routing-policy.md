# Provider routing policy

This policy is intentional and applies to Hermes main-session work and delegated
work unless Mohamed explicitly changes it.

## Normal operating mode

Use cloud providers with first-party or explicitly approved authenticated routes.
The work handled by Hermes is important enough that local models are not an
acceptable normal route: local inference is excluded from primary execution,
fallbacks, delegation, reviews, and indirect auxiliary work.

Nous is an approved route for any suitable model, including Gemini models. The
Gemini model family is permitted through Nous authentication; this does not
imply that a direct Google Gemini API credential exists. Ollama Cloud is also
an approved cloud route when authenticated and available. Local Ollama remains
excluded under the local-inference rule above.

OpenRouter and AISA are also excluded from normal execution. They are retained
as emergency backup providers only. They may be used when credits on approved
primary providers are exhausted or when Mohamed explicitly authorizes their
activation. Once authorized, any suitable model on the approved provider may
be selected under the same verification and reporting requirements.
They must not be selected as a silent fallback, an indirect delegation route, or
an auxiliary provider during normal operation.

When Gemini is selected, prefer the authenticated Nous API route unless a
direct Google credential is separately verified. Hermes must not assume that a
direct Google Gemini API credential exists.

## Delegation admission

Before any Claude Code or Codex delegation:

```text
python3 scripts/check_coding_agents.py
```

For work that selects Claude Code, also run the bounded live smoke check:

```text
python3 scripts/check_coding_agents.py --smoke
```

A failed check blocks admission. Do not silently switch to local inference,
OpenRouter, AISA, or another unapproved route.

## Change control

Any exception requires explicit user approval, an identified reason, a bounded
scope, and a post-run record of the provider and model actually used. Provider
selection must be reported honestly; a failed or unavailable route is not
counted as successful model work.
