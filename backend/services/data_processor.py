import pandas as pd
from typing import List, Optional

# Mapping dictionary directly from the requirements table
COLUMN_MAPPING = {
    "nombre": "Station",
    "fhora": "Datetime",
    "temp": "Temperature (ºC)",
    "pres": "Pressure (hpa)",
    "vel": "Speed (m/s)"
}

def process_weather_data(raw_data: list, data_types: Optional[List[str]], aggregation: str = "None") -> list:
    """
    Transforms raw AEMET JSON into the required format using Pandas.
    Handles column renaming, timezone conversion (CET/CEST), and filtering.
    """
    if not raw_data:
        return []

    # Convert the raw list of dictionaries into a Pandas DataFrame
    df = pd.DataFrame(raw_data)

    # 1. Keep only the columns requested in the challenge (if they exist in the AEMET response)
    available_cols = [col for col in COLUMN_MAPPING.keys() if col in df.columns]
    df = df[available_cols]

    # 2. Rename columns to match the output dataset field names
    df = df.rename(columns=COLUMN_MAPPING)

   # --- TIMEZONE CONVERSION ---
    if "Datetime" in df.columns:
        # Convert text to a UTC-aware datetime object
        df["Datetime"] = pd.to_datetime(df["Datetime"], utc=True)
        # Convert timezone to Madrid (Pandas handles DST automatically)
        df["Datetime"] = df["Datetime"].dt.tz_convert("Europe/Madrid")
    # --------------------------------------------------------------

    # --- TIME AGGREGATION ---
    if aggregation != "None" and "Datetime" in df.columns:
        # Set Datetime as index to allow time-based resampling
        df.set_index("Datetime", inplace=True)
        
        # Define how to aggregate each column (keep the first station name, average the numbers)
        agg_rules = {col: 'mean' for col in df.columns if col != "Station"}
        if "Station" in df.columns:
            agg_rules["Station"] = 'first'
            
        # Resample based on the requested frequency
        if aggregation == "Hourly":
            df = df.resample("h").agg(agg_rules)
        elif aggregation == "Daily":
            df = df.resample("D").agg(agg_rules)
        elif aggregation == "Monthly":
            df = df.resample("ME").agg(agg_rules)
            
        # Drop empty time bins created by resampling and reset index
        df = df.dropna(subset=[col for col in df.columns if col not in ["Station", "Datetime"]])
        df = df.reset_index()

    # Format Datetime back to string with offset AFTER aggregation
    if "Datetime" in df.columns:
        df["Datetime"] = df["Datetime"].apply(lambda x: x.isoformat())

    # 3. Filter by specific data types if the user selected any
    if data_types:
        # Station and Datetime are always included as base columns
        requested_cols = ["Station", "Datetime"]
        
        if "temperature" in data_types and "Temperature (ºC)" in df.columns:
            requested_cols.append("Temperature (ºC)")
        if "pressure" in data_types and "Pressure (hpa)" in df.columns:
            requested_cols.append("Pressure (hpa)")
        if "speed" in data_types and "Speed (m/s)" in df.columns:
            requested_cols.append("Speed (m/s)")
            
        # Filter the DataFrame to only include the requested columns
        df = df[requested_cols]

    # Convert DataFrame back to list of dictionaries, replacing NaN with None for valid JSON
    return df.replace({float('nan'): None}).to_dict(orient="records")