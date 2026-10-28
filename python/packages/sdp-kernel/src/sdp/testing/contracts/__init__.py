"""One contract suite per driven port (§6, §11.2, TST-003).

Each suite is a mixin class whose tests use a fixture named after the port. A test module
parametrises that fixture over the fake and every real adapter available in the environment;
the fake is never allowed to pass a case the real adapter fails.
"""
