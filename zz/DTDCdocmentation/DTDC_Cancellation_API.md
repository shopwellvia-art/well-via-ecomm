# DTDC Cancellation API
### (for B2C & Express Customer)
**Ver. 2.0**

---

## Overview

This API allows customers to cancel consignments by providing the AWB (Airway Bill) number(s) and the associated customer code.

## Base URLs

- **Staging URL:**
  `https://alphademodashboardapi.shipsy.io/api/customer/integration/consignment/cancel`
- **Production URL:**
  `http://dtdcapi.shipsy.io/api/customer/integration/consignment/cancel`

## Method

- **HTTP Method:** POST

## Headers

- **Content-Type:** `application/json`
- **api-key:** `<API KEY>` (replace `<API KEY>` with the actual key provided)

## Request Body

```json
{
  "AWBNo": [
    "D78326386"
  ],
  "customerCode": "<customer_code>"
}
```

## Request Field Descriptions

| Field | Type | Mandatory | Description |
|---|---|---|---|
| AWBNo | Array of Strings | Yes | List of Airway Bill numbers (AWBs) that need to be canceled. |
| customerCode | String | Yes | The unique code assigned to the customer for identification purposes. |

## Example Request

```bash
curl --location '<url>' \
--header 'api-key: <API-key>' \
--header 'Content-Type: application/json' \
--data '{
  "AWBNo": [
    "D78326386"
  ],
  "customerCode": "<customer_code>"
}'
```

## Response

```json
{
  "status": "OK",
  "success": true,
  "successConsignments": [
    {
      "success": true,
      "reference_number": "7V000008715"
    }
  ]
}
```

## Detailed Explanation of Key Fields

1. **status:**
   - This field shows the overall status of the API request.
   - `OK` indicates that the API processed the request correctly without errors.

2. **success:**
   - A boolean value indicating whether the entire request was successful.
   - If `true`, the request was processed successfully; if `false`, it means there was an issue with the request.

3. **successConsignments:**
   - An array containing objects that provide details about each consignment that was successfully processed.
   - Each object inside this array contains fields specific to the consignment's processing status.

4. **success (inside successConsignments):**
   - This boolean indicates whether the individual consignment was processed successfully.
   - A value of `true` means the consignment was successfully canceled or processed.

5. **reference_number:**
   - This string contains the reference number of the consignment that was processed successfully.
   - The reference number helps identify which consignment was affected by the request.

## Response Code Remarks

| Response | Code Remarks |
|---|---|
| 200 | Each consignment will be processed independently. For each consignment, the `success` key will be either true or false. If success is true for a consignment, then the consignment is successfully entered into the DTDC system. If success is false, the consignment is not entered (in case of false, the response contains an error message reason). |
| 400 | There is some validation error in the overall request format. In this case, the complete request is rejected. |
| 401 | There is an authentication error. |

---

*www.dtdc.com*
