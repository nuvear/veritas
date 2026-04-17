# FoodDB FD-Series Naming Schema & Mapping Rules

To build the `fooddb_veritas1` skeleton, we need a stable, continuous ID system. The existing `gold_standard` JSON files use a variety of prefix patterns (D, EC, EI, EM, EX, F, G, VT) with differing numeric lengths. 

To create a clean, unified `FD`-series (e.g., `FD000001`), we will map the existing records to the new ID schema.

## 1. The New Naming Schema

The new canonical ID for every food record will follow this format:

```text
FD{6-digit sequential number}
Example: FD000001
```

### Folder Naming
The folder name for each record will combine the stable ID and the slug (derived from the `food_name`):
```text
{ID}_{slug}
Example: FD000001_hainanese_chicken_rice
```

### Shard Naming
Records will be grouped into shard folders of 500 to avoid filesystem limits:
```text
000001_000500
000501_001000
...
```

## 2. ID Mapping Strategy

We currently have 4,315 `gold_standard` files distributed across 8 original prefix series:
- **D** (363 files)
- **EC** (98 files)
- **EI** (44 files)
- **EM** (93 files)
- **EX** (84 files)
- **F** (1,609 files)
- **G** (1,735 files)
- **VT** (289 files)

### The Mapping Rule
To assign `FD` numbers, we will:
1. **Sort** all 4,315 filenames alphabetically. This naturally groups them by their original prefix series (D, EC, EI, etc.) and then by their original numeric sequence.
2. **Assign** a sequential integer starting from `1` to `4315`.
3. **Format** the integer as a 6-digit zero-padded string prefixed with `FD`.

**Example Mapping:**
| Original Filename | New ID | New Slug | New Folder Name |
|-------------------|--------|----------|-----------------|
| `D01010001_alcohol_free_beer_pass.json` | `FD000001` | `alcohol_free_beer` | `FD000001_alcohol_free_beer` |
| `D01020001_ale_pass.json` | `FD000002` | `ale` | `FD000002_ale` |
| ... | ... | ... | ... |
| `F01010010_cottage_cheese_pass.json` | `FD000364` | `cottage_cheese` | `FD000364_cottage_cheese` |

## 3. The `food.json` Header Injection

When the raw `gold_standard` files are migrated into the new `fooddb_veritas1` structure, the ingestion script must inject the following header at the top of the JSON payload, preserving all existing Veritas pipeline data below it.

```json
{
  "food_id": "FD000001",
  "slug": "alcohol_free_beer",
  "record_status": "approved",
  "version": 1,
  "has_images": false,
  "image_roles": ["hero", "closeup", "context"],
  ... (existing veritas_meta, food_name, per_100g, etc.)
}
```

*Note: `has_images` defaults to `false` until the image pipeline populates the `images/original/` directory.*

## 4. Preservation of Provenance

To ensure traceability back to the original Veritas pipeline run, the `veritas_meta.input_file_path` and the original filename should be preserved within the JSON structure (e.g., inside `veritas_meta` or a new `provenance` block). This allows cross-referencing if any data anomalies are discovered later.
