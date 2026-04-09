"""Lambing analysis modules.

Provides data loading, animal classification, lineage utilities, and loss
categorisation for lambing season analysis.

Key terminology:
- "Born" = live lambs (fate=Alive or fate=Sold).
- fate=Sold means successfully raised/harvested -- this is NOT a loss.
- Use "loss" rather than "death" in user-facing strings.
- daysReared=None or 0 -> stillborn
- daysReared 1-90 -> early loss (lambing-related)
- daysReared > 90 -> late loss (not lambing-related)
"""
