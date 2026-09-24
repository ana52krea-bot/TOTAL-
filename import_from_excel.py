from __future__ import annotations

import math
import re
import sqlite3
import sys
import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _col_num(cell_ref: str) -> int:
    m = re.match(r"([A-Z]+)", cell_ref)
    n = 0
    for ch in m.group(1):
        n = n * 26 + ord(ch) - 64
    return n


def _as_float(value, default=0.0):
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_text(value) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _excel_date(value):
    if value in (None, ""):
        return None
    s = str(value).strip()
    try:
        n = float(s)
        if 20000 <= n <= 70000:
            return (datetime(1899, 12, 30) + timedelta(days=n)).date().isoformat()
    except ValueError:
        pass
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return s


class XlsxReader:
    """Minimal XLSX reader using only the Python standard library.

    It reads cached cell values and formulas without modifying the source file.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.zf = zipfile.ZipFile(self.path)
        self.shared_strings = self._read_shared_strings()
        self.sheets = self._read_sheet_map()

    def close(self):
        self.zf.close()

    def _read_shared_strings(self):
        if "xl/sharedStrings.xml" not in self.zf.namelist():
            return []
        root = ET.fromstring(self.zf.read("xl/sharedStrings.xml"))
        out = []
        for si in root.findall(f"{{{MAIN_NS}}}si"):
            out.append("".join(t.text or "" for t in si.iter(f"{{{MAIN_NS}}}t")))
        return out

    def _read_sheet_map(self):
        wb = ET.fromstring(self.zf.read("xl/workbook.xml"))
        rels = ET.fromstring(self.zf.read("xl/_rels/workbook.xml.rels"))
        rid_to_target = {r.attrib["Id"]: r.attrib["Target"] for r in rels}
        out = {}
        sheets = wb.find(f"{{{MAIN_NS}}}sheets")
        for s in sheets:
            rid = s.attrib[f"{{{REL_NS}}}id"]
            target = rid_to_target[rid]
            if not target.startswith("xl/"):
                target = "xl/" + target.lstrip("/")
            out[s.attrib["name"]] = target
        return out

    def rows(self, sheet_name: str):
        root = ET.fromstring(self.zf.read(self.sheets[sheet_name]))
        for row in root.findall(f".//{{{MAIN_NS}}}sheetData/{{{MAIN_NS}}}row"):
            row_no = int(row.attrib["r"])
            data = {}
            formulas = {}
            for cell in row.findall(f"{{{MAIN_NS}}}c"):
                col = _col_num(cell.attrib["r"])
                cell_type = cell.attrib.get("t")
                v = cell.find(f"{{{MAIN_NS}}}v")
                f = cell.find(f"{{{MAIN_NS}}}f")
                value = None
                if cell_type == "s" and v is not None:
                    try:
                        value = self.shared_strings[int(v.text)]
                    except Exception:
                        value = v.text
                elif cell_type == "inlineStr":
                    inline = cell.find(f"{{{MAIN_NS}}}is")
                    value = "".join(t.text or "" for t in inline.iter(f"{{{MAIN_NS}}}t")) if inline is not None else ""
                elif v is not None:
                    value = v.text
                data[col] = value
                if f is not None:
                    formulas[col] = f.text or ""
            yield row_no, data, formulas

    def external_links(self):
        out = []
        for i in (1, 2, 3, 4, 5):
            rel_name = f"xl/externalLinks/_rels/externalLink{i}.xml.rels"
            if rel_name not in self.zf.namelist():
                continue
            root = ET.fromstring(self.zf.read(rel_name))
            targets = []
            for rel in root:
                target = rel.attrib.get("Target", "")
                if target and target not in targets:
                    targets.append(target)
            out.append((i, targets))
        return out

    def formula_audit(self):
        errors = defaultdict(lambda: defaultdict(int))
        bad_refs = defaultdict(list)
        external_refs = defaultdict(lambda: defaultdict(int))
        for sheet, path in self.sheets.items():
            root = ET.fromstring(self.zf.read(path))
            for cell in root.iter(f"{{{MAIN_NS}}}c"):
                v = cell.find(f"{{{MAIN_NS}}}v")
                f = cell.find(f"{{{MAIN_NS}}}f")
                if cell.attrib.get("t") == "e" and v is not None:
                    errors[sheet][v.text] += 1
                if f is not None and f.text:
                    if "#REF!" in f.text:
                        bad_refs[sheet].append((cell.attrib.get("r", ""), f.text))
                    for ext in re.findall(r"\[(\d+)\]", f.text):
                        external_refs[sheet][ext] += 1
        return errors, bad_refs, external_refs


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS reps (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_no TEXT NOT NULL UNIQUE,
    rep_code TEXT,
    name TEXT NOT NULL,
    region TEXT,
    category TEXT,
    visit_day TEXT,
    responsible_person TEXT,
    mobile TEXT,
    start_date TEXT,
    role TEXT,
    specialization TEXT,
    notes TEXT,
    balance_adjustment REAL NOT NULL DEFAULT 0,
    opening_balance REAL NOT NULL DEFAULT 0,
    source_row INTEGER,
    FOREIGN KEY (rep_code) REFERENCES reps(code)
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    volume REAL NOT NULL DEFAULT 0,
    opening_stock REAL NOT NULL DEFAULT 0,
    sale_price REAL NOT NULL DEFAULT 0,
    cost_price REAL NOT NULL DEFAULT 0,
    source_row INTEGER,
    UNIQUE(name, volume)
);
CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rep_code TEXT,
    customer_id INTEGER,
    account_no TEXT NOT NULL,
    product_id INTEGER,
    product_name TEXT NOT NULL,
    volume REAL NOT NULL DEFAULT 0,
    quantity REAL NOT NULL,
    liters REAL NOT NULL DEFAULT 0,
    unit_price REAL NOT NULL DEFAULT 0,
    discount_rate REAL NOT NULL DEFAULT 0,
    total REAL NOT NULL DEFAULT 0,
    sale_date TEXT NOT NULL,
    month INTEGER NOT NULL,
    source_row INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (rep_code) REFERENCES reps(code),
    FOREIGN KEY (customer_id) REFERENCES customers(id),
    FOREIGN KEY (product_id) REFERENCES products(id)
);
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rep_code TEXT,
    customer_id INTEGER,
    account_no TEXT NOT NULL,
    amount REAL NOT NULL,
    payment_date TEXT NOT NULL,
    month INTEGER NOT NULL,
    fx_rate REAL NOT NULL DEFAULT 0,
    source_row INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (rep_code) REFERENCES reps(code),
    FOREIGN KEY (customer_id) REFERENCES customers(id)
);
CREATE TABLE IF NOT EXISTS rep_routes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rep_code TEXT NOT NULL,
    day_name TEXT NOT NULL,
    route_text TEXT NOT NULL,
    source_row INTEGER,
    FOREIGN KEY (rep_code) REFERENCES reps(code)
);
CREATE TABLE IF NOT EXISTS audit_issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    severity TEXT NOT NULL,
    category TEXT NOT NULL,
    location TEXT,
    details TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sales_date ON sales(sale_date);
CREATE INDEX IF NOT EXISTS idx_sales_account ON sales(account_no);
CREATE INDEX IF NOT EXISTS idx_sales_rep ON sales(rep_code);
CREATE INDEX IF NOT EXISTS idx_payments_date ON payments(payment_date);
CREATE INDEX IF NOT EXISTS idx_payments_account ON payments(account_no);
"""


def build_database(xlsx_path: str | Path, db_path: str | Path):
    xlsx_path = Path(xlsx_path)
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    reader = XlsxReader(xlsx_path)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)

    reps = {
        "A": "الجزيرة",
        "B": "Yassen",
        "C": "Hosam",
        "G": "Ramee",
        "E": "Saleh",
        "F": "Akkawi",
        "M": "M",
    }
    conn.executemany("INSERT OR IGNORE INTO reps(code,name) VALUES(?,?)", reps.items())

    # Customers: duplicate account numbers in the source are intentionally deduplicated.
    seen_accounts = set()
    duplicate_customers = []
    for row_no, d, _ in reader.rows("شجرة الزبائن"):
        if row_no == 1:
            continue
        account = _as_text(d.get(2))
        if not account:
            continue
        if account in seen_accounts:
            duplicate_customers.append((row_no, account, _as_text(d.get(3))))
            continue
        seen_accounts.add(account)
        rep_code = _as_text(d.get(1)) or None
        if rep_code and rep_code not in reps:
            conn.execute("INSERT OR IGNORE INTO reps(code,name) VALUES(?,?)", (rep_code, rep_code))
        conn.execute(
            """INSERT INTO customers(
                account_no,rep_code,name,region,category,visit_day,responsible_person,mobile,
                start_date,role,specialization,notes,balance_adjustment,opening_balance,source_row
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                account, rep_code, _as_text(d.get(3)) or account, _as_text(d.get(4)), _as_text(d.get(5)),
                _as_text(d.get(6)), _as_text(d.get(13)), _as_text(d.get(14)), _excel_date(d.get(15)),
                _as_text(d.get(16)), _as_text(d.get(17)), _as_text(d.get(18)), _as_float(d.get(19)), 0.0, row_no,
            ),
        )

    # Products: use name + volume as the product key. This fixes ambiguous VLOOKUPs on duplicate names.
    seen_products = set()
    duplicate_products = []
    for row_no, d, _ in reader.rows("شجرة المواد"):
        if row_no == 1:
            continue
        name = _as_text(d.get(1))
        if not name:
            continue
        volume = _as_float(d.get(2))
        key = (name, volume)
        if key in seen_products:
            duplicate_products.append((row_no, name, volume))
            continue
        seen_products.add(key)
        conn.execute(
            "INSERT INTO products(name,volume,opening_stock,sale_price,cost_price,source_row) VALUES(?,?,?,?,?,?)",
            (name, volume, _as_float(d.get(3)), _as_float(d.get(7)), _as_float(d.get(9)), row_no),
        )

    customer_ids = {r[1]: r[0] for r in conn.execute("SELECT id,account_no FROM customers")}
    product_ids = {(r[1], float(r[2])): r[0] for r in conn.execute("SELECT id,name,volume FROM products")}

    orphan_customers = set()
    orphan_products = set()
    invalid_sale_totals = []
    sale_dates = []
    # Sales: actual source rows have account, product, non-zero quantity and a date.
    for row_no, d, _ in reader.rows("مبيعات"):
        if row_no == 1:
            continue
        account = _as_text(d.get(2))
        product_name = _as_text(d.get(4))
        qty = _as_float(d.get(6))
        sale_date = _excel_date(d.get(12))
        if not (account and product_name and qty != 0 and sale_date):
            continue
        rep_code = _as_text(d.get(1)) or None
        if rep_code and rep_code not in reps:
            conn.execute("INSERT OR IGNORE INTO reps(code,name) VALUES(?,?)", (rep_code, rep_code))
        if account not in customer_ids:
            conn.execute("INSERT INTO customers(account_no,rep_code,name) VALUES(?,?,?)", (account, rep_code, _as_text(d.get(3)) or account))
            customer_ids[account] = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            orphan_customers.add(account)
        volume = _as_float(d.get(5))
        pkey = (product_name, float(volume))
        if pkey not in product_ids:
            conn.execute(
                "INSERT OR IGNORE INTO products(name,volume,opening_stock,sale_price,cost_price,source_row) VALUES(?,?,?,?,?,?)",
                (product_name, volume, 0.0, _as_float(d.get(8)), _as_float(d.get(15)), row_no),
            )
            pid = conn.execute("SELECT id FROM products WHERE name=? AND volume=?", pkey).fetchone()[0]
            product_ids[pkey] = pid
            orphan_products.add(pkey)
        liters = _as_float(d.get(7), volume * qty)
        unit_price = _as_float(d.get(8))
        discount = _as_float(d.get(9))
        raw_total = d.get(10)
        if raw_total in (None, ""):
            total = math.ceil(qty * unit_price * (1 - discount)) if unit_price != 0 else 0.0
        else:
            try:
                total = float(raw_total)
            except (TypeError, ValueError):
                # Excel SUM ignores text values. Preserve that behavior and flag the bad cell for review.
                total = 0.0
                invalid_sale_totals.append((row_no, str(raw_total), account, product_name))
        month = int(_as_float(d.get(13))) if _as_float(d.get(13)) else int(str(sale_date)[5:7])
        conn.execute(
            """INSERT INTO sales(rep_code,customer_id,account_no,product_id,product_name,volume,quantity,
                liters,unit_price,discount_rate,total,sale_date,month,source_row)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rep_code, customer_ids[account], account, product_ids[pkey], product_name, volume, qty, liters,
             unit_price, discount, total, sale_date, month, row_no),
        )
        sale_dates.append(sale_date)

    payment_dates = []
    for row_no, d, _ in reader.rows("مقبوضات من الزبائن"):
        if row_no == 1:
            continue
        account = _as_text(d.get(2))
        amount = _as_float(d.get(4))
        payment_date = _excel_date(d.get(6))
        if not (account and amount != 0 and payment_date):
            continue
        rep_code = _as_text(d.get(1)) or None
        if account not in customer_ids:
            conn.execute("INSERT INTO customers(account_no,rep_code,name) VALUES(?,?,?)", (account, rep_code, _as_text(d.get(3)) or account))
            customer_ids[account] = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            orphan_customers.add(account)
        month = int(_as_float(d.get(7))) if _as_float(d.get(7)) else int(str(payment_date)[5:7])
        conn.execute(
            "INSERT INTO payments(rep_code,customer_id,account_no,amount,payment_date,month,fx_rate,source_row) VALUES(?,?,?,?,?,?,?,?)",
            (rep_code, customer_ids[account], account, amount, payment_date, month, _as_float(d.get(8)), row_no),
        )
        payment_dates.append(payment_date)

    # Routes from the legacy route sheet.
    route_name_to_code = {"yaseen": "B", "housam": "C", "rame": "G", "saleh": "E", "3kkawi": "F"}
    current_rep = None
    for row_no, d, _ in reader.rows("سحب من المندوبين"):
        value_e = _as_text(d.get(5))
        day = _as_text(d.get(4))
        if value_e.lower() in route_name_to_code:
            current_rep = route_name_to_code[value_e.lower()]
            continue
        if current_rep and day and value_e:
            route = value_e
            if _as_text(d.get(7)):
                route += " - " + _as_text(d.get(7))
            conn.execute("INSERT INTO rep_routes(rep_code,day_name,route_text,source_row) VALUES(?,?,?,?)", (current_rep, day, route, row_no))

    # Audit issues carried forward from the workbook review.
    for row_no, account, name in duplicate_customers:
        conn.execute("INSERT INTO audit_issues(severity,category,location,details) VALUES(?,?,?,?)",
                     ("warning", "duplicate_customer", f"شجرة الزبائن!B{row_no}", f"رقم الحساب {account} مكرر ({name})؛ تم الاحتفاظ بأول سجل فقط."))
    for row_no, name, volume in duplicate_products:
        conn.execute("INSERT INTO audit_issues(severity,category,location,details) VALUES(?,?,?,?)",
                     ("warning", "duplicate_product", f"شجرة المواد!A{row_no}", f"المادة {name} بحجم {volume:g} مكررة تمامًا؛ تم دمجها."))
    for account in sorted(orphan_customers):
        conn.execute("INSERT INTO audit_issues(severity,category,location,details) VALUES(?,?,?,?)",
                     ("warning", "orphan_customer", "import", f"الحساب {account} ظهر في حركة ولم يكن موجودًا في شجرة الزبائن؛ تم إنشاء سجل مختصر."))
    for name, volume in sorted(orphan_products):
        conn.execute("INSERT INTO audit_issues(severity,category,location,details) VALUES(?,?,?,?)",
                     ("warning", "orphan_product", "import", f"المادة {name} بحجم {volume:g} ظهرت في المبيعات ولم تكن مطابقة لشجرة المواد؛ تم إنشاء سجل."))
    for row_no, raw_value, account, product_name in invalid_sale_totals:
        conn.execute("INSERT INTO audit_issues(severity,category,location,details) VALUES(?,?,?,?)",
                     ("critical", "invalid_sale_total", f"مبيعات!J{row_no}", f"الإجمالي يحتوي قيمة غير رقمية ({raw_value}) للحساب {account} / {product_name}. تم احتسابه 0 مثل سلوك SUM في Excel."))

    for link_no, targets in reader.external_links():
        conn.execute("INSERT INTO audit_issues(severity,category,location,details) VALUES(?,?,?,?)",
                     ("critical", "external_link", f"externalLink{link_no}", "رابط Excel خارجي: " + " | ".join(targets)))

    errors, bad_refs, ext_refs = reader.formula_audit()
    for sheet, kinds in errors.items():
        for err, count in kinds.items():
            conn.execute("INSERT INTO audit_issues(severity,category,location,details) VALUES(?,?,?,?)",
                         ("critical" if err in ("#REF!", "#VALUE!") else "warning", "formula_error", sheet, f"{count} خلية بالقيمة {err}"))
    for sheet, refs in bad_refs.items():
        if refs:
            sample = ", ".join(cell for cell, _ in refs[:8])
            conn.execute("INSERT INTO audit_issues(severity,category,location,details) VALUES(?,?,?,?)",
                         ("critical", "broken_reference", sheet, f"{len(refs)} معادلة تحتوي #REF!؛ أمثلة: {sample}"))
    for sheet, refs in ext_refs.items():
        if refs:
            total = sum(refs.values())
            conn.execute("INSERT INTO audit_issues(severity,category,location,details) VALUES(?,?,?,?)",
                         ("warning", "external_formula_refs", sheet, f"{total} مرجع معادلة إلى ملفات خارجية."))

    # Metadata and verification totals.
    meta = {
        "app_schema_version": "1",
        "source_filename": xlsx_path.name,
        "imported_at": datetime.now().isoformat(timespec="seconds"),
        "source_sales_min_date": min(sale_dates) if sale_dates else "",
        "source_sales_max_date": max(sale_dates) if sale_dates else "",
        "source_payments_min_date": min(payment_dates) if payment_dates else "",
        "source_payments_max_date": max(payment_dates) if payment_dates else "",
        "legacy_debt_age_as_of": "2026-04-20",
        "balance_policy": "opening_balance + sales - payments - balance_adjustment",
        "external_links_policy": "not imported as opening balances to avoid possible double counting",
    }
    conn.executemany("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", meta.items())
    conn.commit()

    stats = {
        "customers": conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0],
        "products": conn.execute("SELECT COUNT(*) FROM products").fetchone()[0],
        "sales_rows": conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0],
        "sales_liters": conn.execute("SELECT ROUND(COALESCE(SUM(liters),0),3) FROM sales").fetchone()[0],
        "sales_value": conn.execute("SELECT ROUND(COALESCE(SUM(total),0),3) FROM sales").fetchone()[0],
        "payments_rows": conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0],
        "payments_value": conn.execute("SELECT ROUND(COALESCE(SUM(amount),0),3) FROM payments").fetchone()[0],
        "balance_adjustments": conn.execute("SELECT ROUND(COALESCE(SUM(balance_adjustment),0),3) FROM customers").fetchone()[0],
        "current_balance": conn.execute("""SELECT ROUND(
            COALESCE((SELECT SUM(total) FROM sales),0)-COALESCE((SELECT SUM(amount) FROM payments),0)
            -COALESCE((SELECT SUM(balance_adjustment) FROM customers),0)
            +COALESCE((SELECT SUM(opening_balance) FROM customers),0),3)""").fetchone()[0],
        "audit_issues": conn.execute("SELECT COUNT(*) FROM audit_issues").fetchone()[0],
    }
    conn.close()
    reader.close()
    return stats


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python import_from_excel.py <source.xlsx> <output.db>")
        raise SystemExit(2)
    result = build_database(sys.argv[1], sys.argv[2])
    for k, v in result.items():
        print(f"{k}: {v}")
