"""Generate deterministic synthetic sample datasets (no real client data).

    python samples/generate_samples.py
"""
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parent
rng = np.random.default_rng(2024)


def weather(idx):
    doy = idx.day_of_year.values + idx.hour.values / 24.0
    base = 12 - 11 * np.cos(2 * np.pi * (doy - 15) / 365.25)           # annual cycle (deg C)
    diurnal = 4 * np.sin(2 * np.pi * (idx.hour.values - 9) / 24.0)
    return base + diurnal + rng.normal(0, 1.5, len(idx))


def hourly_office(days=200, start="2023-01-01", new_period=False):
    idx = pd.date_range(start, periods=days * 24, freq="h")
    T = weather(idx)
    hour, dow = idx.hour.values, idx.day_of_week.values
    occ = np.where((dow < 5) & (hour >= 7) & (hour <= 18), 0.85, 0.05) * (1 + rng.normal(0, 0.05, len(idx)))
    rh = np.clip(60 + 15 * np.sin(2 * np.pi * hour / 24) + rng.normal(0, 5, len(idx)), 20, 95)
    cool = np.maximum(0, T - 17) * (6 + 14 * occ)
    heat = np.maximum(0, 14 - T) * 3.5
    kwh = 40 + 90 * occ + cool + heat + 0.05 * rh + rng.normal(0, 4, len(idx))
    if new_period:  # simulate an efficiency loss (+12 %) in the reporting data
        kwh = kwh * 1.12
    return pd.DataFrame({"Timestamp": idx, "Electricity_kWh": kwh.round(2), "OAT_C": T.round(2), "RH_pct": rh.round(1), "Occupancy": occ.round(3)})


def daily_plant(days=730):
    idx = pd.date_range("2022-01-01", periods=days, freq="D")
    T = weather(idx + pd.Timedelta(hours=12))
    dow = idx.day_of_week.values
    prod = np.where(dow < 5, 1000, 250) * (1 + rng.normal(0, 0.08, days)) * (1 + 0.0003 * np.arange(days))
    hum = np.clip(55 + 20 * np.sin(2 * np.pi * idx.day_of_year.values / 365.25) + rng.normal(0, 6, days), 20, 95)
    elec = 1800 + 2.6 * prod + 38 * np.maximum(0, T - 18) + rng.normal(0, 90, days)
    gas = 300 + 0.45 * prod + 95 * np.maximum(0, 15 - T) + rng.normal(0, 40, days)
    water = 50 + 0.08 * prod + 1.2 * np.maximum(0, T - 20) + rng.normal(0, 5, days)
    df = pd.DataFrame({"Date": idx, "Electricity_kWh": elec.round(1), "Gas_m3": gas.round(1), "Water_m3": water.round(1),
                       "OAT_C": T.round(2), "Humidity_pct": hum.round(1), "Production_units": prod.round(0)})
    df.loc[rng.choice(days, 12, replace=False), "OAT_C"] = np.nan   # realistic missing values
    df.loc[rng.choice(days, 3, replace=False), "Electricity_kWh"] *= 3  # a few spikes (outliers)
    return df


def monthly_building(months=36):
    idx = pd.date_range("2021-01-01", periods=months, freq="MS")
    T = 12 - 11 * np.cos(2 * np.pi * (idx.day_of_year.values - 15) / 365.25) + rng.normal(0, 0.8, months)
    hdd = np.maximum(0, 15.5 - T) * idx.days_in_month.values
    cdd = np.maximum(0, T - 18) * idx.days_in_month.values
    kwh = 12000 + 21 * hdd + 48 * cdd + rng.normal(0, 600, months)
    return pd.DataFrame({"Month": idx, "Energy_kWh": kwh.round(0), "Avg_Temp_C": T.round(2), "HDD": hdd.round(1), "CDD": cdd.round(1)}).drop(columns=["HDD", "CDD"])


def main():
    hourly_office().to_csv(OUT / "hourly_office.csv", index=False)
    hourly_office(days=30, start="2023-07-20", new_period=True).to_csv(OUT / "hourly_office_new_data.csv", index=False)
    daily_plant().to_excel(OUT / "daily_plant_multitarget.xlsx", index=False)
    monthly_building().to_csv(OUT / "monthly_building.csv", index=False)
    print("Samples written to", OUT)


if __name__ == "__main__":
    main()
