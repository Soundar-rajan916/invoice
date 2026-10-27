# Synthetic Indian GST Invoice Generator & Ground Truth Benchmark

A high-fidelity synthetic Indian GST invoice generator built with Python and ReportLab. It produces diverse, professional invoice PDFs alongside paired ground-truth JSON files and a unified JSONL manifest for benchmarking Document AI, OCR engines, vision-language models (VLMs), and invoice validation pipelines.

---

## 🌟 Key Features

- **3 Distinct Visual Layouts:**
  - `classic`: Crisp standard layout with table borders, light grey headers, and formal styling.
  - `banner`: Contemporary enterprise design with navy primary accents (`#1F4E79`), white-on-blue header rows, and alternating zebra stripes.
  - `compact`: Dense, space-efficient receipt-style format with serif typography (`Times-Roman`) and minimal padding.
- **Realistic Indian GST Compliance:**
  - Valid 15-character GSTINs generated using official statutory rules (State Code + PAN + Entity Number + 'Z' + Mod-36 Checksum).
  - Intra-state (CGST + SGST split 50/50) and inter-state (IGST) automatic tax routing based on state codes.
  - Standard GST tax tiers: **5%**, **12%**, **18%**, and **28%**.
  - Proper Indian numbering format (e.g. `12,34,567.50` instead of Western `1,234,567.50`).
- **Configurable Anomaly & Edge-Case Injection:**
  - **`wrong_total`**: Deliberate mathematical discrepancy in total amount.
  - **`bad_gstin`**: Corrupted GSTIN checksum digit for validation rule testing.
  - **`wrong_line_amount`**: Mismatched line item totals (`quantity * rate != amount`).
  - **`duplicate`**: Cross-template duplicate invoices with optional mangled invoice numbers to evaluate deduplication and fuzzy matching models.
  - **Multi-page invoices**: Long bills spanning across 2+ pages with dynamic `Page X of Y` page numbering.
- **Complete Ground Truth Annotations:**
  - Individual `<file_id>.json` pairing for each PDF.
  - Full dataset streaming with `<out>/manifest.jsonl`.
  - Rich metadata tracking expected flags, invoice numbers, dates, line items, and tax breakdowns.
- **Deterministic & Reproducible:** Seed-controlled generation ensuring identical datasets can be regenerated anytime.

---

## 📁 Repository Structure

```text
invoice/
├── main.py              # Core generator script (data model, layout engine, CLI)
├── requirements.txt     # Python dependencies (faker, reportlab)
├── out/                 # Generated dataset output directory
│   ├── pdfs/            # Rendered PDF invoices (INV_0001.pdf, ...)
│   ├── ground_truth/    # Ground-truth JSON files (INV_0001.json, ...)
│   └── manifest.jsonl   # Line-delimited JSON manifest of all invoices
└── README.md            # Project documentation
```

---

## 🚀 Quick Start

### 1. Prerequisites & Virtual Environment

Python 3.9+ is recommended. Set up a virtual environment:

```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
# Windows (PowerShell):
.\venv\Scripts\Activate.ps1
# Windows (CMD):
.\venv\Scripts\activate.bat
# Linux / macOS:
source venv/bin/activate
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

*(Dependencies: `faker`, `reportlab`)*

### 3. Generate Invoices

Generate a standard set of 50 base invoices:

```bash
python main.py --count 50 --out out --seed 42
```

---

## ⚙️ CLI Reference & Options

| Argument | Type | Default | Description |
| :--- | :---: | :---: | :--- |
| `--count` | `int` | `50` | Total number of base synthetic invoices to produce. |
| `--out` | `str` | `out` | Directory where `pdfs/`, `ground_truth/`, and `manifest.jsonl` are saved. |
| `--seed` | `int` | `42` | Random seed for deterministic generation. |
| `--end-date` | `str` | *today* | Maximum invoice date (`YYYY-MM-DD`). Invoices are dated within a 90-day window preceding this. |
| `--multipage-ratio`| `float` | `0.15` | Fraction of invoices that generate extra line items spanning multiple pages (15% by default). |
| `--faulty-ratio` | `float` | `0.10` | Fraction of invoices containing injected errors (`wrong_total`, `bad_gstin`, or `wrong_line_amount`). |
| `--duplicates` | `int` | `5` | Number of duplicate invoice pairs generated across alternating layouts. |

### Example CLI Commands

**Generate 100 benchmark invoices with 20% multi-page and 15% faulty cases:**
```bash
python main.py --count 100 --multipage-ratio 0.20 --faulty-ratio 0.15 --duplicates 10 --out dataset
```

**Generate a clean dataset (no errors or duplicates) for baseline OCR training:**
```bash
python main.py --count 200 --faulty-ratio 0.0 --duplicates 0 --multipage-ratio 0.10 --out clean_dataset
```

---

## 📊 Ground Truth Schema

For each invoice, a corresponding JSON file is written to `<out>/ground_truth/<file_id>.json` and appended to `<out>/manifest.jsonl`:

```json
{
  "file": "INV_0001.pdf",
  "layout": "classic",
  "pages": 1,
  "invoice_number": "INV-2024-84920",
  "invoice_date": "2024-08-15",
  "invoice_date_printed": "15-Aug-2024",
  "supplier_name": "Sharma Logistics Pvt Ltd",
  "supplier_gstin": "27AAACS1234F1Z8",
  "line_items": [
    {
      "description": "Basmati Rice 25 kg bag (Premium)",
      "quantity": 5,
      "rate": 2100.0,
      "amount": 10500.0
    }
  ],
  "taxable_value": 10500.0,
  "cgst": 0.0,
  "sgst": 0.0,
  "igst": 525.0,
  "total": 11025.0,
  "gstin_valid": true,
  "expected_flags": [],
  "duplicate_of": null
}
```

### Anomaly Flags (`expected_flags`)

| Flag Value | Description |
| :--- | :--- |
| `[]` | Valid, error-free invoice. |
| `["wrong_total"]` | The printed total does not equal `taxable_value + taxes`. |
| `["bad_gstin"]` | The supplier GSTIN fails the Mod-36 checksum verification. |
| `["wrong_line_amount"]` | A line item has `amount != quantity * rate`. |
| `["duplicate"]` | Invoice is a re-rendered copy of an earlier base invoice (`duplicate_of` indicates the source ID). |

---

## 🎯 Use Cases

1. **Document AI & OCR Benchmarking:**
   - Test extraction accuracy on scanned/digital Indian GST invoices (e.g. Tesseract, PaddleOCR, AWS Textract, Google Document AI, Azure AI Document Intelligence).
2. **VLM & LLM Evaluation:**
   - Benchmark structured information extraction (JSON schema adherence, table parsing, key-value extraction) with models like GPT-4o, Claude 3.5 Sonnet, or Gemini Flash.
3. **Automated Accounts Payable (AP) Rule Testing:**
   - Verify automated accounts payable validation logic, GST compliance engines, and anomaly detection filters against ground-truth labels.
