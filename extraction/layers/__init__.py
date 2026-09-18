"""Candidate layers, ordered by how much the page vouches for them.

Each module reads one class of source and adds Candidates to a shared bundle.
They are interchangeable: the pipeline runs all of them and the tiers sort the
results, so adding a source means adding a module here.
"""
