"""WADDL submission helpers.

Resolve vial tokens (last-4 EID / VID / name) into the per-animal fields a
WADDL (Washington Animal Disease Diagnostic Lab) CoreOne submission needs:
ID, Taxon/breed, Sex Code, and Age.

The browser-side fill recipe (selectors, portal internal IDs, gotchas) lives in
``agriwebb/docs/waddl-submission.md`` and the ``waddl-submission`` skill; this
package is pure data resolution from the local ``animals.json`` cache.
"""

from agriwebb.waddl.resolver import (
    ResolvedAnimal,
    resolve,
    resolve_token,
)

__all__ = ["ResolvedAnimal", "resolve", "resolve_token"]
