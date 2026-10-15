"""zuspec.be.sv.passes — SystemVerilog backend passes.

The two synthesis passes are resolved on first use (PEP 562): they import
zuspec-synth at module scope, and the IR-to-SV builders in this package
(``mmr_to_sv``, ``fsm_to_sv``, ``protocol_to_sv``) are imported by
zuspec-dataclasses, which must not need synth for that.
"""

__all__ = ["PipelineToSVPass", "SVEmitPass"]

_LAZY = {
    "PipelineToSVPass": ".pipeline_to_sv",
    "SVEmitPass": ".sv_emit",
}


def __getattr__(name):
    mod = _LAZY.get(name)
    if mod is None:
        raise AttributeError("module %r has no attribute %r" % (__name__, name))
    import importlib
    return getattr(importlib.import_module(mod, __name__), name)
