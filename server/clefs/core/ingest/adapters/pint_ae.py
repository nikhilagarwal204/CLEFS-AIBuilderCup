"""PINT AE (UAE e-invoicing, Peppol UBL 2.1) adapter: a documented stub
(02 spec 2.2 step 8).

The UAE e-invoicing mandate starts in phases from 2027 (UNVERIFIED dates:
confirm against the Ministry of Finance timeline before building). Until
then no OTA or supplier sends UBL XML, so this only records the mapping the
real parser will implement:

| Canonical field (OTA_STATEMENT / CREDIT_NOTE / SUPPLIER_INVOICE) | UBL path |
|---|---|
| doc_no | /Invoice/cbc:ID (CreditNote: /CreditNote/cbc:ID) |
| doc date (invoice_date) | /Invoice/cbc:IssueDate |
| related_doc_no | cac:BillingReference/cac:InvoiceDocumentReference/cbc:ID |
| currency | /Invoice/cbc:DocumentCurrencyCode |
| supplier | cac:AccountingSupplierParty/cac:Party/cac:PartyLegalEntity/cbc:RegistrationName |
| supplier TRN | cac:AccountingSupplierParty/cac:Party/cac:PartyTaxScheme/cbc:CompanyID |
| one line per cac:InvoiceLine (cac:CreditNoteLine) | |
| - ota_res_id / item | cac:InvoiceLine/cac:Item/cbc:Name or cac:InvoiceLine/cac:DocumentReference/cbc:ID |
| - qty | cac:InvoiceLine/cbc:InvoicedQuantity |
| - unit_cost_minor | cac:InvoiceLine/cac:Price/cbc:PriceAmount |
| - amount_minor / commission_minor | cac:InvoiceLine/cbc:LineExtensionAmount |
| - VAT per line | cac:InvoiceLine/cac:Item/cac:ClassifiedTaxCategory/cbc:Percent |
| VAT total | cac:TaxTotal/cbc:TaxAmount (and cac:TaxSubtotal per category) |
| printed total (gate) | cac:LegalMonetaryTotal/cbc:TaxExclusiveAmount; payable cbc:PayableAmount |

Amounts are decimal strings in the document currency; the parser will turn
them into minor units with core.money.to_minor, and the printed-total gate
will compare the lines with cbc:TaxExclusiveAmount like a PDF's total.
"""

from __future__ import annotations


def parse_ubl(xml: str) -> list[dict]:
    """UBL invoice XML -> canonical statement lines. Not built yet: raises
    NotImplementedError, and ingest turns that into a FAILED batch with
    code xml_not_supported."""
    raise NotImplementedError("PINT AE e-invoice parsing arrives with UAE e-invoicing (2027)")
