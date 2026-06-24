# Synthetic OTel HDF5 — schema & structural-equivalence spec

`generate_hdf5.py` emits dense OpenTelemetry-framed HDF5 that is a **domain-neutral
structural analog** of the reference dense-telemetry HDF5 a collector hands us. "Structural
analog" means: same hierarchical depth, same group/dataset topology, same per-node metadata
**cardinality and type mix**, and the same dataset dtypes/shapes — reframed entirely in OTel
terms. The point is to exercise our N-D OTel access patterns (Navigator viewport → hyperslab →
datashader; kerchunk ranged GETs) against a source that behaves like the real thing.

## Container geometry (depth 4 · 7 groups · 5 datasets · 56 attributes)

```
/                                              attrs:1    collection.uuid
└─ ResourceMetrics                             attrs:21   collection/service/sdk + 6 value+unit pairs
   ├─ Scope                                    attrs:9    scope/SDK config (int32×3, f64×2, bool×2, str×2)
   │  └─ Windows            dataset (Nw,)      compound (StartIndex i4, EndIndex i4, Stride i4)
   ├─ Resource[0]                              attrs:4    resource semconv (4 fixed strings)
   │  ├─ ScopeAnchors[0]                       attrs:2    + SeriesAnchor (2,)     compound (i8,f8,f8)
   │  └─ ScopeAnchors[1]                       attrs:2    + SeriesAnchor (N,)     compound (i8,f8,f8)
   └─ Metric[0]                                attrs:6    counts + output.data.rate+unit + value.unit + uuid
      ├─ Values           dataset (N, T) int16  attrs:5   the dominant 2D block (chunked, or --contiguous)
      └─ Timestamps       dataset (T,)  int64   attrs:6   unix-nanos index
```

Per-node attribute-count multiset = `{1, 21, 9, 4, 2, 2, 6, 5, 6}` → **56 total**, identical to
the reference (verified by audit, below).

## Attribute typing (mirrors the reference)

- **Fixed-length ASCII strings** (`|S<len>`, not h5py's default variable-length UTF-8): IDs are
  `|S36` (uuids), ISO-8601 timestamps are `|S32`, units/enums are short `|S*`. Helper: `_fix()`.
- **value + `.unit` pairs** — a `float64` metric value beside a fixed-string `.unit` sibling
  (e.g. `collection.interval` + `collection.interval.unit="s"`); this is exactly an OTel metric's
  (value, unit). Helper: `_pair()`. `ResourceMetrics` carries 6 such pairs.
- **`int64`/`int32` counts**, **`bool` flags** — match the reference's numeric cardinality
  (`float64=9, int64=8, int32=3, bool=3` across the file).

## Datasets

| dataset | shape | dtype | storage |
|---|---|---|---|
| `Metric[0]/Values` | (N series, T time) | `int16` (`--dtype` configurable) | chunked + `fill_time=NEVER`, or `--contiguous` |
| `Metric[0]/Timestamps` | (T,) | `int64` unix-nanos | contiguous |
| `Scope/Windows` | (Nw,) | compound `(i4,i4,i4)` | contiguous |
| `Resource[0]/ScopeAnchors[{0,1}]/SeriesAnchor` | (2,) and (N,) | compound `(i8,f8,f8)` | contiguous |

## Access-pattern mapping

```
Navigator viewport (RangeXY)  ==  hyperslab Values[series_lo:series_hi, t_lo:t_hi]
filter-before-rasterize       ==  read only that hyperslab
datashader rasterization      ==  over the selected 2D block
time-window query             ==  hyperslab on the time axis (+ Timestamps index)
```
Default chunking is tuned for viewport reads; `--emit-kerchunk` writes a reference manifest so a
viewport fetches only the chunks it covers (virtual Zarr over fsspec/Dask, ranged S3 GETs).
`--contiguous` reproduces the reference's unchunked storage when fidelity to that layout matters.

## Regenerate & verify equivalence

```bash
uv run --with h5py,numpy python zarf/scripts/generate_hdf5.py --out ./local_hdf5 \
  --n-series 5001 --n-time 10000            # full-size analog (add --contiguous for exact storage)
```

Equivalence is checked structurally (depth · group/dataset topology · per-node attribute
cardinality · attr/dataset dtype histograms · compound dtypes · dataset ranks) — the synthetic
output's `(depth, kind, #attrs)` fingerprint matches the reference's exactly. Re-run with any
read-only HDF5 auditor that walks groups/datasets/attrs and compare the fingerprints.
