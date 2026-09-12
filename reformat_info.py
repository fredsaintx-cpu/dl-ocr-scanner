
import re
from pathlib import Path

root = Path(r"C:\Users\analf\Downloads\AAABnyMk0_p58maWE80D")
fixed = 0

for info in sorted(root.rglob("info.txt")):
    try:
        content = info.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()

        # Split into the name+address block and the DL# block
        # DL# is the last non-empty line
        non_empty = [l.strip() for l in lines if l.strip()]
        if not non_empty: continue

        dl_num = non_empty[-1].replace('-', '').replace(' ', '')
        name_addr_parts = non_empty[:-1]

        # Join all name+address parts into one line
        one_line = " ".join(name_addr_parts)

        # Clean up: remove -4digit zip extension, remove USA, collapse whitespace
        one_line = re.sub(r"-\d{4}\b", "", one_line)
        one_line = re.sub(r"\bUSA\b", "", one_line, flags=re.IGNORECASE)
        one_line = " ".join(one_line.split())

        new_content = f"{one_line}\n\n\n{dl_num}\n"
        info.write_text(new_content, encoding="utf-8")
        print(f"  {info.parent.name}: {one_line} | {dl_num}")
        fixed += 1
    except Exception as e:
        print(f"  FAIL {info.parent.name}: {e}")

print(f"\nReformatted {fixed} files")
