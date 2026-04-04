"""SVEmitPass — write a SystemVerilog file from the synthesised IR."""
from __future__ import annotations

import logging
import os

from zuspec.synth.passes.synth_pass import SynthPass
from zuspec.synth.ir.synth_ir import SynthConfig, SynthIR

_log = logging.getLogger(__name__)


class SVEmitPass(SynthPass):
    """Write a ``.sv`` file at *path* from the synthesised ``SynthIR``.

    When ``ir.pipeline_ir`` is set (i.e. :class:`~zuspec.synth.passes.LowerPass`
    has run) this emits a full multi-stage pipeline.  Otherwise it emits a
    single-module FSM stub driven by ``config.pipeline_stages``.

    **SV IR path:** When ``ir.rtl_modules`` is non-empty (i.e.
    :class:`PipelineToSVPass` has run) the SV is serialised via
    :class:`~zuspec.be.sv.ir.SVEmitter`.  Otherwise the legacy
    ``_generate_pipeline_sv`` string-builder is used as a fallback.

    Emission is a terminal side-effect; ``ir`` is returned unchanged except
    that ``ir.sv_path`` is updated.

    Args:
        config: Synthesis configuration.
        path: Output file path.
        module_prefix: Optional prefix prepended to all module names.
    """

    def __init__(self, config: SynthConfig, path: str, *, module_prefix: str = "") -> None:
        super().__init__(config=config)
        self._path = path
        self._module_prefix = module_prefix

    @property
    def name(self) -> str:
        return "sv_emit"

    def run(self, ir: SynthIR) -> SynthIR:
        from zuspec.synth.mls import _generate_pipeline_sv, _generate_sv_from_meta
        from zuspec.dataclasses.transform.pass_manager import (
            _collect_domain_nodes,
            DomainNodeNotLoweredError,
        )

        remaining = _collect_domain_nodes(ir, set())
        if remaining:
            names = [type(n).__name__ for n in remaining]
            raise DomainNodeNotLoweredError(
                f"SVEmitPass: {len(remaining)} unlowered domain node(s) "
                f"found before SV emission: {names}"
            )

        os.makedirs(os.path.dirname(os.path.abspath(self._path)), exist_ok=True)

        if ir.pipeline_ir is not None:
            if ir.rtl_modules:
                # SV IR path: serialise via SVEmitter
                from zuspec.be.sv.ir import SVEmitter
                content = SVEmitter().emit_all(ir.rtl_modules)
                _log.debug("[SVEmitPass] using SVEmitter (%d modules)", len(ir.rtl_modules))
            else:
                # Legacy fallback: string-builder
                content = _generate_pipeline_sv(ir, self._module_prefix)
        else:
            content = _generate_sv_from_meta(ir, pipeline_stages=self.config.pipeline_stages)

        with open(self._path, "w") as fh:
            fh.write(content)
        ir.sv_path = self._path
        _log.info("[SVEmitPass] wrote %s", self._path)
        return ir
