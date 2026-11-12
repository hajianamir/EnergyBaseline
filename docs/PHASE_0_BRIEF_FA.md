# PHASE 0 BRIEF — Truth & Rails (دستور کار اجباری عامل اجرا)

**پیش‌نیاز:** بستهٔ تصمیم‌ها RATIFIED است (`docs/DECISIONS_FA.md`): D1=B, D2=B+A, D3=A, D4=B, D5=A.
**هدف فاز:** مخزن را از «نمونهٔ اولیه» به «کارگاه قابل حسابرسی» تبدیل کن. **در این فاز هیچ فیچر محصولی اضافه نمی‌شود**
(نه VSG، نه TCP، نه کاتالوگ تست، نه UI جدید). اگر چیزی خارج از این برگه اضافه کنی، رد می‌شود.

**دامنه:** G-1, G-2, G-7, §27, AC-21, AC-22, PKG-6 + پیامدهای D1..D5 + S1..S6

**زمان تخمینی:** ۱ تا ۱٫۵ مهندس-هفته · **خروجی:** `artifacts/phase0/REPORT.md` (قالب ۴.۲ برنامه) + push روی شاخه

---

## 0. ممنوعیت‌های مطلق این فاز

1. هیچ فایلی زیر `energybaseline/core/` را برای «بهبود» دست نزن؛ فقط دو تغییر مجاز است: (الف) `launcher.py`
   برای حذف بازنویسی `DATABASE_URL`، (ب) `core/report.py` برای حذف مسیر WeasyPrint.
2. هیچ تست موجودی را حذف/ضعیف نکن. دو تست شکسته با **تصمیم قراردادی مستند** حل می‌شود (بند ۸).
3. هیچ وابستگی جدیدی جز فهرست بند ۲ اضافه نکن.
4. `main` را لمس نکن؛ فقط `arena/af928d3c-energybaseline`.

---

## 1. پاک‌سازی استک وب (G-1/G-2/AC-01/AC-35)

```bash
git rm -r package.json package-lock.json next.config.ts next-env.d.ts tsconfig.json \
         postcss.config.mjs eslint.config.mjs drizzle.config.json src/
```
- `README.md`: حذف هر اشارهٔ Next/Tailwind/drizzle و افزودن بخش «Python-only, PySide6, offline».
- `install.bat`/`start.bat`/`stop.bat`: حذف هر فراخوانی npm/node.
- **اثبات:** `git ls-files | grep -Ei 'package.json|tsconfig|\.tsx?$|next\.config|drizzle|eslint|postcss'` باید خالی باشد
  و `guard_web_free.py` سبز.

---

## 2. وابستگی‌ها: پین + lock + حذف WeasyPrint (D5, S1, PKG-6)

1. `requirements.in` (قیدهای منطقی) → `requirements.txt` با **پین دقیق `==`** برای همه، شامل موارد **غایب فعلی**:
   `joblib`, `mapie==1.6.0`, `arch==9.0.0`, `sqlglot==30.31.0`, `reportlab==5.1.0`, `pydantic>=2` (پین‌شده),
   `hypothesis`, `pytest-qt`, `ruff`, `cyclonedx-bom`, `pip-audit`, `pywin32` (فقط ویندوز via marker).
2. `requirements.lock` با `pip-compile --generate-hashes` (یا `uv pip compile --generate-hashes`) — **hash-locked**.
3. **حذف `weasyprint`** از همه‌جا: `requirements.txt`, `energybaseline/core/report.py`, `scripts/build_exe.py`,
   `README.md`, `docs/USER_GUIDE.md`.
4. پایتون هدف: **3.11.9** (ثبت در `docs/SUPPORT_MATRIX.md`؛ `python_requires = >=3.11,<3.12`).
5. **اثبات:** `pip install -r requirements.lock --require-hashes --dry-run` موفق؛ `grep -rn "weasyprint"` (بدون
   پوشهٔ docs برنامه‌ریزی) خالی؛ `python -c "import reportlab, mapie, arch, sqlglot"` موفق.

---

## 3. دروازه‌های ماشین‌خوان (این‌ها ابزار سنجش من هستند — نه تزئین)

سه اسکریپت زیر را بساز و **برای هر کدام تست منفی** بنویس (تست باید ثابت کند دروازه واقعاً گیر می‌دهد):

| اسکریپت | چه می‌سنجد | تست منفی اجباری |
|---|---|---|
| `scripts/gates/guard_web_free.py` | AST scan روی `*.py` برای import ممنوع (`weasyprint`, `QtWebEngineWidgets`, `flask`, `fastapi`, `uvicorn`, `http.server`) + اسکن manifest + رد فایل‌های استک وب در git | `tests/gates/test_guard_web_free.py` یک فایل موقت با `import weasyprint` می‌سازد و انتظار exit≠0 دارد |
| `scripts/gates/guard_env_readers.py` | فقط `config/settings.py` مجاز به خواندن envهای DB | تست منفی: فایل موقت با `os.environ["DATABASE_URL"]` ⇒ exit≠0 |
| `scripts/gates/guard_tests_integrity.py` | SHA-256 تست‌های پایه در `tests/.baseline.sha256` | تست منفی: تغییر یک بایت از یک تست ⇒ exit≠0 |

**استثنای مستند:** `docs/EXECUTION_PLAN_FA.md` و `docs/DECISIONS_FA.md` و `artifacts/**` از اسکن واژه‌ای مستثنا هستند
(سند ≠ کد). این استثنا باید در docstring اسکریپت نوشته شود، نه فقط در گزارش.

---

## 4. CI اجباری (D2, PKG-6, §27)

`.github/workflows/ci.yml`:
```yaml
strategy: { matrix: { os: [windows-latest, ubuntu-latest] } }
steps:
  - checkout
  - setup-python 3.11.9
  - pip install -r requirements.lock --require-hashes       # ویندوز: کامل؛ لینوکس: بدون pywin32
  - ruff check . && ruff format --check .
  - python -m pyflakes energybaseline
  - python scripts/gates/guard_web_free.py
  - python scripts/gates/guard_env_readers.py
  - python scripts/gates/guard_tests_integrity.py
  - python -m pytest -q --cov=energybaseline/core --cov-fail-under=60      # S5
  - pytest-qt smoke: python -m pytest -q tests/gui -p no:cacheprovider     # ویندوز: platform=windows
  - pip-audit -r requirements.lock
  - cyclonedx-py requirements -o sbom.json
  - upload-artifact: sbom.json, coverage.xml, artifacts/**
```
- **گیت G0 نمی‌گذارد** CI فقط روی لینوکس اجرا شود؛ ماتریس دو-OS اجباری است.
- `.github/workflows/r-reference.yml`: شبانه + `workflow_dispatch` (D4) — در این فاز فقط اسکلت + `tests/reference/README.md`.

---

## 5. ماتریس الزامات مولد-محور (AC-22, Traceability)

`scripts/gen_matrix.py`:
- منبع IDها: **فایل داده** `docs/requirements.yaml` (نه کد) با همهٔ AC-01..AC-35 و خانواده‌های
  DAT-1..8, VSG-1..9, TCP-1..14, T1..T14, UQ-1..5, PM-1..7, DR-1..5, PER-1..6, REP-1..5, RPT-1..7, UX-1..12, SHL-1..9, DB-1..11, LIF-1..5, SCR-1..7, AUTO-1..6, MT-1..5, VAL-1..7, SC-1..10, CI-1..4, DOM-1..3, PKG-1..6.
- هر ردیف: `id, priority(M/S/O), phase, code_refs[], test_ids[], status`.
- `--check` باید: (الف) هر ID موجود باشد، (ب) هیچ `status=done` بدون `test_ids` نباشد، (ج) هیچ AC بدون فاز نباشد.
- خروجی: `docs/requirements_matrix.md` (تولیدشده؛ هدر «generated — do not edit»).

---

## 6. مستندات فرآیندی (اجباری، انگلیسی طبق G-7)

| فایل | محتوا | وضعیت اولیه |
|---|---|---|
| `docs/deviations.md` | ۱۰ شکاف بخش ۰ برنامه + انحرافات کتابخانه‌ای (§2) + **PKG-3 → [S] با trigger** (D1) | همه با وضعیت OPEN |
| `docs/assumptions.md` | S1..S6 + پیش‌فرض‌های سکوت | ADOPTED (پنجرهٔ اعتراض تا G0) |
| `docs/not_implemented.md` | TFT/PatchTST/Informer، CatBoost-اگر-نبود… | فهرست اولیه |
| `SECURITY.md` | threat model اولیه (local attacker, malicious data file, SQL injection, artifact tampering) | draft |
| `THIRD_PARTY_NOTICES.md` | مجوزها | draft |
| `docs/SUPPORT_MATRIX.md` | Python 3.11.9 · Win10 22H2/Win11 · **سخت‌افزار مرجع D2** · نسخهٔ چرخ torch CPU | نهایی |
| `LICENSE` | انتخاب کاربر (پیش‌فرض: proprietary/internal) | **نیاز به تأیید** |

---

## 7. .gitignore و بهداشت مخزن

- `data/`, `logs/`, `data/models`, `data/runs`, `artifacts/**/*.db|sqlite|joblib`, `build/`, `dist/`, `_internal/`,
  `EnergyBaseline.exe`, `*.spec`, `__pycache__/`, `.venv/` (بخشی از این‌ها در کامیت ناظر اضافه شد — کاملش کن).
- بررسی: `git ls-files | grep -E "\.pyc$|__pycache__"` خالی.

---

## 8. دو تست شکسته — با تصمیم قراردادی، نه با شل‌کردن assert

**۸.۱ `test_daily_multitarget_independent` (وابستگی به فایل commit‌نشده)**
- **قرارداد:** `samples/*.xlsx` از git خارج و در `conftest.py` با `samples/generate_samples.py` (seed ثابت) ساخته می‌شود
  (fixture با `scope=session`، در `tmp_path_factory`).
- تست باید به generator وابسته باشد، نه به آرتیفکت. اگر فایل xlsx لازم است، در CI تولید شود.

**۸.۲ `test_nn_explicitly_rejected_for_small_data` (تعارض رفتار با assert)**
- **قرارداد جدید (صریح و مستند):** پورتفولیو **باید** هر دو حالت را در رجیستر ثبت کند:
  `not evaluated` (پیش‌نیاز نصب نیست، مثلاً torch absent) و `rejected` (داده کافی نیست).
  تست به‌صورت **پارامتری** نوشته می‌شود: برای `torch present` ⇒ `rejected`، برای `torch absent` ⇒ `not evaluated`.
- **ممنوع:** حذف شرط `all(...)` بدون جایگزین؛ یا skip کردن تست.

**اثبات:** `pytest -q` روی ماشین تمیز (بدون torch) و روی ماشین با torch، هر دو با نتیجهٔ مستند در گزارش.

---

## 9. بذر تست‌های آینده (بدون فیچر)

- `tests/gui/` (خالی + یک smoke که فقط `QApplication` می‌سازد و می‌بندد؛ روی ویندوز `platform=windows`) — زیرساخت UX.
- `tests/reference/README.md` (قالب JSON مرجع R طبق D4) — بدون تست آماری.
- `tests/fixtures/synthetic.py` — **فقط** بازآرایی generator موجود برای استفاده در تست‌ها (بدون منطق تحلیل).

---

## گیت پذیرش G0 — دقیقاً این دستورها باید exit 0 بدهند

```bash
python -m pytest -q                                             # همه تست‌ها سبز (شامل دو تست بند ۸)
python -m pytest -q tests/gates                                 # تست‌های منفی دروازه‌ها سبز
python -m ruff check . && python -m ruff format --check .
python -m pyflakes energybaseline
python scripts/gates/guard_web_free.py
python scripts/gates/guard_env_readers.py
python scripts/gates/guard_tests_integrity.py
python scripts/gen_matrix.py --check
python -m pytest -q --cov=energybaseline/core --cov-fail-under=60
git ls-files | grep -Ei "package.json|tsconfig|\.tsx?$|next\.config|drizzle|eslint|postcss"   # باید خالی
git ls-files | grep -E "__pycache__|\.pyc$"                                                 # باید خالی
grep -rn "weasyprint" energybaseline scripts requirements*.txt REQUIREMENTS*                # باید خالی
```
و در ویندوز (CI): `ci.yml` سبز + `choco install r.project` فقط در جاب شبانه (اسکلت).

---

## بستهٔ شواهد مورد انتظار (`artifacts/phase0/`)

| فایل | محتوا |
|---|---|
| `REPORT.md` | قالب بخش ۴.۲ برنامه (۹ بند) |
| `pytest.txt` | خروجی خام `pytest -q` (هر دو ماشین: torch/no-torch) |
| `gates.txt` | خروجی هر سه دروازه + تست منفی هرکدام |
| `matrix_check.txt` | خروجی `gen_matrix.py --check` و شمارش IDها |
| `deps.txt` | `pip freeze` + SHA-256 فایل lock + خروجی `pip-audit` |
| `sbom.json` | CycloneDX |
| `git_clean.txt` | خروجی grepهای پاک‌سازی (بند ۱ و ۷) |

---

## معیارهای رد فوری در این فاز (بدون بحث)

- دروازه‌ها بدون تست منفی.
- ماتریس با `status=done` و `test_ids` خالی.
- حل تست شکسته با `xfail`/`skip`/حذف assert.
- باقی‌ماندن یک ارجاع به `weasyprint` در کد/manifest.
- باقی‌ماندن فایل‌های Next/React در git.
- lock بدون hash یا با `>=`.
