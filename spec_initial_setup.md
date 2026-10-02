# Objective

This is aimed to be a long running project to capture interesting features about our city, and explore how those features relate. The root of it should be a data collection system that aggregates interesting datasets (primarily from NYC OpenData and similar sources) to geographic areas - FIPS, Zip Codes, Community Neighbhorhoods, Boroughs, etcs. It should also have the capability to produce production grade visualizations of the features overlaid on a map or viewed together, and generally be a reasearch aid in exploring urban data analysis.

I suggest that we structure this in 4 system parrts:

* **Collection**: Responsible for identifying interesting datasets, collecting them, and keeping monitored datasets up to date.
* **Geographic** Pivot: Responsible for presenting a featurized view of the collected datasets such that features can be attributed to geographic points and aggregated to common geographic modes.
* **Visualization**: An interactive visualization of the features, in the form of a webpage that allows you to select which features to show.
* **Analysis**: A set of prefilled notebooks that profile features, particularly aimed at exploring correlation and regression between features

For technology: I prefer python as the backend, with standard data analysis tools. Overall look and feel of any UI should be quant researcher focused.

For a starter on interesting features, we can look at:

* Trees planted throughout the city
* 311 complaint types
* Restaurant sanitation ratings
* Location of public bathrooms
* Availabilty of public wifi hotspots
* Median income
* Commute distances