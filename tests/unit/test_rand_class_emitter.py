"""SVRandClassEmitter: every constraint reaches the SV class, or emission fails.

The emitter used to drop constraints silently. A body built with a loop parsed
to no expressions, so the class came out unconstrained and the simulator
happily produced duplicate values for an all-different benchmark. Module
constants were emitted by name and failed to elaborate. These cases pin both.
"""
import pytest
import zuspec.dataclasses as zdc

from zuspec.be.sv.rand_class_emitter import SVRandClassEmitter

_N = 4
_TOP = 0x0000_FFFF_FFFF_FFFF


def _emit(cls):
    return SVRandClassEmitter().emit_class(cls)


def _loop_class():
    fields = {f"v{i}": zdc.rand(domain=(0, 7), default=i) for i in range(_N)}
    cons = {}
    for i in range(_N):
        def make(idx):
            def _c(self):
                vi = getattr(self, f"v{idx}")
                for j in range(idx + 1, _N):
                    assert vi != getattr(self, f"v{j}")
            _c.__name__ = f"c_u{idx}"
            _c.__qualname__ = f"Loop.c_u{idx}"
            return zdc.constraint(_c)
        cons[f"c_u{i}"] = make(i)
    return zdc.dataclass(type("Loop", (), {**fields, **cons}))


def test_loop_constraints_are_unrolled():
    sv = _emit(_loop_class())
    pairs = {(i, j) for i in range(_N) for j in range(i + 1, _N)}
    for i, j in pairs:
        assert f"v{i} != v{j};" in sv
    assert sv.count("!=") == len(pairs)


@zdc.dataclass
class _Consts:
    a: zdc.rand(domain=(0, _TOP), default=0)

    @zdc.constraint
    def c_top(self):
        assert self.a <= _TOP


def test_module_constant_becomes_a_sized_literal():
    sv = _emit(_Consts)
    assert "_TOP" not in sv
    assert "a <= 64'd%d;" % _TOP in sv


@zdc.dataclass
class _Cond:
    mode: zdc.rand(domain=(0, 1), default=0)
    x: zdc.rand(domain=(0, 9), default=0)
    y: zdc.rand(domain=(0, 9), default=0)

    @zdc.constraint
    def c_m(self):
        if self.mode == 0:
            assert self.x == self.y
            assert self.x > 2


def test_if_body_becomes_one_implication():
    sv = _emit(_Cond)
    assert "(mode == 0) -> ((x == y) && (x > 2));" in sv


@zdc.dataclass
class _Unknown:
    a: zdc.rand(domain=(0, 9), default=0)

    @zdc.constraint
    def c(self):
        assert self.a < _not_an_int_constant


_not_an_int_constant = "text"


def test_unresolvable_name_is_an_error_not_an_identifier():
    with pytest.raises(ValueError, match="_not_an_int_constant"):
        _emit(_Unknown)


@zdc.dataclass
class _While:
    a: zdc.rand(domain=(0, 9), default=0)

    @zdc.constraint
    def c(self):
        while False:
            pass
        assert self.a < 5


def test_unsupported_statement_is_an_error_not_a_dropped_constraint():
    with pytest.raises(NotImplementedError, match="While"):
        _emit(_While)
