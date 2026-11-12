# PROMPT — عامل اجرا: فاز ۰ (Truth & Rails) · EnergyBaseline v6

> این متن را کامل به عامل اجرایی بده. مکمل آن، سه سند زیر **اجباری‌الخواندن** است:
> `docs/EXECUTION_PLAN_FA.md` · `docs/DECISIONS_FA.md` · `docs/PHASE_0_BRIEF_FA.md`

---

## نقش و قاعدهٔ بازی

تو **عامل اجرا** هستی، نه داور. یک ناظر مستقل کد تو را می‌خواند، دستورها را خودش اجرا می‌کند و
PASS/FAIL می‌دهد. سه چیز تو را فوراً رد می‌کند: ادعای بدون خروجی خام، دست‌کاری تست، و افزودن چیزی
خارج از دامنهٔ فاز.

- **شاخه:** `arena/af928d3c-energybaseline` (تنها شاخهٔ مجاز). `main` را لمس نکن. force-push ممنوع.
- **کامیت پایهٔ کار:** `b0dc49d` (روی `2c92f2a`).
- **دامنهٔ این نوبت: فقط فاز ۰.** هیچ فیچر محصولی اضافه نکن: نه VSG، نه TCP، نه کاتالوگ تست،
  نه ProcessPool، نه گزارش جدید، نه UI جدید. اگر اضافه کنی، رد می‌شود.
- **زبان:** کد، مستندات محصول، پیام کامیت و گزارش‌ها **انگلیسی** (G-7). گفت‌وگوی تو با من فارسی.
- **پس از تحویل گزارش: توقف کن.** فاز ۱ تا رأی ناظر باز نمی‌شود.

---

## وضعیت تأییدشدهٔ مخزن (این‌ها را خودِ ناظر اجرا کرده؛ درباره‌شان بحث نکن)

| سنجه | مقدار امروز |
|---|---|
| `pytest -q` | **2 failed, 17 passed** — `test_nn_explicitly_rejected_for_small_data`, `test_daily_multitarget_independent` |
| نقض AST (import ممنوع) | **۲ مورد** — `weasyprint` در `energybaseline/core/report.py` |
| `weasyprint` در manifest | موجود در `requirements.txt` |
| استک وب در git | `package.json`, `next.config.ts`, `tsconfig.json`, `postcss.config.mjs`, `eslint.config.mjs`, `drizzle.config.json`, `src/**` |
| شاخص‌های غایب | `ProcessPoolExecutor`=0، `freeze_support`=0، `aboutToQuit`=0، `QLocalServer`=0، `QSettings`=0، `QTranslator`=0، `ebproj`=0، `mapie`=0، `arch`=0، `sqlglot`=0، `reportlab`=0، `vsg`=0، `tcp`=0، `QAbstractTableModel`=0، `fetchMore`=0، CI و lock و `hypothesis` و `pytest-qt` و مرجع R و SBOM = نداریم |
| حجم/نسبت | ۷٬۷۳۲ خط پایتون، ۱۹ تست، ۷۵ فایل tracked |

**واقعیت‌های کتابخانه‌ای که باید در `docs/deviations.md` ثبت کنی** (ناظر تست کرده است):
- `statsmodels 0.15`: `MICEData.mcar_test()` **وجود ندارد** → پیاده‌سازی بومی + مرجع R در فاز ۴.
- `statsmodels 0.15`: `acorr_ljungbox` در `statsmodels.stats.diagnostic` است، نه `tsa.stattools`.
- `statsmodels 0.15`: `grangercausalitytests(verbose=)` حذف شده؛ `adfuller/kpss/acorr_lm` هشدار تغییر نوع بازگشتی می‌دهند.
- `mapie 1.6.0`، `arch 9.0.0`، `sqlglot 30.31.0`، `reportlab 5.1.0`: وجود دارند و ادعاهای پرامپت درست است.
- `scipy 1.17`: `brunnermunzel(midrank=)` منسوخ → `variant`.

---

## تصمیم‌های تصویب‌شده (غیرقابل مذاکره در این نوبت)

| # | تصمیم |
|---|---|
| **D1=B** | امضا تعویق؛ `PKG-3 → [S]`؛ اسکریپت امضا ساخته و با گواهی self-signed تست می‌شود؛ trigger «پیش از هر توزیع بیرونی» ثبت شود |
| **D2=B+A** | CI روی `windows-latest` اجباری + سخت‌افزار مرجع رسمی: `8-core x64 · 16 GB · NVMe · Win11 23H2 · 1920×1080@100%` |
| **D3=A** | نصب Full پیش‌فرض + بیلد Lite اختیاری (`build_exe.py --profile full\|lite`) |
| **D4=B** | JSON مرجع R + جاب شبانهٔ R؛ در این فاز فقط اسکلت و قالب |
| **D5=A** | **فقط ReportLab**؛ WeasyPrint/Qt-PDF/matplotlib-PDF حذف |
| **S1** | Python **3.11.9** |
| **S2** | بدون بازنویسی تاریخچه؛ حذف رو به جلو |
| **S3** | pool پروسه‌ای با `spawn` و کویوی فایل‌محور (فاز ۱، فقط یادداشت) |
| **S4** | `ruff` (lint + format) + `pyflakes` |
| **S5** | پوشش `--cov-fail-under=60` روی `energybaseline/core` |
| **S6** | `samples/*.xlsx` تولیدشدنی، خارج از git |
| **S7** | مجوز **Proprietary / Internal** (فایل `LICENSE` قبلاً اضافه شده — تغییرش نده) |

---

## کارهای فاز ۰ (D0.1 … D0.10) — دقیقاً همین‌ها

### D0.1 — حذف استک وب
```bash
git rm -r package.json package-lock.json next.config.ts next-env.d.ts tsconfig.json \
         postcss.config.mjs eslint.config.mjs drizzle.config.json src/
```
`README.md`، `install.bat`، `start.bat`، `stop.bat` را از هر اشارهٔ Node/npm/Next/Tailwind/drizzle پاک کن و
جملهٔ «Python-only · PySide6 · offline · no web server» را نگه دار.

### D0.2 — وابستگی‌ها: پین + lock + حذف WeasyPrint
- `requirements.in` بساز؛ `requirements.txt` با **پین دقیق `==`** برای همه؛ `requirements.lock` با
  `--generate-hashes` (pip-tools یا uv).
- **اضافه کن** (اکنون import می‌شوند یا لازم‌اند ولی اعلام نشده‌اند): `joblib`, `mapie==1.6.0`, `arch==9.0.0`,
  `sqlglot==30.31.0`, `reportlab==5.1.0`, `pydantic` (پین), `hypothesis`, `pytest-qt`, `ruff`,
  `cyclonedx-bom`, `pip-audit`؛ `pywin32` با marker ویندوز.
- **حذف کن:** `weasyprint` از همه‌جا.
- `python_requires = ">=3.11,<3.12"` و `docs/SUPPORT_MATRIX.md` با 3.11.9.

### D0.3 — سه دروازه + **تست منفی** برای هرکدام
| اسکریپت | رفتار |
|---|---|
| `scripts/gates/guard_web_free.py` | **AST scan** روی همهٔ `*.py` برای import ممنوع: `weasyprint`, `PySide6.QtWebEngineWidgets`, `flask`, `fastapi`, `uvicorn`, `http.server`؛ + اسکن manifestها؛ + رد حضور فایل‌های استک وب در `git ls-files` |
| `scripts/gates/guard_env_readers.py` | فقط `energybaseline/config/settings.py` مجاز به خواندن `DATABASE_URL/POSTGRES_URL/DB_CONNECTION_STRING/DB_URI/EB_DATABASE_URL` |
| `scripts/gates/guard_tests_integrity.py` | SHA-256 تست‌های پایه در `tests/.baseline.sha256`؛ تغییر/حذف تست بدون مجوز ⇒ خطا |

**الزامی:** `tests/gates/` با تست منفی برای هر سه (فایل موقت حاوی `import weasyprint` ⇒ exit≠0؛
`os.environ["DATABASE_URL"]` در فایل دیگر ⇒ exit≠0؛ تغییر یک بایت از تست ⇒ exit≠0).
**استثنای مستند:** `docs/EXECUTION_PLAN_FA.md`, `docs/DECISIONS_FA.md`, `docs/PHASE_0_BRIEF_FA.md`,
`artifacts/**` فقط «واژه» را نقل می‌کنند و اسکن نمی‌شوند — این را در docstring بنویس، نه در گزارش.

### D0.4 — CI
`.github/workflows/ci.yml` با ماتریس `[windows-latest, ubuntu-latest]`، ترتیب:
lock → `ruff check` + `ruff format --check` → `pyflakes` → سه دروازه → `pytest -q --cov=... --cov-fail-under=60`
→ `pytest-qt` smoke → `pip-audit` → `cyclonedx-py` → آپلود `sbom.json`, `coverage.xml`, `artifacts/**`.
`.github/workflows/r-reference.yml`: فقط **اسکلت** (شبانه + `workflow_dispatch`) — منطق اجرا در فاز ۴.

### D0.5 — ماتریس مولد-محور
`docs/requirements.yaml` با **همهٔ IDها**: AC-01..AC-35، DB-1..11، SHL-1..9، IPC-1..7، LIF-1..5، SCR-1..7،
DAT-1..8، VSG-1..9، AUTO-1..6، TCP-1..14، T1..T14، TC-1..8، CI-1..4، F1..F10، VAL-1..7، SC-1..10،
UQ-1..5، PM-1..7، DR-1..5، MT-1..5، PER-1..6، REP-1..5، RPT-1..7، UX-1..12، DOM-1..3، PKG-1..6.
هر ردیف: `id, priority, phase, code_refs[], test_ids[], status`.
`scripts/gen_matrix.py --check` باید: هر ID موجود · هیچ `done` بدون `test_ids` · هیچ AC بدون فاز.
خروجی `docs/requirements_matrix.md` با هدر «generated — do not edit».

### D0.6 — مستندات فرآیندی (انگلیسی)
`docs/deviations.md` (۱۰ شکاف پایه + انحرافات کتابخانه‌ای بالا + **PKG-3→[S] با trigger**) ·
`docs/assumptions.md` (S1..S7) · `docs/not_implemented.md` · `SECURITY.md` (threat model چهارگانه) ·
`THIRD_PARTY_NOTICES.md` · `docs/SUPPORT_MATRIX.md` · `CHANGELOG.md`.

### D0.7 — بهداشت مخزن
`.gitignore` کامل: `data/ logs/ artifacts/**/*.db|sqlite|joblib *.joblib *.ebproj build/ dist/ _internal/
EnergyBaseline.exe *.spec __pycache__/ .venv/` + تأیید صفر فایل `.pyc`/`__pycache__` در git.

### D0.8 — دو تست شکسته: **قرارداد، نه شل‌کردن**
1. `test_daily_multitarget_independent`: وابستگی به `samples/daily_plant_multitarget.xlsx` که commit نیست.
   قرارداد: fixture در `conftest.py` با `samples/generate_samples.py` (seed ثابت) در `tmp_path_factory`.
   تست به **مولد** وابسته شود، نه به آرتیفکت.
2. `test_nn_explicitly_rejected_for_small_data`: رفتار فعلی دو حالت دارد (`not evaluated` وقتی torch نیست،
   `rejected` وقتی داده کم است). قرارداد: تست **پارامتری** شود؛ torch present ⇒ `rejected`، torch absent ⇒ `not evaluated`.
   پورتفولیو باید هر دو را در رجیستر ثبت کند.
**ممنوع:** `skip`، `xfail`، حذف assert، یا تغییر منطق `core/`.

### D0.9 — بذرهای فاز بعد (بدون فیچر)
`tests/gui/` با یک smoke که فقط `QApplication` می‌سازد و می‌بندد (روی ویندوز `platform=windows`) ·
`tests/reference/README.md` با قالب JSON مرجع طبق D4 · `tests/fixtures/synthetic.py` (بازآرایی generator موجود).

### D0.10 — بستهٔ شواهد
`artifacts/phase0/`: `REPORT.md` · `pytest.txt` (خام، هر دو حالت torch) · `gates.txt` (هر سه دروازه + تست منفی) ·
`matrix_check.txt` · `deps.txt` (`pip freeze` + SHA-256 lock + `pip-audit`) · `sbom.json` · `git_clean.txt` · `ci_links.txt`.

---

## تغییرات مجاز در کد محصول (به‌جز این‌ها، `energybaseline/` را دست نزن)

1. `energybaseline/core/report.py` — **فقط** حذف شاخهٔ WeasyPrint و باقی‌گذاشتن مسیر جایگزین فعلی؛
   در `deviations.md` بنویس: «interim renderer; the ReportLab renderer lands in phase 8».
2. `energybaseline/config/*` — **فقط** اگر لازم بود پایتون‌ریکوئرمنت/متادیتا.
3. تست موجود را فقط در مرزهای D0.8 تغییر بده.

`launcher.py`، `db/`، `core/pipeline.py`، `core/models.py` و بقیه **در این فاز دست‌نخورده‌اند** (فاز ۱).

---

## قالب گزارش پایان فاز (`artifacts/phase0/REPORT.md` + خلاصه در چت)

1. کامیت SHA کامل + `git diff --stat`
2. جدول: `ID` (D0.x و AC/IDهای پوشش‌داده‌شده) → فایل/تابع → تست
3. فرمان‌های اجراشده + مسیر خروجی خام
4. `pytest -q` summary + پوشش `core/` (عدد، نه توصیف)
5. آرتیفکت‌ها + SHA-256
6. شواهد ویندوز (لاگ CI `windows-latest` با لینک اجرا)
7. delta در deviations / assumptions / not_implemented
8. آنچه **اثبات نشد** و چرا
9. ریسک‌های باز و اثرشان روی فاز ۱

---

## گیت پذیرش G0 (همه باید exit 0)

```bash
python -m pytest -q
python -m pytest -q tests/gates
python -m ruff check . && python -m ruff format --check .
python -m pyflakes energybaseline
python scripts/gates/guard_web_free.py
python scripts/gates/guard_env_readers.py
python scripts/gates/guard_tests_integrity.py
python scripts/gen_matrix.py --check
python -m pytest -q --cov=energybaseline/core --cov-fail-under=60
git ls-files | grep -Ei "package.json|tsconfig|\.tsx?$|next\.config|drizzle|eslint|postcss"   # خالی
git ls-files | grep -E "__pycache__|\.pyc$"                                                 # خالی
grep -rn "weasyprint" energybaseline scripts requirements*.txt requirements*.in REQUIREMENTS*  # خالی
```

---

## رد فوری (Auto-Fail)

- دروازهٔ بدون تست منفی · ماتریس با `done` و `test_ids` خالی · حل تست با `skip`/`xfail`/حذف assert
- باقی‌ماندن یک ارجاع `weasyprint` در کد یا manifest · باقی‌ماندن فایل Next/React در git
- lock بدون hash یا با `>=` · تست mock-محور برای شناسهٔ `[M]` · افزودن فیچر خارج از دامنه
- ادعای «سبز است» بدون خروجی خام · تغییر `LICENSE` یا حذف `artifacts/review_baseline/`

---

## در پایان

گزارش را بده و **متوقف شو**. اگر چیزی در محیط تو غیرممکن است (مثلاً نبود `windows-latest` یا نبود torch)،
همان را صریح بنویس و شاهد بگذار — پنهان‌کاری گران‌تر از نقص است. سپس منتظر رأی ناظر بمان؛
فاز ۱ (Lifecycle & Skeleton) بدون PASS فاز ۰ باز نمی‌شود.
