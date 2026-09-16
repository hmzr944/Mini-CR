# prism_v2/data — donnees de collecte

Contenu **non versionne** (regenerable depuis l'API publique OKX) :

- `l2_snapshots/*.jsonl` — snapshots de carnet L2 horodates, produits par
  `prism_v2/collector.py`. Volumineux et croissants.
- `instruments_<run_id>.json` — instantane du Registry au moment d'un run,
  produit par le smoke test. Conserve la provenance et la date de decouverte.

Ce qui EST versionne se trouve dans `prism_v2/ledger/captures.jsonl` :
le Capture Ledger, memoire append-only du systeme.

Regenerer :

    python3 -m prism_v2.smoke_test --duration 30 --instruments 5

Collecter plus longtemps (plusieurs regimes de marche) :

    python3 -c "
    from prism_v2.market_data import OKXPublicClient
    from prism_v2.collector import L2Collector
    from prism_v2.instruments import InstrumentType
    c = OKXPublicClient(); reg = c.load_registry(['SWAP'])
    univ, _ = reg.executable_universe(InstrumentType.SWAP_INVERSE)
    print(L2Collector(c).collect(univ, duration_s=3600, interval_s=5)[1].to_dict())
    "
