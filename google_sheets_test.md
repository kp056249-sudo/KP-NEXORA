# Google Sheets Test

## Public import
1. Create a Google Sheet with columns: date, product, region, units, revenue.
2. Add the rows from `../sample_data/demo_sales.csv`.
3. In Google Sheets choose **File → Share → Publish to web**.
4. Choose **Link → Entire document → Web page**, then click **Publish**.
5. Copy the complete generated published URL. It normally contains `/pub` or `/pubhtml`.
6. Open KP NEXORA → Data Sources → Google Sheets.
7. Paste that published URL into **Import Public Sheet** and import it.

The public importer also accepts a normal Google Sheets `/spreadsheets/d/<id>/...` URL when the sheet is publicly readable.

## Private import
1. Connect the Google account in KP NEXORA.
2. Use the normal private Google Sheets URL in **Import Private Sheet**.
3. Approve the `spreadsheets.readonly` permission.

Never publish a sheet containing confidential data to the web.
