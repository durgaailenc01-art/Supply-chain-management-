# Canonical CSV importer

`backend.canonical_import` reads a WMS-neutral package described in
[`WAREHOUSE_DATA_CONTRACT.md`](WAREHOUSE_DATA_CONTRACT.md). It does not transform
source WMS exports: an adapter must first supply the exact canonical headers,
base-unit quantities, UTC timestamps, manifest lineage and checksums. It does
not change `data/starter`, call the demo optimizer, or publish a recommendation.

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m backend.canonical_import C:\path\to\canonical-package --report C:\path\to\validation-report.json
```

Exit code `0` means the package passed structural and relational validation;
`1` means hard errors were found. The JSON report separates `errors` (rejected
package), `warnings` (limited coverage), and `incidents` (known unsafe or
unverified current bins). A valid package can contain unsafe current stock;
those bins remain in the imported tables and appear in `blocked_locations`.
`candidate_locations` is a physical screening result, **not** a SKU-specific
placement approval. `accepted_for_recommendations` remains false because
external snapshot attestations, site certificates, source control totals and
optimizer integration still require independent verification.

The importer checks file membership, hashes, exact columns and types, required
fields, identifiers, warehouse-safe foreign keys, composite keys, effective
dates, stock/pack quantities, lot and serial identity, assignment scope,
partial event links, temperature, hazard and co-storage policy, current weight
and volume, package fit, packing certificate upper bounds, shared-bin plans and
picker graph reachability. Missing optional history does not create synthetic
events or inferred labor performance. A `demand_only` package can pass import
validation but has no eligible locations.

Run the synthetic fixture suite with:

```powershell
.\.venv\Scripts\python.exe -m unittest backend.tests.test_canonical_import -v
```

The fixtures exercise a valid shared bin with two partial picks, invalid
references, incomplete event history, missing shared-bin approvals, unsafe
locations, implausible packing plans, a demand-only extract and checksum
tampering. Fixture certificates and consistency tokens are synthetic test
values; they are not evidence for a real warehouse.
