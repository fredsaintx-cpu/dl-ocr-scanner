#!/usr/bin/env python3
"""Mindee DL OCR - Rich UI Edition - Orange/Gold Theme"""
import os, re, shutil, sys, time, math, json

# Fix Mac SSL certificate verification issues
try:
    import certifi, ssl
    os.environ.setdefault('SSL_CERT_FILE', certifi.where())
    os.environ.setdefault('REQUESTS_CA_BUNDLE', certifi.where())
except: pass
from pathlib import Path
from datetime import date, datetime
from PIL import Image, ImageOps
from mindee import PathInput
from mindee.v2 import Client, ExtractionParameters, ExtractionResponse

# Optional heavy deps — only used as Vision fallback, not required
try: import cv2, numpy as np; _CV2_OK = True
except: _CV2_OK = False
try: import pytesseract; _TESS_OK = True
except: _TESS_OK = False
try: from pyzbar.pyzbar import decode as _pyzbar_decode; _PYZBAR_OK = True
except: _PYZBAR_OK = False

# Suppress zbar/pyzbar console warnings on Windows at OS level
try:
    import ctypes
    ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002)
    import os as _os
    _devnull = open(_os.devnull, 'w')
    _old_stderr_fd = sys.stderr.fileno()
    _saved_stderr_fd = _os.dup(_old_stderr_fd)
except: pass

def _suppress_stderr():
    try:
        sys.stderr.flush()
        _os.dup2(_devnull.fileno(), _old_stderr_fd)
    except: pass

def _restore_stderr():
    try:
        _os.dup2(_saved_stderr_fd, _old_stderr_fd)
    except: pass

API_KEY = "md_QW4S2lEwLKgoyCKJv5fWHOyrOYS1MiwQk6fCnH2E240"
MODEL_ID = "de9096d4-9d88-4c0a-a61f-a857255bf437"
EXTS = {'.jpg', '.jpeg', '.png', '.bmp', '.webp'}

# Load face detector once at startup
_face_net = None
def get_face_net():
    if not _CV2_OK: return None
    global _face_net
    if _face_net is None:
        model = os.path.expanduser('~') + '/face_deploy.prototxt'
        weights = os.path.expanduser('~') + '/face_res10.caffemodel'
        try: _face_net = cv2.dnn.readNetFromCaffe(model, weights)
        except: pass
    return _face_net

DL_FRONT_WORDS = ["license", "driver", "class", "expires", "issued", "dob",
                   "sex", "hgt", "wgt", "eyes", "hair", "donor", "organ",
                   "ohio", "georgia", "florida", "california", "texas", "new jersey",
                   "south carolina", "virginia", "new york", "north carolina",
                   "motor", "vehicle", "corrective", "lenses", "sustaining", "equipment",
                   "restriction", "endorsement", "domestic", "not for federal"]

def _clear_vision_cache(folder):
    """Remove all vision_cache/* entries from mindee_data.json so stale
    entries from old filenames don't prevent fresh classification."""
    cache_file = Path(folder) / "mindee_data.json"
    if not cache_file.exists():
        return
    try:
        d = json.loads(cache_file.read_text(encoding="utf-8"))
        keys = [k for k in d if k.startswith("vision_cache/") or k.startswith("vision_rotation/")]
        if keys:
            for k in keys: del d[k]
            cache_file.write_text(json.dumps(d), encoding="utf-8")
    except: pass


def cleanup_messy_folder(folder):
    """
    Pre-process folders that came from external sources (e.g. Mac-originated
    submissions with ._* metadata, multiple selfie variants, generic filenames).
    Runs automatically before Mindee extraction on every folder.

    Does:
    1. Delete all Mac AppleDouble metadata files (._*)
    2. Normalize generic DL image names:
       - front.jpg/front.jpeg -> photo1 (1).jpg
       - back.jpg/back.jpeg   -> photo1.jpg
       - selfie.gif/selfie.jpeg -> selfie.jpg (convert gif to jpg if needed)
    3. Multiple selfie variants (center/left/right_photo_processed):
       - Keep only center_photo_processed (or largest if no center)
       - Rename it to selfie.jpg
       - Delete the rest
    """
    folder = Path(folder)

    # Step 1: Delete Mac metadata
    for f in list(folder.iterdir()):
        if f.name.startswith("._"):
            try: f.unlink()
            except: pass

    # Step 2: Normalize front/back generic names
    for old, new in [("front.jpeg", "photo1 (1).jpg"),
                     ("front.jpg",  "photo1 (1).jpg"),
                     ("back.jpeg",  "photo1.jpg"),
                     ("back.jpg",   "photo1.jpg")]:
        src = folder / old
        dst = folder / new
        if src.exists() and not dst.exists():
            src.rename(dst)

    # Step 3: Handle selfie.gif (convert to jpg)
    gif = folder / "selfie.gif"
    jpg = folder / "selfie.jpg"
    if gif.exists() and not jpg.exists():
        try:
            from PIL import Image as _PIL
            _PIL.open(str(gif)).convert("RGB").save(str(jpg), quality=95)
            gif.unlink()
        except: pass

    # Step 4: Multiple selfie variants -> keep best, rename to selfie.jpg
    selfie_variants = [f for f in folder.iterdir()
                       if f.is_file() and not f.name.startswith("._")
                       and f.suffix.lower() in (".jpg",".jpeg",".png")
                       and any(v in f.name.lower() for v in
                               ["center_photo_processed","left_photo_processed",
                                "right_photo_processed","photo_processed"])]
    if selfie_variants and not (folder / "selfie.jpg").exists():
        # Prefer center, then largest
        center = next((f for f in selfie_variants if "center" in f.name.lower()), None)
        keeper = center if center else max(selfie_variants, key=lambda f: f.stat().st_size)
        dst = folder / "selfie.jpg"
        keeper.rename(dst)
        for f in selfie_variants:
            try:
                if f != keeper and f.exists(): f.unlink()
            except: pass

    # Always clear vision cache after any rename so stale entries don't mislead classifier
    _clear_vision_cache(folder)


def prepend_doc_id_to_txts(folder, doc_id):
    """Prepend doc_id as first line of every .txt file in folder if not already there."""
    if not doc_id:
        return
    for f in Path(folder).iterdir():
        if not f.is_file() or f.suffix.lower() != ".txt" or f.name.startswith("._"):
            continue
        try:
            raw = f.read_bytes()
            try: content = raw.decode("utf-8")
            except: content = raw.decode("latin-1", errors="replace")
            if content.split("\n")[0].strip() == doc_id:
                continue  # already prepended
            f.write_text(doc_id + "\n" + content, encoding="utf-8")
        except: pass


def ocr_classify(img_path):
    """Use Tesseract OCR — optional fallback, only runs if pytesseract is installed."""
    if not _TESS_OK:
        return None
    try:
        from PIL import ImageEnhance
        pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        img = Image.open(img_path)
        try: img = ImageOps.exif_transpose(img)
        except: pass
        best_score = 0
        for angle in (0, 90, 180, 270):
            candidate = img.rotate(angle, expand=True) if angle else img
            cW, cH = candidate.size
            crop = candidate.crop((int(cW*0.05), int(cH*0.05), int(cW*0.95), int(cH*0.95)))
            w, h = crop.size
            up = crop.resize((w*2, h*2), Image.Resampling.LANCZOS)
            gray = ImageEnhance.Contrast(up.convert("L")).enhance(1.5)
            text = pytesseract.image_to_string(gray, config="--psm 11 -l eng").lower()
            score = sum(1 for w in DL_FRONT_WORDS if w in text)
            best_score = max(best_score, score)
        return 'front' if best_score >= 1 else None
    except:
        return None

GOOGLE_VISION_API_KEY = "AIzaSyCTn98xx18aD-3urZP9mXf6IlJSnJL8TQ4"

def _call_vision_api(img_path):
    """
    Call Google Vision DOCUMENT_TEXT_DETECTION on img_path.
    Caches result in folder's mindee_data.json under key
    'vision_cache/<filename>' to avoid repeat API calls.
    Returns the full Vision response dict, or None on failure.
    """
    try:
        import base64, urllib.request, io as _io
        img_path = Path(img_path)
        folder = img_path.parent
        cache_file = folder / "mindee_data.json"
        cache_key = f"vision_cache/{img_path.name}"

        # Check cache first
        if cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                if cache_key in cached:
                    return cached[cache_key]
            except: pass

        # Call Vision API — thumbnail first to keep payload small
        img = Image.open(str(img_path))
        try: img = ImageOps.exif_transpose(img)
        except: pass
        img.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        buf = _io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        content = base64.b64encode(buf.getvalue()).decode()
        body = json.dumps({"requests":[{"image":{"content":content},"features":[{"type":"DOCUMENT_TEXT_DETECTION"}]}]}).encode()
        url = f"https://vision.googleapis.com/v1/images:annotate?key={GOOGLE_VISION_API_KEY}"
        req = urllib.request.Request(url, data=body, headers={"Content-Type":"application/json"})
        # Call Vision API with retry on connection errors
        for attempt in range(3):
            try:
                resp = json.loads(urllib.request.urlopen(req, timeout=90).read())
                break
            except Exception as _conn_e:
                if attempt == 2:
                    raise _conn_e
                time.sleep(2 ** attempt)  # backoff: 1s, 2s
        result = resp.get("responses", [{}])[0]
        # Check for API-level errors
        if "error" in result:
            return None

        # Cache the result
        try:
            existing = {}
            if cache_file.exists():
                try: existing = json.loads(cache_file.read_text(encoding="utf-8"))
                except: pass
            existing[cache_key] = result
            cache_file.write_text(json.dumps(existing), encoding="utf-8")
        except: pass

        return result
    except Exception as _e:
        import sys as _sys
        print(f"_call_vision_api ERROR: {_e}", file=_sys.stderr)
        return None


def _vision_classify(img_path):
    """
    Use Google Vision text detection to classify image as front/back/self.
    - front: Vision reads DL field text (name, DOB, address, license number)
    - back:  Vision reads barcode-related text or minimal structured content
    - self:  Vision finds little/no card text (person photo, not a card)
    - None:  Vision unavailable or ambiguous — fall through to CV classifier

    Also caches the rotation angle so clipdrop_GENX.py can reuse it.
    """
    try:
        resp = _call_vision_api(img_path)
        if not resp:
            return None

        fta = resp.get("fullTextAnnotation")
        if not fta:
            # No text at all — likely a selfie
            return "self"

        text = fta.get("text", "").lower()
        word_count = len(text.split())

        # Strong front indicators — DL field labels
        FRONT_KEYWORDS = [
            "driver license", "driver's license", "drivers license",
            "identification card", "not for federal", "real id",
            "expires", "expiration", "date of birth", "dob",
            "organ donor", "dept of motor",
            "issued", "sex", "height", "hgt", "eyes", "hair",
        ]
        # Strong back indicators
        BACK_KEYWORDS = [
            "notify", "if found", "penndot", "dmvnow", "dmv.wv.gov",
            "skip the trip", "class: c", "class: d", "class: e",
            "endorsements: none", "restrictions:", "restr:", "rev.",
            "operator dl", "noncommercial",
            "retains all property", "property rights",
            "must notify", "address change within",
            "doa.alaska.gov", "dmv/dol", "dot.state",
            "rev. ", "replacement lic", "replacement license",
            "gvwr", "passenger vehicles", "buses designed",
            "trucks and vans", "9a. endorsements", "12. restrictions",
            "9. class", "3-passenger", "transport fifteen",
            "fewer occupants", "lbs gvwr", "lbs. gvwr",
            "vehicles of any gvwr", "commercial vehicles",
            "medical alert", "encoded data", "transaction dates",
            "dl/id card #", "issuing state", "2d barcode",
            "pdf417", "magnetic stripe", "v1.0", "rev 0",
            "card version", "card ver", "0019959", "barcode",
            "scan here", "see reverse", "see back",
            # Ohio back specific
            "o motor vehicle", "corrective lenses", "life sustaining",
            "domestic", "sustaining equipment",
            # Colorado / generic ID back
            "not a driver license", "not a drivers license",
            "identification purposes only", "previous type",
            "for identification purposes",
        ]

        front_score = sum(1 for kw in FRONT_KEYWORDS if kw in text)
        back_score  = sum(1 for kw in BACK_KEYWORDS  if kw in text)

        # Flexible back patterns that handle formatting variations
        import re as _re2
        BACK_PATTERNS = [
            r'class\s*[a-e]\b',          # "class d", "class: e", "class d-all"
            r'endorsements?\s*[:\n]',     # "endorsements:", "endorsements:\n"
            r'restrictions?\s*[:\n]',     # "restrictions:", "rest:\n"
            r'rev\.?\s+\d{2}/',           # "rev 12/02/2011", "rev. 01/08/2020"
            r'\d{2,}\s+\d{10,}',         # long barcode numbers
            r'renew\s+online',            # "renew online"
            r'corrective\s+lens',         # "corrective lenses", "corrective lens"
            r'non.commercial\s+veh',      # "non-commercial veh"
            r'not\s+requiring\s+a\s+cdl', # "not requiring a cdl"
        ]
        for pat in BACK_PATTERNS:
            if _re2.search(pat, text, _re2.IGNORECASE):
                back_score += 1

        # Also extract rotation and cache it for clipdrop reuse
        try:
            import math, statistics as _stat
            pages = fta.get("pages", [])
            if pages:
                block_data = []
                for page in pages:
                    for block in page.get("blocks", []):
                        bv = block["boundingBox"]["vertices"]
                        bxs=[p.get("x",0) for p in bv]; bys=[p.get("y",0) for p in bv]
                        barea=(max(bxs)-min(bxs))*(max(bys)-min(bys))
                        words=[]
                        for para in block.get("paragraphs",[]):
                            for word in para.get("words",[]):
                                v=word["boundingBox"]["vertices"]
                                if len(v)<2: continue
                                x0=v[0].get("x",0);y0=v[0].get("y",0)
                                x1=v[1].get("x",0);y1=v[1].get("y",0)
                                wa=math.hypot(x1-x0,y1-y0)
                                words.append((x1-x0,y1-y0,wa))
                        block_data.append((barea,words))
                block_data.sort(key=lambda b:b[0],reverse=True)
                total_area=sum(b[0] for b in block_data)
                dxs,dys,weights=[],[],[]
                cum=0
                for barea,words in block_data:
                    for dx,dy,wa in words:
                        dxs.append(dx);dys.append(dy);weights.append(wa)
                    cum+=barea
                    if cum>=total_area*0.6 and len(dxs)>=5: break
                if dxs:
                    sx=sum(d*w for d,w in zip(dxs,weights))
                    sy=sum(d*w for d,w in zip(dys,weights))
                    vec=math.degrees(math.atan2(sy,sx))
                    a=vec%360
                    rotation=min([0,90,180,270],key=lambda k:min(abs(a-k),360-abs(a-k)))
                    # Cache rotation angle in mindee_data.json
                    try:
                        cache_file=Path(img_path).parent/"mindee_data.json"
                        existing={}
                        if cache_file.exists():
                            try: existing=json.loads(cache_file.read_text(encoding="utf-8"))
                            except: pass
                        existing[f"vision_rotation/{Path(img_path).name}"] = rotation
                        cache_file.write_text(json.dumps(existing),encoding="utf-8")
                    except: pass
        except: pass

        # Non-DL document indicators — flag as back so it doesn't steal front slot
        NON_DL_KEYWORDS = [
            "directive to physician", "emergency contact",
            "allergic reaction", "medical history", "insurance card",
            "social security", "prescription", "patient name",
            "date of service", "provider", "diagnosis",
        ]
        non_dl_score = sum(1 for kw in NON_DL_KEYWORDS if kw in text)
        if non_dl_score >= 1:
            return "back"  # treat as back so real front wins

        # Full mailing address = definitive front (backs never have street addresses)
        # Restrict to single line only - no newlines within address pattern
        import re as _re
        _addr_pat = r'^\d+[ \t]+[A-Za-z]+([ \t]+\w+){0,4}[ \t]+(street|avenue|road|drive|boulevard|lane|court|way|place|circle|terrace|parkway)\b'
        _abbr_pat = r'^\d+[ \t]+[A-Za-z]+([ \t]+\w+){0,4}[ \t]+(st|ave|rd|dr|blvd|ln|ct|pl|cir|ter|pkwy)\b(?!\w)'
        if _re.search(_addr_pat, text, _re.IGNORECASE | _re.MULTILINE) or _re.search(_abbr_pat, text, _re.IGNORECASE | _re.MULTILINE):
            return "front"

        # Low word count with any back signal = back (barcode data echoing front fields)
        if word_count < 20 and back_score >= 1:
            return "back"

        # If very few words with no personal data → likely back of card
        if word_count < 15:
            has_personal = any(kw in text for kw in ["dob", "date of birth", "address", "height", "sex", "eyes", "hair", "exp", "iss"])
            if not has_personal:
                return "back"

        # Back score wins if it's higher or equal with any hits
        if back_score > front_score and back_score >= 1:
            return "back"
        if front_score >= 2 and front_score > back_score:
            return "front"
        if back_score >= 1:
            return "back"
        if front_score >= 1:
            return "front"
        # Low text count with no clear keywords — probably selfie
        if word_count < 10:
            return "self"
        # Default for cards with text but no clear match
        return "back"

    except Exception:
        return None


def classify_image(img_path, mindee_fields=None):
    """
    Classify image as 'front', 'back', or 'self'.
    Primary: Google Vision text detection (reliable, reads actual card content).
    Fallback: barcode detection → OCR → face detection → Mindee field count.
    """
    # Primary: Google Vision
    vision_result = _vision_classify(img_path)
    if vision_result is not None:
        return vision_result

    # Fallback: original CV pipeline (only if Vision fails)
    try:
        img = Image.open(img_path)
        try: img = ImageOps.exif_transpose(img)
        except: pass
        w, h = img.width, img.height
        img_area = w * h

        # Barcode = definitive back
        if _PYZBAR_OK:
            try:
                _suppress_stderr()
                barcodes = _pyzbar_decode(img)
                _restore_stderr()
                if barcodes: return 'back'
            except: _restore_stderr()

        # OCR
        ocr_result = ocr_classify(img_path)
        if ocr_result == 'front':
            return 'front'

        # Face detection
        if _CV2_OK:
            try:
                cv_img = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
                net = get_face_net()
                if net:
                    blob = cv2.dnn.blobFromImage(cv2.resize(cv_img,(300,300)),1.0,(300,300),(104.,177.,123.))
                    net.setInput(blob)
                    dets = net.forward()
                    best_conf, best_box = 0, None
                    for i in range(dets.shape[2]):
                        c = dets[0,0,i,2]
                        if c > best_conf and c > 0.3:
                            best_conf = c
                            best_box = (dets[0,0,i,3:7] * [w,h,w,h]).astype(int)
                    if best_box is not None:
                        x1,y1,x2,y2 = best_box
                        face_ratio = ((x2-x1)*(y2-y1)) / img_area
                        return 'self' if face_ratio > 0.08 else 'front'
            except: pass

        if mindee_fields is not None:
            return 'front' if mindee_fields >= 3 else 'back'

        return 'back'
    except Exception:
        return 'front'


def rotate_dl_card(img_path):
    """
    Rotate DL card using a fresh Vision call (not cache) since the cache
    may be stale if the file was renamed between classification and rotation.
    Falls back to old CV method if Vision fails.
    """
    try:
        img_path = Path(img_path)
        img = Image.open(str(img_path))
        try: img = ImageOps.exif_transpose(img)
        except: pass

        # Always call Vision fresh — don't use cache since filename may have changed
        import base64, io as _io, urllib.request as _req
        buf = _io.BytesIO(); img.save(buf, format="JPEG", quality=90)
        content = base64.b64encode(buf.getvalue()).decode()
        body = json.dumps({"requests":[{"image":{"content":content},"features":[{"type":"DOCUMENT_TEXT_DETECTION"}]}]}).encode()
        url = f"https://vision.googleapis.com/v1/images:annotate?key={GOOGLE_VISION_API_KEY}"
        request = _req.Request(url, data=body, headers={"Content-Type":"application/json"})
        resp = json.loads(_req.urlopen(request, timeout=30).read())
        fta = resp["responses"][0].get("fullTextAnnotation")
        if fta:
            import math
            dxs=[]; dys=[]; weights=[]
            for page in fta.get("pages",[]):
                for block in page.get("blocks",[]):
                    for para in block.get("paragraphs",[]):
                        for word in para.get("words",[]):
                            v=word["boundingBox"]["vertices"]
                            if len(v)<2: continue
                            x0=v[0].get("x",0);y0=v[0].get("y",0)
                            x1=v[1].get("x",0);y1=v[1].get("y",0)
                            wa=math.hypot(x1-x0,y1-y0)
                            if wa>20: dxs.append(x1-x0);dys.append(y1-y0);weights.append(wa)
            if dxs:
                # Use mode (most common angle bucket) not weighted average
                # to handle cards with multi-directional text
                buckets={0:0,90:0,180:0,270:0}
                for dx,dy in zip(dxs,dys):
                    a=round(math.degrees(math.atan2(dy,dx))%360)
                    best=min(buckets,key=lambda k:min(abs(a-k),360-abs(a-k)))
                    buckets[best]+=1
                rotation=max(buckets,key=buckets.get)
                if rotation!=0:
                    img.rotate(rotation,expand=True).save(str(img_path),quality=95)
                    # Update cache with correct fresh value
                    try:
                        cache=img_path.parent/"mindee_data.json"
                        existing={}
                        if cache.exists():
                            try: existing=json.loads(cache.read_text(encoding="utf-8"))
                            except: pass
                        existing[f"vision_rotation/{img_path.name}"]=rotation
                        cache.write_text(json.dumps(existing),encoding="utf-8")
                    except: pass
                    return True
                return False
    except: pass

    # Vision failed — return False (no rotation applied)
    return False

def classify_and_rename_folder(folder, fn, ln, mindee_log=None, pre_classify=None):
    """Classify all images as front/back/self, rotate DL cards if sideways, rename."""
    imgs = [f for f in Path(folder).iterdir() if f.is_file() and f.suffix.lower() in EXTS]

    # Detect liveness video (.webm) — its presence means selfie should be tagged "genx self"
    videos = [f for f in Path(folder).iterdir() if f.is_file() and f.suffix.lower() == '.webm']
    has_video = len(videos) > 0

    # Build field count map for fallback
    field_counts = {}
    if mindee_log:
        sorted_entries = sorted(mindee_log.values(), key=lambda x: x.get('n_fields',0), reverse=True)
        sorted_imgs = sorted([i for i in imgs if 'self' not in i.name.lower()], key=lambda f: f.stat().st_size, reverse=True)
        for i, img in enumerate(sorted_imgs):
            if i < len(sorted_entries):
                field_counts[str(img)] = sorted_entries[i].get('n_fields', 0)

    classified = {'front': [], 'back': [], 'self': []}
    for img in imgs:
        nl = img.name.lower()
        if 'self' in nl:
            tag = 'self'
        else:
            # Always classify via Vision — never trust front/back filename labels
            tag = pre_classify.get(img.name) if pre_classify else None
            if not tag:
                fields = field_counts.get(str(img), None)
                tag = classify_image(str(img), mindee_fields=fields)
        classified[tag].append(img)

    # Rotate front/back cards if sideways (portrait photo = card on its side)
    for tag in ['front', 'back']:
        for img in classified[tag]:
            rotate_dl_card(str(img))

    # Rename with proper tags
    counters = {'front': 0, 'back': 0, 'self': 0}
    for tag, files in classified.items():
        for img in files:
            counters[tag] += 1
            count = counters[tag]
            if tag == 'self' and has_video:
                new_name = f"{fn} {ln} genx self{img.suffix}" if count == 1 else f"{fn} {ln} genx self{count}{img.suffix}"
            else:
                new_name = f"{fn} {ln} {tag}{img.suffix}" if count == 1 else f"{fn} {ln} {tag}{count}{img.suffix}"
            new_path = img.parent / new_name
            if img != new_path:
                try: img.rename(new_path)
                except: pass

    # Rename liveness video (.webm) if present
    for v in videos:
        new_name = f"{fn} {ln} self{v.suffix}"
        new_path = v.parent / new_name
        if v != new_path:
            try: v.rename(new_path)
            except: pass

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def is_already_labeled(name):
    """Check if folder is already renamed: 'XX Firstname Lastname'"""
    if name.lower() == 'expired':
        return True
    if len(name) > 3 and name[2] == ' ' and name[:2].isalpha() and name[:2].isupper():
        return True
    return False

def has_images(folder_path):
    """Check if folder contains image files"""
    try:
        for f in Path(folder_path).iterdir():
            if f.is_file() and f.suffix.lower() in EXTS:
                return True
    except:
        pass
    return False

def get_all_pending_folders(root_dir):
    """Find all person folders that need renaming, flattening nested structures first."""
    pending = []
    skipped = 0

    root = Path(root_dir)

    def process_dir(directory):
        nonlocal skipped
        for item in sorted(directory.iterdir()):
            if not item.is_dir(): continue
            if item.name.lower() == 'expired': continue

            # If already labeled at this level, skip
            if is_already_labeled(item.name):
                skipped += 1
                continue

            # Check if this is a grouping folder (numeric name or short name with no images directly)
            # e.g. "7", "batch1" etc - recurse into it instead of processing it
            if not has_images(item) and not any(
                f.is_file() and f.suffix.lower() in EXTS
                for f in item.iterdir()
                if f.is_file()
            ):
                # No images directly in this folder - it's a grouping folder, recurse
                process_dir(item)
                continue

            # Flatten: regardless of subfolder names, move ALL files up to item level
            for sub in list(item.rglob('*')):
                if sub.is_file() and sub.parent != item:
                    dest = item / sub.name
                    if not dest.exists():
                        sub.rename(dest)
                    else:
                        sub.rename(item / f"_{sub.name}")

            # Force delete ALL subfolders now that files are moved up
            for sub in list(item.iterdir()):
                if sub.is_dir():
                    try: shutil.rmtree(str(sub))
                    except: pass

            # Now process this item
            if has_images(item):
                pending.append(item)

    process_dir(root)
    return pending, skipped

def get_front_image(folder):
    """Get front image - always classify via Vision, never trust filename labels."""
    imgs = [f for f in Path(folder).iterdir() if f.is_file() and f.suffix.lower() in EXTS]
    if not imgs:
        return None, []
    # Always classify via Vision — filename labels can be wrong
    classified_front = []
    others = []
    for img in imgs:
        if 'self' in img.name.lower():
            others.append(img)
            continue
        tag = classify_image(str(img))
        if tag == 'front':
            classified_front.append(img)
        else:
            others.append(img)
    if classified_front:
        ordered = classified_front + others
        return ordered[0], ordered
    # No front found — sort by size descending
    imgs_sorted = sorted(imgs, key=lambda f: f.stat().st_size, reverse=True)
    return imgs_sorted[0], imgs_sorted

def display_header():
    clear_screen()
    print("\033[38;5;208m" + "=" * 79 + "\033[0m")
    print()
    t1 = "Mindee DL Scanner - Driver License Automation v1.0"
    t2 = "Auto-Rename | Expiry Check | All 50 States | 95%+ Accuracy"
    print("\033[38;5;220m" + " " * ((79-len(t1))//2) + t1 + "\033[0m")
    print("\033[38;5;214m" + " " * ((79-len(t2))//2) + t2 + "\033[0m")
    print()
    print("\033[38;5;208m" + "=" * 79 + "\033[0m")
    print()

def display_menu(config):
    print("\033[38;5;209mConfiguration Setup:\033[0m")
    print("Please configure your automation settings below")
    print()
    print("\033[38;5;214mCurrent Settings:\033[0m")
    print()
    print(f"  \033[38;5;209mRoot folder:\033[0m {config['root']}")
    print()
    print("\033[38;5;214mAvailable Options:\033[0m")
    print(f"  \033[38;5;220m1\033[0m - Edit root folder")
    print(f"  \033[38;5;220mq\033[0m - Quit")
    print()
    print("\033[38;5;82mEnter path to DL folders (or press Enter for current):\033[0m ", end='', flush=True)

def run_menu():
    config = {'root': r"C:\Users\analf\Downloads\test mega DL\New-200"}
    while True:
        display_header()
        display_menu(config)
        choice = input().strip().strip('"').strip("'")
        if choice.lower() == 'q':
            print("\nGoodbye!"); sys.exit(0)
        elif choice == '1':
            new = input("\033[38;5;82mNew root folder:\033[0m ").strip().strip('"')
            if new and Path(new).exists(): config['root'] = new
            continue
        elif choice and Path(choice).exists():
            config['root'] = choice
            return config
        elif not choice:
            return config
        else:
            print(f"\033[91mPath not found: {choice}\033[0m")
            input("Press Enter to continue...")

def start_processing(config):
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn, TimeElapsedColumn
    from rich.panel import Panel
    from rich.text import Text
    from rich.table import Table
    from rich.align import Align
    from rich.spinner import Spinner
    from rich.live import Live
    from rich.console import Group

    console = Console(force_terminal=True, width=120)
    clear_screen()

    header_text = Text()
    header_text.append("🪪  MINDEE DL SCANNER - AUTO RENAME ENGINE  🪪", style="bold orange1")
    header_panel = Panel(Align.center(header_text), style="dark_orange", padding=(0, 1))
    console.print(header_panel)

    root = config['root']
    client = Client(API_KEY)

    # Scan ALL subfolders recursively
    pending, skipped = get_all_pending_folders(root)

    # Config panel
    ct = Table.grid(padding=0)
    ct.add_column(style="orange1", justify="left", width=20)
    ct.add_column(style="white", justify="left")
    ct.add_row("Folders to process:", f"{len(pending)}")
    ct.add_row("Already labeled:", f"{skipped} (skipped)")
    ct.add_row("Provider:", "Mindee API v2")
    ct.add_row("Model:", "Driver License OCR")
    ct.add_row("Actions:", "Rename front/back/self + folder")
    ct.add_row("Expired IDs:", "Moved to expired/")
    console.print(Panel(ct, title="Configuration", border_style="dark_orange", title_align="left", padding=(0, 1)))
    print()

    if not pending:
        console.print("[bright_green]Nothing to process! All folders already labeled.[/bright_green]")
        input("\nPress Enter to return...")
        return

    processed = 0
    valid_count = 0
    expired_count = 0
    fail_count = 0
    recent_status = "Initializing..."

    def create_spinners():
        a = Text()
        a.append("🔥 Activity: ", style="bold orange1")
        if "OK" in recent_status:
            a.append("✅ " + recent_status, style="bright_green")
        elif "EXPIRED" in recent_status:
            a.append("⏰ " + recent_status, style="bright_yellow")
        elif "FAIL" in recent_status:
            a.append("❌ " + recent_status, style="bright_red")
        else:
            a.append(recent_status, style="orange1")

        s = Text()
        s.append("📊 Stats: ", style="bold bright_yellow")
        s.append(f"✅ {valid_count} renamed  ", style="bright_green")
        s.append(f"⏰ {expired_count} expired  ", style="yellow")
        s.append(f"❌ {fail_count} failed", style="red")

        q = Text()
        q.append("📋 Queue: ", style="bold dark_orange")
        remaining = len(pending) - processed
        if remaining > 0:
            upcoming = [p.name[:20] for p in pending[processed:processed+3]]
            display = ", ".join(upcoming)
            if remaining > 3: display += f" (+{remaining-3} more)"
            q.append(display, style="orange1")
        else:
            q.append("All complete 🎉", style="bright_green")

        return Group(
            Spinner("dots", text=a, style="orange1"),
            Spinner("dots2", text=s, style="bright_yellow"),
            Spinner("dots3", text=q, style="dark_orange")
        )

    progress = Progress(
        SpinnerColumn(spinner_name="dots2", style="orange1"),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=None, complete_style="orange1", finished_style="bright_green"),
        MofNCompleteColumn(), TextColumn("•"), TimeElapsedColumn(),
        console=console
    )
    task = progress.add_task("🪪 [orange1]0%[/orange1] • Scanning DLs... 🔍", total=len(pending))

    def create_live_display():
        a = Text()
        a.append("🔥 Activity: ", style="bold orange1")
        if "OK:" in recent_status: a.append(recent_status, style="bright_green")
        elif "EXPIRED:" in recent_status: a.append(recent_status, style="bright_yellow")
        elif "FAIL:" in recent_status: a.append(recent_status, style="bright_red")
        else: a.append(recent_status, style="orange1")

        s = Text()
        s.append("📊 Stats: ", style="bold bright_yellow")
        s.append(f"✅ {valid_count} renamed  ", style="bright_green")
        s.append(f"⏰ {expired_count} expired  ", style="yellow")
        s.append(f"❌ {fail_count} failed", style="red")

        q = Text()
        q.append("📋 Queue: ", style="bold dark_orange")
        remaining = len(pending) - processed
        if remaining > 0:
            upcoming = [p.name[:20] for p in pending[processed:processed+3]]
            disp = ", ".join(upcoming)
            if remaining > 3: disp += f" (+{remaining-3} more)"
            q.append(disp, style="orange1")
        else:
            q.append("All complete 🎉", style="bright_green")

        return Group(
            progress,
            Spinner("dots", text=a, style="orange1"),
            Spinner("dots", text=s, style="bright_yellow"),
            Spinner("dots", text=q, style="dark_orange"),
        )

    with Live(create_live_display(), console=console, refresh_per_second=10) as live:
        for i, fld in enumerate(pending):
            processed = i
            recent_status = f"Scanning {fld.name}..."
            live.update(create_live_display())

            # Auto-clean messy folders (Mac metadata, multiple selfies, generic names)
            cleanup_messy_folder(fld)

            front_img, all_imgs = get_front_image(fld)
            if not front_img:
                recent_status = f"FAIL: {fld.name} - no images"
                fail_count += 1
                progress.update(task, advance=1)
                live.update(create_live_display())
                continue

            try:
                import json
                params = ExtractionParameters(model_id=MODEL_ID, rag=None, raw_text=None, polygon=True, confidence=True)
                # Pre-classify ALL images first before Mindee name extraction
                pre_classify = {}
                for img in all_imgs:
                    pre_classify[img.name] = classify_image(str(img))

                mindee_log = {}
                first = ''; last = ''; flds = None; state = None; expiry = ''; doc_id = ''
                for attempt_img in all_imgs:
                    # Convert/resize to JPEG before sending to Mindee
                    # Mindee drops connections on large webp/png files
                    import tempfile, io as _io
                    _mindee_img = Image.open(str(attempt_img))
                    try: _mindee_img = ImageOps.exif_transpose(_mindee_img)
                    except: pass
                    _mindee_img.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
                    _tmp = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False)
                    _mindee_img.convert('RGB').save(_tmp.name, format='JPEG', quality=92)
                    _tmp.close()
                    # Retry Mindee up to 3 times on connection errors
                    for _attempt in range(3):
                        try:
                            response = client.enqueue_and_get_result(ExtractionResponse, PathInput(_tmp.name), params)
                            break
                        except Exception as _me:
                            if _attempt == 2: raise _me
                            time.sleep(2 ** _attempt)
                    try: os.unlink(_tmp.name)
                    except: pass
                    flds = response.inference.result.fields
                    first = str(flds.first_name).strip()
                    last = str(flds.last_name).strip()
                    expiry = str(flds.expiry_date).strip()
                    doc_id = str(flds.document_id).strip() if hasattr(flds, 'document_id') else ''
                    m = re.search(r':state:\s*([A-Z]{2})', str(flds.address))
                    state = m.group(1) if m else None
                    # Collect polygon data for expander
                    all_xs, all_ys = [], []
                    for key, field in flds.items():
                        for loc in getattr(field, 'locations', []):
                            for pt in loc.polygon:
                                all_xs.append(pt.x); all_ys.append(pt.y)
                    n_fields = len(all_xs) // 4
                    mindee_log[str(attempt_img.name)] = {
                        'all_xs': all_xs,
                        'all_ys': all_ys,
                        'n_fields': n_fields,
                        'img_w': Image.open(str(attempt_img)).width,
                        'img_h': Image.open(str(attempt_img)).height,
                    }
                    if first and last:
                        break

                if not first or not last:
                    recent_status = f"FAIL: {fld.name} - name not found"
                    with open(str(Path(root) / "error_log.txt"), "a") as ef:
                        ef.write(f"FAIL: {fld.name} - name not found (first='{first}' last='{last}') fields={str(flds)[:200]}\n")
                    fail_count += 1
                    progress.update(task, advance=1)
                    live.update(create_live_display())
                    continue

                expired = False
                if expiry:
                    try: expired = date.fromisoformat(expiry) < date.today()
                    except: pass

                fn, ln = first.title(), last.title()
                st = state or 'XX'
                new_name = f"{st} {fn} {ln}"
                # Always place renamed folder directly under root,
                # regardless of how deeply nested the original folder was
                # (e.g. 589-12-6009/FL Carleen Lobo -> root/FL Carleen Renita Lobo)
                new_folder = Path(root) / new_name
                final_path = fld

                # Debug log
                import json as _json
                _dbg = {
                    'fld': str(fld),
                    'fld_parent': str(fld.parent),
                    'new_folder': str(new_folder),
                    'new_folder_exists': new_folder.exists(),
                    'fld_exists': fld.exists(),
                    'fld_contents': [str(x) for x in fld.iterdir()] if fld.exists() else [],
                }
                with open(r'C:\Users\analf\Downloads\mindee_rename_debug.log', 'a', encoding='utf-8') as _f:
                    _f.write(_json.dumps(_dbg, indent=2) + '\n---\n')

                if fld != new_folder:
                    try:
                        if new_folder.exists():
                            for f in list(fld.iterdir()):
                                dest = new_folder / f.name
                                if not dest.exists():
                                    shutil.move(str(f), str(dest))
                            shutil.rmtree(str(fld))
                        else:
                            fld.rename(new_folder)
                        final_path = new_folder
                    except Exception as e_rename:
                        with open(r'C:\Users\analf\Downloads\mindee_rename_debug.log', 'a', encoding='utf-8') as _f:
                            _f.write(f'RENAME ERROR: {e_rename}\n---\n')
                        try:
                            shutil.move(str(fld), str(new_folder))
                            final_path = new_folder
                        except: pass

                # Clean up empty SSN shell folders if still exist
                try:
                    if fld.exists() and not any(fld.iterdir()):
                        fld.rmdir()
                except: pass
                # Clean up outer parent shell (e.g. 589-12-6009 after FL Carleen Lobo moves out)
                for check_parent in [fld.parent, new_folder.parent]:
                    try:
                        if (check_parent != Path(root) and
                            check_parent.exists() and
                            not any(check_parent.iterdir())):
                            check_parent.rmdir()
                    except: pass
                # Broader cleanup: remove any empty dirs directly under root
                try:
                    for d in list(Path(root).iterdir()):
                        if d.is_dir() and d.name.lower() != 'expired':
                            if not any(d.iterdir()):
                                d.rmdir()
                except: pass

                with open(r'C:\Users\analf\Downloads\mindee_rename_debug.log', 'a', encoding='utf-8') as _f:
                    _f.write(f'final_path={final_path} exists={final_path.exists()} fld_still_exists={fld.exists()}\n===\n')

                classify_and_rename_folder(final_path, fn, ln, mindee_log=mindee_log, pre_classify=pre_classify)
                # Prepend DL# to all txt files in the folder
                prepend_doc_id_to_txts(final_path, doc_id)
                # Create info.txt with name, address, DL number
                try:
                    addr_raw = str(flds.address).strip() if flds else ''
                    addr = re.sub(r':.*?:\s*', '', addr_raw).strip()
                    addr = re.sub(r'-\d{4}\b', '', addr)          # remove -4digit zip ext
                    addr = re.sub(r'\bUSA\b', '', addr, flags=re.IGNORECASE).strip()
                    addr = ' '.join(addr.split())                  # collapse whitespace
                    info_path = final_path / 'info.txt'
                    if not info_path.exists():
                        doc_id_clean = doc_id.replace('-', '').replace(' ', '')
                        content = f'{fn} {ln} {addr}\n\n\n{doc_id_clean}\n'
                        info_path.write_text(content, encoding='utf-8')
                except: pass
                try: os.utime(final_path, (datetime.now().timestamp(),)*2)
                except: pass

                # Save Mindee polygon log for expander to use (avoids re-calling Mindee)
                try:
                    log_path = final_path / "mindee_data.json"
                    with open(str(log_path), 'w') as jf:
                        json.dump(mindee_log, jf)
                except: pass

                if expired:
                    exp_dir = Path(root) / "expired"
                    exp_dir.mkdir(exist_ok=True)
                    try: shutil.move(str(final_path), str(exp_dir / final_path.name))
                    except: pass
                    # Clean up any empty parent folder left behind (e.g. original SSN folder)
                    try:
                        parent = fld.parent
                        if parent != Path(root) and parent.exists() and not any(parent.iterdir()):
                            parent.rmdir()
                    except: pass
                    recent_status = f"EXPIRED: {fn} {ln} (exp {expiry})"
                    expired_count += 1
                else:
                    recent_status = f"OK: {st} {fn} {ln}"
                    valid_count += 1

            except Exception as e:
                recent_status = f"FAIL: {fld.name} - {str(e)[:80]}"
                with open(str(Path(root) / "error_log.txt"), "a") as ef:
                    ef.write(f"FAIL: {fld.name} - {str(e)}\n")
                fail_count += 1

            pct = int(((i+1)/len(pending))*100)
            progress.update(task, advance=1, description=f"🪪 [orange1]{pct}%[/orange1] • 🔍")
            live.update(create_live_display())

        processed = len(pending)
        recent_status = "All processing complete! 🎉"
        progress.update(task, description="🪪 [bright_green]100%[/bright_green] • 🎉 Done!")
        live.update(create_live_display())

        # Final pass: remove any leftover empty dirs under root
        try:
            for d in sorted(Path(root).iterdir(), key=lambda x: len(str(x)), reverse=True):
                if d.is_dir() and d.name.lower() != 'expired':
                    try:
                        if not any(d.iterdir()):
                            d.rmdir()
                    except: pass
        except: pass

        time.sleep(2)

    print(f"\n\033[38;5;82mDONE!\033[0m {valid_count} renamed, {expired_count} expired, {fail_count} failed")
    input("\nPress Enter to return to main menu...")

def main():
    try:
        os.system('color')
        # Support command-line argument for non-interactive use (Mac/Linux/batch)
        if len(sys.argv) > 1:
            folder_arg = sys.argv[1].strip().strip('"').strip("'")
            if Path(folder_arg).exists():
                start_processing({'root': folder_arg})
                return
            else:
                print(f"ERROR: Folder not found: {folder_arg}")
                sys.exit(1)
        while True:
            config = run_menu()
            start_processing(config)
    except KeyboardInterrupt:
        print("\n\nGoodbye!")
        sys.exit(0)

if __name__ == "__main__":
    main()
