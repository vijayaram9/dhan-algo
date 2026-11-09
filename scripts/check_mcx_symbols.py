import pandas as pd
df = pd.read_csv("data/scrip_master_2026-11-06.csv", low_memory=False)
m = df[(df["SEM_EXM_EXCH_ID"] == "MCX") & (df["SEM_INSTRUMENT_NAME"].isin(["OPTFUT", "FUTCOM"]))]
print(m.groupby(["SM_SYMBOL_NAME", "SEM_INSTRUMENT_NAME"]).size().to_string())
print(m[m["SM_SYMBOL_NAME"].astype(str).str.startswith("CRUDE")][["SM_SYMBOL_NAME","SEM_INSTRUMENT_NAME","SEM_LOT_UNITS"]].drop_duplicates().to_string())
