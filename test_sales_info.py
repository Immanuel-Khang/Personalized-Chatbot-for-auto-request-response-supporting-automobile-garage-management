#!/usr/bin/env python3
"""Test script to verify DbProductService returns sales_info."""
from app.services.product import DbProductService
import json

def main():
    service = DbProductService()

    print("=== Testing DbProductService.search() ===")
    products = service.search(query='WONDER', limit=1)

    if products:
        p = products[0]
        print(f'✓ Product: {p.name}')
        print(f'  Price: {p.list_price_vnd:,} VND')
        print(f'  Price as of: {p.price_as_of}')
        print(f'  Sales info present: {p.sales_info is not None}')

        if p.sales_info:
            print(f'\n📊 Sales Info:')
            print(json.dumps(p.sales_info, indent=2, ensure_ascii=False))
            print(f'\n✓ SUCCESS: sales_info returned correctly')
        else:
            print(f'\n✗ FAIL: sales_info is None')
    else:
        print('✗ No products found')

if __name__ == '__main__':
    main()
