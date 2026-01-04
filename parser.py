import re
from datetime import datetime

# Regex to capture: "EQ 1.5 C 12/19/2025"
TITLE_PATTERN = r"[\S\s]*?([A-Z]+)\s+([\d,.]+)\s+([CP])\s+(\d{1,2}/\d{1,2}/\d{4})[\S\s]*"


def parse_yeetz_alert(embed):
    """
    Parses a Discord embed (used in Yeetz trading alerts) to extract
    core trade parameters (Ticker, Strike, Exp, Entry Price, Vol, OI).
    """
    try:
        raw_title = embed.title
        if not raw_title:
            return None

        # 1. Parse Title (Ticker, Strike, Type, Date)
        match = re.search(TITLE_PATTERN, raw_title)
        if not match:
            return None

        ticker = match.group(1)
        strike = float(match.group(2).replace(",", ""))
        opt_type = match.group(3)
        exp_str = match.group(4)
        exp_date = datetime.strptime(exp_str, "%m/%d/%Y").date()

        # 2. Parse Fields (Avg Fill, Interval Vol, OI)
        avg_fill = 0.0
        interval_vol = 0
        embed_oi = 0

        # REGEX to find key-value pairs (e.g., **Key:** Value)
        KV_PATTERN = re.compile(r'\*\*([A-Za-z\s/%]+):\*\*\s*([\d\s,.$K]+)')
        extracted_data = {}

        # Iterate over fields where the data is inside the field.value string
        for field in embed.fields:
            raw_value = field.value.replace('>>>', '').strip()

            # Find all key/value matches in the field's value
            matches = KV_PATTERN.findall(raw_value)

            for key, value in matches:
                extracted_data[key.lower().strip()] = value.strip()

        # --- Lookup and Conversion using the cleaned data ---

        # Helper to clean value strings (remove $, K, commas)
        def clean_value_float(raw_val):
            return float(raw_val.replace('$', '').replace(',', ''))

        # 2.1. Avg Fill (Entry Price)
        avg_fill_raw = extracted_data.get('avg fill')
        if avg_fill_raw:
            avg_fill = clean_value_float(avg_fill_raw)

        # 2.2. Interval Vol (Entry Size / Volume)
        interval_vol_raw = extracted_data.get('interval vol')
        if interval_vol_raw:
            interval_vol = int(interval_vol_raw.replace(',', ''))

        # 2.3. OI (Entry OI)
        embed_oi_raw = extracted_data.get('oi')
        if embed_oi_raw:
            embed_oi = int(embed_oi_raw.replace(',', ''))

        return {
            "ticker": ticker,
            "strike": strike,
            "option_type": opt_type,
            "expiration_date": exp_date,
            "entry_price": avg_fill,
            "entry_size": interval_vol,
            "entry_oi": embed_oi,
            "raw_title": raw_title
        }

    except Exception as e:
        print(f"❌ Error in parse_yeetz_alert: {e}")
        return None