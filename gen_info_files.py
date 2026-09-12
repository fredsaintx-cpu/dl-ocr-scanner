
import sys, re
sys.path.insert(0, r"C:\CLAUDE")
from pathlib import Path
from mindee import PathInput
from mindee.v2 import Client, ExtractionParameters, ExtractionResponse
from PIL import Image

API_KEY = "md_QW4S2lEwLKgoyCKJv5fWHOyrOYS1MiwQk6fCnH2E240"
MODEL_ID = "de9096d4-9d88-4c0a-a61f-a857255bf437"
ROOT = Path(r"C:\Users\analf\Downloads\AAABnyMk0_p58maWE80D")
EXTS = {'.jpg','.jpeg','.png','.webp','.bmp'}

client = Client(API_KEY)
params = ExtractionParameters(model_id=MODEL_ID, rag=None, raw_text=None, polygon=False, confidence=True)

def get_front(folder):
    # Prefer file with 'front' in name (not front2), else largest non-self image
    imgs = [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in EXTS and 'self' not in f.name.lower() and 'genx' not in f.name.lower()]
    fronts = [f for f in imgs if 'front' in f.name.lower() and 'front2' not in f.name.lower() and 'front3' not in f.name.lower()]
    if fronts: return fronts[0]
    if imgs: return max(imgs, key=lambda f: f.stat().st_size)
    return None

processed = 0; skipped = 0; failed = 0
for folder in sorted(ROOT.rglob('*')):
    if not folder.is_dir(): continue
    if folder.name == 'orig_docs': continue
    info = folder / 'info.txt'
    if info.exists(): skipped += 1; continue
    front = get_front(folder)
    if not front: continue
    try:
        resp = client.enqueue_and_get_result(ExtractionResponse, PathInput(str(front)), params)
        flds = resp.inference.result.fields
        fn = str(flds.first_name).strip().title()
        ln = str(flds.last_name).strip().title()
        doc_id = str(flds.document_id).strip() if hasattr(flds,'document_id') else ''
        addr_raw = str(flds.address).strip() if flds else ''
        addr = re.sub(r':.*?:\s*', '', addr_raw).strip()
        addr = re.sub(r'[-]\d{4}\b', '', addr)  # remove -4digit zip ext
        addr = re.sub(r'\bUSA\b', '', addr, flags=re.IGNORECASE).strip()
        addr = ' '.join(addr.split())  # collapse whitespace
        if not fn or not ln: failed += 1; print(f"  SKIP (no name): {folder.name}"); continue
        content = f"{fn} {ln} {addr}\n\n\n{doc_id}\n"
        info.write_text(content, encoding='utf-8')
        print(f"  OK: {folder.name} -> {fn} {ln} | {doc_id}")
        processed += 1
    except Exception as e:
        print(f"  FAIL: {folder.name} - {e}")
        failed += 1

print(f"\nDone: {processed} created, {skipped} already existed, {failed} failed")
