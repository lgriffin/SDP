# sdp-stellar

The UC-01 science module: open-cluster recovery and candidate search (architecture spec §10). It imports only the kernel's domain and science seams, receives its clustering algorithms as injected `ClusterSearchAlgorithm` services, and registers as the `uc01-stellar-clusters` entry point.

See [`docs/protocols/uc01-stellar-clusters.md`](../../../docs/protocols/uc01-stellar-clusters.md). No Gaia data is included; `python -m sdp_stellar.synthetic` writes synthetic fields with planted clusters and synthetic benchmark tables, and says so in their metadata.
