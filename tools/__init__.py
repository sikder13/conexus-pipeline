"""Executable pipeline tools.

Each subpackage is one tool with a `main.py` entrypoint, runnable from the repo
root as `python -m tools.<name>`. Tools own their workflow and their CLI; the
shared rules they all obey live in `lib/`.
"""
