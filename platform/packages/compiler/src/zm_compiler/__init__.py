"""workflow.json → release: BPMN, JSON Schema forms, UI config, groups (#19, S1-08a)."""

from zm_compiler.compile import CompileError, compile, process_key
from zm_compiler.release import FormSpec, Group, Release

__version__ = "0.1.0"

__all__ = ["CompileError", "FormSpec", "Group", "Release", "compile", "process_key"]
