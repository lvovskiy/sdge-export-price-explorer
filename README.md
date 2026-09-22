# SDG&E Solar Export Price Explorer

A small Streamlit web app that downloads the current SDG&E Solar Billing Plan / NBT export-pricing ZIP and displays hourly export compensation as a heat map.

## What the app does

- Downloads SDG&E's latest current-year MIDAS pricing file automatically.
- Converts timestamps to Pacific time.
- Combines Generation + Delivery export prices for bundled/non-CCA SDG&E customers.
- Shows SDG&E Delivery pricing separately for CCA customers, with a warning that CCA Generation pricing must be obtained from the CCA.
- Lets users select one or more months.
- Uses discrete $0.10/kWh heat-map bands.
- Highlights weekend/holiday pricing days.
- Lets the user enter a simple break-even value, such as $0.10/kWh.
- Summarizes the hours when export compensation exceeds that value.
- Allows the heat map and selected pricing to be downloaded.

## Data source

SDG&E Solar Billing Plan Export Pricing:
https://www.sdge.com/solar/solar-billing-plan/export-pricing

Current-year MIDAS ZIP used by the app:
https://www.sdge.com/sites/default/files/CurrentYearNBTPricingUploadMIDAS.zip

## Run locally

```bash
python -m pip install -r requirements.txt
streamlit run streamlit_app.py
```

## Deploy on Streamlit Community Cloud

1. Put these files in a public GitHub repository.
2. Sign in at https://share.streamlit.io using GitHub.
3. Choose **Create app** -> **Yup, I have an app**.
4. Select the repository and `main` branch.
5. Set the entrypoint to `streamlit_app.py`.
6. Choose a custom `streamlit.app` subdomain if desired.
7. Click **Deploy**.

Every committed change to the GitHub repository will then update the deployed app automatically.

## Important note

This is an informational visualization, not a bill calculator. CCA customers receive a Generation export price from their CCA, so SDG&E's Generation + Delivery combined value is not applicable to them. Battery losses and degradation are not included in the simple break-even comparison.
