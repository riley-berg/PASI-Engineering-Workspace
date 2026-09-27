"""Canonical PASI computer-use package boundary.

Keep package import side effects minimal. Runtime modules import their required
submodules explicitly so optional or historical adapters cannot break startup.
"""
