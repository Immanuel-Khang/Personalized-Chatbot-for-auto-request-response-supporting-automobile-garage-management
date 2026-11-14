#!/usr/bin/env python3
"""Test script to debug search issues."""
from app.services.product import DbProductService
import json

def main():
    service = DbProductService()

    print("=== Issue 1: Tìm MIGHTY EX900XL ===")
    products = service.find_model(name='MIGHTY EX900XL', limit=5)
    print(f"Kết quả find_model('MIGHTY EX900XL'): {len(products)} xe")
    for p in products:
        print(f"  - {p.name} (ID: {p.id}, giá: {p.list_price_vnd:,} VND)")

    # Thử search thay vì find_model
    print("\nThử search('MIGHTY EX900XL'):")
    products2 = service.search(query='MIGHTY EX900XL', limit=5)
    print(f"Kết quả search: {len(products2)} xe")
    for p in products2:
        print(f"  - {p.name} (ID: {p.id}, giá: {p.list_price_vnd:,} VND)")

    print("\n" + "="*60)
    print("=== Issue 2: Xe đắt nhất trong toàn bộ catalog ===")

    # Lấy tất cả xe để tìm xe đắt nhất
    all_vehicles = service.list_vehicles(limit=100)  # Giới hạn 100 xe
    print(f"\nTổng số xe ACTIVE có giá: {len(all_vehicles)}")

    if all_vehicles:
        sorted_by_price = sorted(all_vehicles, key=lambda x: x.list_price_vnd, reverse=True)
        print(f"\nTop 5 xe đắt nhất:")
        for i, p in enumerate(sorted_by_price[:5], 1):
            print(f"  {i}. {p.name}: {p.list_price_vnd:,} VND")

        print(f"\nXe rẻ nhất: {sorted_by_price[-1].name}: {sorted_by_price[-1].list_price_vnd:,} VND")

    # Kiểm tra vehicle_summary
    summary = service.vehicle_summary()
    print(f"\nVehicle summary: {summary}")

if __name__ == '__main__':
    main()
