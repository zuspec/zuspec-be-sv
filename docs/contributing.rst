############
Contributing
############

Thank you for your interest in contributing to the Zuspec SystemVerilog Backend!

Getting Started
===============

Development Setup
-----------------

1. Clone the repository:

   .. code-block:: bash

      git clone https://github.com/zuspec/zuspec-be-sv
      cd zuspec-be-sv

2. Create a virtual environment:

   .. code-block:: bash

      python -m venv venv
      source venv/bin/activate  # On Windows: venv\\Scripts\\activate

3. Install in development mode:

   .. code-block:: bash

      pip install -e ".[dev]"

4. Run tests to verify setup:

   .. code-block:: bash

      pytest

Code Style
==========

Python Style
------------

The project follows these conventions:

* **Line length**: 100 characters
* **Style**: Black formatting
* **Imports**: isort for import sorting
* **Type hints**: Optional but encouraged for public APIs

Format code:

.. code-block:: bash

   black src/ tests/
   isort src/ tests/

Check formatting:

.. code-block:: bash

   black --check src/ tests/
   isort --check src/ tests/

Documentation Style
-------------------

* Use reStructuredText (RST) for documentation
* Follow Sphinx conventions
* Include code examples for new features
* Update API reference for new public methods

Building Documentation
----------------------

Build docs locally:

.. code-block:: bash

   cd docs
   make html
   
   # View in browser
   open _build/html/index.html

Contribution Workflow
=====================

1. Fork and Branch
------------------

Fork the repository and create a feature branch:

.. code-block:: bash

   git checkout -b feature/my-new-feature

2. Make Changes
---------------

* Write code with clear, descriptive names
* Add tests for new functionality
* Update documentation as needed
* Keep commits focused and atomic

3. Test
-------

Run tests before committing:

.. code-block:: bash

   # Run all tests
   pytest
   
   # Run specific tests
   pytest tests/unit/test_smoke.py
   
   # Check coverage
   pytest --cov=zuspec.be.sv

4. Commit
---------

Write clear commit messages:

.. code-block:: text

   Add support for packed arrays in SystemVerilog generation
   
   - Extend type conversion to handle packed array syntax
   - Add test cases for packed array ports
   - Update documentation with packed array examples

5. Push and Create PR
---------------------

.. code-block:: bash

   git push origin feature/my-new-feature

Then create a Pull Request on GitHub with:

* Clear description of changes
* Link to related issues
* Test results
* Documentation updates

Code Review Process
===================

PR Requirements
---------------

Pull requests must:

* ✅ Pass all CI tests
* ✅ Include tests for new functionality
* ✅ Update documentation
* ✅ Follow code style guidelines
* ✅ Have clear commit messages

Review Checklist
----------------

Reviewers will check:

* Correctness of SystemVerilog generation
* Test coverage
* Documentation completeness
* Code clarity and maintainability
* Performance implications

Types of Contributions
======================

Bug Fixes
---------

Report bugs via GitHub Issues with:

* Description of the bug
* Minimal reproducible example
* Expected vs actual behavior
* Environment details (Python version, OS)

For bug fix PRs:

* Reference the issue number
* Include regression test
* Explain the root cause

New Features
------------

Before implementing major features:

1. Open a GitHub Issue to discuss
2. Get feedback on design approach
3. Consider backwards compatibility
4. Plan documentation and tests

Feature PR should include:

* Feature implementation
* Comprehensive tests
* Documentation with examples
* Update to changelog

Documentation Improvements
--------------------------

Documentation contributions are valuable:

* Fix typos or unclear wording
* Add examples
* Improve API reference
* Write tutorials

Testing Improvements
--------------------

Help improve test coverage:

* Add test cases for edge cases
* Improve test clarity
* Add simulation tests
* Performance benchmarks

Development Guidelines
======================

Generator Architecture
----------------------

The SVGenerator follows this structure:

1. **Entry point**: ``generate(ctxt)`` - processes all components
2. **Component generation**: ``_generate_component()`` - creates module structure
3. **Statement generation**: ``_generate_stmt()`` - converts IR statements
4. **Expression generation**: ``_generate_expr()`` - converts IR expressions
5. **Type conversion**: ``_get_sv_type()`` - maps types to SystemVerilog

When adding features, maintain this separation of concerns.

IR Understanding
----------------

The generator operates on the IR (Intermediate Representation) from zuspec-dataclasses:

* **Components** → ``ir.DataTypeComponent``
* **Fields** → ``ir.Field``, ``ir.FieldInOut``
* **Statements** → ``ir.Stmt*`` classes
* **Expressions** → ``ir.Expr*`` classes

Study existing IR patterns in tests before extending.

Testing Strategy
----------------

Follow the testing pyramid:

* **Unit tests** (majority): Test individual transformations
* **Integration tests**: Test component hierarchies
* **Simulation tests**: Verify correctness with HDL simulator
* **Performance tests**: Ensure scalability

Common Pitfalls
===============

Name Sanitization
-----------------

Always use ``_sanitize_sv_name()`` for Python names:

.. code-block:: python

   # Correct
   module_name = self._sanitize_sv_name(comp.name)
   
   # Wrong - may generate invalid SystemVerilog
   module_name = comp.name

Type Inference
--------------

Handle cases where types may not be fully specified:

.. code-block:: python

   # Defensive type checking
   if isinstance(field.datatype, ir.DataTypeInt):
       bits = field.datatype.bits
   else:
       bits = self._infer_bits_from_name(field.name)

Bundle Handling
---------------

Bundles need careful handling:

* Resolve bundle type from context
* Flatten to individual signals
* Preserve port directions

Signal References
-----------------

Be careful with signal references in bindings:

* ``self.port`` - direct port reference
* ``self.inst.port`` - subcomponent port reference
* ``self.bundle.field`` - flattened bundle field

Getting Help
============

* **GitHub Issues**: Bug reports and feature requests
* **Discussions**: Questions and general discussion
* **Email**: matt.ballance@gmail.com for private inquiries

Code of Conduct
===============

Be respectful and constructive:

* Welcome newcomers
* Be patient with questions
* Provide constructive feedback
* Focus on code, not people
* Respect different viewpoints

Release Process
===============

Releases follow semantic versioning:

* **Major** (1.0.0): Breaking changes
* **Minor** (0.1.0): New features, backwards compatible
* **Patch** (0.0.1): Bug fixes

Release checklist:

1. Update version in ``pyproject.toml``
2. Update CHANGELOG.md
3. Run full test suite
4. Build documentation
5. Create git tag
6. Push to PyPI
7. Create GitHub release

Acknowledgments
===============

Thank you to all contributors who help improve this project!

Contributors are recognized in:

* CONTRIBUTORS.md file
* GitHub contributors page
* Release notes
