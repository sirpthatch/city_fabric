Datasets to add to the system and explore:

<!-- Add new ideas as "- [ ] description" anywhere in the To do section. Done items
     name the dataset spec (config/datasets/<name>.yaml), its source and caveats. -->

## Done

- [x] **Affordable housing, broken up by income bracket composition**
  → `affordable_housing`: HPD Affordable Housing Production by Building ([hg8x-zxpr](https://data.cityofnewyork.us/d/hg8x-zxpr)), 2014–present.
  Features: affordable units (/km², /1k residents), new construction vs. preservation, mix by AMI band
  (extremely low ≤30%, very low, low, moderate, middle), share with 3+ bedrooms, rental share.
  Caveat: ~1% of units at confidential sites have no coordinates and are excluded.

- [x] **Number of Grocery Stores in the neighborhood**
  → `grocery_stores`: NYS Ag & Markets Retail Food Stores ([data.ny.gov 9a8c-vfzj](https://data.ny.gov/d/9a8c-vfzj)), NYC counties only.
  Features: all food stores, grocery stores (2,500+ sq ft), supermarkets (10,000+ sq ft), bodega-scale share,
  food retail floor area per resident. Caveat: square footage is missing for ~23% of stores, which count
  only toward the all-stores total.

- [x] **Number of Schools (Elementary, Middle, High)**
  → `facilities` (school features): DCP Facilities Database ([ji82-xba5](https://data.cityofnewyork.us/d/ji82-xba5)).
  Features: public & charter schools, by level served, non-public schools, charter share, DOE seat capacity
  per resident. Caveat: K-8, secondary (6–12) and K-12 schools count toward every level they serve.

- [x] **Number and composition of municipal buildings**
  → `facilities` (city features): facilities overseen by a City agency, distinct buildings housing them,
  composition by domain (education, health & human services, infrastructure, parks, libraries & culture,
  administration, public safety), libraries. Caveat: FacDB lists facilities/programs, not buildings;
  `city_facility_buildings` dedupes by BIN.

- [x] **City owned property in the neighborhood**
  → `city_property`: DCP City Owned and Leased Property ([fn4k-qyk2](https://data.cityofnewyork.us/d/fn4k-qyk2)).
  Features: city lots (owned or leased), owned lots, lots with no current use, leased share, composition by use
  category. Caveat: ~4% of COLP lots are outside the city (e.g. DEP watershed land) and drop out.

- [x] **Construction permits**
  → `dob_permits`: DOB NOW: Build Approved Permits ([rbx6-tga4](https://data.cityofnewyork.us/d/rbx6-tga4)), incremental, trailing 12 months.
  Features: initial permits, estimated cost of newly permitted jobs (total and median), mix of permitted work types.
  → `dob_job_filings`: DOB NOW Job Application Filings ([w9ak-ipjd](https://data.cityofnewyork.us/d/w9ak-ipjd)), trailing 5 years.
  Features: new buildings permitted, full demolitions, net new dwelling units permitted, median stories.
  Caveat: legacy BIS permits ([ipu4-2q9a](https://data.cityofnewyork.us/d/ipu4-2q9a), ~6% of 2025 volume) are not included.

- [x] **Number of LPC properties**
  → `landmarks`: LPC Individual Landmark & Historic District Building Database ([gpmc-yuvp](https://data.cityofnewyork.us/d/gpmc-yuvp)).
  Features: protected buildings, individual landmarks, buildings in historic districts, distinct historic
  districts, median construction year. Caveat: interior and scenic landmarks are not in this database.

## To do

