# Kaggle retail sample attribution

Source: [Superstore Sales Dataset — rohitsahoo](https://www.kaggle.com/datasets/rohitsahoo/sales-forecasting). Kaggle API metadata labels the dataset **GPL 2**. Source last updated 2020-09-11; observations cover 2015–2018. This is an educational historical retail sample, not a live market feed.

`src/alphacentauric_bi/data/kaggle_retail_monthly.csv` is a derived monthly sum grouped by region and category, with order-line counts. Customer names, IDs, addresses and transaction/product details are excluded. The provenance JSON records source and derived SHA-256 hashes and transformations. `tools/prepare_kaggle.py` downloads the original data and reproduces the transformation. Derived data is supplied under the source GPL-2.0 terms; see `DATA-LICENSE.txt`. No additional accuracy warranty is given. Other source code has no new license grant implied by this data notice.
