> ⚠️ **این فایل ترجمهٔ فارسی برای مالک محصول است. نسخهٔ انگلیسی مرجع است:**
> `docs/EXECUTION_PLAN.md` · `docs/DECISIONS.md` · `docs/PHASE0_BRIEF.md` · `docs/PROMPT_PHASE0_EXECUTOR.md`
> در صورت اختلاف، نسخهٔ انگلیسی معتبر است (G-7).

# برنامهٔ اجرایی فازبندی‌شده — EnergyBaseline v6 (PySide6 / Python-only / Offline Windows)

> **جایگاه این سند:** سند حاکمیت داخلی بین *ناظر* (نویسندهٔ این سند) و *عامل اجرا*.
> این سند **محصول** نیست: مستندات محصول (`docs/USER_GUIDE.md`، `docs/requirements_matrix.md`،
> `docs/deviations.md`، `docs/assumptions.md`، `docs/not_implemented.md`، `SECURITY.md`) طبق G-7 باید **انگلیسی** باشند.
> زبان گفت‌وگو و گزارش‌های داوری فارسی است.

شاخه: `arena/af928d3c-energybaseline` · کامیت پایه: `2c92f2a` · تاریخ بازبینی: 2026-11-12
**وضعیت تصمیم‌ها:** ✅ RATIFIED (D1=B, D2=B+A, D3=A, D4=B, D5=A) — `docs/DECISIONS_FA.md`
**وضعیت فازها:** فاز ۰ **باز**؛ فاز ۱ تا PASS فاز ۰ باز نمی‌شود.

---

## 0. حکم داوری در یک نگاه (Executive Verdict)

**نتیجه: REJECT برای شروع M-1 به‌شکل «ادامهٔ کدنویسی روی وضع موجود».**

شاخهٔ فعلی یک **نمونهٔ اولیهٔ پایتونی کارآمد** است، نه یک محصول مطابق پرامپت. نکات مثبت واقعی وجود دارد
(موتور AUTO با ثبت signal/rule، resolver واحد DB، تفکیک `core/`، گزارش HTML/Excel تولیدشده، تست‌های
پایپ‌لاین سبز). اما فاصلهٔ آن با آستانه‌های **release-blocking** پرامپت، ساختاری است نه سطحی:

| # | شکاف مسدودکنندهٔ انتشار | شناسه‌های نقض‌شده | شاهد |
|---|---|---|---|
| 1 | هیچ **ProcessPoolExecutor / Job Object / freeze_support** وجود ندارد؛ محاسبات در `QThread` داخل پروسهٔ UI اجرا می‌شوند و `store.save_run` از همان thread در SQLite **می‌نویسد** | SCR-1, SCR-2, SHL-5, LIF-2, AC-03 | `grep -rn ProcessPoolExecutor\|win32job\|freeze_support` → ۰ نتیجه؛ `gui/pages_setup.py::AnalysisWorker(QThread)` |
| 2 | **خاموشی تمیز و aboutToQuit/closeEvent** وجود ندارد | G-8, LIF-1 | `grep -rn aboutToQuit\|closeEvent` → ۰ نتیجه |
| 3 | **VSG و TCP به‌کل وجود ندارند** (نه UI، نه مدل داده، نه کش نتیجه، نه provenance) | §10, §13, PIPE-1..3, AC-23, AC-24 | `grep -rni "vsg\|tcp"` → ۰ نتیجه |
| 4 | **Single-instance / path-guard / command registry / i18n / QSettings** وجود ندارند | SHL-1, SHL-3, SHL-4, SHL-6, G-7, AC-04 | `grep -rn QLocalServer\|QTranslator\|QSettings` → ۰ نتیجه؛ ماژول `energybaseline/io/paths.py` موجود نیست |
| 5 | **`.ebproj`، autosave، checkpoint، backup/restore (online API)، retention** وجود ندارند؛ آرتیفکت‌ها `joblib` بدون بررسی SHA-256 بارگذاری می‌شوند | PER-1..6, AC-18 | `grep -rn ebproj\|zipfile\|checkpoint` → ۰ نتیجه؛ `core/store.py::load_run` → `joblib.load` بی‌اعتبارسنجی |
| 6 | **UQ/Conformal، Drift، Causal M&V** وجود ندارند؛ بازهٔ اطمینان **هاردکد 0.95** با کوانتایل باقی‌مانده | UQ-1..5, DR-1..5, T14, AC-15, AC-16, AC-29 | `core/pipeline.py:328` `"level": 0.95`؛ `grep -ni "mapie\|psi\|ks_2samp\|wasserstein\|cusum"` → ۰ نتیجه |
| 7 | **REP-1/RPT-* برقرار نیست:** مدل گزارش JSON واحد نیست؛ PDF با **WeasyPrint** (صریحاً ممنوع) و Jinja/HTML ساخته می‌شود؛ روایت با **artifact ID** و تست عدد-به-آرتیفکت وجود ندارد؛ Glossary/How-to-read/report diff نیست | REP-1..5, RPT-1..7, G-9, G-11, AC-17, AC-28 | `core/report.py:1` و `requirements.txt:29` (weasyprint) |
| 8 | **آلودگی استک وب:** `package.json`, `next.config.ts`, `src/app/page.tsx`, `drizzle`, `eslint`, `tsconfig`, `postcss` در ریشهٔ مخزن | G-1, G-2, AC-01, AC-35 | `ls package.json next.config.ts src/app/page.tsx` |
| 9 | **نبود زیرساخت کیفیت:** بدون CI، بدون lock، بدون LICENSE/.gitignore، بدون `hypothesis`/`pytest-qt`، بدون مرجع R، بدون SBOM/threat model؛ ۱۹ تست برای ۷٬۷۳۲ خط کد | §27, AC-21, AC-22, PKG-6 | `ls .github` → موجود نیست؛ `requirements.txt` فقط `>=` |
| 10 | **Inclusion-state / quantity-type / role** (DAT-1) اصلاً مدل نشده‌اند؛ تجمیع با **regex نام ستون** انتخاب می‌شود | DAT-1, DAT-2, DAT-3, AC-06 | `core/temporal.py::default_agg_for` + `ENERGY_NAME` |
| 11 | جدول‌ها **`QTableWidget` با سقف ۳۰۰۰ ردیف** هستند، نه `QAbstractTableModel` + `fetchMore` | IPC-7, UX-5, AC-19 | `gui/widgets.py::fill_table(max_rows=3000)` |
| 12 | **DB-2/DB-3/DB-4/DB-5/DB-8/DB-10** نیمه‌کاره: `launcher.py` مقدار `DATABASE_URL` را **بازنویسی** می‌کند (env کاربر بی‌اثر می‌شود)؛ `APP_HOME` = `%LOCALAPPDATA%` نیست؛ `portable.flag` نیست؛ نردبان fallback (`%TEMP%` → in-memory) نیست؛ backup پیش از migration نیست؛ `busy_timeout`/`synchronous` ست نمی‌شود | DB-2..DB-10, AC-05 | `launcher.py:60`، `config/settings.py::get_base_dir`، `db/session.py::_make_engine` |

**پیامد فرآیندی:** ترتیب فازها باید از پرامپت (§3) تبعیت کند **به‌علاوهٔ یک فاز صفر** که بدهی‌های ساختاری و
زیرساخت شواهد را می‌بندد. از این پس **هیچ فازی بدون PASS فاز قبل شروع نمی‌شود** و هیچ PASSی بدون
«بستهٔ شواهد ماشین‌خوان» صادر نمی‌شود.

---

## 1. روش بازبینی و دامنهٔ اعتبار شواهد

### 1.1 کاری که ناظر انجام داد (قابل بازتولید)

```bash
# محیط ایزوله (پایتون 3.11.2 سیستم + venv)
python3 -m venv /tmp/ebv
/tmp/ebv/bin/pip install numpy pandas scipy statsmodels scikit-learn sqlalchemy alembic matplotlib \
    joblib openpyxl jinja2 keyring cryptography pillow pytest pytest-cov pyflakes \
    PySide6 pytest-qt mapie arch sqlglot reportlab
python -m pytest -q                      # اجرای واقعی تست‌ها
```

هارنس GUI بی‌سر (فقط برای **منطق ویجت**، هرگز برای رفتار بومی ویندوز):
stubهای *link-only* برای `libGL/libEGL/libxkbcommon/libdbus` + `QT_QPA_PLATFORM=offscreen` + `LD_LIBRARY_PATH`.
این هارنس در بستهٔ شواهد محصول **مجاز نیست**؛ فقط ابزار بازبینی است (پیوست C).

### 1.2 سه سطح شاهد

| سطح | تعریف | برای چه شناسه‌هایی |
|---|---|---|
| **E1** | خروجی خام دستور در `artifacts/phaseX/*.txt` + SHA-256 کامیت | همه |
| **E2** | JSON ماشین‌خوان تولیدشده توسط خود محصول (مثلاً `system.health`, `system.sbom`, AUTO-log hash) | DB-*, AUTO-*, TCP-* |
| **E3** | شاهد از **ماشین واقعی Windows 10 22H2 / Windows 11 x64**: لاگ، اسکرین‌شات، زمان‌سنجی، SHA-256 باینری | LIF-1/2, SCR-1/6, PKG-1..6, UX-* بومی، Authenticode |

**قاعدهٔ سخت:** هر شناسهٔ `[M]` که فقط با E3 اثبات‌شدنی است و E3 ندارد ⇒ **FAIL**، حتی اگر کد «به‌نظر درست» باشد.
در این سندباکس نه Job Object، نه NSIS، نه Authenticode و نه PyInstaller ویندوزی قابل اجراست؛ بنابراین
**ادعای شفاهی ممنوع** است.

---

## 2. انحرافات پرامپت در برابر کتابخانه‌های واقعاً نصب‌شده (بخش ۰ «library-verification»)

این‌ها را ناظر **اجرا کرد**، نه فرض. عامل اجرا باید دقیقاً همین‌ها را در `docs/deviations.md` ثبت کند:

| ادعای پرامپت | واقعیت در نسخهٔ نصب‌شده | اقدام اجباری |
|---|---|---|
| `statsmodels.imputation.mice.MICEData(df).mcar_test()` | **وجود ندارد** (statsmodels 0.15.0) → `AttributeError` | پیاده‌سازی **بومی Little's MCAR** + تست مرجع R (`MissMech::mcartest`) |
| `statsmodels.tsa.stattools.acorr_ljungbox` | در `statsmodels.stats.diagnostic.acorr_ljungbox` | import اصلاح و در deviations ثبت شود |
| `grangercausalitytests(..., verbose=False)` | آرگومان `verbose` حذف شده (0.15) → `TypeError` | حذف آرگومان + تست سازگاری نسخه |
| — (پرامپت چیزی نمی‌گوید) | statsmodels 0.15 برای `adfuller`/`kpss`/`acorr_lm` هشدار `result_object` می‌دهد و **نوع بازگشتی عوض می‌شود** | **پین دقیق نسخه‌ها** + `result_object=True/False` صریح در کد |
| MAPIE v1 با ۵ کلاس + `TimeSeriesRegressor(method="enbpi"/"aci")` | **درست است** (mapie 1.6.0): هر ۵ کلاس موجود؛ `method` پیش‌فرض `'enbpi'` | در requirements اضافه شود (فعلاً **نیست**) |
| `arch` برای PP/DF-GLS/Zivot–Andrews | **درست است** (arch 9.0.0) | در requirements اضافه شود (فعلاً **نیست**) |
| `sqlglot.parse` (نه `parse_one`) | **درست است** (30.31.0): `parse("SELECT …; DROP …")` دو statement با ریشه‌های `Select`,`Drop` می‌دهد | جایگزینی `validate_select` regex با guard مبتنی بر AST |
| reportlab برای PDF | **درست است** (5.1.0، `reportlab.pdfgen.canvas` موجود) | نصب + حذف WeasyPrint |
| `scipy.stats.brunnermunzel` | موجود، اما `midrank` منسوخ → `variant` (SciPy 1.19) | استفاده از `variant` |
| `shapiro` برای n>5000، `kpss` interpolation | هشدار/محدودیت واقعی | زیرنمونه‌گیری مستند (TC-8) |

**نتیجه:** فایل `requirements.txt` فعلی (۳۰ قید `>=`، صفر پین) با AUTO-5 و PKG-6 در تضاد است؛
`numpy 2.4 / pandas 3.1 / statsmodels 0.15` امروز نصب می‌شوند و فردا چیز دیگری. **پین دقیق + lock اجباری است.**

---

## 3. پنج تصمیم مسدودکننده (تفصیل کامل در `docs/DECISIONS_FA.md`)

پرامپت حداکثر ۵ پرسش را مجاز کرده است. پاسخ نداده ⇒ پیش‌فرض زیر اعمال و در `docs/assumptions.md` ثبت می‌شود.
گزینه‌ها، هزینه‌ها، ریسک‌ها و اثر هر انتخاب بر ماتریس انطباق در **`docs/DECISIONS_FA.md`** آمده است؛ جدول زیر فقط خلاصه است.

| # | پرسش | ✅ تصمیم تصویب‌شده (2026-11-12) |
|---|---|---|
| D1 | گواهی **Authenticode** | **B** — تعویق + اسکریپت امضای آماده/تست‌شده؛ `PKG-3 → [S]`، trigger اجباری پیش از توزیع عمومی |
| D2 | **ماشین مرجع E3** | **B+A** — CI `windows-latest` کار اجباری + ماشین فیزیکی مرجع `8-core/16GB/NVMe/Win11 23H2` برای اعداد رسمی |
| D3 | **استک NN** | **A** — Full پیش‌فرض (torch CPU پین) + بیلد Lite اختیاری؛ AC-10 روی Full شاهد می‌گیرد |
| D4 | **مرجع R** | **B** — JSON نسخه‌دار با هش اسکریپت + جاب شبانهٔ R که در اختلاف issue باز می‌کند |
| D5 | **موتور PDF** | **A** — فقط **ReportLab**؛ WeasyPrint/Qt-PDF/matplotlib-PDF حذف و با guard test ممنوع |

جزئیات، گزینه‌های رد‌شده و «اگر انتخاب دیگری می‌کردی چه می‌شد»: `docs/DECISIONS_FA.md` (وضعیت: RATIFIED).

---

## 4. قواعد تعامل: چگونه گزارش بده، چگونه داوری می‌شود

### 4.1 قواعد آهنین عامل اجرا

1. فقط روی همین شاخه کار کن؛ `main` را دست نزن؛ force-push ممنوع.
2. **تست را با باگ هم‌تراز نکن.** تغییر/حذف تست موجود برای سبز شدن = **FAIL آنی**.
3. هر شناسهٔ `[M]` باید **تست خودکار** یا **چک‌لیست دستی مستندشده** داشته باشد؛ «تست دستی» بدون فایل شاهد = نداشته.
4. هر ادعا در گزارش، **مسیر آرتیفکت** می‌خواهد (فایل، SHA-256، خط فرمان).
5. عدد، متریک، p-value و ترجمهٔ آماری **از آرتیفکت** می‌آید (G-9)؛ هیچ عددی در قالب گزارش هاردکد نمی‌شود.
6. هیچ وابستگی وب/JS/Electron/Tauri/HTTP اضافه نکن؛ هیچ `QWebEngineView` و هیچ `weasyprint`.
7. حذف/تغییر داده فقط با Action Policy و لاگ (G-6).
8. اگر چیزی را نتوانستی اثبات کنی، **همان‌طور بنویس** («اثبات نشد») — پنهان‌کاری گران‌تر از نقص است.

### 4.2 قالب اجباری گزارش پایان فاز

فایل `artifacts/phaseX/REPORT.md` + خلاصه در چت:

```
1) شناسه فاز و کامیت (SHA کامل) + git diff --stat
2) نگاشت شناسه → فایل/تابع → تست (جدول؛ هر ردیف یک ID)
3) فرمان‌های اجراشده و خروجی خام (مسیر فایل‌ها)
4) pytest -q summary + coverage برای ماژول‌های لمس‌شده
5) آرتیفکت‌ها: گزارش/داشبورد/لاگ/DB/بستهٔ نصب + SHA-256
6) E3 (ویندوز): لاگ/زمان‌سنجی/اسکرین‌شات + سخت‌افزار مرجع
7) delta در deviations / assumptions / not_implemented
8) آنچه اثبات نشد و چرا  +  9) ریسک‌های باز و اثرشان روی فاز بعد
```

### 4.3 قالب رأی ناظر

- **PASS** — همهٔ گیت‌ها با E1/E2/E3 سبز.
- **PASS-WITH-CONDITIONS** — فقط برای `[S]/[O]`؛ شرط‌ها شماره‌دار و دارای مهلت (باید در فاز بعد بسته شوند).
- **FAIL + فهرست نقص** — هر نقص: `D-xx`, شناسهٔ نقض‌شده، شاهد، اصلاح لازم، تستی که باید اضافه شود.
- حلقهٔ اصلاح: **حداکثر ۳ دور**؛ دور چهارم ⇒ توقف و ارجاع به کاربر با گزارش ریسک.

### 4.4 محرک‌های FAIL آنی (Auto-Fail)

ادعای تست بدون خروجی خام · تستِ mock-محور برای شناسهٔ `[M]` · دست‌کاری تست · عدد هاردکد در قالب گزارش ·
ادعای ویندوزی بدون E3 · افزودن استک وب · تغییر بی‌لاگ داده · `eval`/`getattr` پویا در dispatcher ·
بارگذاری pickle بدون SHA-256.

### 4.5 تعریف «PASS» برای هر فاز (DoD مشترک)

پس از هر فاز، همهٔ موارد زیر باید هم‌زمان برقرار باشند وگرنه فاز تمام‌شده نیست:
`pytest -q` سبز · `pyflakes` پاک · `docs/requirements_matrix.md` به‌روز (بدون ردیف «done» بدون تست) ·
`docs/deviations.md` به‌روز · آرتیفکت قابل اجرا (نه mock) · بدون رگرسیون در تست‌های فازهای قبل.

---

## 5. فازبندی

> ترتیب، **وابستگی فنی** را رعایت می‌کند: بدون lifecycle/process-pool و بدون VSG/TCP، هر کد مدل‌سازی بعدی
> باید دوباره نوشته شود. برآورد زمان «مهندس-هفته» است و با اجرای تک‌نفره/موازی‌محور تغییر می‌کند.

| فاز | عنوان | وابسته به | برآورد |
|---|---|---|---|
| 0 | Truth & Rails (بدهی ساختاری + زیرساخت شواهد) | — | 1–1.5 |
| 1 | Lifecycle & Skeleton (M-1 بازسازی) | 0 | 3–4 |
| 2 | Data / Time / Column Setup | 1 | 2–3 |
| 3 | VSG + TCP + Cache (قلب انطباق) | 2 | 4–5 |
| 4 | Test Catalog T1–T14 | 3 | 6–8 |
| 5 | EDA / Mining F1–F10 + کارایی داده بزرگ | 3,4 | 4–5 |
| 6 | Modeling / Validation / Scoring / Top-5 / Lock | 3,4,5 | 4–6 |
| 7 | UQ + Drift + Prediction & Monitoring + Causal M&V | 6 | 4–5 |
| 8 | Reporting & Audit depth | 4,5,6,7 | 3–4 |
| 9 | Persistence / UX / Security / Packaging / Release | 1,8 | 3–4 |

**مسیر بحرانی:** 0 → 1 → 3 → 4 → 6 → 7 → 8 → 9. فاز ۵ می‌تواند پس از ۴ موازی شود، ولی Top-5 بدون
EDA/Mining «کامل» نیست (AC-27).

---

### فاز ۰ — Truth & Rails (اجباری؛ پیش از هر کد محصول)

**هدف:** مخزن را از «نمونهٔ اولیه» به «کارگاه قابل حسابرسی» تبدیل کن؛ هیچ فیچر محصولی در این فاز اضافه نشود.

**دامنه:** G-1, G-2, G-7, §27, AC-21, AC-22, PKG-6

**خروجی‌های اجباری**
1. حذف کامل `package.json`, `next.config.ts`, `next-env.d.ts`, `src/`, `drizzle.config.json`, `eslint.config.mjs`,
   `tsconfig.json`, `postcss.config.mjs`, `node_modules` و هر اشارهٔ وب در README/bat.
2. `requirements.txt` با **پین دقیق** + `requirements.lock` (hash-locked) + `constraints.txt`؛ افزودن
   `joblib` (اکنون import می‌شود ولی اعلام نشده)، `mapie==1.6.0`, `arch==9.0.0`, `sqlglot==30.31.0`,
   `reportlab==5.1.0`, `pydantic>=2`, `pywin32` (ویندوز), `pytest-qt`, `hypothesis`, `cyclonedx-bom`, `pip-audit`.
   حذف `weasyprint`.
3. `.gitignore` (data/, logs/, runs/, models/, dist/, build/, *.sqlite, artifacts/)، `LICENSE`، `CHANGELOG.md`.
4. CI: `.github/workflows/ci.yml` روی `windows-latest` (+ `ubuntu-latest` برای تست‌های غیر-GUI):
   نصب از lock → `pyflakes` → `pytest -q` → `pytest-cov` → `pip-audit` → `cyclonedx-py` → آپلود artifacts.
5. اسکریپت‌های دروازه (تعریف دقیق، برای اینکه دروازه با خودش تضاد نداشته باشد):
   - `scripts/gates/guard_web_free.py` — **AST scan** روی همهٔ `*.py` برای `import/from` ممنوع
     (`weasyprint`, `PySide6.QtWebEngineWidgets`, `flask`, `fastapi`, `uvicorn`, `http.server`, `electron`-bridge)
     + اسکن `requirements*.txt`/`requirements.lock` + رد حضور `package.json`/`src/*.tsx`/`next.config.*` در فایل‌های git.
     **دامنهٔ استثنا (مستند در docstring):** `docs/EXECUTION_PLAN_FA.md`, `docs/DECISIONS_FA.md`, `artifacts/**`
     فقط «واژه» را نقل می‌کنند و کد اجرایی نیستند؛ آن‌ها اسکن نمی‌شوند. **متن مستندات ≠ استفادهٔ کتابخانه.**
   - `scripts/gates/guard_env_readers.py` — فقط `energybaseline/config/settings.py` مجاز به خواندن
     `DATABASE_URL/POSTGRES_URL/DB_CONNECTION_STRING/DB_URI/EB_DATABASE_URL` است (بقیه: خطا).
   - `scripts/gates/guard_tests_integrity.py` — هش SHA-256 تست‌های پایه در `tests/.baseline.sha256`؛
     تغییر/حذف تست موجود بدون تأیید صریح ناظر ⇒ خطا.
6. `docs/requirements_matrix.md` مولد-محور (`scripts/gen_matrix.py`) با **همهٔ** AC-01..AC-35 و IDهای
   T1..T14، DAT-*, VSG-*, TCP-*, UQ-*, DR-*, PER-*, REP-*, RPT-*, UX-*؛ وضعیت اولیه `not_started`.
7. ثبت ۱۰ شکاف بخش ۰ این سند در `docs/deviations.md` + جدول انحرافات کتابخانه‌ای §2 همین سند.
8. رفع دو نقص تست موجود:
   - `samples/daily_plant_multitarget.xlsx` یا commit شود یا تست به generator وابسته شود (بدون وابستگی به آرتیفکت commit‌نشده).
   - تعارض `test_nn_explicitly_rejected_for_small_data` با رفتار واقعی (ثبت `LSTM (PyTorch)` در حالت
     «not evaluated» وقتی torch نیست) با **تصمیم قراردادی صریح** حل شود، نه با شل‌کردن assert.
9. `docs/SUPPORT_MATRIX.md`: نسخهٔ پایتون پین‌شده (**3.11.9** طبق S1)، ویندوز هدف، و **سخت‌افزار مرجع رسمی**
   `8-core x64 · 16 GB · NVMe · Windows 11 23H2 · 1920×1080@100%` (تصویب D2).
10. **پیامدهای D1..D5 در فاز ۰:**
   - `scripts/sign/sign_artifacts.ps1` + تست CI با گواهی self-signed (`signtool sign` → `signtool verify /pa`) — D1.
   - `scripts/build_exe.py --profile full|lite` (پیش‌فرض full) — D3.
   - `tests/reference/README.md` + `tests/test_reference_staleness.py` (هش اسکریپت R ↔ JSON) — D4.
   - حذف کامل `weasyprint` از `requirements.txt`, `core/report.py`, `README.md`, `docs/USER_GUIDE.md`, `scripts/build_exe.py` — D5.
   - `ruff` (lint+format) در CI — S4؛ آستانهٔ پوشش `--cov-fail-under=60` روی `core/` — S5.

**گیت پذیرش (G0)**
```bash
python -m pytest -q                      # exit 0
python -m pyflakes energybaseline        # exit 0
python scripts/gates/guard_web_free.py   # exit 0 (AST-based; docs/artifacts excluded by design)
python scripts/gates/guard_tests_integrity.py   # exit 0
python scripts/gates/guard_env_readers.py# exit 0
python scripts/gen_matrix.py --check     # هر AC/T-ID موجود، هیچ status=done بدون test_id
pip install -r requirements.lock --require-hashes --dry-run   # exit 0
git ls-files | grep -Ei "package.json|tsconfig|\.tsx?$|next.config" ; test $? -eq 1
```

**ناظر چگونه رد می‌کند:** اگر ماتریس «تولید دستی» باشد و با کد drift کند؛ اگر lock از `>=` ساخته شده باشد؛
اگر `src/` فقط «خالی» شده ولی در git history/ارجاعات مانده باشد؛ اگر تست NN با تغییر assert «حل» شده باشد.

---

### فاز ۱ — Lifecycle & Skeleton (بازسازی M-1)

**هدف:** ستون فقرات غیرقابل‌عبور: UI هیچ‌وقت محاسبه/IO سنگین نمی‌کند؛ ویندوز هیچ‌وقت یتیم رها نمی‌کند؛ DB هرگز boot را نمی‌شکند.

**دامنه:** §6 (SHL-1..9, IPC-1..7, LIF-1,2,4,5), §8 (DB-1..11), SCR-1..4, PKG-1,2,6, AC-03, AC-04, AC-05

**تغییر ساختاری کلیدی (R-1)** — این سخت‌ترین جابه‌جایی کل پروژه است و باید **اول** انجام شود:
`run_analysis` از `QThread` به worker پروسه‌ای منتقل می‌شود. الزامات:
- `ProcessPoolExecutor(max_workers=min(CPU//2, tasks))` + `multiprocessing.freeze_support()` + `spawn`.
- پایپ‌لاین worker **نباید** SQLite را باز کند؛ بازگشت نتیجه از طریق `multiprocessing.Queue`؛
  payload > 1 MiB به‌صورت فایل `{path, sha256, rows}` زیر `APP_HOME/cache/`.
- پیشرفت/کنسل با token در مرزهای stage و داخل epoch/trial/fold/test؛ worker لجباز پس از ۱۰ s kill شود.
- `store.save_run` فقط در **پروسهٔ اصلی** (SHL-5).

**خروجی‌های اجباری**
1. `energybaseline/app/registry.py`: رجیستری دستی (dict صریح) از **دقیقاً** دستورات SCR-7؛ dispatcher اعتبارسنجی
   ID، سقف حجم payload، رد ID ناشناس؛ بدون `eval`/`getattr` پویا.
2. `energybaseline/io/paths.py`: path guard (اجازهٔ APP_HOME، مسیرهای انتخابی `QFileDialog`، temp؛ رد `..`, UNC، symlink).
3. `energybaseline/app/pool.py`: pool + handshake + readiness timeout (10 s warm / 30 s first) + protocol-version.
4. `energybaseline/app/lifecycle.py`: `QApplication.aboutToQuit` → cancel → join 5 s → hard stop → checkpoint → DB close؛
   `closeEvent` متصل به همان مسیر؛ Windows Job Object با `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`.
5. `energybaseline/app/single_instance.py`: `QLocalServer` داخل APP_HOME (named pipe) + فعال‌سازی پنجرهٔ موجود.
6. DB: `get_database_url()` تنها خواننده؛ `APP_HOME` = `%LOCALAPPDATA%\EnergyBaseline` یا پوشهٔ exe با `portable.flag`؛
   نردبان fallback: URL پیکربندی‌شده → APP_HOME → `%TEMP%` (بنر «data may be lost») → `sqlite:///:memory:` (بنر قرمز)؛
   probe ۳ ثانیه‌ای برای غیر-SQLite + audit + بنر؛ `EB_DATABASE_URL` با اولویت [S]؛ pragmas کامل
   (`WAL`, `foreign_keys=ON`, `busy_timeout=5000`, `synchronous=NORMAL`)؛ **حذف بازنویسی env در `launcher.py`**؛
   backup پیش از Alembic با online backup API + restore در شکست؛ `system.status`/`system.health`/`system.sbom`.
7. Status page واقعی: pool state، DB `{ok,url,fallback,writable}`، نسخه‌ها، مسیرها، «Where is my data stored?».
8. PyInstaller **onedir** + installer NSIS/Inno (per-user، `%LOCALAPPDATA%`) + `.lnk` با آیکون + SBOM CycloneDX + notices.

**گیت پذیرش (G1)**
```bash
pytest -q tests/test_lifecycle.py tests/test_pool.py tests/test_db_fallback.py tests/test_registry.py
# E1/E2 (این سندباکس، قابل اجرا):
python -m pytest -q                    # + تست‌های: no-env, whitespace-env, unreachable-PG, read-only APP_HOME,
                                       #   %TEMP% fallback, in-memory fallback, sentinel self-test, guard: ممنوعیت eval
# E3 (ویندوز، الزامی):
powershell -File scripts/evidence/lif_orphans.ps1     # kill main → 0 orphans ≤ 5 s (JSON: pids before/after, kill latency)
powershell -File scripts/evidence/startup_time.ps1    # 5 cold + 5 warm runs → JSON {median, p95}; هدف ≤ 3 s، سقف 10 s
powershell -File scripts/evidence/installer_smoke.ps1 # نصب سکوت، association، اجرا، حذف با حفظ داده
powershell -File scripts/evidence/dpi_matrix.ps1      # 100/150/200 % scaling
runs-on: windows-latest                                 # همین تست‌ها در CI ویندوزی (D2)
# اندازهٔ نصب و idle RAM اندازه‌گیری و منتشر شود (SCR-6)
```

**ناظر چگونه رد می‌کند:** هر محاسبهٔ سنگین در thread UI باقی مانده باشد · `store.*` از worker صدا زده شود ·
`aboutToQuit` فقط لاگ بزند و join نکند · Job Object فقط ساخته شود و assign نشود · بنر fallback در UI نباشد ·
test_id برای DB-11 موجود نباشد · نصب‌کننده در حالت per-user تست نشده باشد.

---

### فاز ۲ — Data / Time / Column Setup

**دامنه:** §9 (DAT-1..8), MT-2 ورودی‌ها, UX-1 بخش داده, AC-06

**خروجی‌های اجباری**
1. مدل نقش/نوع/واحد/دسترس‌پذیری **در سطح داده** (نه در UI): `role ∈ {time,target,predictor,static_factor,group,weight,ignore}`،
   `quantity ∈ {flow,stock,rate,quality,index}`، `unit`, `availability ∈ {known_in_advance,observed_only}`؛ ذخیره در `.ebproj`/DB.
2. تجمیع بر پایهٔ **quantity type** (flow→sum، stock→last، rate→time-weighted، quality→mean/median، index→mean) و
   **حذف `default_agg_for` نام‌محور** به‌عنوان تصمیم‌گیر (فقط به‌عنوان پیشنهاد در ویزارد، با لاگ).
3. DATA_REFUSED برای دادهٔ درشت‌تر + پیام توضیحی؛ هرگز disaggregation.
4. DST صریح: روزهای ۲۳/۲۵ ساعته، ساعت تکراری/غایب، UTC ذخیره/محلی نمایش، policy لاگ‌شده.
5. Non-Routine Events table (DAT-7) + Outlier Action Policy (`flag_only|ask_user|auto_apply`) با ثبت
   rule/rows/training-set delta؛ هدف هرگز در evaluation set تغییر نکند.
6. ingest: CSV/Excel/Parquet، تشخیص encoding/اعشار، خواندن stream + کش Parquet، عدم بارگذاری کامل فایل بزرگ در حافظه.
7. Column Setup UI با badge منبع پیشنهاد («suggestion only») و بدون تصمیم خودکار.

**گیت (G2):** تست‌های DST (hypothesis، سری‌های ساعتی حول تغییر ساعت در ۴ منطقه)، تست refusal، تست
quantity-based aggregation (ماتریس ۵×۴)، تست Action Policy (هر سه حالت)، تست NRE، تست Parquet/encoding،
تست «no fabrication» (DAT-5): هیچ ردیف ساختگی، هیچ imputation هدف در evaluation set.

**رد می‌شود اگر:** تجمیع هنوز از regex نام بیاید · نقش‌ها در UI نگه داشته شوند و در پروژه ذخیره نشوند ·
تست DST فقط با یک منطقهٔ بدون DST اجرا شود.

---

### فاز ۳ — VSG + TCP + Cache (قلب انطباق محصول)

**دامنه:** §10 (VSG-1..9), §12 (AUTO-1..6), §13 (TCP-1..14), PIPE-1..3, AC-08, AC-23, AC-24, AC-33

**چرا این فاز پیش از هر تست آماری است:** بدون VSG/TCP، هر تستی که نوشته شود باید دوباره سیم‌کشی شود؛
و PIPE-1 «هیچ تست استنباطی قبل از عبور از گیت» است.

**خروجی‌های اجباری**
1. مدل VSG: per-target × per-variable با ۴ حالت، پیش‌فرض AUTO، رزولوشن AUTO با signal/rule،
   pre-test profile (missing%, unique, variance, constant/near-constant, leakage suspicion, availability, mini-histogram)،
   Impact Preview (تعداد متغیر/تست/خانوادهٔ چندگانه/زمان تخمینی Fast vs Full).
2. Bulk actions، preset JSON، copy بین targets، descriptive profile برای `exclude` (VSG-9 [S]).
3. Integrity checks **غیرقابل خاموش‌کردن**: leakage (MT-2)، ترتیب زمانی، DST/duplicate، target هرگز predictor؛
   نمایش conflict در صورت `force_include` متضاد.
4. provenance: `source ∈ {AUTO,USER_OVERRIDE}`، `phase ∈ {pre_test,post_test}`، برچسب `post_hoc` و بازتاب در Limitations.
5. TCP کامل: α (۰٫۰۰۱–۰٫۲۰ + هشدار >۰٫۱۰ + presets)، confidence (80/90/95/99/Custom 50–99.9)،
   correction (none/Bonferroni/Holm/BH/BY) با **هر دو p خام و تصحیح‌شده**، family definition، تصمیم
   (`p_only|p_and_effect_size|ci_based|equivalence`)، per-test panel (alternative, lags spec, deterministic terms,
   HAC kernel/bandwidth, min n, effect-size thresholds, TOST margin, bootstrap config)،
   test tree با وابستگی/هزینه/«چرا نگه داری»، scope matrix (variables × families)، expected-sign editor،
   gate-role/action-policy editor، guard rails (TCP-11)، pre-registration lock با hash (TCP-9).
6. **کش نتیجه** با کلید `(data_hash, variable_set, test_id, parameters, seed, library_versions)` (TCP-10)
   و **what-if بدون محاسبهٔ مجدد** (TCP-6) + جدول decision-sensitivity (TCP-7).
7. AUTO Log: هش قطعیِ AUTO-log، مرتب‌سازی per-target (MT-3)، inspector read-only، بخش در گزارش.

**گیت (G3)**
```bash
pytest -q tests/test_vsg.py tests/test_tcp.py tests/test_cache.py tests/test_auto_determinism.py
```
- cache-key invalidation: تغییر data/variable_set/params/seed/lib_versions ⇒ recompute تنها مراحل وابسته (VSG-8).
- what-if: تغییر α/correction/decision rule ⇒ **۰ فراخوانی موتور تست** (شمارندهٔ فراخوانی assert شود) ولی flipهای درست.
- fuzz (hypothesis) روی TCP/VSG: مقادیر خارج از بازه، ناسازگاری cross-parameter، presets خراب.
- اثبات غیرقابل‌خاموش‌شدن integrity: تلاش برای disable ⇒ خطای صریح + لاگ.
- هش AUTO-log برای دو اجرای یکسان با همان seed ⇒ برابر.
- **پوشش UI**: `pytest-qt` روی VSG/TCP screens (E3 روی ویندوز برای چیدمان/فوکوس).

**رد می‌شود اگر:** تست قبل از Accept شدن VSG/TCP اجرا شود · p تصحیح‌شده ذخیره نشود · what-if تست را دوباره اجرا کند ·
`post_hoc` فقط در UI دیده شود و در گزارش نیاید · «AUTO» در UI به مقدار هاردکد تبدیل شود.

---

### فاز ۴ — کاتالوگ تست T1–T14

**دامنه:** §14 (T1..T14, TC-1..8), §15 (CI-1..4), §27 (R reference), AC-09, AC-25, AC-26

**خروجی‌های اجباری**
1. موتور تست با **قرارداد یکسان**: هر تست ⇒ `{statistic, p_raw, p_adj, df, effect_size, ci, n_used, assumptions_checked,
   decision, interpretation, downstream_action, mode}` + ثبت نسخهٔ کتابخانه.
2. T1–T9 و T13 پیش از مدل؛ T10–T12 پس از مدل روی OOF؛ T14 فقط با ستون `group`.
3. TC-1..TC-8: declaration of assumptions، سوییچ خودکار به robust/non-parametric با لاگ، HAC/block-bootstrap برای
   سری‌های خودهمبسته با برچسب «naive» برای p کلاسیک، ماتریس تصمیم ایستایی، Chow/QA با p بوت‌استرپ،
   Little's MCAR **بومی**، flag-only برای influence، زیرنمونه‌گیری n بزرگ.
4. تست‌های سفارشی با **مرجع R**: Chow, Quandt–Andrews, Pettitt, Diebold–Mariano(HLN), Nemenyi, Little's MCAR,
   MCS؛ هر یک `tests/reference/<test>.R` + `tests/reference/<test>.json` + تست تلورانس مستند.
5. Type I error Monte Carlo زیر H0 (≈α با CI) + power برای اثر کاشته‌شده (T13).
6. CI-1/CI-2: هر برآورد کلیدی با CI در سطح اطمینان انتخابی + روش اعلام‌شده (block bootstrap Politis–White).

**گیت (G4)**
```bash
pytest -q tests/test_tests_catalog.py tests/test_r_reference.py tests/test_type1.py -m "not slow"
pytest -q -m slow tests/test_power.py     # شبانه/هفتگی در CI
```
پوشش: حداقل یک تست موفق + یک تست شکست برای **هر** ID از T1..T14؛ تست «disabled ⇒ not run (user)» در گزارش؛
تست نبود «observed power»؛ تست برچسب naive روی سری خودهمبسته.

**رد می‌شود اگر:** تست سفارشی بدون مرجع R باشد · p تصحیح‌شده به‌جای خام (یا برعکس) گزارش شود ·
effect size در تصمیم نادیده گرفته شود ولی `p_and_effect_size` ادعا شود · تعداد runs کمتر از تعداد IDها باشد.

---

### فاز ۵ — EDA / Mining F1–F10 + کارایی داده بزرگ

**دامنه:** §16, UX-5/UX-8 (جدول‌ها), IPC-7, AC-27

**خروجی‌های اجباری**
1. F1..F9 با نمودارها/جدول‌های مصرح؛ F8 با Kendall's W و consensus؛ F9 با فرمول شفاف و breakdown.
2. F10 [S] attach خودکار (`ydata-profiling`/`sweetviz`) با برچسب «descriptive, not inferential» و نصب اختیاری.
3. KMeans/GMM/Hierarchical/DBSCAN/HDBSCAN + validity + **bootstrap ARI**؛ regime labels **هرگز** ورودی مدل (MT-5).
4. جدول‌ها: `QAbstractTableModel` + `fetchMore` + `QSortFilterProxyModel` + copy/paste + CSV؛
   حذف `QTableWidget` با سقف ۳۰۰۰ ردیف از مسیرهای داده؛ downsampling LTTB/M4 برای نمودارهای بزرگ.
5. benchmark: ۱M ردیف ⇒ اسکرول ۶۰fps، حافظهٔ محدود (اندازه‌گیری و انتشار).

**گیت (G5):** اجرای F1–F9 روی dataset مصنوعی با اثر کاشته‌شده و بررسی بازیابی (tolerance مستند)؛
تست fetchMore روی ۱M ردیف؛ تست ARI روی خوشه‌بندی پایدار/ناپایدار؛ تست «labels به مدل نمی‌روند».

**رد می‌شود اگر:** خوشه‌بندی بدون validity/stability باشد · جدول‌های بزرگ هنوز QTableWidget باشند ·
مقایسهٔ روش‌ها بدون rank agreement انجام شود.

---

### فاز ۶ — Modeling / Validation / Scoring / Top-5 / Lock

**دامنه:** §17, §18, MT-1..5, VAL-1..7, SC-1..10, AC-07, AC-10..AC-13, AC-31, AC-32

**خروجی‌های اجباری**
1. پورتفولیوی [M] کامل (mean, degree-day, MLR, Ridge, Lasso, EN, piecewise 1P–5P, TOWT, RF, ExtraTrees, GB,
   XGBoost, LightGBM, SVR-RBF, MLP, یک sequence NN) + [S]/[O] با ثبت دقیق.
2. **leakage-safe**: purge gap ≥ **max(1 day, 1 step, max lag, max rolling window)** (اکنون فقط ۱ روز است)،
   preprocessing/selection داخل fold، HPO تودرتو (اکنون در fold موجود است — حفظ و تست شود)، holdout پایانی.
3. NN gate با آستانهٔ AUTO (پیش‌فرض ۵٪) و دلیل ثبت‌شده؛ ensemble فقط با بهبود > ۳٪ و **NNLS روی inner-fold OOF**.
4. SC-1..SC-10: ۱۳ معیار + وزن‌ها + profile + **حساسیت ±۲۰٪** + tie-break قطعی + دو رجیستر جدا
   (Rejected / Qualified-not-shortlisted) + SC-9 (کمتر از ۵ / هیچ‌کدام) + تأیید صریح کاربر.
5. Top-5 با **SC-7**: معادلهٔ بسته برای پارامتریک (با خطا/واحد)، معماری+HPO برای غیرپارامتریک + **surrogate با fidelity R²**،
   importance/PDP/ALE؛ Excel formula export [S]؛ CI و بازهٔ پیش‌بینی در کارت.
6. Lock = **Baseline نسخه‌دار و تغییرناپذیر** (SHA-256 آرتیفکت، features، pipeline، snapshotهای VSG/TCP، پنجرهٔ آموزش،
   thresholds، profile، seed، calibration summary) + model card + history.
7. رفع ابهام فعلی: چرا Top-1 با CV(RMSE)=6.19 از Gradient boosting با 5.38 جلو زده؟ (وزن‌ها) —
   باید در گزارش با SC-3/SC-4 توضیح داده شود، نه پنهان.

**گیت (G6):** تست purge (lag بزرگ ⇒ gap بزرگ‌تر)، تست «scaler/imputer/selector داخل fold»، تست
`k_max=min(p,n/20)`، تست NN-gate (قبول/رد با نسبت مشخص)، تست NNLS ensemble (وزن‌ها روی inner fold و ارزیابی روی outer)،
تست ترتیب قطعی tie-break، تست SC-4 (رتبه پایدار/ناپایدار)، تست lock (تغییر ⇒ نسخهٔ جدید + audit)،
تست Excel-formula ✅ با بازپردازش عددی.

**رد می‌شود اگر:** Top-5 قبل از lock قابل مشاهده نباشد یا lock بدون imutability باشد · رجیستر ردّی‌ها با
«امتیاز پایین‌تر» مخلوط شود · purge فقط ۱ روز بماند · surrogate به‌عنوان فرمول مدل ارائه شود.

---

### فاز ۷ — UQ + Drift + Prediction & Monitoring + Causal M&V

**دامنه:** §19 (UQ-1..5), §20 (PM-1..7), §21 (DR-1..5), §14 T14, AC-14..AC-16, AC-29, AC-30

**خروجی‌های اجباری**
1. **Conformal پیش‌فرض** با MAPIE 1.6: `SplitConformalRegressor`, `CrossConformalRegressor`,
   `JackieAfterBootstrapRegressor`→`JackknifeAfterBootstrapRegressor`, `ConformalizedQuantileRegressor`,
   `TimeSeriesRegressor(method="enbpi"|"aci")`؛ coverage انتخابی و پیش‌فرض از TCP-1؛
   fallback کوانتایل/بوت‌استرپ با برچسب روش.
2. گزارش کالیبراسیون: coverage تجربی/اسمی، عرض بازه، Winkler، coverage شرطی بر regime/فصل/سطح بار،
   T12 (Kupiec, Christoffersen)، PIT فقط برای مدل‌های توزیعی.
3. PM-1..PM-7: unlock per target پس از lock؛ فایل/Excel/Parquet با schema validation و هشدار extrapolation؛
   **SQL guard با `sqlglot.parse`** (تک statement، ریشه Select/Union/Intersect/Except، رد DDL/DML و
   `xp_cmdshell|OPENROWSET|LOAD_FILE|INTO OUTFILE|COPY|ATTACH|PRAGMA`، رد stacked)، read-only، row-limit، timeout ~10 s،
   TLS پیش‌فرض؛ اعتبارنامه در keyring/DPAPI و **هرگز** در `.ebproj`/لاگ/export؛ ورود دستی + paste batch؛
   خروجی‌های per-timestamp با قرارداد علامت و NaN-guard؛ هشدار AUTO `max(10%, 2×CV(RMSE))` + persistence/hysteresis؛
   داشبورد درون‌پنجره + export + snapshot با ارجاع به baseline version و input hash.
4. Drift: PSI (بین آموزش)، KS، Wasserstein، chi²؛ CUSUM/EWMA/Page-Hinkley روی باقی‌ماندهٔ استانداردشده؛
   rolling CV(RMSE)/NMBE؛ traffic light؛ **توصیه به re-validation، هرگز retrain خودکار**.
5. Causal M&V (فقط با `group`): T14 (TOST/SMD/Love plot/parallel trends/propensity overlap/kernel balancing) +
   اثر با DoWhy/EconML + refutation؛ گزارش «possible savings / non-routine event».
6. Fuzz guard: corpus مخرب/بی‌خطر در `tests/fuzz/sql_corpus.json` (شامل CTE-wrapped DML، stacked، xp_cmdshell).

**گیت (G7):** coverage تجربی ≈ اسمی (tolerance)، Kupiec/Christoffersen روی سری مصنوعی، تست
«هیچ اعتبارنامه‌ای در .ebproj/لاگ/report نیست» (grep + بازرسی ZIP)، corpus fuzz ۱۰۰٪ رد/قبول،
تست snapshot با تغییر baseline version، تست PSI/KS روی دادهٔ shift‌یافته، تست «drift هرگز مدل را عوض نمی‌کند».

**رد می‌شود اگر:** coverage «تضمین distribution-free» ادعا شود برای سری خودهمبسته · SQL همچنان regex باشد ·
password در `ConnectionProfile` به دیسک برود · drift به retrain وصل شود.

---

### فاز ۸ — Reporting & Audit depth

**دامنه:** §23 (REP-1..5, RPT-1..7), G-9, G-11, AC-17, AC-28

**خروجی‌های اجباری**
1. **یک مدل گزارش JSON** = منبع یگانهٔ in-app/PDF/Excel؛ هیچ رندرری داده مستقیم از RunResult نگیرد.
2. PDF با **ReportLab** (حذف WeasyPrint/Qt/matplotlib PDF)؛ Excel چند-برگه با openpyxl؛ preview درون‌برنامه با matplotlib
   (Agg برای استاتیک، QtAgg برای تعاملی) — بدون `QWebEngineView`.
3. بخش‌های REP-2 کامل (شامل EDA Atlas، Correlation Atlas، Full Test Results Appendix، Sensitivity Summary،
   Rejected/Not-Shortlisted، AUTO Log، Model Cards، Drift، Reproducibility، Glossary، Audit Appendix).
4. RPT-1 (Purpose→Method→Settings(AUTO/USER)→Results→Charts→Interpretation→Decision→Caveats) برای **هر** بخش؛
   RPT-2 سه عمق با TOC لینک‌دار؛ RPT-5 استاندارد نمودار (عنوان/واحد/legend/n/CI/palette/alt-text).
5. **موتور روایت قطعی** با artifact ID برای هر جمله + تست build-failing که هر عدد روایت را با آرتیفکت مقایسه کند.
6. report diff [S] بین دو run/baseline؛ Excel formula export [S].

**گیت (G8):** تست برابری عددی سه رندر (JSON↔PDF↔Excel برای ۲۰ متریک کلیدی)، تست شماره-ثبت‌نشده در قالب
(گزارش بدون داده باید تولید شود و تست شود)، تست alt-text، تست TOC، تست «هیچ متریک هاردکد در قالب»،
تست narrative↔artifact برای ۱۰۰٪ جملات عددی.

**رد می‌شود اگر:** any renderer از RunResult مستقیم بخواند · جملهٔ روایت بدون artifact ID باشد ·
گزارش با WeasyPrint/Qt ساخته شود · عمق Executive از ۱۰ صفحه بگذرد.

---

### فاز ۹ — Persistence / UX / Security / Packaging / Release

**دامنه:** §22 (PER-1..6, DOM-1..3), §24 (UX-1..12), §25, §26, AC-02, AC-18..AC-22, AC-30, AC-33..AC-35

**خروجی‌های اجباری**
1. `.ebproj` (ZIP + manifest با dataset relpath+SHA256+size+rows، config/VSG/TCP با provenance، AUTO log،
   selections، snapshots، artifact hashes، model cards؛ بدون اعتبارنامه) + relink dialog + schema migration.
2. Autosave ۵ دقیقه/رویداد debounce + نوشتن اتمیک + stage checkpoints + resume (LIF-4).
3. backup با **SQLite online backup API** + hash manifest + restore با اعتبارسنجی؛ retention با dry-run (locked baselines هرگز purge نشوند).
4. PER-6: آرتیفکت امن (ONNX/skops/JSON)؛ pickle فقط با SHA-256 و هشدار کاربر؛ import `.ebproj` بدون تأیید ممنوع.
5. UX: stepper قفل‌دار (UX-1) + command palette Ctrl+K روی registry (UX-3) + QSS/تم dark-light با QSettings +
   دسترس‌پذیری (`:focus`، QAccessible، ۸۰–۲۰۰٪ فونت، high-contrast) + toast/empty/skeleton + taskbar progress +
   drag&drop + tray notification + i18n-ready (`QTranslator`، فقط انگلیسی).
6. Security: `SECURITY.md` با threat model (local attacker, malicious data file, SQLi, artifact tampering)،
   log scrubbing (تست)، SBOM در نصب‌کننده، `pip-audit` در CI.
7. Packaging: NSIS/Inno per-user، `.ebproj` association، uninstaller با «keep data»، updater امضاشده (Ed25519) که
   فقط با اقدام کاربر اجرا شود، checksumها، بیلد بازتولیدپذیر.
8. مستندات نهایی: `docs/requirements_matrix.md` کامل (همه AC)، `USER_GUIDE.md` انگلیسی شامل «Where is my data stored?»،
   `THIRD_PARTY_NOTICES.md`، Not-Implemented/Deviations/Assumptions نهایی.

**گیت (G9):** چرخهٔ کامل end-to-end روی ویندوز (E3): نصب → ingest نمونه → VSG → TCP → run → Top-5 → lock →
monitoring → report → backup/restore → uninstall با حفظ داده؛
sweep نهایی **AC-01..AC-35** با شاهد برای هرکدام؛ accessibility sweep؛ i18n smoke؛ single-instance؛
`.ebproj` association؛ resume پس از kill؛ zero-orphan.

**رد می‌شود اگر:** هر AC بدون شاهد باشد · ماتریس «done» داشته باشد ولی تست گم شده باشد ·
uninstaller داده را حذف کند · updater در boot اجرا شود.

---

## 6. نگاشت AC → فاز (بذر traceability)

| AC | فاز | AC | فاز | AC | فاز |
|---|---|---|---|---|---|
| AC-01, AC-02, AC-04, AC-05 | 0/1 | AC-12, AC-13, AC-31, AC-32 | 6 | AC-22, AC-30, AC-33 | 0/9 |
| AC-03 | 1 | AC-14, AC-15, AC-16, AC-29 | 7 | AC-34, AC-35 | 7/0 |
| AC-06 | 2 | AC-17, AC-28 | 8 | AC-23, AC-24 | 3 |
| AC-07, AC-08, AC-10, AC-11 | 3/6 | AC-18, AC-19 | 9 | AC-09, AC-25, AC-26 | 4 |
| AC-20, AC-21 | 1/9 | AC-27 | 5 | | |

---

## 7. ریسک‌های اصلی و تله‌ها (از دید ناظر)

1. **R-1 (بالاترین ریسک):** انتقال محاسبات به ProcessPoolExecutor در فازی که UI از قبل کامل است ⇒ بازنویسی
   گسترده. راه‌حل: در فاز ۱ فقط *مسیر* جابه‌جا شود و پایپ‌لاین دست نخورد (آداپتر `run_in_pool(payload)`).
2. **R-2:** «AUTO همه‌جا» به بهانهٔ AUTO، به هاردکد پنهان تبدیل شود. دفاع: تستی که برای هر setting مقدار
   AUTO و وجود Resolution را الزام می‌کند (AC-33).
3. **R-3:** تورم مصنوعی: تست‌هایی که خودشان mock را assert می‌کنند. دفاع: قاعدهٔ 4.2 و بررسی import/monkeypatch در بازبینی.
4. **R-4:** گزارش «زیبا» ولی بی‌پشتوانه. دفاع: تست narrative↔artifact و «هیچ متریک هاردکد».
5. **R-5:** رانش نسخه‌ها (statsmodels 0.15 انواع بازگشتی را عوض می‌کند). دفاع: lock + `result_object` صریح.
6. **R-6:** نبود شاهد ویندوز ⇒ ادعاهای LIF/PKG بی‌اثبات. دفاع: E3 اجباری + اسکریپت‌های `scripts/evidence/`.
7. **R-7:** دامنهٔ عظیم T1–T14 (بیش از ۱۰۰ تست) ⇒ فرسودگی. دفاع: ماتریس T-ID با وضعیت، اجرای تدریجی داخل فاز ۴
   با معیار «هر ID: یک تست موفق + یک شکست + مرجع» و پذیرش فازی زیرگیت‌ها (G4a T1–T4، G4b T5–T9، G4c T10–T14).
8. **R-8:** بازگشت WeasyPrint/Node به‌دلیل راحتی. دفاع: guard scripts در CI از فاز ۰.

---

## 8. آنچه اکنون از عامل اجرا انتظار می‌رود (اولین قدم)

1. ~~تأیید یا رد D1..D5~~ ✅ **انجام شد** (2026-11-12) — بستهٔ تصمیم‌ها بسته است.
2. اجرای **فاز ۰** دقیقاً مطابق بخش «فاز ۰» (با پیامدهای تصویب‌شدهٔ D1..D5) و ارسال `artifacts/phase0/REPORT.md` طبق قالب ۴.۲.
3. **توقف** تا رأی ناظر؛ سپس فاز ۱. هیچ فازی خودگشا نیست.

> یادآوری: ناظر کد نمی‌نویسد و اصلاح نمی‌کند. ناظر فقط کد را می‌خواند، اجرا می‌کند، شواهد را می‌سنجد و
> PASS/FAIL می‌دهد. هیچ فازی با «به‌نظر می‌رسد کار می‌کند» تأیید نمی‌شود.

---

## پیوست A — دستورهای بازتولید محیط بازبینی (سندباکس)

```bash
python3 -m venv /tmp/ebv
/tmp/ebv/bin/pip install -r requirements.lock            # پس از فاز ۰؛ امروز: requirements.txt
QT_QPA_PLATFORM=offscreen LD_LIBRARY_PATH=/tmp/qstub /tmp/ebv/bin/python -m pytest -q
```

هارنس Qt بی‌سر (فقط بازبینی منطق ویجت): stubهای link-only برای `libGL/libEGL/libxkbcommon/libdbus`
با نمادهای حاصل از `nm -D --undefined-only` روی `libQt6Gui`/`libqoffscreen`؛ `xkb` با تگ نسخهٔ `V_0.5.0`.
**این هارنس هرگز به‌عنوان شاهد رفتار بومی ویندوز پذیرفته نمی‌شود.**

## پیوست B — چک‌لیست سریع ناظر برای هر فاز

- [ ] SHA کامیت و `git diff --stat` مطابق گزارش است.
- [ ] هر ID در ماتریس: کد + تست + شاهد دارد؛ هیچ ID «done» بدون تست.
- [ ] `pytest -q` واقعاً اجرا شده (خروجی خام، نه خلاصهٔ دستی).
- [ ] تست‌ها monkeypatch/mock را برای شناسهٔ `[M]` assert نمی‌کنند.
- [ ] guard scripts سبز (وب‌گریزی، نردبان env، هش تست‌ها).
- [ ] آرتیفکت‌ها موجود و SHA-256 ثبت است.
- [ ] E3 برای شناسه‌های ویندوزی موجود است (یا FAIL).
- [ ] deviations/assumptions/not_implemented به‌روز است.
- [ ] هیچ رگرسیون در تست‌های فازهای قبل.
