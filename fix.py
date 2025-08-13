import re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import csv

INPUT_DIR = "configs"
OUTPUT_DIR = "output"
LOG_CSV = "data_log.csv"
MAXLEN_DEFAULT = 25

TR069_TEMPLATE = [
    "  service TR069 gemport 1 vlan 100\n",
    "  tr069-mgmt 1 state unlock\n",
    "  tr069-mgmt 1 acs http://172.17.11.6:7547 validate basic username {user} password {password}\n",
    "  tr069-mgmt 1 tag pri 0 vlan 100\n"
]

Path(OUTPUT_DIR).mkdir(exist_ok=True)

# Regex
interface_re = re.compile(r"^(interface\s+\S+)([\s\S]*?)(?=^interface\s+|\Z)", re.MULTILINE)
ponmng_re = re.compile(r"^(pon-onu-mng\s+(\S+))([\s\S]*?)(?=^pon-onu-mng\s+|\Z)", re.MULTILINE)
name_re = re.compile(r"^(\s*)name\s+(\S+)", re.MULTILINE)
desc_re = re.compile(r"^(\s*)description\s+ODP-[^\s-]*-(\d+/\d+)", re.MULTILINE | re.IGNORECASE)
pppoe_user_re = re.compile(r"\buser\s+\S+")
pppoe_pass_re = re.compile(r"\bpassword\s+\S+")
tr069_check_re = re.compile(r"tr069-mgmt")

def build_final_name(base: str, code: str, maxlen: int = MAXLEN_DEFAULT) -> str:
    parts = base.split("-")
    if not parts:
        return "JMP-" + code

    # pastikan 'JMP' selalu di awal
    if parts[0] != "JMP":
        parts = ["JMP"] + parts

    first = parts[0]  # selalu JMP
    *middle, last = parts[1:] if len(parts) > 2 else (parts[1:-1], parts[-1] if len(parts)>1 else "")
    new_parts = [first]

    for part in middle:
        candidate = "-".join(new_parts + [part, last, code])
        if len(candidate) <= maxlen:
            new_parts.append(part)
        else:
            max_len_part = max(1, len(part) - (len(candidate)-maxlen))
            new_parts.append(part[:max_len_part])

    if last:
        new_parts.append(last)

    final_name = "-".join(new_parts + [code])

    # jika masih terlalu panjang, potong huruf terakhir dari middle terakhir
    while len(final_name) > maxlen and len(new_parts) > 2:
        new_parts[-2] = new_parts[-2][:-1] or new_parts[-2]
        final_name = "-".join(new_parts + [code])

    return final_name

def process_interface_block(header, body, interface_to_name, logs, filename):
    mname = name_re.search(body)
    mdesc = desc_re.search(body)
    interface_name = header.split()[1]

    if not mname or not mdesc:
        logs.append((filename, interface_name, "", "", "SKIP"))
        return header + body

    base_name = mname.group(2)
    code = mdesc.group(2)
    final_name = build_final_name(base_name, code)
    interface_to_name[interface_name] = final_name
    logs.append((filename, interface_name, base_name, final_name, "OK"))

    body = name_re.sub(f"{mname.group(1)}name {final_name}", body, count=1)
    return header + body

def process_ponmng_block(header, body, intf_name, interface_to_name):
    final_name = interface_to_name.get(intf_name)
    if not final_name:
        return header + body

    lines = body.splitlines(True)
    new_lines = []
    last_wifi_idx = None

    for idx, ln in enumerate(lines):
        ln = pppoe_user_re.sub(f"user {final_name}", ln)
        ln = pppoe_pass_re.sub(f"password {final_name}", ln)
        new_lines.append(ln)

        if re.search(r"service HOTSPOT", ln) and not any("service TR069" in l for l in new_lines):
            new_lines.append(TR069_TEMPLATE[0])

        if re.search(r"vlan port wifi", ln):
            last_wifi_idx = len(new_lines) -1

    if last_wifi_idx is not None and not any(tr069_check_re.search(l) for l in new_lines):
        for ln in reversed(TR069_TEMPLATE[1:]):
            ln_formatted = ln.format(user=final_name, password=final_name)
            new_lines.insert(last_wifi_idx +1, ln_formatted)

    return header + "".join(new_lines)

def process_file(file_path):
    text = file_path.read_text(encoding="utf-8", errors="ignore")
    interface_to_name = {}
    logs = []

    # backup otomatis
    backup_path = Path(file_path.parent) / f"{file_path.name}.bak"
    backup_path.write_text(text, encoding="utf-8")

    # proses interface
    last =0
    out_text=[]
    for m in interface_re.finditer(text):
        out_text.append(text[last:m.start()])
        out_text.append(process_interface_block(m.group(1), m.group(2), interface_to_name, logs, file_path.name))
        last = m.end()
    out_text.append(text[last:])
    text_after_interface = "".join(out_text)

    # proses pon-onu-mng
    final_text=[]
    last=0
    for m in ponmng_re.finditer(text_after_interface):
        final_text.append(text_after_interface[last:m.start()])
        final_text.append(process_ponmng_block(m.group(1), m.group(3), m.group(2), interface_to_name))
        last = m.end()
    final_text.append(text_after_interface[last:])

    output_path = Path(OUTPUT_DIR)/file_path.name
    output_path.write_text("".join(final_text), encoding="utf-8")

    return logs

def main():
    all_logs=[]
    files = list(Path(INPUT_DIR).glob("*.txt"))
    with ThreadPoolExecutor() as executor:
        results = executor.map(process_file, files)
        for log in results:
            all_logs.extend(log)

    # tulis CSV log
    with open(LOG_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["filename","interface","old_name","new_name","status"])
        writer.writerows(all_logs)

    print(f"[INFO] Semua file selesai. Log disimpan di {LOG_CSV}")

if __name__ == "__main__":
    main()
