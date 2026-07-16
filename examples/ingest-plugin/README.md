# OpenKB Ingest Plugin Example

This directory shows the minimal shape of an external package that contributes
bundle ingest components through Python entry points.

Install a package like this in the same environment as OpenKB, then enable its
components in `.openkb/config.yaml`:

```yaml
ingest:
  pipeline: bundle
  importers:
    enabled:
      - example_text
  normalizers:
    enabled:
      - example_text
```

The entry point groups are:

- `openkb.ingest.importers`
- `openkb.ingest.normalizers`
- `openkb.ingest.enrichers`

Each entry point should resolve to a class or factory returning an object that
matches the corresponding OpenKB ingest protocol.
