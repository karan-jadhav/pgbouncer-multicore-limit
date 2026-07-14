#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from decimal import Decimal

import boto3

INSTANCE_COUNTS = {
    "m7i.4xlarge": 1,
    "c7i.8xlarge": 1,
    "c7i.4xlarge": 2,
}


def hourly_price(client: object, instance_type: str, location: str) -> Decimal:
    response = client.get_products(
        ServiceCode="AmazonEC2",
        Filters=[
            {"Type": "TERM_MATCH", "Field": "instanceType", "Value": instance_type},
            {"Type": "TERM_MATCH", "Field": "location", "Value": location},
            {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": "Linux"},
            {"Type": "TERM_MATCH", "Field": "tenancy", "Value": "Shared"},
            {"Type": "TERM_MATCH", "Field": "preInstalledSw", "Value": "NA"},
            {"Type": "TERM_MATCH", "Field": "capacitystatus", "Value": "Used"},
        ],
        MaxResults=100,
    )
    for raw_product in response["PriceList"]:
        product = json.loads(raw_product)
        for term in product["terms"]["OnDemand"].values():
            for dimension in term["priceDimensions"].values():
                if dimension["unit"] == "Hrs":
                    return Decimal(dimension["pricePerUnit"]["USD"])
    raise RuntimeError(f"no on-demand Linux price found for {instance_type}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=Decimal, required=True)
    parser.add_argument("--location", default="Asia Pacific (Mumbai)")
    args = parser.parse_args()
    pricing = boto3.client("pricing", region_name="us-east-1")
    total = Decimal(0)
    for instance_type, count in INSTANCE_COUNTS.items():
        price = hourly_price(pricing, instance_type, args.location)
        subtotal = price * count * args.hours
        total += subtotal
        print(f"{instance_type:14} x {count}: ${subtotal:.2f}")
    print(f"EC2 estimate for {args.hours} hours: ${total:.2f}")
    print("EBS, data transfer, and taxes are not included.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
