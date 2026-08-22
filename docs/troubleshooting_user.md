# User troubleshooting

## A provider is unavailable

You can continue in Demo Mode. Opening settings or status never tests provider connectivity. PVE uses safe configuration presence and recorded history unless an owner separately authorizes a live canary.

## Account or billing blocked

The provider cannot currently accept requests because account or quota state needs attention. PVE saves the request history and does not retry automatically. Ask the technical owner to resolve the external account state.

## Temporarily rate-limited

PVE applies bounded retry policy. Do not repeatedly click Run; duplicate interaction is idempotent.

## Ambiguous previous request

PVE cannot prove whether the provider accepted it, so automatic resubmission is blocked. An expert must review the preserved evidence and use the approval-bound reconciliation workflow.

## Preview changed

Configuration, manifest, dependencies, or routing changed after the preview. Create and review a new preview; the old one cannot execute.

## Finding work

Open **My Materials**, then search by module, keyword, or material type. Filesystem paths are not required.
