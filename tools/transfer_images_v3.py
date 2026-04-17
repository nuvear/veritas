"""
transfer_images_v3.py
---------------------
Final image transfer script with:
1. Slug-based matching
2. Food name matching from 1-800 JSON files
3. Alias-based matching from food.json records
4. Core name matching (strip parenthetical qualifiers)
5. Manual override table for curated mappings

Records flagged for manual review: #762, #790-#800
"""
import os, re, csv, shutil, json
from pathlib import Path
from datetime import datetime

BASE = Path("/Users/rajkumarrajagobalan/db_veritas1")
FOODDB = BASE / "fooddb_veritas1" / "production" / "data" / "01_canonical"
FD_CSV = BASE / "fooddb_veritas1" / "production" / "data" / "00_registry" / "fd_id_mapping.csv"
JSON_BASE = BASE / "1-800 JSON"
IMAGE_BATCHES = [
    BASE / "food_images_001_200",
    BASE / "food_images_201_400",
    BASE / "food_images_401_600",
    BASE / "food_images_601_800",
]

# Records flagged for manual review (Option B)
FLAGGED = {762, 790, 791, 792, 793, 794, 795, 796, 797, 798}

# Image rename map
IMG_RENAME = {
    "img_01_hero.jpg":        "hero.jpg",
    "img_02_macro.jpg":       "closeup.jpg",
    "img_03_in_the_wild.jpg": "context.jpg",
}

# Manual override: img_num -> fd_id (curated best-match mappings)
# Only include cases where the match is semantically correct
MANUAL_OVERRIDE = {
    # Laksa (Singapore) -> Nonya Laksa (Laksa Lemak) — Singapore laksa is Nonya laksa
    3:   "FD001211",
    # Fried Carrot Cake / Chai Tow Kway (black) -> exact match exists
    12:  "FD001366",
    # Bak Chor Mee (dry) -> Bak Chor Mee (Soup) — closest available
    15:  "FD001144",
    # Cooked Rice Vermicelli / Bee Hoon (plain) -> Bee Hoon (Rice Vermicelli)
    34:  "FD002442",
    # Silken Tofu (steamed, plain) -> Silken Tofu (Soft/Silken Grade, Undrained)
    39:  "FD004258",
    # Tom Yum Soup (clear broth, with prawns) -> Tom Yum Goong (Clear Broth)
    42:  "FD000855",
    # Palak Paneer -> Palak Paneer with Tandoori Roti (closest)
    67:  "FD002714",
    # Chana Masala -> Chana Masala Rice (closest)
    69:  "FD002717",
    # Aloo Gobi -> Aloo Gobi Masala with Tandoori Roti (closest)
    72:  "FD003417",
    # Semolina / Rava / Sooji (raw) -> Semolina (Raw, Uncooked)
    108: "FD001301",
    # Whole Green Mung Beans (raw, dried) -> Whole Green Mung Beans, Raw Dried
    117: "FD001705",
    # Tempeh (fresh, plain, uncooked) -> Tempeh, Raw (Fermented Soybean Cake, Uncooked)
    126: "FD004276",
    # Pork Belly (raw) -> Braised Pork Belly with Dark Soy (closest cooked version)
    # Skip - too different
    # Raita -> Cucumber Raita (closest)
    100: "FD002141",
    # Mango Lassi -> no standalone mango lassi record, skip
    # Uttapam -> no record, skip
    # Pongal -> no record, skip
    # Keerai Masiyal -> no record, skip
    # Peanut Sundal -> no record, skip
    # Pani Puri -> no record, skip
    # Bhel Puri -> no record, skip
    # Fish Moilee -> no record, skip
    # Red Lentils / Masoor Dal -> no standalone record
    # Chickpeas raw -> no standalone raw record
    # Chicken breast raw -> no raw ingredient record
    # Chicken thigh raw -> no raw ingredient record
    # Beef lean raw -> no raw ingredient record
    # Pork tenderloin raw -> no raw ingredient record
    # Lamb loin chop raw -> no raw ingredient record
    # Duck breast raw -> no raw ingredient record
    # Tiger prawns raw -> no raw ingredient record
    # Salmon fillet raw -> no raw ingredient record
    # Tuna steak raw -> no raw ingredient record
    # Red bell pepper raw -> no raw ingredient record
    # Lemongrass raw -> no raw ingredient record
    # Bubur Ayam Indonesian -> Bubur Ayam (Indonesian Chicken Rice Congee)
    760: "FD004232",
    # Samgyeopsal -> no standalone record
    # Kare-Kare -> no record
    # Lechon Kawali -> no record
    # McDonald's McNuggets -> no record
    # McDonald's French Fries -> no record
    # Pennywort Salad -> no record
    # Beef Kofta Kebab -> no record
    # Sago Pudding with Gula Melaka -> no record
    # Apam Balik -> Martabak Manis (Indonesian Sweet Thick Pancake) - similar
    737: "FD004067",
    # Acai Bowl -> no record
    # Oat milk plain (image #800 food_name is actually Sugarcane Juice)
    800: "FD000143",  # Sugar Cane Drink (Packaged) - closest available
    # Bak Chor Mee (dry) #651 -> same as #15
    651: "FD001144",
    # Heineken -> no branded beer record
    # Soju Jinro -> no branded soju record
    # Singapore Sling -> no record
    # Scrambled Eggs -> no standalone record
    # French Omelette -> no standalone record
    # Century Egg -> no standalone record
    # Salted Duck Egg -> no standalone record
    # Fried Egg (hawker style) -> no standalone record
    # Hard-Boiled Egg -> no standalone record
    # Steamed Egg Custard -> no standalone record
    # Cheddar Cheese -> no standalone record
    # Mozzarella Fresh -> no standalone record
    # Cream Cheese -> no standalone record
    # Parmesan -> no standalone record
    # Processed Cheese Slice -> no standalone record
    # Kefir Plain -> no standalone record
    # Red Rice Cooked -> no standalone record
    # Millet Cooked -> no standalone record
    # Chickpeas Cooked -> no standalone record
    # Black Beans Cooked -> no standalone record
    # Kidney Beans Cooked -> no standalone record
    # Moong Dal Whole -> no standalone record
    # Pea Protein Isolate -> no standalone record
    # Creatine Monohydrate -> no standalone record
    # Quest Bar -> no record
    # Clif Bar -> no record
    # Monster Energy -> no record
    # Pocari Sweat -> no record
    # Gatorade -> no record
}

# Also check for sugarcane juice
SUGARCANE_FD = None

def slugify(s):
    return re.sub(r'[^a-z0-9]+', '_', s.lower().strip()).strip('_')

# ── Load fd_id_mapping ────────────────────────────────────────────────────────
print("Loading fd_id_mapping.csv...")
fd_records = {}  # fd_id -> row dict
fd_by_slug = {}  # slug -> row
fd_by_name = {}  # normalized food_name -> row

with open(FD_CSV) as f:
    for row in csv.DictReader(f):
        fd_id = row['fd_id']
        fd_records[fd_id] = row
        fd_by_slug[row['slug']] = row
        fd_by_name[row['food_name'].lower().strip()] = row
        fd_by_slug[slugify(row['food_name'])] = row

print(f"  Loaded {len(fd_records)} FD records")

# Verify manual override FD IDs exist
for num, fd_id in list(MANUAL_OVERRIDE.items()):
    if fd_id not in fd_records:
        print(f"  WARNING: Manual override #{num} -> {fd_id} not found in fd_records!")
        del MANUAL_OVERRIDE[num]

# ── Build alias index from food.json files ────────────────────────────────────
print("Building alias index from food.json files...")
alias_index = {}  # normalized alias -> fd row
alias_count = 0

for shard_dir in sorted(FOODDB.iterdir()):
    if not shard_dir.is_dir():
        continue
    for record_dir in sorted(shard_dir.iterdir()):
        if not record_dir.is_dir():
            continue
        food_json = record_dir / "food.json"
        if not food_json.exists():
            continue
        m = re.match(r'^(FD\d+)_', record_dir.name)
        if not m:
            continue
        fd_id = m.group(1)
        if fd_id not in fd_records:
            continue
        row = fd_records[fd_id]
        with open(food_json) as f:
            try:
                d = json.load(f)
            except Exception:
                continue
        aliases = d.get('aliases', [])
        for alias in aliases:
            if isinstance(alias, str) and alias.strip():
                key = alias.lower().strip()
                if key not in alias_index:
                    alias_index[key] = row
                    alias_count += 1
                slug_key = slugify(alias)
                if slug_key not in alias_index:
                    alias_index[slug_key] = row

print(f"  Built alias index with {alias_count} entries")

def find_fd(img_slug, food_name=None):
    """Find FD record using multiple matching strategies."""
    # 1. Exact slug match
    if img_slug in fd_by_slug:
        return fd_by_slug[img_slug], 'slug'
    
    # 2. Food name match (from JSON)
    if food_name:
        fn_lower = food_name.lower().strip()
        if fn_lower in fd_by_name:
            return fd_by_name[fn_lower], 'food_name'
        fn_slug = slugify(food_name)
        if fn_slug in fd_by_slug:
            return fd_by_slug[fn_slug], 'food_name_slug'
        if fn_lower in alias_index:
            return alias_index[fn_lower], 'alias'
        if fn_slug in alias_index:
            return alias_index[fn_slug], 'alias_slug'
        # Core name (strip parenthetical)
        core = re.split(r'[\(\—]', food_name)[0].strip()
        if core != food_name:
            core_lower = core.lower().strip()
            if core_lower in fd_by_name:
                return fd_by_name[core_lower], 'core_name'
            core_slug = slugify(core)
            if core_slug in fd_by_slug:
                return fd_by_slug[core_slug], 'core_slug'
            if core_lower in alias_index:
                return alias_index[core_lower], 'core_alias'
            if core_slug in alias_index:
                return alias_index[core_slug], 'core_alias_slug'
        # Alt core (split on ' / ')
        alt_core = food_name.split(' / ')[0].strip()
        if alt_core != food_name:
            alt_lower = alt_core.lower().strip()
            if alt_lower in fd_by_name:
                return fd_by_name[alt_lower], 'alt_core_name'
            alt_slug = slugify(alt_core)
            if alt_slug in fd_by_slug:
                return fd_by_slug[alt_slug], 'alt_core_slug'
            if alt_lower in alias_index:
                return alias_index[alt_lower], 'alt_core_alias'
    
    # 3. Alias match on img_slug
    if img_slug in alias_index:
        return alias_index[img_slug], 'alias_slug'
    
    # 4. Partial keyword match
    parts = img_slug.split('_')
    for i in range(1, len(parts)):
        partial = '_'.join(parts[i:])
        if partial in fd_by_slug:
            return fd_by_slug[partial], 'partial_slug'
        partial2 = '_'.join(parts[:len(parts)-i])
        if partial2 in fd_by_slug:
            return fd_by_slug[partial2], 'partial_slug'
    
    return None, None

# ── Collect all image folders ─────────────────────────────────────────────────
img_folders = {}
for batch in IMAGE_BATCHES:
    for name in sorted(os.listdir(batch)):
        m = re.match(r'^(\d+)_(.+)$', name)
        if m:
            num = int(m.group(1))
            img_folders[num] = {
                'slug': m.group(2),
                'path': batch / name,
                'folder': name,
            }

print(f"Image folders found: {len(img_folders)}")

# ── Load JSON food_names ──────────────────────────────────────────────────────
json_food_names = {}
for root, dirs, files in os.walk(JSON_BASE):
    for fname in files:
        if fname.endswith('.json') and not fname.startswith('gi_'):
            m = re.match(r'^(\d+)_', fname)
            if m:
                num = int(m.group(1))
                try:
                    with open(os.path.join(root, fname)) as f:
                        d = json.load(f)
                    json_food_names[num] = d.get('food_name', '')
                except Exception:
                    pass

print(f"JSON food names loaded: {len(json_food_names)}")

# ── Transfer images ───────────────────────────────────────────────────────────
transferred = 0
skipped_flagged = 0
skipped_no_match = 0
skipped_no_images = 0
errors = []
flagged_report = []
no_match_report = []
match_methods = {}
transfer_log = []

for num in sorted(img_folders.keys()):
    info = img_folders[num]
    slug = info['slug']
    src_dir = info['path']
    food_name = json_food_names.get(num, '')

    # Skip flagged records
    if num in FLAGGED:
        skipped_flagged += 1
        flagged_report.append({
            'num': num,
            'img_slug': slug,
            'food_name': food_name,
            'reason': 'Flagged for manual review'
        })
        transfer_log.append({'num': num, 'slug': slug, 'food_name': food_name, 'fd_id': '', 'fd_slug': '', 'shard': '', 'method': '', 'status': 'FLAGGED_MANUAL_REVIEW'})
        continue

    # Check manual override first
    fd = None
    method = None
    if num in MANUAL_OVERRIDE:
        fd_id = MANUAL_OVERRIDE[num]
        if fd_id in fd_records:
            fd = fd_records[fd_id]
            method = 'manual_override'

    # Auto-match if no manual override
    if fd is None:
        fd, method = find_fd(slug, food_name)

    if fd is None:
        skipped_no_match += 1
        no_match_report.append({'num': num, 'img_slug': slug, 'food_name': food_name})
        transfer_log.append({'num': num, 'slug': slug, 'food_name': food_name, 'fd_id': '', 'fd_slug': '', 'shard': '', 'method': '', 'status': 'NO_FD_MATCH'})
        print(f"  [NO MATCH] #{num}: '{slug}' / '{food_name[:50]}'")
        continue

    match_methods[method] = match_methods.get(method, 0) + 1

    # Find the record folder in fooddb_veritas1
    shard = fd['shard']
    fd_id = fd['fd_id']
    fd_slug = fd['slug']
    record_folder_name = f"{fd_id}_{fd_slug}"
    record_path = FOODDB / shard / record_folder_name
    img_dest = record_path / "images" / "original"

    if not record_path.exists():
        errors.append(f"#{num}: Record folder not found: {record_path}")
        transfer_log.append({'num': num, 'slug': slug, 'food_name': food_name, 'fd_id': fd_id, 'fd_slug': fd_slug, 'shard': shard, 'method': method, 'status': 'ERROR_NO_FOLDER'})
        continue

    img_dest.mkdir(parents=True, exist_ok=True)

    # Check source images exist
    src_images = list(src_dir.glob("*.jpg")) + list(src_dir.glob("*.jpeg")) + list(src_dir.glob("*.png"))
    if not src_images:
        skipped_no_images += 1
        transfer_log.append({'num': num, 'slug': slug, 'food_name': food_name, 'fd_id': fd_id, 'fd_slug': fd_slug, 'shard': shard, 'method': method, 'status': 'NO_IMAGES'})
        continue

    # Copy and rename images
    copied_count = 0
    for src_file in sorted(src_dir.iterdir()):
        if src_file.suffix.lower() not in ('.jpg', '.jpeg', '.png'):
            continue
        dest_name = IMG_RENAME.get(src_file.name)
        if dest_name is None:
            fn = src_file.name.lower()
            if 'hero' in fn or '_01_' in fn or fn.startswith('01'):
                dest_name = 'hero.jpg'
            elif 'macro' in fn or '_02_' in fn or fn.startswith('02'):
                dest_name = 'closeup.jpg'
            elif 'wild' in fn or '_03_' in fn or fn.startswith('03'):
                dest_name = 'context.jpg'
            else:
                dest_name = src_file.name
        dest_file = img_dest / dest_name
        shutil.copy2(str(src_file), str(dest_file))
        copied_count += 1

    if copied_count > 0:
        transferred += 1
        transfer_log.append({'num': num, 'slug': slug, 'food_name': food_name, 'fd_id': fd_id, 'fd_slug': fd_slug, 'shard': shard, 'method': method, 'status': 'TRANSFERRED'})
        if transferred % 100 == 0:
            print(f"  Transferred {transferred} records...")
    else:
        transfer_log.append({'num': num, 'slug': slug, 'food_name': food_name, 'fd_id': fd_id, 'fd_slug': fd_slug, 'shard': shard, 'method': method, 'status': 'NO_IMAGES'})

print(f"\n=== TRANSFER COMPLETE ===")
print(f"  Transferred:       {transferred}")
print(f"  Flagged (skipped): {skipped_flagged}")
print(f"  No FD match:       {skipped_no_match}")
print(f"  No images:         {skipped_no_images}")
print(f"  Errors:            {len(errors)}")
print(f"\nMatch methods used:")
for method, count in sorted(match_methods.items(), key=lambda x: -x[1]):
    print(f"  {method}: {count}")

if errors:
    print("\nErrors:")
    for e in errors:
        print(f"  {e}")

# ── Write review report ───────────────────────────────────────────────────────
report_path = BASE / "IMAGE_MAPPING_REVIEW_REQUIRED.md"
with open(report_path, 'w') as f:
    f.write(f"# Image Mapping — Manual Review Required\n\n")
    f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
    f.write(f"## Summary\n\n")
    f.write(f"| Metric | Count |\n|--------|-------|\n")
    f.write(f"| Transferred | {transferred} |\n")
    f.write(f"| Flagged (manual review) | {skipped_flagged} |\n")
    f.write(f"| No FD match | {skipped_no_match} |\n")
    f.write(f"| No images | {skipped_no_images} |\n")
    f.write(f"| Errors | {len(errors)} |\n\n")
    if flagged_report:
        f.write(f"## Flagged Records (Manual Review)\n\n")
        f.write(f"| # | Image Slug | Food Name | Reason |\n")
        f.write(f"|---|-----------|-----------|--------|\n")
        for r in flagged_report:
            f.write(f"| {r['num']} | `{r['img_slug']}` | {r['food_name']} | {r['reason']} |\n")
    if no_match_report:
        f.write(f"\n## No FD Match Found (Not in Database)\n\n")
        f.write(f"These foods have no matching record in the Veritas gold_standard database.\n")
        f.write(f"They may need to be added as new records or the images can be discarded.\n\n")
        f.write(f"| # | Image Slug | Food Name |\n|---|-----------|----------|\n")
        for r in no_match_report:
            f.write(f"| {r['num']} | `{r['img_slug']}` | {r['food_name'][:80]} |\n")
    f.write(f"\n## Action Required\n\n")
    f.write(f"For each no-match record:\n")
    f.write(f"1. Determine if the food exists in the database under a different name\n")
    f.write(f"2. If yes, manually copy images to the correct FD record folder\n")
    f.write(f"3. If no, consider adding the food as a new Veritas record\n")
    f.write(f"4. Update `assets.json` for any manually transferred records\n")

print(f"\nReview report saved: {report_path}")

# ── Write transfer log CSV ────────────────────────────────────────────────────
log_path = BASE / "image_transfer_log.csv"
with open(log_path, 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['img_num', 'img_slug', 'food_name', 'fd_id', 'fd_slug', 'shard', 'match_method', 'status'])
    for entry in transfer_log:
        writer.writerow([
            entry['num'], entry['slug'], entry['food_name'],
            entry['fd_id'], entry['fd_slug'], entry['shard'],
            entry['method'], entry['status']
        ])

print(f"Transfer log saved: {log_path}")
