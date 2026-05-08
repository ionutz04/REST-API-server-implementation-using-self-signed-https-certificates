import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime

df = pd.read_csv("forecast_5h.csv", index_col=False)

print(df.tail(5))

# timestamps = datetime.timestamp(df["timestamp"], format="YYYY-MM-DD HH:MM")

plt.plot(df['timestamp'], df['temperature_C'])
plt.xlabel('DateTime')
plt.ylabel('temperature(˚C)')
plt.savefig("future_temperature")

