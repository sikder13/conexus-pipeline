"""Shared code for the Conexus-100 pipeline.

Everything in this package is imported by more than one tool. Tool-specific
logic belongs in ``tools/<name>/`` instead, so that the rules encoded here —
claim shape, tier meaning, config loading, database access — have exactly one
definition and cannot drift between tools.
"""
