import pandas as pd
df = pd.read_csv("data/scrip_master_2026-11-06.csv", low_memory=False)
row = df[df["SEM_SMST_SECURITY_ID"] == 582921]
print(row.to_dict(orient="records"))
