# DTDC Shipping Label API WS
### (for E-commerce, GS, LTL & Express based Customer)
**Ver. 2.0**

---

## Introduction

This API allows you to generate shipping labels for your packages. You can choose to receive the label in PDF format or as a Base64 encoded string.

## Base URL

Replace `<API_BASE_URL>` with the actual base URL provided below.

- **Live environment:** `https://dtdcapi.shipsy.io`
- **Staging environment:** `https://alphademodashboardapi.shipsy.io`

**Endpoint:** `<base_url>/api/customer/integration/consignment/shippinglabel/stream`

## Method

GET

## Authentication

Authentication is done using an API key. Include the API key in the request headers as follows:

```
--header 'api-key: YOUR_API_KEY'
```

## Request Parameters

- **reference_number** (Required): The unique reference number assigned to the shipment within Shipsy.
- **label_code** (Required): The specific code for the type of shipping label you want to generate.
- **label_format** (Optional, defaults to `pdf`): The format of the downloadable label.

## Supported Label Codes

The following are the supported label codes along with their descriptions:

| Label Description | label_code |
|---|---|
| Shipping Label A4 | SHIP_LABEL_A4 |
| Shipping Label A6 | SHIP_LABEL_A6 |
| Shipping Label POD | SHIP_LABEL_POD |
| Shipping Label 4x6 | SHIP_LABEL_4X6 |
| Routing Label A4 | ROUTE_LABEL_A4 |
| Routing Label 4x4 | ROUTE_LABEL_4X4 |
| Invoice Print (for International orders only) | INVOICE |
| Address Label A4 (for Document) | ADDR_LABEL_A4 |
| Address Label 4x2 (for Document) | ADDR_LABEL_4X2 |

## Supported Label Formats

- **pdf:** Generates a downloadable PDF file containing the shipping label.
- **base64:** Returns the label data as a Base64 encoded string, which can be decoded and used within your application.

## Curl Examples

**Generate PDF Label:**

```bash
curl --location 'https://dtdcapi.shipsy.io/api/customer/integration/consignment/shippinglabel/stream?reference_number=<awb_no>&label_code=SHIP_LABEL_4X6&label_format=pdf' \
--header 'api-key: <api-key>'
```

**Generate Base64 Encoded Label:**

```bash
curl --location 'https://dtdcapi.shipsy.io/api/customer/integration/consignment/shippinglabel/stream?reference_number=<awb_no>&label_code=SHIP_LABEL_4X6&label_format=base64' \
--header 'api-key: <api-key>'
```

## Curl Explanation

- **reference_number=&lt;awb_no&gt;:** This parameter specifies the reference number of the shipment for which you want to generate a label. Replace `<awb_no>` with your actual AWB/shipping/reference number.
- **label_code=SHIP_LABEL_4X6:** This parameter specifies the code for the desired shipping label format. In this example, `SHIP_LABEL_4X6` is used, indicating a 4" x 6" label. Refer to the documentation above for available `label_code` options.
- **label_format=pdf or label_format=base64:** This parameter specifies the format of the downloadable label.

## Response

- **PDF Format:** The response will be the raw PDF data of the generated shipping label. You will need to save this data to a `.pdf` file using an appropriate method depending on your programming language or environment.

### Sample Labels

The documentation includes sample label images for each label type:

1. **SHIP_LABEL_4X6** — 4" x 6" shipping label with barcode, TO/FROM addresses, mode, product description, and weight.
2. **SHIP_LABEL_A4 / SHIP_LABEL_A6 / SHIP_LABEL_POD** — Full-page consignment note with Sender's Copy, Account's Copy, and POD Copy sections.
3. **ROUTE_LABEL_A4 / ROUTE_LABEL_4X4** — Routing label with QR code, ship-to details, and barcode.
4. **ADDR_LABEL_A4 / ADDR_LABEL_4X2** — Address label with ORG/DES/CUST-ID and reference barcode.
5. **INVOICE** (for international orders only) — Export invoice with shipper, bill-to/ship-to party, commodity, and bank details.

> *Note: The original PDF contains rendered images of each sample label. These are visual samples and are referenced here by description.*

- **Base64 Format:** The response will be a string containing the Base64 encoded representation of the shipping label data. You will need to decode this string using a Base64 decoder in your application to obtain the label data for further use.

### Sample Response (Base64)

```json
{
  "referenceNumber": "7X100761088",
  "label": "JVBERi0xLjQKJfbk/N8KMSAwIG9iago8PAovVHlwZSAvQ2F0YWxvZwovVmVyc2lvbiAvMS41Ci9QYWdlcyAyIDAgUgovTmFtZXMgMyAwIFIKPj4KZW5kb2JqCjQgMCBvYmoK...<truncated base64 PDF data>...DlDPiA8QTBDNDkwOEE1QzcwN0I1OUEwRkExQUE4RkU5RDYwOUM+XQovU2l6ZSAxNwo+PgpzdGFydHhyZWYKODkxNgolJUVPRgo="
}
```

> *Note: The `label` value in the original document is a very long Base64-encoded PDF string. It has been truncated here for readability — the full string is returned by the API at runtime.*

## Response Code Remarks

| Response | Code Remarks |
|---|---|
| 200 | Each consignment will be processed independently. For each consignment, the `success` key will be either true or false. If success is true for a consignment, then the consignment is successfully entered into the DTDC system. If success is false, the consignment is not entered (in case of false, the response contains an error message reason). |
| 400 | There is some validation error in the overall request format. In this case, the complete request is rejected. |
| 401 | There is an authentication error. |
