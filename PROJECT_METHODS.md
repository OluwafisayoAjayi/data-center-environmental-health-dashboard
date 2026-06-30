# PhD-Standard Project Design

## Working title
**Data Center Expansion, Environmental Exposure, and Health Vulnerability in the United States**

## Research motivation
Data centers are energy-intensive digital infrastructure. Their environmental-health relevance depends not only on where the buildings are located, but also on whether they overlap with pollution exposure, power-sector emissions risk, social vulnerability, and existing health vulnerability.

## Main research question
Which U.S. counties face the greatest overlap of data center infrastructure, pollution exposure, emissions-related pressure, and health vulnerability?

## Scope of the dashboard
The dashboard is a spatial screening and policy-priority tool. It is not designed to prove that data centers cause pollution or health outcomes. Instead, it identifies counties where data center activity overlaps with environmental and health risk factors.

## Unit of analysis
County-level panel or cross-sectional unit, depending on data availability. The dashboard uses county FIPS codes to harmonize datasets.

## Conceptual framework
Data centers may affect environmental-health concern through several channels:

1. **Electricity demand channel**: data centers increase electricity demand.
2. **Power-sector emissions channel**: the environmental implication depends on the marginal generation mix and grid emissions intensity.
3. **Local pollution and cumulative exposure channel**: counties with existing air quality burdens may face greater concern from additional energy infrastructure pressure.
4. **Health vulnerability channel**: the same exposure may matter more in places with elevated asthma, COPD, cardiovascular disease, or poor health status.
5. **Environmental justice channel**: siting and infrastructure burdens may overlap with socioeconomic vulnerability and climate burden.

## Main index
The dashboard constructs the **Data Center Environmental Health Pressure Index (DCEHPI)**:

DCEHPI = 0.30(Data Center Pressure)
       + 0.25(Pollution Exposure)
       + 0.25(Health Vulnerability)
       + 0.10(Social Vulnerability)
       + 0.10(Environmental Justice / Climate Burden)

All variables are first converted to national percentile ranks. Domain scores are calculated as the mean of percentile-ranked variables in each domain. If a domain is unavailable during an automated update, the weights are renormalized across available domains.

## Baseline descriptive model
A first academic paper can estimate:

Y_c = alpha + beta DC_c + X'_c gamma + delta_s + epsilon_c

where:
- Y_c is pollution exposure, health vulnerability, or environmental justice burden in county c.
- DC_c is data center count, data center square footage, or data center square footage per 100,000 residents.
- X_c includes county controls such as population, poverty, renter share, income, industrial composition, and urbanization.
- delta_s are state fixed effects.

This model is descriptive. It asks whether data-center-intensive counties are systematically different from other counties within the same state.

## Causal extension
A stronger later-stage PhD paper can use an event-study or difference-in-differences design if data center opening dates, expansion dates, or electricity-load announcements are available:

Y_ct = alpha + sum_k beta_k DataCenterEntry_{c,t+k} + X'_ct gamma + mu_c + lambda_t + epsilon_ct

where:
- Y_ct is annual pollution, electricity-sector emissions, electricity prices, hospitalizations, mortality, or other outcomes.
- mu_c are county fixed effects.
- lambda_t are year fixed effects.

## Key limitations
1. The dashboard does not provide causal evidence.
2. Data center load is not always observed, so square footage and count are imperfect measures of electricity demand.
3. Some health measures are modeled estimates and should be interpreted as vulnerability indicators rather than directly observed outcomes.
4. County-level measures may hide within-county environmental inequality.
5. The data center source is partly derived from OpenStreetMap and requires careful validation for academic publication.

## Best use in dissertation development
Use the dashboard as the data infrastructure and exploratory product. Then develop one causal paper from it after identifying a credible event, policy shock, grid interconnection record, or data center opening/expansion dataset.
