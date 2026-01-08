# Zuspec SystemVerilog Backend

The Zuspec SystemVerilog (SV) Backend is a code generator that transforms Zuspec hardware component models into synthesizable SystemVerilog RTL.

## Features

- **Component Translation**: Converts Zuspec Components to SystemVerilog modules
- **Clocked Processes**: Transforms `@sync` decorated methods to `always` blocks
- **Async Processes**: Converts `@process` methods to `initial` blocks with timing control
- **Parameterization**: Supports parameterized component widths using const fields
- **Bundle Flattening**: Automatically flattens interface bundles to port lists
- **Instance Hierarchy**: Generates module instantiations with port connections
- **Export Interfaces**: Creates SystemVerilog interfaces for export fields with bound tasks
- **Debug Annotations**: Optional source location comments in generated code

## Installation

```bash
pip install zuspec-be-sv
```

## Quick Start

```python
import zuspec.dataclasses as zdc
from zuspec.be.sv import SVGenerator
from pathlib import Path

@zdc.dataclass
class Counter(zdc.Component):
    clock : zdc.bit = zdc.input()
    reset : zdc.bit = zdc.input()
    count : zdc.bit32 = zdc.output()

    @zdc.sync(clock=lambda s:s.clock, reset=lambda s:s.reset)
    def _count(self):
        if self.reset:
            self.count = 0
        else:
            self.count += 1

# Generate SystemVerilog
factory = zdc.DataModelFactory()
ctxt = factory.build(Counter)

generator = SVGenerator(Path("output"))
sv_files = generator.generate(ctxt)
```

This generates:

```systemverilog
module Counter(
  input logic clock,
  input logic reset,
  output logic [31:0] count
);

  always @(posedge clock or posedge reset) begin
    if (reset) begin
      count <= 0;
    end else begin
      count <= count + 1;
    end
  end

endmodule
```

## Documentation

Full documentation is available at: https://zuspec.github.io/packages/zuspec-be-sv/docs/_build/html/

Topics covered:

- [Quickstart Guide](docs/quickstart.rst) - Get started in 5 minutes
- [Feature Guide](docs/features.rst) - Parameterization, bundles, interfaces
- [Generator Details](docs/generator.rst) - Deep dive into code generation
- [Examples](docs/examples.rst) - Complete working examples
- [API Reference](docs/api.rst) - Complete API documentation
- [Testing](docs/testing.rst) - Test suite and writing tests
- [Contributing](docs/contributing.rst) - Development guide

## Examples

### Parameterized Component

```python
@zdc.dataclass
class ConfigurableAdder(zdc.Component):
    DATA_WIDTH : int = zdc.const(default=32)
    
    a : zdc.int = zdc.input(width=lambda s: s.DATA_WIDTH)
    b : zdc.int = zdc.input(width=lambda s: s.DATA_WIDTH)
    sum : zdc.int = zdc.output(width=lambda s: s.DATA_WIDTH)
```

Generates:

```systemverilog
module ConfigurableAdder #(
  parameter int DATA_WIDTH = 32
)(
  input logic [(DATA_WIDTH-1):0] a,
  input logic [(DATA_WIDTH-1):0] b,
  output logic [(DATA_WIDTH-1):0] sum
);
  // ...
endmodule
```

### Hierarchical System

```python
@zdc.dataclass
class System(zdc.Component):
    clock : zdc.bit = zdc.input()
    
    counter1 : Counter = zdc.inst()
    counter2 : Counter = zdc.inst()
    
    def __bind__(self):
        return {
            self.counter1.clock : self.clock,
            self.counter2.clock : self.clock
        }
```

Generates module with instantiated subcomponents and connections.

### Export Interface

```python
from typing import Protocol

class SendIF(Protocol):
    async def send(self, data: int) -> int: ...

@zdc.dataclass
class Transactor(zdc.Component):
    send_if : SendIF = zdc.export()
    
    def __bind__(self):
        return {
            self.send_if.send : self._send_impl
        }
    
    async def _send_impl(self, data: int) -> int:
        self.data_out = data
        await self.posedge(self.clock)
        return data * 2
```

Generates SystemVerilog interface with task.

## Requirements

- Python >= 3.7
- zuspec-dataclasses >= 0.0.1

## Development

Clone and install in development mode:

```bash
git clone https://github.com/zuspec/zuspec-be-sv
cd zuspec-be-sv
pip install -e ".[dev]"
```

Run tests:

```bash
pytest
```

Build documentation:

```bash
cd docs
make html
```

## License

Apache-2.0

## Contributing

Contributions are welcome! Please see [Contributing Guide](docs/contributing.rst) for details.

## Related Projects

- [zuspec-dataclasses](https://github.com/zuspec/zuspec-dataclasses) - Core Zuspec language
- [zuspec-be-sw](https://github.com/zuspec/zuspec-be-sw) - Software backend
- [Zuspec Website](https://zuspec.github.io) - Main documentation site
