"""Probabilistic cross-observatory association (Integration v0.4.0 §4, ADR-019).

Two implementations behind ``AssociationService``, required to agree on the reference fixture
before either feeds a gate (INT-012):

- ``BayesFactorAssociation``: the CPU reference, Budavári and Szalay (2008), computed on the sphere
  in radians with the full positional covariance;
- ``LikelihoodRatioAssociation``: Sutherland and Saunders (1992), computed on the tangent plane in
  arcseconds against the background density.

They share only the kernel's pure geometry (``sdp.kernel.science.association``) and the graph
assembly below; the probabilities are computed independently.
"""

from sdp.adapters.association.bayes import BayesFactorAssociation
from sdp.adapters.association.likelihood_ratio import LikelihoodRatioAssociation

__all__ = ["BayesFactorAssociation", "LikelihoodRatioAssociation"]
