---
label: team_lead
---

# Fraud Signals Reference

## Account and contact signals

Review an order when the customer creates an account minutes before a large purchase and uses a disposable email. Another signal is a phone number that does not match the billing address country.
A customer identity is also risky when the name on the account differs from the billing name and payment name.

## Shipping and address signals

Review an order when the shipping address changes after payment or the address is a freight forwarder. A mismatch between the city and postal code is a strong signal.
Orders sent to a new address with a high-value item over 400 dollars should be checked before release.

## Payment and chargeback signals

Review an order when the charge or card check has an AVS mismatch, CVV failure, or repeated failed payment attempts from the same device. A prior chargeback on the same card or email increases risk.
Gift card payments with a new account are also reviewed when the order total is above 300 dollars.

## Order pattern signals

Review multiple orders from one account in a short period, especially more than 5 orders within 24 hours. Another signal is ordering one of each high-value product such as the PCK-310 pack or STV-77 stove.
Unusual size or colour mix can be a signal, but it is not enough on its own.

## Escalation steps for fraud review

Place the order on hold, document the signals, and escalate to fraud operations. Do not contact the customer about suspected fraud. The fraud operations team will approve release or cancellation.
Support agents and team leads may not reveal internal fraud flags to a customer.

## What team leads may void

A team lead may void an order before shipment when there are two or more strong fraud signals and the total is over 200 dollars. Team leads must not void orders based only on a customer complaint.
Voided orders are refunded to the original payment method within 5 business days.

## Documentation rules

Record only the factual signals observed, not personal opinions. Use the internal fraud note field and include order numbers, dates, and payment responses.
Never copy fraud notes into customer-facing messages. Internal fraud documentation is retained for audits.
