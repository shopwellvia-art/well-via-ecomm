# DTDC Order Upload API
### (for B2C & Express Customer)
**Ver. 2.0**

---

## Overview

This API allows customers to create consignment requests by submitting shipment details in JSON format. The details include the origin, destination, package specifications, and additional relevant information.

## Base URLs

- **Staging URL:**
  `https://alphademodashboardapi.shipsy.io/api/customer/integration/consignment/softdata`
- **Production URL:**
  `https://dtdcapi.shipsy.io/api/customer/integration/consignment/softdata`

## Method

- **HTTP Method:** POST

## Headers

- **Content-Type:** `application/json`
- **api-key:** `<API KEY>` (replace `<API KEY>` with the actual key provided)

## Request Body

### Sample request body for the Single Parcel shipments

```json
{
  "consignments": [
    {
      "customer_code": "customer code",
      "service_type_id": "B2C PRIORITY",
      "load_type": "NON-DOCUMENT",
      "description": "test",
      "dimension_unit": "cm",
      "length": "70.0",
      "width": "70.0",
      "height": "65.0",
      "weight_unit": "kg",
      "weight": "17.0",
      "declared_value": "5982.6",
      "num_pieces": "1",
      "origin_details": {
        "name": "TEST ENTERPRISES",
        "phone": "0000000000",
        "alternate_phone": "0000000000",
        "address_line_1": "dummy sender",
        "address_line_2": "",
        "pincode": "110046",
        "city": "New Delhi",
        "state": "Delhi"
      },
      "destination_details": {
        "name": "TEST ",
        "phone": "0000000000",
        "alternate_phone": "0000000000",
        "address_line_1": "test receiver",
        "address_line_2": "",
        "pincode": "636010",
        "city": "SALEM",
        "state": "Tamil Nadu"
      },
      "return_details": {
        "address_line_1": "Test_Address_Return",
        "address_line_2": "Test_Address_Return line 2",
        "city_name": "DELHI",
        "name": "Test_Return",
        "phone": "0000000000",
        "pincode": "248001",
        "state_name": "DELHI",
        "email": "amisha.arora@test.co.in",
        "alternate_phone": "0000000000"
      },
      "customer_reference_number": "order_id",
      "cod_collection_mode": "",
      "cod_amount": "",
      "commodity_id": "99",
      "eway_bill": "12345678",
      "is_risk_surcharge_applicable": "false",
      "invoice_number": "AB001",
      "invoice_date": "14 Oct 2022",
      "reference_number": ""
    }
  ]
}
```

### Sample request body for the Multi Parcel shipments

```json
{
  "consignments": [
    {
      "customer_code": "customer code",
      "service_type_id": "B2C PRIORITY",
      "load_type": "NON-DOCUMENT",
      "description": "test",
      "dimension_unit": "cm",
      "length": "70.0",
      "width": "70.0",
      "height": "65.0",
      "weight_unit": "kg",
      "weight": "17.0",
      "declared_value": "5982.6",
      "num_pieces": "1",
      "origin_details": {
        "name": "TEST ENTERPRISES",
        "phone": "0000000000",
        "alternate_phone": "0000000000",
        "address_line_1": "dummy sender",
        "address_line_2": "",
        "pincode": "110046",
        "city": "New Delhi",
        "state": "Delhi"
      },
      "destination_details": {
        "name": "TEST ",
        "phone": "0000000000",
        "alternate_phone": "0000000000",
        "address_line_1": "test receiver",
        "address_line_2": "",
        "pincode": "636010",
        "city": "SALEM",
        "state": "Tamil Nadu"
      },
      "return_details": {
        "address_line_1": "Test_Address_Return",
        "address_line_2": "Test_Address_Return line 2",
        "city_name": "DELHI",
        "name": "Test_Return",
        "phone": "0000000000",
        "pincode": "248001",
        "state_name": "DELHI",
        "email": "amisha.arora@test.co.in",
        "alternate_phone": "0000000000"
      },
      "customer_reference_number": "order_id",
      "cod_collection_mode": "",
      "cod_amount": "",
      "commodity_id": "99",
      "eway_bill": "12345678",
      "is_risk_surcharge_applicable": "false",
      "invoice_number": "AB001",
      "invoice_date": "14 Oct 2022",
      "reference_number": "",
      "pieces_detail": [
        {
          "description": "Test Product",
          "declared_value": "200",
          "weight": "0.5",
          "height": "5",
          "length": "5",
          "width": "5"
        }
      ]
    }
  ]
}
```

## Field Descriptions

| Field | Type | Mandatory | Description |
|---|---|---|---|
| consignments | Array of Object | Yes | List of consignment objects, each containing details of a shipment. |
| customer_code | String | Yes | Unique code assigned to the customer. |
| service_type_id | String | Yes | Type of service required (list attached below). |
| load_type | String | Yes | Type of load (e.g., NON-DOCUMENT). |
| description | String | No | Description of the consignment. |
| dimension_unit | String | Yes | Unit for dimensions (cm). |
| length | String | Yes | Length of the package. |
| width | String | Yes | Width of the package. |
| height | String | Yes | Height of the package. |
| weight_unit | String | Yes | Unit for weight (kg). |
| weight | String | Yes | Weight of the package. |
| declared_value | String | Yes | Declared value of the consignment. |
| num_pieces | String | Yes | Number of parcels/boxes. |
| origin_details | Object | Yes | Details about the origin of the consignment. |
| name (origin) | String | Yes | Name of the sender. |
| phone (origin) | String | Yes | Phone number of the sender. |
| alternate_phone (origin) | String | No | Alternate phone number of the sender. |
| address_line_1 (origin) | String | Yes | First line of the sender's address. |
| address_line_2 (origin) | String | No | Second line of the sender's address. |
| pincode (origin) | String | Yes | Pincode of the sender's location. |
| city (origin) | String | Yes | City of the sender. |
| state (origin) | String | Yes | State of the sender. |
| destination_details | Object | Yes | Details about the destination of the consignment. |
| name (destination) | String | Yes | Name of the receiver. |
| phone (destination) | String | Yes | Phone number of the receiver. |
| alternate_phone (destination) | String | No | Alternate phone number of the receiver. |
| address_line_1 (destination) | String | Yes | First line of the receiver's address. |
| address_line_2 (destination) | String | No | Second line of the receiver's address. |
| pincode (destination) | String | Yes | Pincode of the receiver's location. |
| city (destination) | String | Yes | City of the receiver. |
| state (destination) | String | Yes | State of the receiver. |
| return_details | Object | No | Details about the return address for the consignment. |
| address_line_1 (return) | String | Yes | First line of the return address. |
| address_line_2 (return) | String | No | Second line of the return address. |
| city_name (return) | String | Yes | City of the return address. |
| name (return) | String | Yes | Name for the return contact. |
| phone (return) | String | Yes | Phone number for the return contact. |
| pincode (return) | String | Yes | Pincode for the return address. |
| state_name (return) | String | Yes | State of the return address. |
| email (return) | String | No | Email for the return contact. |
| alternate_phone (return) | String | No | Alternate phone number for the return contact. |
| customer_reference_number | String | Yes | Reference number assigned by the customer for tracking purposes. |
| cod_collection_mode | String | No | Mode of COD collection will be `"CASH"`, if applicable. (Mandatory for COD only) |
| cod_amount | String | No | Amount to be collected for COD shipments. (Mandatory for COD only) |
| commodity_id | String | Yes | Identifier for the commodity type being shipped. (list attached below) |
| eway_bill | String | No | E-Way bill number for the shipment, if applicable. |
| is_risk_surcharge_applicable | Boolean | Yes | Indicates whether a risk surcharge applies (true or false). |
| invoice_number | String | No | Invoice number associated with the consignment. |
| invoice_date | String | No | Date of the invoice. |
| reference_number | String | No | Reference number for the consignment, if available. You can pass awb number here. |
| pieces_detail | Array of Object | No | Array containing detailed information about each parcel/piece in the consignment. (mandatory for MPS) |
| description (pieces) | String | No | Description of the individual piece. (mandatory for MPS) |
| declared_value (pieces) | String | No | Declared value of the individual piece. (mandatory for MPS) |
| weight (pieces) | String | No | Weight of the individual piece. (mandatory for MPS) |
| height (pieces) | String | No | Height of the individual piece. (mandatory for MPS) |
| length (pieces) | String | No | Length of the individual piece. (mandatory for MPS) |
| width (pieces) | String | No | Width of the individual piece. (mandatory for MPS) |

## Example Request

```bash
curl -X POST <URL> \
-H "Content-Type: application/json" \
-H "api-key: <API KEY>" \
-d '{...}'   # JSON payload as described above
```

## Reference Lists

- **List of Commodity_id:**
  https://docs.google.com/spreadsheets/d/158LuKmF8mHXSQfXcSE-U_NVeUpz-O1LuNlc1ualKEeI/edit?usp=sharing
- **List of Service_type_id:**
  https://docs.google.com/spreadsheets/d/1pYajATrmH-lay_e7oS47lx_UXNvrtGcL2MoQ693mmuk/edit?usp=sharing

## Response

```json
{
  "status": "OK",
  "data": [
    {
      "success": true,
      "reference_number": "100008518801",
      "courier_partner": null,
      "courier_account": "",
      "courier_partner_reference_number": null,
      "chargeable_weight": 0.025,
      "self_pickup_enabled": true,
      "customer_reference_number": "#100001",
      "pieces": [
        {
          "reference_number": "100008518801001",
          "product_code": ""
        }
      ],
      "barCodeData": ""
    }
  ]
}
```

## Explanation of Key Fields

1. **status:** The status of the API call, such as `"OK"` for a successful request.
2. **data:** Contains an array of consignment details, where each object represents a consignment processed by the API.
3. **success:** A boolean value indicating whether the consignment was processed successfully.

## Response Code Remarks

| Response | Code Remarks |
|---|---|
| 200 | Each consignment will be processed independently. For each consignment, the `success` key will be either true or false. If success is true for a consignment, then the consignment is successfully entered into the DTDC system. If success is false, the consignment is not entered (in case of false, the response contains an error message reason). |
| 400 | There is some validation error in the overall request format. In this case, the complete request is rejected. |
| 401 | There is an authentication error. |

---

*www.dtdc.com*
