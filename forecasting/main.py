"""
forecast_5h_standalone.py
Trains the multihead LSTM from scratch and writes a clean 5-hour forecast CSV.

Output:
  forecast_5h.csv with 10 rows (every 30 min for 5 hours)
  Columns: timestamp, hour_offset, temperature_C, humidity_pct,
           pressure_hPa, rain_probability
"""

from __future__ import annotations

import sys
import numpy as np
import pandas as pd
import tensorflow as tf
from tensorflow.keras.callbacks import EarlyStopping, ReduceLROnPlateau

from predict_rain_multihead import (
    LOOKBACK,
    LSTM_TARGET_OUT_NAMES,
    LSTM_TARGET_COLS,
    SEASONALITY_COLS,
    LSTM_EPOCHS,
    LSTM_BATCH_SIZE,
    LSTM_LR,
    RECENT_FRACTION,
    RECENT_WEIGHT,
    TRAIN_RATIO,
    VAL_RATIO,
    add_seasonality,
    seasonality_row,
    build_multihead_lstm,
    build_pressure_features,
    train_pressure_model,
    predict_pressure_one_step,
    PRESSURE_LAG_MINUTES,
)
from predict_rain import (
    get_data_from_redis,
    prepare_features,
    make_targets,
    estimate_rain_probability_from_pressure,
    chronological_split_indices,
    fit_feature_scaler_on_train,
    apply_feature_scaler,
    fit_target_scaler_on_train,
    apply_target_scaler,
    build_multi_target_sequences,
    TEMP_HORIZON,
)

# Forecast config
FORECAST_HOURS = 5
STEP_MINUTES = 30           # output cadence
SENSOR_CADENCE_MINUTES = 5  # native sensor cadence
STEPS_PER_OUTPUT = STEP_MINUTES // SENSOR_CADENCE_MINUTES  # 6 internal steps per row
TOTAL_OUTPUT_ROWS = (FORECAST_HOURS * 60) // STEP_MINUTES  # 10 rows
OUTPUT_CSV = "forecast_5h.csv"


def train_model(df_with_targets: pd.DataFrame, feature_cols: list):
    """Train the multihead LSTM and the linear pressure model."""
    train_end, val_end = chronological_split_indices(
        len(df_with_targets), TRAIN_RATIO, VAL_RATIO
    )

    feat_scaler = fit_feature_scaler_on_train(df_with_targets, feature_cols, train_end)
    df_scaled = apply_feature_scaler(df_with_targets, feature_cols, feat_scaler)

    targ_scaler = fit_target_scaler_on_train(df_scaled, LSTM_TARGET_OUT_NAMES, train_end)
    df_scaled = apply_target_scaler(df_scaled, LSTM_TARGET_OUT_NAMES, targ_scaler)

    X_tr, Y_tr, _ = build_multi_target_sequences(
        df_scaled, feature_cols, LSTM_TARGET_OUT_NAMES, LOOKBACK, 0, train_end
    )
    X_va, Y_va, _ = build_multi_target_sequences(
        df_scaled, feature_cols, LSTM_TARGET_OUT_NAMES, LOOKBACK, train_end, val_end
    )

    season_idx = [feature_cols.index(c) for c in SEASONALITY_COLS]
    S_tr = X_tr[:, -1, season_idx]
    S_va = X_va[:, -1, season_idx]
    Y_tr_t, Y_tr_h = Y_tr[:, 0:1], Y_tr[:, 1:2]
    Y_va_t, Y_va_h = Y_va[:, 0:1], Y_va[:, 1:2]

    model = build_multihead_lstm(n_steps=X_tr.shape[1], n_features=X_tr.shape[2])
    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=LSTM_LR, clipnorm=1.0),
        loss={"temperature_out": "mse", "humidity_out": "mse"},
        metrics={"temperature_out": "mae", "humidity_out": "mae"},
    )

    es = EarlyStopping(monitor="val_loss", patience=10,
                       restore_best_weights=True, verbose=1)
    rlr = ReduceLROnPlateau(monitor="val_loss", factor=0.5,
                            patience=3, min_lr=1e-5, verbose=1)

    sw = np.ones(len(X_tr), dtype=np.float32)
    if RECENT_FRACTION > 0 and len(sw) > 0:
        n_recent = max(1, int(round(len(sw) * RECENT_FRACTION)))
        sw[-n_recent:] = RECENT_WEIGHT

    print(f"[train] Training on {len(X_tr)} sequences...")
    model.fit(
        [X_tr, S_tr], [Y_tr_t, Y_tr_h],
        sample_weight=[sw, sw],
        validation_data=([X_va, S_va], [Y_va_t, Y_va_h]),
        epochs=LSTM_EPOCHS,
        batch_size=LSTM_BATCH_SIZE,
        callbacks=[es, rlr],
        shuffle=False,
        verbose=1,
    )

    p_df = build_pressure_features(df_with_targets)
    p_train_end, p_val_end = chronological_split_indices(
        len(p_df), TRAIN_RATIO, VAL_RATIO
    )
    p_model = train_pressure_model(p_df, p_train_end, p_val_end)

    return model, feat_scaler, targ_scaler, p_model, df_scaled


def roll_forecast(model, feat_scaler, targ_scaler, p_model,
                  df_scaled, df_original, feature_cols):
    """Recursively predict at 5-min cadence, sample every 30 min, keep 10 rows."""
    season_idx = [feature_cols.index(c) for c in SEASONALITY_COLS]
    target_to_feat = {t: feature_cols.index(t) for t in LSTM_TARGET_COLS
                      if t in feature_cols}

    last_ts = pd.Timestamp(df_original["timestamp"].iloc[-1])
    bucket_td = pd.Timedelta(minutes=SENSOR_CADENCE_MINUTES)

    window = df_scaled[feature_cols].values[-LOOKBACK:].copy()
    last_raw = df_original[feature_cols].iloc[-1].values.astype(float).copy()

    max_lag_steps = max(PRESSURE_LAG_MINUTES) // SENSOR_CADENCE_MINUTES + 2
    hist_pressure = list(
        df_original["pressure"].astype(float).tail(max_lag_steps).values
    )

    hist_p_full = pd.to_numeric(df_original["pressure"], errors="coerce").dropna()
    hist_h_full = pd.to_numeric(df_original["humidity"], errors="coerce").dropna()
    hist_t_full = pd.to_numeric(df_original["temperature"], errors="coerce").dropna()
    hist_ts_full = pd.to_datetime(df_original["timestamp"]).reset_index(drop=True)

    total_internal_steps = (FORECAST_HOURS * 60) // SENSOR_CADENCE_MINUTES
    pred_temps, pred_hums, pred_press, pred_timestamps = [], [], [], []

    output_rows = []

    for step in range(1, total_internal_steps + 1):
        seq_in = window[np.newaxis, :, :]
        season_in = window[-1, season_idx][np.newaxis, :]
        t_s, h_s = model.predict([seq_in, season_in], verbose=0)
        pred_scaled = np.array([[t_s[0, 0], h_s[0, 0]]])
        pred_real = targ_scaler.inverse_transform(pred_scaled)[0]
        t_pred, h_pred = float(pred_real[0]), float(pred_real[1])

        forecast_ts = last_ts + bucket_td * step
        p_pred = predict_pressure_one_step(p_model, hist_pressure, forecast_ts)

        pred_temps.append(t_pred)
        pred_hums.append(h_pred)
        pred_press.append(p_pred)
        pred_timestamps.append(forecast_ts)
        hist_pressure.append(p_pred)

        # Sample only the rows we want in the final CSV (every STEPS_PER_OUTPUT)
        if step % STEPS_PER_OUTPUT == 0:
            synth_df = pd.DataFrame({
                "timestamp": pd.concat(
                    [hist_ts_full, pd.Series(pred_timestamps)], ignore_index=True),
                "pressure": pd.concat(
                    [hist_p_full, pd.Series(pred_press)], ignore_index=True),
                "humidity": pd.concat(
                    [hist_h_full, pd.Series(pred_hums)], ignore_index=True),
                "temperature": pd.concat(
                    [hist_t_full, pd.Series(pred_temps)], ignore_index=True),
            })
            rain_prob = estimate_rain_probability_from_pressure(synth_df)

            output_rows.append({
                "timestamp": forecast_ts.strftime("%Y-%m-%d %H:%M"),
                "hour_offset": round(step * SENSOR_CADENCE_MINUTES / 60.0, 1),
                "temperature_C": round(t_pred, 1),
                "humidity_pct": round(h_pred, 1),
                "pressure_hPa": round(p_pred, 1),
                "rain_probability": round(rain_prob, 3),
            })

        # Roll the LSTM input window forward
        new_raw = last_raw.copy()
        new_raw[target_to_feat["temperature"]] = t_pred
        new_raw[target_to_feat["humidity"]] = h_pred
        if "pressure" in feature_cols:
            new_raw[feature_cols.index("pressure")] = p_pred

        next_ts = forecast_ts + bucket_td
        season = seasonality_row(next_ts)
        for i, col in enumerate(SEASONALITY_COLS):
            new_raw[feature_cols.index(col)] = season[i]

        new_scaled = feat_scaler.transform(
            pd.DataFrame([new_raw], columns=feature_cols)
        ).flatten()
        window = np.vstack([window[1:], new_scaled])
        last_raw = new_raw

    return output_rows

def main():
    print("[main] Loading sensor data from Redis...")
    df = get_data_from_redis()
    if df.empty:
        raise RuntimeError("No data loaded from Redis.")

    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = prepare_features(df)
    df = add_seasonality(df)
    df_with_targets = make_targets(df, horizon=TEMP_HORIZON)
    print(f"[main] Rows after prep: {len(df_with_targets)}")

    feature_cols = ["temperature", "humidity", "pressure"] + SEASONALITY_COLS

    model, feat_scaler, targ_scaler, p_model, df_scaled = train_model(
        df_with_targets, feature_cols
    )

    print("\n[forecast] Rolling forward 5 hours...")
    rows = roll_forecast(
        model, feat_scaler, targ_scaler, p_model,
        df_scaled, df_with_targets, feature_cols,
    )

    out_df = pd.DataFrame(rows)
    out_df.to_csv(OUTPUT_CSV, index=False)
    print(f"\n[forecast] Saved {len(out_df)} rows to {OUTPUT_CSV}")
    print(out_df.to_string(index=False))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)