"""The LI Document IQ desktop shell (Track C).

Display and orchestration only. This package holds no pipeline logic: it talks
to the pipeline exclusively through :mod:`dociq.gui.pipeline`, and
``tests/test_import_graph.py`` asserts that it imports none of the pipeline
packages.
"""
