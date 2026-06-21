# DTDC API — REST Tracking
**Reference Document — DTDC API version 2.2**

---

## Introduction

Representational state transfer (REST) or RESTful web service are one way of providing interoperability between computer systems on the Internet. REST-compliant web services allow requesting systems to access and manipulate textual representations of web services using a uniform and predefined set of stateless operations. Other forms of web service exist, which expose their own arbitrary sets of operations such as WSDL and SOAP.

To make a web API call from a client application, you must supply an **authentication token** on the call. The token acts like an electronic key that lets you access the API.

DTDC Tracking services allow third party providers to integrate DTDC tracking services into a platform or website. Once integrated, your application will access DTDC servers over REST style architecture using XML/JSON.

---

## Authentication Token Request

To request an authentication token for a user for the REST web API:

- **Staging:**
  `http://dtdcstagingapi.dtdc.com/dtdc-tracking-api/dtdc-api/api/dtdc/authenticate?username=<username>&password=<password>`
- **Production:**
  `https://blktracksvc.dtdc.com/dtdc-api/api/dtdc/authenticate?username=<username>&password=<password>`

### Query Request Parameters

**HTTP Method:** GET

| Parameter Name | Parameter Value | Remarks |
|---|---|---|
| username* | username | |
| password* | password | |

### Response Status

- **200** – Will send `Token Access key` if authentication is successful
- **201** – `Partial content` (validation failed for request parameters)
- **400** – `Bad Request` (wrong data passed as request parameter)
- **401** – `Unauthorized`
- **500** – `Error Occurred`

---

## XML Format Response

- **Staging:**
  `http://dtdcstagingapi.dtdc.com/dtdc-tracking-api/dtdc-api/rest/XMLCnTrk/getDetails?strcnno=<AWBNo>&TrkType=cnno&addtnlDtl=Y&apikey=<Token Key>`
- **Production:**
  `https://blktracksvc.dtdc.com/dtdc-api/rest/XMLCnTrk/getDetails?strcnno=<AWBNo>&TrkType=cnno&addtnlDtl=Y&apikey=<Token Key>`

### Query Request Parameters

**HTTP Method:** GET

| Parameter Name | Parameter Value | Remarks |
|---|---|---|
| TrkType* | cnno (or) reference | Consignment number tracking (cnno) or Reference number tracking (reference). |
| strcnno* | Consignment number (9 chars with first char as alphabet and the remaining 8 chars in digits) or reference number | |
| addtnlDtl* | Y (or) N | Y – the additional details will be sent in the XML. N – No additional details will be sent. |
| apikey* | Application key to be passed in each request to authenticate the API request | |

### Response XML Consignment Data

| Node Name | Attribute "NAME" for the FIELD node | Remarks | Sample Data |
|---|---|---|---|
| DTDCREPLY | | Root Header | |
| CONSIGNMENT | | Consignment Node | |
| CNHEADER | | Consignment details header | |
| CNTRACK | | True / False. Availability of the consignment details. | 'True' / 'False' |
| FIELD | strShipmentNo | The consignment number | V01197967 |
| | strRefNo | The reference Number | N/A |
| | strCNType | Booked by Direct Party, Walk-in, etc | DP, WI |
| | strCNTypeCode | Direct Party Code | LL676 |
| | strCNTypeName | Direct Party Name | BMP E-GROUP SOLUTION PVT. LTD |
| | strCNProduct | Consignment Product | LITE, PTP, etc. |
| | strModeCode | The billing mode of the consignment Code | AR1/SF1/AC1 |
| | strMode | The billing mode of the consignment | AIR / SURFACE / AIR CARGO |
| | strCNProdCODFOD | The product code | COD/FOD/CUD |
| | strOrigin | Consignment booked by or at the office | AHMEDABAD (VEJALPUR), AHMEDABAD |
| | strOriginRemarks | This field tells about the strOrigin node. The values will be 'Booked By', 'Received From', 'Scanned At', 'Booked At' | Booked At |
| | strBookedOn | Consignment booked on date (DDMMYYYY) | 15062009 |
| | strPieces | The number of pieces in the consignment | 1 |
| | strWeightUnit | Unit of the Weight | Kg |
| | strWeight | Weight of the consignment in Kg | 0.020 Kg |
| | strDestination | The destination place of the consignment | Bangalore |
| | strStatus | The status of the consignment: DELIVERED / DELIVERY PROCESS IN PROGRESS / ATTEMPTED / HELDUP / RTO | DELIVERED |
| | strStatusTransOn | Delivered / Attempted / Heldup / Out for Delivery / Consignment Returned On / FDM Prepared On date (DDMMYYYY) | 16062009 |
| | strStatusTransTime | Delivered / Attempted / Heldup / Out for Delivery / Consignment Returned On / FDM Prepared time (HHMM) | 1234 |
| | strStatusRelCode | Relationship Code | BRO/DAU/EMP |
| | strStatusRelName | Relationship Name | Brother, Daughter, Employee |
| | strRemarks | The receiver details / Heldup due to reason / not delivered due to reason | Shakthi / PARTY NOT AVAILABLE |
| | strNoOfAttempts | The number of attempts for the consignment delivery | 1 |
| | strRtoNumber | RTO consignment number will be available if shipment is RTO'ed | 000000339085 |
| | strActualServiceType | Consignment Product | LITE, PTP, etc. |
| | strExpectedAgent | Expected connection Agent | TNT |
| | strActualAgent | Actual Connection Agent | FEDEX |
| | strConnectionDateTime | Connection Date and Time | 2019/08/04 14:38:10 |
| | strAltReferenceNumber | Alt Ref number | 564000857151 |
| | strAgentConnectionLocation | Agent Connection location | DELHI APEX |
| | strBookingType | Shipment Identification: Domestic "DOM" or International "INT" | DOM |
| | strError | Error Description (if consignment is not found) | NO DATA FOUND FOR THIS CNNO NUMBER |
| CNBODY | | Body Node | |
| CNACTION | | Node for each Action | |
| CNACTIONTRACK | | True / False. Availability of the consignment additional details. | True / False |
| | strCode | Action Code | BKD |
| | strAction | Action Details (e.g., DISPATCHED, BOOKED, RECEIVED, OUT FOR DELIVERY, DELIVERED, NOT DELIVERED, HELDUP, CONSIGNMENT RELEASED, CONSIGNMENT HAS RETURNED, POD DISPATCHED, ARRIVAL AT AIRPORT, CUSTOMS CLEARED, HELDUP AT CUSTOMS) | BOOKED |
| | strManifestNo | Manifest Number | O0154799 |
| | strOrigin | Manifest is dispatched from / Consignment is not delivered at / Out for delivery from / Consignment is released from / Consignment is returned from / Heldup at | SALEM BRANCH, SALEM |
| | strDestination | Manifest is dispatched to / Consignment is released to / Consignment is returned to / Out for delivery by | TRICHY BRANCH, TRICHY |
| | strActionDate | Manifest dispatch date / manifest received date / Out for delivery on / not delivered on / Heldup on / Consignment has returned on / Consignment released on (DDMMYYYY) | 16062009 |
| | strRemarks | Non–delivery reason / Heldup reason | ADDRESS NOT FOUND |
| | strError | Error Description (if additional details are not found) | NO DATA FOUND FOR THIS CNNO NUMBER. |

> **Action codes reference:** https://docs.google.com/spreadsheets/d/10KolSYlWhN4eFZsVSPUxk3YEBsxJvELNpWt-CWxGcFM/edit?usp=drive_web&ouid=113448660306017136829

### Sample Data for XML Output

```xml
<DTDCREPLY xmlns="http://dtdc.com">
  <CONSIGNMENT xmlns="">
    <CNHEADER>
      <CNTRACK>true</CNTRACK>
      <FIELD name="strShipmentNo" value="V34070628"/>
      <FIELD name="strRefNo"/>
      <FIELD name="strCNType" value="CC"/>
      <FIELD name="strCNTypeCode" value="OC014"/>
      <FIELD name="strCNTypeName" value="COCHIN APEX - CO. OWNED CCC"/>
      <FIELD name="strCNProduct" value="PREMIUM EXPRESS PRODUCT"/>
      <FIELD name="strModeCode"/>
      <FIELD name="strMode" value=""/>
      <FIELD name="strCNProdCODFOD"/>
      <FIELD name="strOrigin" value="COCHIN"/>
      <FIELD name="strOriginRemarks" value="Booked By"/>
      <FIELD name="strBookedDate" value="08022017"/>
      <FIELD name="strBookedTime" value="19:26:21"/>
      <FIELD name="strPieces" value="1"/>
      <FIELD name="strWeightUnit" value="KG"/>
      <FIELD name="strWeight" value="0.1000"/>
      <FIELD name="strDestination" value="DELHI"/>
      <FIELD name="strStatus" value="Delivered"/>
      <FIELD name="strStatusTransOn" value="09022017"/>
      <FIELD name="strStatusTransTime" value="1845"/>
      <FIELD name="strStatusRelCode" value="RNM"/>
      <FIELD name="strStatusRelName" value=""/>
      <FIELD name="strRemarks" value="Documents"/>
      <FIELD name="strNoOfAttempts" value="1"/>
      <FIELD name="strRtoNumber"/>
      <FIELD name="strActualServiceType" value="STANDARD"/>
      <FIELD name="strExpectedAgent" value=""/>
      <FIELD name="strActualAgent" value=""/>
      <FIELD name="strConnectionDateTime" value=""/>
      <FIELD name="strAltReferenceNumber" value=""/>
      <FIELD name="strAgentConnectionLocation" value=""/>
      <FIELD name="strBookingType" value="DOM"/>
    </CNHEADER>
    <CNBODY>
      <CNACTIONTRACK>true</CNACTIONTRACK>
      <CNACTION>
        <FIELD name="strCode" value="BKD"/>
        <FIELD name="strAction" value="Booked"/>
        <FIELD name="strManifestNo" value=""/>
        <FIELD name="strOrigin" value="COCHIN APEX"/>
        <FIELD name="strDestination" value=""/>
        <FIELD name="strActionDate" value="08022017"/>
        <FIELD name="strActionTime" value="1926"/>
        <FIELD name="sTrRemarks" value=""/>
      </CNACTION>
      <CNACTION>
        <FIELD name="strCode" value="CDOUT"/>
        <FIELD name="strAction" value="In Transit"/>
        <FIELD name="strManifestNo" value=""/>
        <FIELD name="strOrigin" value="PUNE APEX"/>
        <FIELD name="strDestination" value="COCHIN APEX"/>
        <FIELD name="strActionDate" value="26042016"/>
        <FIELD name="strActionTime" value="0002"/>
        <FIELD name="sTrRemarks" value=""/>
      </CNACTION>
      <CNACTION>
        <FIELD name="strCode" value="OBMD"/>
        <FIELD name="strAction" value="In Transit"/>
        <FIELD name="strManifestNo" value="P7660991"/>
        <FIELD name="strOrigin" value="PUNE APEX"/>
        <FIELD name="strDestination" value="COCHIN APEX"/>
        <FIELD name="strActionDate" value="26042016"/>
        <FIELD name="strActionTime" value="1409"/>
        <FIELD name="sTrRemarks"/>
      </CNACTION>
      <CNACTION>
        <FIELD name="strCode" value="CDIN"/>
        <FIELD name="strAction" value="In Transit"/>
        <FIELD name="strManifestNo" value="1236"/>
        <FIELD name="strOrigin" value="DELHI APEX"/>
        <FIELD name="strDestination"/>
        <FIELD name="strActionDate" value="26012017"/>
        <FIELD name="strActionTime" value="1325"/>
        <FIELD name="sTrRemarks" value=""/>
      </CNACTION>
      <CNACTION>
        <FIELD name="strCode" value="OPMF"/>
        <FIELD name="strAction" value="In Transit"/>
        <FIELD name="strManifestNo" value="V8191377"/>
        <FIELD name="strOrigin" value="COCHIN APEX"/>
        <FIELD name="strDestination" value="DELHI AIRPORT APEX"/>
        <FIELD name="strActionDate" value="09022017"/>
        <FIELD name="strActionTime" value="0050"/>
        <FIELD name="sTrRemarks"/>
      </CNACTION>
      <CNACTION>
        <FIELD name="strCode" value="OBMD"/>
        <FIELD name="strAction" value="In Transit"/>
        <FIELD name="strManifestNo" value="V4492715"/>
        <FIELD name="strOrigin" value="COCHIN APEX"/>
        <FIELD name="strDestination" value="DELHI AIRPORT APEX"/>
        <FIELD name="strActionDate" value="09022017"/>
        <FIELD name="strActionTime" value="0050"/>
        <FIELD name="sTrRemarks"/>
      </CNACTION>
      <CNACTION>
        <FIELD name="strCode" value="OMBM"/>
        <FIELD name="strAction" value="In Transit"/>
        <FIELD name="strManifestNo" value="O8726956"/>
        <FIELD name="strOrigin" value="COCHIN APEX"/>
        <FIELD name="strDestination" value="DELHI AIRPORT APEX"/>
        <FIELD name="strActionDate" value="09022017"/>
        <FIELD name="strActionTime" value="0435"/>
        <FIELD name="sTrRemarks"/>
      </CNACTION>
      <CNACTION>
        <FIELD name="strCode" value="CDOUT"/>
        <FIELD name="strAction" value="In Transit"/>
        <FIELD name="strManifestNo" value="08762261"/>
        <FIELD name="strOrigin" value="COCHIN APEX"/>
        <FIELD name="strDestination" value="DELHI AIRPORT APEX"/>
        <FIELD name="strActionDate" value="09022017"/>
        <FIELD name="strActionTime" value="1001"/>
        <FIELD name="sTrRemarks" value=""/>
      </CNACTION>
      <CNACTION>
        <FIELD name="strCode" value="IPMF"/>
        <FIELD name="strAction" value="In Transit"/>
        <FIELD name="strManifestNo" value="V6016486"/>
        <FIELD name="strOrigin" value="COCHIN APEX"/>
        <FIELD name="strDestination" value="DELHI AIRPORT APEX"/>
        <FIELD name="strActionDate" value="09022017"/>
        <FIELD name="strActionTime" value="1409"/>
        <FIELD name="sTrRemarks" value="0.00"/>
      </CNACTION>
      <CNACTION>
        <FIELD name="strCode" value="OBMD"/>
        <FIELD name="strAction" value="In Transit"/>
        <FIELD name="strManifestNo" value="N9642591"/>
        <FIELD name="strOrigin" value="DELHI AIRPORT APEX"/>
        <FIELD name="strDestination" value="VIJAYANAGAR BRANCH"/>
        <FIELD name="strActionDate" value="09022017"/>
        <FIELD name="strActionTime" value="1435"/>
        <FIELD name="sTrRemarks"/>
      </CNACTION>
      <CNACTION>
        <FIELD name="strCode" value="CDOUT"/>
        <FIELD name="strAction" value="In Transit"/>
        <FIELD name="strManifestNo"/>
        <FIELD name="strOrigin" value="DELHI AIRPORT APEX"/>
        <FIELD name="strDestination" value="VIJAYANAGAR BRANCH"/>
        <FIELD name="strActionDate" value="09022017"/>
        <FIELD name="strActionTime" value="1436"/>
        <FIELD name="sTrRemarks" value=""/>
      </CNACTION>
      <CNACTION>
        <FIELD name="strCode" value="inscan"/>
        <FIELD name="strAction" value="Recieved At Destination"/>
        <FIELD name="strManifestNo" value="V6933762"/>
        <FIELD name="strOrigin" value="VIJAYANAGAR BRANCH"/>
        <FIELD name="strDestination" value="VIJAYANAGAR BRANCH"/>
        <FIELD name="strActionDate" value="09022017"/>
        <FIELD name="strActionTime" value="1503"/>
        <FIELD name="sTrRemarks" value="0.00"/>
      </CNACTION>
      <CNACTION>
        <FIELD name="strCode" value="IPMF"/>
        <FIELD name="strAction" value="In Transit"/>
        <FIELD name="strManifestNo" value="V6933762"/>
        <FIELD name="strOrigin" value="DELHI APEX"/>
        <FIELD name="strDestination" value="VIJAYANAGAR BRANCH"/>
        <FIELD name="strActionDate" value="09022017"/>
        <FIELD name="strActionTime" value="1503"/>
        <FIELD name="sTrRemarks" value="0.00"/>
      </CNACTION>
      <CNACTION>
        <FIELD name="strCode" value="OUTDLV"/>
        <FIELD name="strAction" value="Out For Delivery"/>
        <FIELD name="strManifestNo" value=""/>
        <FIELD name="strOrigin" value="VIJAYANAGAR BRANCH"/>
        <FIELD name="strDestination" value=""/>
        <FIELD name="strActionDate" value="09022017"/>
        <FIELD name="strActionTime" value="1514"/>
        <FIELD name="sTrRemarks" value=""/>
      </CNACTION>
      <CNACTION>
        <FIELD name="strCode" value="DLV"/>
        <FIELD name="strAction" value="Delivered"/>
        <FIELD name="strManifestNo" value=""/>
        <FIELD name="strOrigin" value="VIJAYANAGAR BRANCH"/>
        <FIELD name="strDestination" value=""/>
        <FIELD name="strActionDate" value="09022017"/>
        <FIELD name="strActionTime" value="1845"/>
        <FIELD name="sTrRemarks" value="SIGNTURE"/>
      </CNACTION>
    </CNBODY>
  </CONSIGNMENT>
</DTDCREPLY>
```

---

## JSON Format Response

- **Staging:** `http://dtdcstagingapi.dtdc.com/dtdc-tracking-api/dtdc-api/rest/JSONCnTrk/getTrackDetails`
- **Production:** `https://blktracksvc.dtdc.com/dtdc-api/rest/JSONCnTrk/getTrackDetails`

### Query Request Parameters

**HTTP Method:** POST

| Parameter Name | Parameter Value | Remarks |
|---|---|---|
| trkType* | cnno (or) reference | Consignment number tracking (cnno) or Reference number tracking (reference). |
| strcnno* | Consignment number (9 chars with first char as alphabet and the remaining 8 chars in digits) or reference number | |
| addtnlDtl* | Y (or) N | Y – the additional details will be sent in the XML. N – No additional details will be sent. |
| X-Access-Token* | Token key to be passed in **header** request to authenticate the API request | |

### Response JSON Consignment Data

| Node Name | Sub Node Name | Remarks | Sample Data |
|---|---|---|---|
| statusCode | | Standard Http request code. 200 – Success and tracking details; 206 – Partial content (validation failed); 400 – Bad Request; 401 – Unauthorized; 500 – Error Occurred | 200 |
| statusFlag | | True / False. Availability of the consignment details. | True / False |
| status | | Tracking status type SUCCESS/FAILED. Availability of the consignment details. | SUCCESS |
| errorDetails | | Will contain error details with error field Name and the error message | `[ { "name": "strShipmentNo", "value": "11111" }, { "name": "strError", "value": "NO DATA FOUND FOR THIS CNNO NUMBER" } ]` |
| trackHeader | strShipmentNo | The consignment number | V01197967 |
| | strRefNo | The reference Number | N/A |
| | strCNType | Booked by Direct Party, Walk-in, etc | DP, WI |
| | strCNTypeCode | Direct Party Code | LL676 |
| | strCNTypeName | Direct Party Name | BMP E-GROUP SOLUTION PVT. LTD |
| | strCNProduct | Consignment Product | LITE, PTP, etc. |
| | strModeCode | The billing mode of the consignment Code | AR1/SF1/AC1 |
| | strMode | The billing mode of the consignment | AIR / SURFACE / AIR CARGO |
| | strCNProdCODFOD | The product code | COD/FOD/CUD |
| | strOrigin | Consignment booked by or at the office | AHMEDABAD (VEJALPUR), AHMEDABAD |
| | strOriginRemarks | This field tells about the strOrigin node. The values will be 'Booked By', 'Received From', 'Scanned At', 'Booked At' | Booked At |
| | strBookedOn | Consignment booked on date (DDMMYYYY) | 15062009 |
| | strPieces | The number of pieces in the consignment | 1 |
| | strWeightUnit | Unit of the Weight | Kg |
| | strWeight | Weight of the consignment in Kg | 0.020 Kg |
| | strDestination | The destination place of the consignment | Bangalore |
| | strStatus | The status of the consignment: DELIVERED / DELIVERY PROCESS IN PROGRESS / ATTEMPTED / HELDUP / RTO | DELIVERED |
| | strStatusTransOn | Delivered / Attempted / Heldup / Out for Delivery / Consignment Returned On / FDM Prepared On date (DDMMYYYY) | 16062009 |
| | strStatusTransTime | Delivered / Attempted / Heldup / Out for Delivery / Consignment Returned On / FDM Prepared time (HHMM) | 1234 |
| | strStatusRelCode | Relationship Code | BRO/DAU/EMP |
| | strStatusRelName | Relationship Name | Brother, Daughter, Employee |
| | strRemarks | The receiver details / Heldup due to reason / not delivered due to reason | Shakthi / PARTY NOT AVAILABLE |
| | strNoOfAttempts | The number of attempts for the consignment delivery | 1 |
| | strRtoNumber | RTO consignment number will be available if shipment is RTO'ed | 000000339085 |
| | strError | Error Description (if consignment is not found) | NO DATA FOUND FOR THIS CNNO NUMBER |
| trackDetails | | True / False. Availability of the consignment additional details. | True / False |
| | strCode | Action Code | BKD |
| | strAction | Action Details (e.g., DISPATCHED, RECEIVED, OUT FOR DELIVERY, DELIVERED, NOT DELIVERED, HELDUP, CONSIGNMENT RELEASED, CONSIGNMENT HAS RETURNED, POD DISPATCHED, ARRIVAL AT AIRPORT, CUSTOMS CLEARED, HELDUP AT CUSTOMS) | BOOKED |
| | strManifestNo | Manifest Number | O0154799 |
| | strOrigin | Manifest is dispatched from / Consignment is not delivered at / Out for delivery from / Consignment is released from / Consignment is returned from / Heldup at | SALEM BRANCH, SALEM |
| | strDestination | Manifest is dispatched to / Consignment is released to / Consignment is returned to / Out for delivery by | TRICHY BRANCH, TRICHY |
| | strActionDate | Manifest dispatch date / manifest received date / Out for delivery on / not delivered on / Heldup on / Consignment has returned on / Consignment released on (DDMMYYYY) | 16062009 |
| | strRemarks | Non–delivery reason / Heldup reason | ADDRESS NOT FOUND |

### Sample Data for JSON Output

```json
{
  "statusCode": 200,
  "statusFlag": true,
  "status": "SUCCESS",
  "errorDetails": null,
  "trackHeader": {
    "strShipmentNo": "B32242001",
    "strRefNo": "",
    "strCNType": "CP",
    "strCNTypeCode": "BF014",
    "strCNTypeName": "AVENUE ROAD",
    "strCNProduct": "LITE",
    "strModeCode": "",
    "strMode": "",
    "strCNProdCODFOD": "",
    "strOrigin": "BANGALORE",
    "strOriginRemarks": "Booked By",
    "strBookedDate": "21062017",
    "strBookedTime": "15:30:25",
    "strPieces": "1",
    "strWeightUnit": "KG",
    "strWeight": "0.1000",
    "strDestination": "MUMBAI",
    "strStatus": "Delivered",
    "strStatusTransOn": "21062017",
    "strStatusTransTime": "1614",
    "strStatusRelCode": "",
    "strStatusRelName": "",
    "strRemarks": "SIGN",
    "strNoOfAttempts": "1",
    "strRtoNumber": ""
  },
  "trackDetails": [
    {
      "strCode": "BKD",
      "strAction": "Booked",
      "strManifestNo": "",
      "strOrigin": "BANGALORE SURFACE APEX",
      "strDestination": "",
      "strActionDate": "21062017",
      "strActionTime": "1530",
      "sTrRemarks": ""
    },
    {
      "strCode": "OBMD",
      "strAction": "In Transit",
      "strManifestNo": "B7701202",
      "strOrigin": "BANGALORE SURFACE APEX",
      "strDestination": "MUMBAI APEX",
      "strActionDate": "21062017",
      "strActionTime": "1533",
      "sTrRemarks": ""
    },
    {
      "strCode": "OPMF",
      "strAction": "In Transit",
      "strManifestNo": "B7701203",
      "strOrigin": "BANGALORE SURFACE APEX",
      "strDestination": "MUMBAI APEX",
      "strActionDate": "21062017",
      "strActionTime": "1533",
      "sTrRemarks": ""
    },
    {
      "strCode": "IBMD",
      "strAction": "In Transit",
      "strManifestNo": "B7701202",
      "strOrigin": "BANGALORE SURFACE APEX",
      "strDestination": "MUMBAI APEX",
      "strActionDate": "21062017",
      "strActionTime": "1533",
      "sTrRemarks": ""
    },
    {
      "strCode": "CDOUT",
      "strAction": "In Transit",
      "strManifestNo": "",
      "strOrigin": "BANGALORE SURFACE APEX",
      "strDestination": "MUMBAI APEX",
      "strActionDate": "21062017",
      "strActionTime": "1546",
      "sTrRemarks": ""
    },
    {
      "strCode": "CDIN",
      "strAction": "In Transit",
      "strManifestNo": "",
      "strOrigin": "BANGALORE SURFACE APEX",
      "strDestination": "MUMBAI APEX",
      "strActionDate": "21062017",
      "strActionTime": "1555",
      "sTrRemarks": ""
    },
    {
      "strCode": "IPMF",
      "strAction": "In Transit",
      "strManifestNo": "B7701203",
      "strOrigin": "BANGALORE SURFACE APEX",
      "strDestination": "MUMBAI APEX",
      "strActionDate": "21062017",
      "strActionTime": "1603",
      "sTrRemarks": "0.00"
    },
    {
      "strCode": "IBMD",
      "strAction": "In Transit",
      "strManifestNo": "B7701202",
      "strOrigin": "BANGALORE SURFACE APEX",
      "strDestination": "MUMBAI APEX",
      "strActionDate": "21062017",
      "strActionTime": "1603",
      "sTrRemarks": ""
    },
    {
      "strCode": "OBMD",
      "strAction": "In Transit",
      "strManifestNo": "B7701202",
      "strOrigin": "BANGALORE SURFACE APEX",
      "strDestination": "MUMBAI APEX",
      "strActionDate": "21062017",
      "strActionTime": "1603",
      "sTrRemarks": ""
    },
    {
      "strCode": "OUTDLV",
      "strAction": "Out For Delivery",
      "strManifestNo": "",
      "strOrigin": "MUMBAI APEX",
      "strDestination": "",
      "strActionDate": "21062017",
      "strActionTime": "1611",
      "sTrRemarks": ""
    },
    {
      "strCode": "DLV",
      "strAction": "Delivered",
      "strManifestNo": "",
      "strOrigin": "MUMBAI APEX",
      "strDestination": "",
      "strActionDate": "21062017",
      "strActionTime": "1614",
      "sTrRemarks": "SIGN"
    }
  ]
}
```
