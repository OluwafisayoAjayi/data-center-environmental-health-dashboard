# Environmental Health and Data Center Pressure Dashboard

This repository builds a public GitHub Pages dashboard that maps where data center infrastructure overlaps with pollution exposure, power-sector emissions risk, health vulnerability, and environmental justice/climate burden across U.S. counties.

## Project title
**Data Center Expansion, Environmental Exposure, and Health Vulnerability in the United States**

## Dashboard title
**Environmental Health and Data Center Pressure Dashboard**

## Main question
Which U.S. counties face the greatest overlap of data center pressure, environmental exposure, emissions-intensive infrastructure, and health vulnerability?

## What the dashboard does
The dashboard creates a county-level **Data Center Environmental Health Pressure Index (DCEHPI)**. The index is a screening tool, not a causal estimate. A higher score means a county has stronger overlap of data center infrastructure, air pollution exposure, electricity/emissions pressure, and vulnerable health conditions.

## Data sources used by the update pipeline
The pipeline is designed to pull current public data from:

1. **IM3 Open Source Data Center Atlas**: data center locations, county identifiers, facility type, latitude/longitude, and square footage where available.
2. **EPA AirData**: annual AQI by county and annual concentration by monitor.
3. **CDC PLACES**: county health measures such as asthma, COPD, coronary heart disease, poor physical health, and poor mental health.
4. **U.S. Census ACS 5-year API**: population and socioeconomic controls.
5. **CDC/ATSDR Environmental Justice Index**: optional environmental justice and climate burden indicators, if the ArcGIS service returns compatible fields.
6. **EPA eGRID / GHGRP**: placeholders are included for future expansion into power-sector and facility emissions layers.

## Repository structure

```text
.
├── docs/
│   ├── index.html
│   ├── methodology.html
│   ├── assets/
│   │   ├── style.css
│   │   └── app.js
│   └── data/
│       ├── demo_dashboard_county.csv
│       ├── dashboard_county_latest.csv          # created by the data update action
│       ├── dashboard_metadata.json              # created by the data update action
│       └── data_dictionary.csv                  # created by the data update action
├── scripts/
│   ├── build_data.py
│   ├── config.yml
│   └── requirements.txt
└── .github/workflows/
    └── update-data.yml
```

## How to use

1. Create a new GitHub repository.
2. Upload the files in this folder.
3. Go to **Settings → Pages**.
4. Choose **Deploy from branch**.
5. Select the branch and set the folder to `/docs`.
6. Go to the **Actions** tab.
7. Run **Update dashboard data** manually the first time.
8. After that, the workflow will update the dashboard weekly.

## Important academic interpretation
This dashboard should be described as a **spatial screening and policy-priority tool**. It should not claim that data centers cause poor health. The dashboard identifies counties where the expansion or concentration of energy-intensive digital infrastructure overlaps with environmental and health vulnerability.

## Suggested citation language
> This dashboard uses public datasets from IM3, EPA AirData, CDC PLACES, U.S. Census ACS, and CDC/ATSDR EJI. The Data Center Environmental Health Pressure Index is constructed by harmonizing indicators to county FIPS codes, ranking variables by national percentiles, and combining domain scores into a weighted composite index.
