"""
update_assets.py
----------------
Updates assets.json for all fooddb_veritas1 records that have images
in their images/original/ directory.

Sets:
  has_images: true
  image_roles: ["hero", "closeup", "context"]  (based on which files exist)
  image_count: N
  images_transferred_at: ISO timestamp
"""
import json, os
from pathlib import Path
from datetime import datetime, timezone

FOODDB = Path("/Users/rajkumarrajagobalan/db_veritas1/fooddb_veritas1/production/data/01_canonical")

updated = 0
skipped_no_images = 0
errors = []
timestamp = datetime.now(timezone.utc).isoformat()

for shard_dir in sorted(FOODDB.iterdir()):
    if not shard_dir.is_dir():
        continue
    for record_dir in sorted(shard_dir.iterdir()):
        if not record_dir.is_dir():
            continue
        img_dir = record_dir / "images" / "original"
        assets_path = record_dir / "assets.json"
        
        if not img_dir.exists():
            continue
        
        # Find what image files exist
        image_files = list(img_dir.glob("*.jpg")) + list(img_dir.glob("*.jpeg")) + list(img_dir.glob("*.png"))
        if not image_files:
            skipped_no_images += 1
            continue
        
        # Determine which roles are present
        image_roles = []
        role_map = {
            'hero.jpg': 'hero',
            'closeup.jpg': 'closeup',
            'context.jpg': 'context',
        }
        for img_file in sorted(image_files):
            role = role_map.get(img_file.name)
            if role:
                image_roles.append(role)
            else:
                # Unknown file name — include as-is
                image_roles.append(img_file.stem)
        
        # Load existing assets.json
        if assets_path.exists():
            try:
                with open(assets_path) as f:
                    assets = json.load(f)
            except Exception as e:
                errors.append(f"{record_dir.name}: Failed to load assets.json: {e}")
                continue
        else:
            assets = {}
        
        # Update assets.json
        assets['has_images'] = True
        assets['image_roles'] = image_roles
        assets['image_count'] = len(image_files)
        assets['images_transferred_at'] = timestamp
        
        # Build images list (replace existing)
        images_list = []
        for img_file in sorted(image_files):
            role = role_map.get(img_file.name, img_file.stem)
            images_list.append({
                'role': role,
                'filename': img_file.name,
                'path': f"images/original/{img_file.name}",
                'size_bytes': img_file.stat().st_size,
            })
        assets['images'] = images_list
        
        # Write back
        try:
            with open(assets_path, 'w') as f:
                json.dump(assets, f, indent=2, ensure_ascii=False)
            updated += 1
        except Exception as e:
            errors.append(f"{record_dir.name}: Failed to write assets.json: {e}")
        
        if updated % 100 == 0 and updated > 0:
            print(f"  Updated {updated} records...")

print(f"\n=== ASSETS UPDATE COMPLETE ===")
print(f"  Updated:           {updated}")
print(f"  Skipped (no imgs): {skipped_no_images}")
print(f"  Errors:            {len(errors)}")

if errors:
    print("\nErrors:")
    for e in errors:
        print(f"  {e}")

# Verify a sample
print("\nSample verification:")
sample_path = FOODDB / "001001_001500" / "FD001211_nonya_laksa_laksa_lemak" / "assets.json"
if sample_path.exists():
    with open(sample_path) as f:
        sample = json.load(f)
    print(f"  FD001211 (Nonya Laksa): has_images={sample.get('has_images')}, roles={sample.get('image_roles')}")
