import { db } from "@/db";
import { sql } from "drizzle-orm";

export const dynamic = "force-dynamic";

const modules = [
  ["energybaseline/config", "Central DATABASE_URL resolver (SQLite fallback, auto-created ./data), logging"],
  ["energybaseline/db + alembic/", "SQLAlchemy 2 models, Alembic migration 0001, start-up self-test (health table)"],
  ["energybaseline/core/temporal.py", "Declared vs detected sampling, aggregation, DST, windows, refuse-to-disaggregate"],
  ["energybaseline/core/quality.py", "Exclusion audit trail, outliers (9 detectors), VIF / collinearity, leakage rules"],
  ["energybaseline/core/mining.py", "PCA, K-Means, GMM, DBSCAN, HDBSCAN, SOM, anomaly ensemble, Apriori/FP-Growth, STL, FFT, change-points, regime classifiers"],
  ["energybaseline/core/models.py + nn.py", "Linear, TOWT, GAM, piecewise, trees, GBMs, SVR, GP, MLP, LSTM, GRU, TCN, CNN, CNN-LSTM, AE, N-BEATS"],
  ["energybaseline/core/scoring.py", "13-criterion scoring, ASHRAE 14 / IPMVP gates, Top-5 shortlist, rejection register"],
  ["energybaseline/core/monitor.py + sqlsource.py", "Deviation analytics; MySQL / MSSQL / PostgreSQL / SQLite / ODBC with DPAPI-encrypted credentials"],
  ["energybaseline/gui/", "PySide6 tabbed-MDI desktop UI, Advanced (AUTO) dialog, AUTO panel, Top-5 selection, monitoring dashboard"],
  ["energybaseline/core/report.py", "22-section report: PDF, multi-sheet Excel, HTML; dashboard PDF/Excel export"],
];

export default async function HomePage() {
  let dbOk = false;
  try {
    await db.execute(sql`select 1`);
    dbOk = true;
  } catch {
    dbOk = false;
  }

  return (
    <main className="mx-auto min-h-screen max-w-4xl px-6 py-12">
      <p className="text-sm uppercase tracking-[0.08em] text-slate-500">Project companion page</p>
      <h1 className="mt-2 text-4xl font-semibold text-slate-950">EnergyBaseline v3 — Windows desktop application</h1>
      <p className="mt-4 text-slate-700">
        The deliverable is an <b>offline, native Python / PySide6 desktop application</b> (no browser, no web server). It lives in this repository under{" "}
        <code className="rounded bg-slate-100 px-1">energybaseline/</code>. This page is only the hosting shell the preview platform requires; it is not the
        application UI.
      </p>

      <section className="mt-8 rounded-2xl bg-white p-6 shadow">
        <h2 className="text-xl font-semibold">Run it on Windows 10/11</h2>
        <ol className="mt-3 list-decimal space-y-1 pl-5 text-slate-700">
          <li>
            Run <code className="rounded bg-slate-100 px-1">install.bat</code> once (creates a venv, installs requirements, applies Alembic migrations, builds{" "}
            <code className="rounded bg-slate-100 px-1">EnergyBaseline.exe</code>, the icon and the <code className="rounded bg-slate-100 px-1">.lnk</code> shortcuts).
          </li>
          <li>Double-click <code className="rounded bg-slate-100 px-1">EnergyBaseline.exe</code> (or the shortcut). Developers can use <code className="rounded bg-slate-100 px-1">start.bat</code> / <code className="rounded bg-slate-100 px-1">stop.bat</code>.</li>
          <li>Open a sample from <code className="rounded bg-slate-100 px-1">samples/</code>, tick target column(s), press <b>Run analysis</b>, pick one of the Top-5 baselines per target, then use <b>Prediction &amp; Monitoring</b>.</li>
        </ol>
        <p className="mt-3 text-sm text-slate-500">
          The .exe cannot be compiled in this Linux sandbox (PyInstaller does not cross-compile); the build script is included and reproducible.
        </p>
      </section>

      <section className="mt-6 rounded-2xl bg-white p-6 shadow">
        <h2 className="text-xl font-semibold">Repository map</h2>
        <ul className="mt-3 divide-y divide-slate-100">
          {modules.map(([path, desc]) => (
            <li key={path} className="py-2">
              <code className="text-sm font-semibold text-sky-800">{path}</code>
              <div className="text-sm text-slate-600">{desc}</div>
            </li>
          ))}
        </ul>
        <p className="mt-3 text-sm text-slate-600">
          Also included: <code>tests/</code> (pytest), <code>samples/</code> (synthetic datasets), <code>examples/</code> (example PDF / Excel / HTML reports),{" "}
          <code>docs/USER_GUIDE.md</code> (with “Where is my data stored?”), <code>scripts/</code> (icon, shortcut, exe build).
        </p>
      </section>

      <p className="mt-6 text-xs text-slate-400">Preview shell database check: {dbOk ? "reachable" : "unreachable"} (unrelated to the desktop app, which uses SQLite only).</p>
    </main>
  );
}
