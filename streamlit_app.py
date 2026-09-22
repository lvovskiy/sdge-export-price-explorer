import io
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
import streamlit as st
from matplotlib.colors import BoundaryNorm

DATA_URL = "https://www.sdge.com/sites/default/files/CurrentYearNBTPricingUploadMIDAS.zip"
SOURCE_PAGE = "https://www.sdge.com/solar/solar-billing-plan/export-pricing"
PACIFIC_TZ = "America/Los_Angeles"
COLOR_STEP = 0.10  # $/kWh

st.set_page_config(
    page_title="SDG&E Solar Export Price Explorer",
    page_icon="☀️",
    layout="wide",
)


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def load_sdge_pricing():
    """Download and parse SDG&E's current-year NBT MIDAS pricing file."""
    response = requests.get(DATA_URL, timeout=60)
    response.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        csv_files = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if not csv_files:
            raise RuntimeError("No CSV file was found in the SDG&E ZIP archive.")

        # The current SDG&E archive contains one MIDAS CSV. If that ever changes,
        # prefer the file that looks like the pricing upload.
        candidates = [
            name for name in csv_files
            if "pricing" in name.lower() or "midas" in name.lower()
        ]
        csv_name = candidates[0] if candidates else csv_files[0]

        with archive.open(csv_name) as f:
            df = pd.read_csv(f)

    required = {
        "RIN", "RateName", "DateStart", "TimeStart",
        "ValueName", "Value", "Unit"
    }
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"SDG&E file is missing expected columns: {sorted(missing)}")

    # Protect against accidentally adding multiple pricing profiles together.
    rate_names = sorted(df["RateName"].dropna().astype(str).unique())
    if len(rate_names) != 1:
        raise RuntimeError(
            "Expected one RateName in the current-year SDG&E file, but found: "
            + ", ".join(rate_names)
        )

    dt_utc = pd.to_datetime(
        df["DateStart"].astype(str) + " " + df["TimeStart"].astype(str),
        utc=True,
        errors="raise",
    )
    df["local_dt"] = dt_utc.dt.tz_convert(PACIFIC_TZ)

    # SDG&E uses XXSD for Generation and SDXX for Delivery in these NBT files.
    df["component"] = np.select(
        [
            df["RIN"].astype(str).str.startswith("USCA-XXSD"),
            df["RIN"].astype(str).str.startswith("USCA-SDXX"),
        ],
        ["Generation", "Delivery"],
        default="Other",
    )
    df = df[df["component"].isin(["Generation", "Delivery"])].copy()

    # The file labels holidays with the Weekend schedule, so this highlights
    # both literal weekends and SDG&E holidays that receive weekend pricing.
    df["weekend_or_holiday"] = (
        df["ValueName"].astype(str).str.contains("Weekend", case=False, na=False)
    )

    downloaded_at = datetime.now(ZoneInfo(PACIFIC_TZ))
    return df, csv_name, rate_names[0], downloaded_at


def format_hour(hour):
    hour %= 24
    if hour == 0:
        return "12 AM"
    if hour < 12:
        return f"{hour} AM"
    if hour == 12:
        return "12 PM"
    return f"{hour - 12} PM"


def contiguous_hour_ranges(hours):
    hours = sorted(set(int(h) for h in hours))
    if not hours:
        return "None"

    ranges = []
    start = previous = hours[0]
    for hour in hours[1:]:
        if hour == previous + 1:
            previous = hour
            continue
        ranges.append((start, previous + 1))
        start = previous = hour
    ranges.append((start, previous + 1))

    return ", ".join(
        f"{format_hour(start)}–{format_hour(end)}"
        for start, end in ranges
    )


def build_hourly(df, customer_type):
    """Return hourly generation/delivery/combined values for selected rows."""
    values = (
        df.pivot_table(
            index=["local_dt", "weekend_or_holiday"],
            columns="component",
            values="Value",
            aggfunc="sum",
        )
        .reset_index()
    )

    for column in ["Generation", "Delivery"]:
        if column not in values.columns:
            values[column] = np.nan

    values["Combined"] = values["Generation"].fillna(0) + values["Delivery"].fillna(0)

    if customer_type == "Bundled SDG&E (non-CCA)":
        values["plot_value"] = values["Combined"]
        value_label = "Generation + Delivery export price ($/kWh)"
    else:
        values["plot_value"] = values["Delivery"]
        value_label = "SDG&E Delivery export price ($/kWh)"

    values["date"] = values["local_dt"].dt.date
    values["hour"] = values["local_dt"].dt.hour
    values["month"] = values["local_dt"].dt.month
    values["month_name"] = values["local_dt"].dt.month_name()
    values["day_type"] = np.where(
        values["weekend_or_holiday"],
        "Weekend / holiday",
        "Weekday",
    )
    return values, value_label


def make_heatmap(hourly, threshold, value_label, highlight_special_days=True):
    heatmap = hourly.pivot(index="hour", columns="date", values="plot_value").sort_index()
    dates = list(heatmap.columns)
    hours = list(heatmap.index)

    raw_min = float(np.nanmin(heatmap.to_numpy()))
    raw_max = float(np.nanmax(heatmap.to_numpy()))
    lower = min(0.0, np.floor(raw_min / COLOR_STEP) * COLOR_STEP)
    upper = np.ceil(raw_max / COLOR_STEP) * COLOR_STEP
    if upper <= lower:
        upper = lower + COLOR_STEP
    bounds = np.arange(lower, upper + COLOR_STEP * 1.01, COLOR_STEP)

    cmap = plt.get_cmap("turbo", len(bounds) - 1)
    norm = BoundaryNorm(bounds, cmap.N, clip=True)

    fig_width = max(12, 0.31 * len(dates))
    fig, ax = plt.subplots(figsize=(fig_width, 7))
    image = ax.imshow(
        heatmap.to_numpy(),
        origin="lower",
        aspect="auto",
        interpolation="nearest",
        cmap=cmap,
        norm=norm,
    )

    # Outline dates using weekend/holiday pricing.
    if highlight_special_days:
        special_by_date = (
            hourly.groupby("date")["weekend_or_holiday"].max().reindex(dates).fillna(False)
        )
        special_positions = [i for i, flag in enumerate(special_by_date) if flag]
        for pos in special_positions:
            ax.axvspan(pos - 0.5, pos + 0.5, alpha=0.11)
            ax.axvline(pos - 0.5, linewidth=0.7)
            ax.axvline(pos + 0.5, linewidth=0.7)

    # Stronger divider at month boundaries.
    for i in range(1, len(dates)):
        if dates[i].month != dates[i - 1].month:
            ax.axvline(i - 0.5, linewidth=2.0)

    label_stride = max(1, int(np.ceil(len(dates) / 24)))
    tick_positions = np.arange(0, len(dates), label_stride)
    tick_labels = [pd.Timestamp(dates[i]).strftime("%b %d") for i in tick_positions]
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(tick_labels, rotation=45, ha="right")

    ax.set_yticks(range(len(hours)))
    ax.set_yticklabels([f"{h:02d}:00" for h in hours])

    # Light grid between individual date/hour cells.
    ax.set_xticks(np.arange(-0.5, len(dates), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(hours), 1), minor=True)
    ax.grid(which="minor", linewidth=0.20)
    ax.tick_params(which="minor", bottom=False, left=False)

    selected_months = list(dict.fromkeys(pd.Timestamp(d).strftime("%B") for d in dates))
    title_months = "–".join(selected_months) if len(selected_months) <= 2 else ", ".join(selected_months)
    year = pd.Timestamp(dates[0]).year
    ax.set_title(f"SDG&E Solar Export Price — {title_months} {year}")
    ax.set_xlabel("Date")
    ax.set_ylabel("Hour of day (Pacific time)")

    cbar = fig.colorbar(
        image,
        ax=ax,
        boundaries=bounds,
        ticks=bounds,
        spacing="proportional",
    )
    cbar.set_label(value_label)
    cbar.ax.set_yticklabels([f"${value:.1f}" for value in bounds])

if lower <= threshold <= upper:
    cbar.ax.axhline(threshold, linewidth=2.2)

    cbar.ax.text(
        0.5,
        -0.06,
        f"Break-even: ${threshold:.2f}",
        ha="center",
        va="top",
        transform=cbar.ax.transAxes,
        fontsize=10,
        bbox=dict(boxstyle="round,pad=0.2", facecolor="white", alpha=0.85, edgecolor="none"),
    )

    fig.tight_layout()
    return fig


def build_summary(hourly, threshold):
    rows = []
    grouped = hourly.groupby(["month", "month_name", "day_type"], sort=True)

    for (month_num, month_name, day_type), group in grouped:
        by_hour = group.groupby("hour", as_index=False)["plot_value"].mean()
        profitable = by_hour.loc[by_hour["plot_value"] > threshold, "hour"].tolist()

        peak = float(by_hour["plot_value"].max())
        peak_hours = by_hour.loc[np.isclose(by_hour["plot_value"], peak), "hour"].tolist()

        rows.append({
            "Month #": month_num,
            "Month": month_name,
            "Day type": day_type,
            f"Export above ${threshold:.2f}/kWh": contiguous_hour_ranges(profitable),
            "Peak price": f"${peak:.3f}/kWh",
            "Peak hour": contiguous_hour_ranges(peak_hours),
        })

    summary = pd.DataFrame(rows).sort_values(["Month #", "Day type"])
    return summary.drop(columns="Month #")


# -----------------------------------------------------------------------------
# App
# -----------------------------------------------------------------------------

st.title("☀️ SDG&E Solar Export Price Explorer")
st.write(
    "See when SDG&E Solar Billing Plan export prices are high enough to make "
    "exporting attractive. Pricing is downloaded directly from SDG&E."
)

try:
    with st.spinner("Downloading the latest SDG&E export pricing..."):
        data, source_csv, rate_name, downloaded_at = load_sdge_pricing()
except Exception as exc:
    st.error("I could not download or parse the SDG&E pricing file.")
    st.exception(exc)
    st.stop()

pricing_year = int(data["local_dt"].dt.year.min())
year_data = data[data["local_dt"].dt.year == pricing_year].copy()
available_months = sorted(year_data["local_dt"].dt.month.unique())

now = datetime.now(ZoneInfo(PACIFIC_TZ))
default_month_num = now.month if now.year == pricing_year and now.month in available_months else available_months[0]
month_options = {pd.Timestamp(2000, m, 1).month_name(): m for m in available_months}
default_month_name = pd.Timestamp(2000, default_month_num, 1).month_name()

with st.sidebar:
    st.header("Choose what to show")

    selected_month_names = st.multiselect(
        "Month(s)",
        options=list(month_options.keys()),
        default=[default_month_name],
        help="Select one or several months in the current SDG&E pricing year.",
    )

    customer_type = st.radio(
        "Customer type",
        ["Bundled SDG&E (non-CCA)", "CCA customer"],
        index=0,
        help=(
            "Bundled customers receive SDG&E Generation + Delivery export credits. "
            "CCA customers should use their CCA's generation export pricing in addition "
            "to SDG&E's delivery component."
        ),
    )

    stored_value_cents = st.number_input(
        "Value/cost of stored energy (¢/kWh)",
        min_value=0.0,
        max_value=200.0,
        value=10.0,
        step=1.0,
        help=(
            "Simple break-even value used to answer 'export or save it for later?'. "
            "Battery losses and degradation are not included."
        ),
    )

    highlight_special_days = st.checkbox(
        "Highlight weekends / SDG&E holidays",
        value=True,
    )

if not selected_month_names:
    st.info("Select at least one month in the sidebar.")
    st.stop()

selected_months = [month_options[name] for name in selected_month_names]
selected = year_data[year_data["local_dt"].dt.month.isin(selected_months)].copy()
hourly, value_label = build_hourly(selected, customer_type)
threshold = stored_value_cents / 100.0

if customer_type == "CCA customer":
    st.warning(
        "CCA customer: this chart shows only SDG&E's Delivery export component. "
        "Your CCA determines the Generation export component, so the chart is not your "
        "complete export compensation."
    )
else:
    st.caption("Bundled/non-CCA view: Generation + Delivery export compensation is combined.")

fig = make_heatmap(hourly, threshold, value_label, highlight_special_days)
st.pyplot(fig, use_container_width=True)

png_buffer = io.BytesIO()
fig.savefig(png_buffer, format="png", dpi=200, bbox_inches="tight")
png_buffer.seek(0)
plt.close(fig)

st.caption(
    f"Color bands are $0.10/kWh wide. The break-even marker is ${threshold:.2f}/kWh. "
    "The heat map itself always shows the actual export price; the threshold is not subtracted."
)

st.subheader("When does export beat your break-even value?")
summary = build_summary(hourly, threshold)
st.dataframe(summary, hide_index=True, use_container_width=True)

st.download_button(
    "Download heat map (PNG)",
    data=png_buffer,
    file_name=f"sdge_export_heatmap_{pricing_year}.png",
    mime="image/png",
)

# Download data with both components so users can audit the combined value.
download_columns = [
    "local_dt", "Generation", "Delivery", "Combined",
    "weekend_or_holiday"
]
download_df = hourly[download_columns].copy()
download_df["local_dt"] = download_df["local_dt"].astype(str)
st.download_button(
    "Download selected prices (CSV)",
    data=download_df.to_csv(index=False).encode("utf-8"),
    file_name=f"sdge_export_prices_{pricing_year}.csv",
    mime="text/csv",
)

st.divider()
st.markdown(
    f"**Pricing year:** {pricing_year}  \n"
    f"**SDG&E rate profile:** `{rate_name}`  \n"
    f"**Source file:** `{source_csv}`  \n"
    f"**Downloaded:** {downloaded_at.strftime('%Y-%m-%d %H:%M %Z')}  \n"
    f"**Source:** [SDG&E Solar Billing Plan Export Pricing]({SOURCE_PAGE})"
)
st.info(
    "This is an informational visualization, not a bill calculator. Actual battery economics "
    "can also depend on round-trip efficiency, battery degradation, taxes/fees, export-credit "
    "rules, and—in the case of CCA customers—the CCA generation export price."
)
